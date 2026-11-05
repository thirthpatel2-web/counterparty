"""Deal memory. Only facts computed by our own code are stored, never vendor free text,
so a vendor cannot plant instructions or false history in the buyer's memory."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Protocol

BUYER_MISSION = ("I am a procurement buyer negotiating electronic components. I judge vendors by what "
                 "they did in closed deals, not by what they claimed.")
RETAIN_MISSION = ("Keep per-vendor negotiation behaviour: final-price claims and whether they held, closing "
                  "prices, rounds, walk-aways, quarter-end effects and pressure claims proven false.")


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
    final_claims: tuple = ()      # (round, price) pairs, computed from the simulator's flag
    false_claims: tuple = ()      # enums of pressure claims proven false by the closing price

    @property
    def when(self) -> str:
        return datetime(self.year, self.month, 15, tzinfo=timezone.utc).isoformat()

    def to_text(self) -> str:
        period = f"{self.year}-{self.month:02d}" + (" (quarter-end month)" if self.month % 3 == 0 else "")
        lines = [f"Deal {self.deal_id} with {self.vendor_name} in {period}: opening price ₹{self.opening:,}."]
        for round_no, price in self.final_claims:
            if self.outcome == "closed" and self.closing < price:
                lines.append(f"At round {round_no} {self.vendor_name} claimed ₹{price:,} was final. That claim was "
                             f"FALSE: the deal later closed ₹{price - self.closing:,} lower.")
            else:
                lines.append(f"At round {round_no} {self.vendor_name} claimed ₹{price:,} was final. "
                             "No lower price was reached after that claim.")
        if self.outcome == "closed":
            lines.append(f"Closed at ₹{self.closing:,} after {self.rounds} rounds.")
        else:
            lines.append(f"{self.vendor_name} WALKED AWAY at round {self.rounds} and the deal was lost. "
                         f"The last price on the table had been ₹{self.closing:,}.")
        for claim in self.false_claims:
            lines.append(f"{self.vendor_name} made a {claim.replace('_', ' ')} pressure claim that proved FALSE.")
        return " ".join(lines)


@dataclass(frozen=True)
class MemoryItem:
    id: str
    text: str
    document_id: str
    kind: str = "experience"


class Memory(Protocol):
    def reset(self) -> None: ...
    def retain(self, record: DealRecord) -> None: ...
    def recall(self, vendor_slug: str, vendor_name: str, month: int, exclude: frozenset = frozenset(),
               raw_facts_only: bool = False) -> list[MemoryItem]: ...
    def playbook(self, vendor_slug: str, vendor_name: str) -> str: ...


@dataclass
class LocalMemory:
    """Rehearsal and test stand-in with exact matching. It is not Hindsight."""
    records: list = field(default_factory=list)
    limit: int = 12

    def reset(self) -> None:
        self.records.clear()

    def retain(self, record: DealRecord) -> None:
        self.records.append(record)

    def recall(self, vendor_slug, vendor_name, month, exclude=frozenset(), raw_facts_only=False):
        matches = [r for r in self.records if r.vendor_slug == vendor_slug and r.deal_id not in exclude]
        return [MemoryItem(r.deal_id, r.to_text(), r.deal_id) for r in reversed(matches[-self.limit:])]

    def playbook(self, vendor_slug, vendor_name):
        rows = [r for r in self.records if r.vendor_slug == vendor_slug]
        closed = [r.closing for r in rows if r.outcome == "closed"]
        false_finals = sum(r.outcome == "closed" and any(r.closing < p for _, p in r.final_claims) for r in rows)
        walks = sum(r.outcome != "closed" for r in rows)
        low = f", lowest close ₹{min(closed):,}" if closed else ""
        return f"Rehearsal summary, not Hindsight reflection: {len(rows)} deals, {false_finals} false finals, {walks} walk-aways{low}."


class HindsightMemory:
    """Hindsight Cloud or self-hosted memory: one bank, one document per deal, one tag per vendor."""
    VENDOR_QUERY = ("How does {name} really negotiate? Which final-price claims were false, what closing prices "
                    "were reached, at which round did they walk away, and what happens in quarter-end months?")

    def __init__(self, client, bank_id: str = "counterparty-buyer"):
        self.client = client
        self.bank_id = bank_id

    def reset(self) -> None:
        # A fresh bank per race keeps earlier runs replayable, including Forget on their memories.
        self.bank_id = f"counterparty-{time.strftime('%Y%m%d-%H%M%S')}"
        self.client.create_bank(
            self.bank_id, name="Counterparty buyer", mission=BUYER_MISSION, retain_mission=RETAIN_MISSION,
            disposition_skepticism=5, disposition_literalism=4, disposition_empathy=1,
            reflect_mission=BUYER_MISSION,
        )

    def retain(self, record: DealRecord) -> None:
        self.client.retain(
            self.bank_id, record.to_text(), timestamp=record.when, context="closed procurement negotiation",
            document_id=record.deal_id, tags=[f"vendor:{record.vendor_slug}"],
            metadata={"vendor": record.vendor_slug, "outcome": record.outcome},
        )

    def recall(self, vendor_slug, vendor_name, month, exclude=frozenset(), raw_facts_only=False):
        # A Forget run compares raw facts on both sides, since an observation could still
        # carry knowledge from the forgotten deal.
        types = ["world", "experience"] if raw_facts_only else ["world", "experience", "observation"]
        response = self.client.recall(
            self.bank_id, self.VENDOR_QUERY.format(name=vendor_name), types=types, budget="mid", max_tokens=1800,
            tags=[f"vendor:{vendor_slug}"], tags_match="any_strict",
        )
        items = []
        for fact in response.results:
            document_id = getattr(fact, "document_id", None) or ""
            if document_id in exclude:
                continue
            items.append(MemoryItem(str(fact.id), fact.text, document_id, getattr(fact, "type", None) or "fact"))
        return items

    def playbook(self, vendor_slug, vendor_name):
        response = self.client.reflect(
            self.bank_id, self.VENDOR_QUERY.format(name=vendor_name) + " Answer in at most 3 short sentences.",
            budget="low", tags=[f"vendor:{vendor_slug}"], tags_match="any_strict",
        )
        return response.text
