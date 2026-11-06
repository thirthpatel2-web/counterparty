"""Deal memory. Only outcomes computed by our own code are stored, never vendor free text and never a
lesson: what to do next time is left for Hindsight to work out from the outcomes."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import time
from typing import Protocol

BUYER_MISSION = ("I am a procurement buyer negotiating electronic components. I judge vendors by what "
                 "they did in closed deals, not by what they claimed.")
RETAIN_MISSION = ("Keep per-vendor negotiation behaviour: final-price claims and whether the vendor went lower "
                  "afterwards, closing prices, rounds, walk-aways, quarter-end months and pressure claims.")
# Plain integers with 0 for "unknown": Hindsight's reflect answers HTTP 500 to nullable types.
BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "final_claims": {"type": "string", "enum": ["usually bluffs", "usually honest", "unknown"],
                         "description": "Whether this vendor's 'final price' claims held up in past deals"},
        "safe_rounds": {"type": "integer",
                        "description": "Most rounds this vendor has negotiated without walking away, 0 if unknown"},
        "walked_at_round": {"type": "integer",
                            "description": "Lowest round at which this vendor walked away, 0 if it never did"},
        "lowest_close": {"type": "integer", "description": "Lowest closing price reached in rupees, 0 if none"},
        "quarter_end": {"type": "string", "enum": ["cheaper", "no difference", "unknown"],
                        "description": "How prices in quarter-end months compared"},
        "advice": {"type": "string", "description": "One sentence for the buyer before this negotiation"},
    },
    "required": ["final_claims", "safe_rounds", "walked_at_round", "lowest_close", "quarter_end", "advice"],
}


def claim_parts(claim) -> tuple[int, int, int]:
    """(round, price, lowest ask after the claim). Runs recorded before the lowest ask was tracked carry two."""
    round_no, price, *rest = claim
    return round_no, price, rest[0] if rest else price


@dataclass(frozen=True)
class DealRecord:
    deal_id: str
    vendor_slug: str
    vendor_name: str
    month: int
    year: int
    opening: int
    closing: int
    outcome: str                  # closed | vendor_walked
    rounds: int
    final_claims: tuple = ()      # (round, price, lowest ask after it) from the simulator's flag
    false_claims: tuple = ()      # enums of pressure claims proven false by the closing price
    accepted_final: bool = False  # the buyer accepted a "final" without testing it

    @property
    def when(self) -> str:
        return datetime(self.year, self.month, 15, tzinfo=timezone.utc).isoformat()

    @property
    def quarter_end(self) -> bool:
        return self.month % 3 == 0

    def to_text(self) -> str:
        period = f"{self.year}-{self.month:02d}" + (" (quarter-end month)" if self.quarter_end else "")
        lines = [f"Deal {self.deal_id} with {self.vendor_name} in {period}: opening price ₹{self.opening:,}."]
        for n, claim in enumerate(self.final_claims, 1):
            round_no, price, lowest_after = claim_parts(claim)
            said = f"At round {round_no} {self.vendor_name} claimed ₹{price:,} was final."
            if lowest_after < price:
                lines.append(f"{said} That claim was FALSE: the vendor later went down to ₹{lowest_after:,}.")
            elif n == len(self.final_claims) and self.accepted_final:
                lines.append(f"{said} The buyer accepted without testing it, so it is UNKNOWN whether it was a bluff.")
            else:
                lines.append(f"{said} When pushed, the vendor did not go lower.")
        if self.outcome == "closed":
            lines.append(f"Closed at ₹{self.closing:,} after {self.rounds} rounds.")
        else:
            lines.append(f"{self.vendor_name} WALKED AWAY at round {self.rounds} and the deal was lost. "
                         f"The last price on the table had been ₹{self.closing:,}.")
        for claim in self.false_claims:
            lines.append(f"{self.vendor_name} made a {claim.replace('_', ' ')} pressure claim that proved FALSE.")
        return " ".join(lines)

    @classmethod
    def from_dict(cls, data: dict) -> "DealRecord":
        return cls(**{**data, "final_claims": tuple(tuple(c) for c in data["final_claims"]),
                      "false_claims": tuple(data["false_claims"])})


@dataclass(frozen=True)
class MemoryItem:
    id: str
    text: str
    document_id: str
    kind: str = "experience"


def brief_text(brief: dict) -> str:
    if not brief:
        return ""
    known = {k: v for k, v in brief.items() if k != "advice" and v not in (None, 0, "unknown")}
    facts = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in known.items()) or "nothing established yet"
    return f"Briefing from memory ({facts}). {brief.get('advice', '')}".strip()


class Memory(Protocol):
    def reset(self) -> None: ...
    def retain(self, record: DealRecord) -> None: ...
    def recall(self, vendor_slug: str, vendor_name: str, month: int) -> list[MemoryItem]: ...
    def brief(self, vendor_slug: str, vendor_name: str, month: int) -> dict: ...
    def forgotten_copy(self, document_ids: set) -> "Memory": ...


@dataclass
class LocalMemory:
    """Rehearsal and test stand-in. It is not Hindsight: its briefing is plain arithmetic."""
    records: list = field(default_factory=list)
    limit: int = 12

    def reset(self) -> None:
        self.records.clear()

    def retain(self, record: DealRecord) -> None:
        self.records.append(record)

    def _of(self, vendor_slug):
        return [r for r in self.records if r.vendor_slug == vendor_slug]

    def recall(self, vendor_slug, vendor_name, month):
        rows = self._of(vendor_slug)[-self.limit:]
        return [MemoryItem(r.deal_id, r.to_text(), r.deal_id) for r in reversed(rows)]

    def brief(self, vendor_slug, vendor_name, month):
        rows = self._of(vendor_slug)
        if not rows:
            return {}
        claims = [claim_parts(c) for r in rows for c in r.final_claims]
        false = sum(after < price for _, price, after in claims)
        walked = [r.rounds for r in rows if r.outcome != "closed"]
        closed = [r for r in rows if r.outcome == "closed"]
        qe = [r.closing for r in closed if r.quarter_end]
        other = [r.closing for r in closed if not r.quarter_end]
        return {
            "final_claims": "usually bluffs" if false * 2 > len(claims) else "usually honest" if claims and not false
            else "unknown",
            "safe_rounds": max((r.rounds for r in closed), default=0),
            "walked_at_round": min(walked, default=0),
            "lowest_close": min((r.closing for r in closed), default=0),
            "quarter_end": ("cheaper" if min(qe) < min(other) * 0.97 else "no difference") if qe and other else "unknown",
            "advice": "Rehearsal summary computed from records, not a Hindsight reflection.",
        }

    def forgotten_copy(self, document_ids):
        return LocalMemory([r for r in self.records if r.deal_id not in document_ids], self.limit)


def rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "quota exhausted" in text


def patiently(call, attempts: int = 8, wait: float = 15.0):
    """Retries a Hindsight call while its LLM provider is rate-limited per minute. Anything else fails at once."""
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            if not rate_limited(exc) or attempt + 1 == attempts:
                raise
            time.sleep(min(60.0, wait * (attempt + 1)))


class HindsightMemory:
    """Hindsight Cloud or self-hosted memory: one bank per race, one document per deal, tagged by vendor."""
    QUERY = ("How does {name} really negotiate, especially in {period}? Which final-price claims were false, what "
             "closing prices were reached, at which round did they walk away, and how do quarter-end months differ?")

    def __init__(self, client, bank_id: str = "", skepticism: int = 5):
        self.client = client
        self.bank_id = bank_id
        self.skepticism = skepticism
        self.brief_failures = 0

    @staticmethod
    def _period(month: int) -> str:
        return f"month {month}" + (", a quarter-end month" if month % 3 == 0 else "")

    def reset(self) -> None:
        # A fresh bank per race keeps earlier runs replayable, including Forget on their memories.
        self.bank_id = f"counterparty-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10_000:04d}"
        self.client.create_bank(
            self.bank_id, name="Counterparty buyer", mission=BUYER_MISSION, retain_mission=RETAIN_MISSION,
            disposition_skepticism=self.skepticism, disposition_literalism=4, disposition_empathy=1,
            reflect_mission=BUYER_MISSION,
        )

    def retain(self, record: DealRecord) -> None:
        patiently(lambda: self.client.retain(
            self.bank_id, record.to_text(), timestamp=record.when, context="closed procurement negotiation",
            document_id=record.deal_id,
            tags=[f"vendor:{record.vendor_slug}", f"quarter_end:{'yes' if record.quarter_end else 'no'}"],
            metadata={"vendor": record.vendor_slug, "outcome": record.outcome},
        ))

    def recall(self, vendor_slug, vendor_name, month):
        response = self.client.recall(
            self.bank_id, self.QUERY.format(name=vendor_name, period=self._period(month)),
            types=["world", "experience", "observation"], budget="mid", max_tokens=1200,
            tags=[f"vendor:{vendor_slug}"], tags_match="any_strict",
        )
        return [MemoryItem(str(f.id), f.text, getattr(f, "document_id", None) or "", getattr(f, "type", None) or "fact")
                for f in response.results]

    def brief(self, vendor_slug, vendor_name, month):
        """Hindsight reflects over the vendor's history, shaped by the bank's skeptical disposition.

        No briefing for a vendor with no history. A failed briefing is counted and the deal goes ahead on recall.
        """
        if not self.recall(vendor_slug, vendor_name, month):
            return {}
        try:
            response = patiently(lambda: self.client.reflect(
                self.bank_id, f"Brief a buyer about to negotiate with {vendor_name} in {self._period(month)}. "
                + self.QUERY.format(name=vendor_name, period=self._period(month)),
                budget="low", response_schema=BRIEF_SCHEMA, tags=[f"vendor:{vendor_slug}"], tags_match="any_strict",
            ))
        except Exception:
            self.brief_failures += 1
            return {}
        data = response.structured_output
        if isinstance(data, str):
            data = json.loads(data)
        return data or {"advice": response.text}

    def forgotten_copy(self, document_ids, timeout: float = 180.0):
        """A real Hindsight forget: clone the bank, delete the deals from the clone, wait for re-consolidation."""
        from hindsight_client.hindsight_client import _run_async

        target = f"{self.bank_id}-forget-{time.time_ns() % 1_000_000:06d}"
        operation = self.client.clone_bank(self.bank_id, target)
        self._wait(lambda: _run_async(self.client.operations.get_operation_status(self.bank_id, operation)).status,
                   timeout)
        for document_id in document_ids:
            _run_async(self.client.documents.delete_document(target, document_id))

        def pending():
            ops = _run_async(self.client.operations.list_operations(target, status="pending", limit=1))
            busy = _run_async(self.client.operations.list_operations(target, status="processing", limit=1))
            return "busy" if (getattr(ops, "operations", None) or getattr(busy, "operations", None)) else "completed"

        self._wait(pending, timeout)
        return HindsightMemory(self.client, target)

    @staticmethod
    def _wait(status, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = status()
            if state == "completed":
                return
            if state in {"failed", "cancelled"}:
                raise RuntimeError(f"Hindsight operation {state}")
            time.sleep(2)
        raise TimeoutError("Hindsight did not finish in time")
