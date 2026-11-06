"""Deal memory. Only outcomes computed by our own code are stored, never vendor free text and never a
lesson: what to do next time is left for Hindsight to work out from the outcomes."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import random
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


BLUFF_MARGIN = 0.01  # a "final" that later moves less than 1% held; the vendor was at its floor


def was_bluff(price: int, lowest_after: int) -> bool:
    return lowest_after < price * (1 - BLUFF_MARGIN)


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
    asks: tuple = ()              # (round, ask, said_final) for every vendor reply, for the notes style

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
            if was_bluff(price, lowest_after):
                lines.append(f"{said} That claim was FALSE: the vendor later went down to ₹{lowest_after:,}.")
            elif lowest_after < price:
                lines.append(f"{said} It held: the vendor later moved only ₹{price - lowest_after:,}, under 1%.")
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

    def to_notes(self) -> str:
        """A buyer's own messy note on the deal: varied wording, irrelevant detail, and no verdicts.

        Nothing says whether a "final" was true. Hindsight has to work that out from the prices that followed,
        the way it would from real purchase notes. The wording is seeded by the deal, so it is reproducible.
        """
        rng = random.Random(self.deal_id)
        months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                  "November", "December"]
        rep = rng.choice(["Rahul", "Priya", "Arjun", "Meera", "Vikram", "Ananya"])
        when = rng.choice([f"{months[self.month - 1]} {self.year}", f"mid-{months[self.month - 1]}",
                           f"the {months[self.month - 1]} order"])
        opener = rng.choice([f"Call with {rep} from {self.vendor_name} about {when}.",
                             f"{self.vendor_name} ({rep}) quoted us for {when}.",
                             f"Notes, {when}: {self.vendor_name}, spoke to {rep}."])
        filler = rng.choice(["Lead time 3 weeks as usual.", "They asked about our Q4 volumes.",
                             "Delivery to the Peenya warehouse.", "Rep mentioned a new SMT line.", ""])
        said = []
        for round_no, ask, final in self.asks:
            if round_no == 0:
                said.append(rng.choice([f"Started at {ask:,}.", f"First quote {ask:,}.", f"Opening ask was Rs {ask:,}."]))
            elif final:
                said.append(rng.choice([f"Round {round_no}: '{ask:,} is our final price.'",
                                        f"Then {rep} said {ask:,} was final, best they can do.",
                                        f"At {ask:,} they said that's it, final."]))
            else:
                said.append(rng.choice([f"Came down to {ask:,}.", f"Round {round_no}: {ask:,}.", f"Next offer {ask:,}."]))
        if self.outcome == "closed":
            end = rng.choice([f"Signed at {self.closing:,}.", f"We closed at Rs {self.closing:,}.",
                              f"PO raised for {self.closing:,}."])
        else:
            end = rng.choice(["They pulled the quote after our next counter.", f"{rep} stopped the talks and withdrew.",
                              "No deal, they walked when we pushed again."])
        return " ".join(x for x in [opener, *said, end, filler] if x)

    @classmethod
    def from_dict(cls, data: dict) -> "DealRecord":
        return cls(**{**data, "final_claims": tuple(tuple(c) for c in data["final_claims"]),
                      "false_claims": tuple(data["false_claims"]),
                      "asks": tuple(tuple(a) for a in data.get("asks", ()))})


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
        false = sum(was_bluff(price, after) for _, price, after in claims)
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


def patiently(call, attempts: int = 14, wait: float = 15.0):
    """Retries a Hindsight call while its LLM provider is rate-limited, for up to about 20 minutes.

    Self-hosted Hindsight on a free LLM tier can mark the provider as blocked for several minutes after a burst.
    Anything other than a rate limit fails at once, including running out of credit.
    """
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            if not rate_limited(exc) or attempt + 1 == attempts:
                raise
            time.sleep(min(120.0, wait * (attempt + 1)))


class HindsightMemory:
    """Hindsight Cloud or self-hosted memory: one bank per race, one document per deal, tagged by vendor."""
    QUERY = ("How does {name} really negotiate, especially in {period}? Which final-price claims were false, what "
             "closing prices were reached, at which round did they walk away, and how do quarter-end months differ?")

    def __init__(self, client, bank_id: str = "", skepticism: int = 5, style: str = "labelled"):
        self.client = client
        self.style = style  # "labelled" records carry verdicts; "notes" records are a buyer's messy notes
        self.bank_id = bank_id
        self.skepticism = skepticism
        self.brief_failures = 0

    @staticmethod
    def _period(month: int) -> str:
        return f"month {month}" + (", a quarter-end month" if month % 3 == 0 else "")

    def reset(self) -> None:
        # A fresh bank per race keeps earlier runs replayable, including Forget on their memories.
        self.use_bank(f"counterparty-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10_000:04d}")

    def use_bank(self, bank_id: str) -> None:
        """Creates the bank, or keeps an existing one's memories and refreshes its settings."""
        self.bank_id = bank_id
        self.client.create_bank(
            self.bank_id, name="Counterparty buyer", mission=BUYER_MISSION, retain_mission=RETAIN_MISSION,
            disposition_skepticism=self.skepticism, disposition_literalism=4, disposition_empathy=1,
            reflect_mission=BUYER_MISSION,
        )

    def retain(self, record: DealRecord) -> None:
        text = record.to_notes() if self.style == "notes" else record.to_text()
        patiently(lambda: self.client.retain(
            self.bank_id, text, timestamp=record.when, context="buyer's notes on a supplier negotiation",
            document_id=record.deal_id,
            tags=[f"vendor:{record.vendor_slug}", f"quarter_end:{'yes' if record.quarter_end else 'no'}"],
            metadata={"vendor": record.vendor_slug, "outcome": record.outcome},
        ))

    def retain_note(self, vendor_slug: str, text: str, when: str, document_id: str) -> None:
        """A note from a buyer's own purchase history, as written, for vendors outside the simulator."""
        patiently(lambda: self.client.retain(
            self.bank_id, text, timestamp=when, context="buyer's notes on a supplier negotiation",
            document_id=document_id, tags=[f"vendor:{vendor_slug}"], metadata={"vendor": vendor_slug},
        ))

    def recall(self, vendor_slug, vendor_name, month):
        try:
            response = self.client.recall(
                self.bank_id, self.QUERY.format(name=vendor_name, period=self._period(month)),
                types=["world", "experience", "observation"], budget="mid", max_tokens=1200,
                tags=[f"vendor:{vendor_slug}"], tags_match="any_strict",
            )
        except Exception as exc:
            if getattr(exc, "status", None) == 404:  # a bank with nothing retained yet has no memories
                return []
            raise
        return [MemoryItem(str(f.id), f.text, getattr(f, "document_id", None) or "", getattr(f, "type", None) or "fact")
                for f in response.results]

    def brief(self, vendor_slug, vendor_name, month):
        """Hindsight reflects over the vendor's history, shaped by the bank's skeptical disposition.

        Callers skip it for a vendor with no history. A failed briefing is counted and the deal goes ahead on recall.
        """
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

        started = time.monotonic()

        def settled():
            # Done once nothing is queued and either a consolidation has run or none was queued within 20 seconds.
            ops = _run_async(self.client.operations.list_operations(target, limit=50)).operations or []
            if any(o.status in {"pending", "processing"} for o in ops):
                return "busy"
            consolidated = any((o.operation_type or o.task_type or "") == "consolidation" for o in ops)
            return "completed" if consolidated or time.monotonic() - started > 20 else "busy"

        self._wait(settled, timeout)
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
