"""The negotiation protocol used in a Band room, independent of the Band SDK so it can be tested.

Each vendor message ends in a machine-readable tag. The buyer acts on the tag alone, so vendor
prose stays untrusted display text.
"""
from dataclasses import dataclass, field
import re

from .arena import DealSpec
from .brains import Brain, DealState
from .memory import DealRecord, Memory, brief_text
from .simulator import VENDOR_BY_SLUG, Negotiation, lost_deal_price

TAG = re.compile(r"\[ask=(\d+) final=([01]) round=(\d+) status=(open|deal|walked)\]\s*$")
START = re.compile(r"\bnegotiate\s+(apex|bharat|nova)\b(?:\s+month\s+(\d{1,2}))?", re.I)
START_VENDOR = re.compile(r"^\s*START\s+(apex|bharat|nova)\s+(\d{1,2})\s+(\d+)\b")
OFFER = re.compile(r"^\s*OFFER\s+(\d+)\b")
ACCEPT = re.compile(r"^\s*ACCEPT\b")
APPROVE = re.compile(r"^\s*approve[.!]?\s*$", re.I)
REJECT = re.compile(r"^\s*reject[.!]?\s*$", re.I)


def strip_mentions(text: str) -> str:
    """Band messages may start with @mentions. Commands are read only from the start of what follows."""
    return re.sub(r"^(\s*@[\w.-]+[,:]?\s*)+", "", text)


def tagged(text: str, ask: int, final: bool, round_no: int, status: str) -> str:
    return f"{text} [ask={ask} final={int(final)} round={round_no} status={status}]"


class VendorRoom:
    """One vendor's side of every negotiation in a room. Numbers come only from the simulator."""
    def __init__(self):
        self.talks: Negotiation | None = None

    def handle(self, text: str) -> str | None:
        text = strip_mentions(text)
        start = START_VENDOR.search(text)
        if start:
            slug, month, seed = start.group(1), int(start.group(2)), int(start.group(3))
            if not 1 <= month <= 12:
                return None
            self.talks = Negotiation(VENDOR_BY_SLUG[slug], month, seed)
            reply = self.talks.opening()
            return tagged(reply.message, reply.price, False, 0, "open")
        if self.talks is None or self.talks.closed:
            return None
        if ACCEPT.search(text):
            reply = self.talks.accept()
            return tagged(reply.message, reply.price, False, self.talks.round, "deal")
        offer = OFFER.search(text)
        if not offer:
            return None
        reply = self.talks.counter(int(offer.group(1)))
        status = "deal" if reply.deal else "walked" if reply.walked else "open"
        return tagged(reply.message, reply.price, reply.says_final, self.talks.round, status)


@dataclass
class BuyerRoom:
    """The buyer's state for one negotiation, ending in a human approval before anything is remembered."""
    brain: Brain
    memory: Memory
    deal_counter: int = 0
    spec: DealSpec | None = None
    memories: list = field(default_factory=list)
    history: list = field(default_factory=list)
    opening: int = 0
    final_claims: list = field(default_factory=list)  # [round, price, lowest ask after it]
    pending: DealRecord | None = None
    brief: dict = field(default_factory=dict)
    accepted_final: bool = False
    last_final: bool = False

    def start(self, text: str) -> tuple[str, str] | None:
        """Human: "negotiate apex month 9". Returns (to, message)."""
        match = START.search(text)
        if not match:
            return None
        slug = match.group(1).lower()
        month = int(match.group(2) or 1)
        if not 1 <= month <= 12:
            return None
        self.deal_counter += 1
        vendor = VENDOR_BY_SLUG[slug]
        self.spec = DealSpec(self.deal_counter, slug, month, 2026, 9000 + self.deal_counter)
        self.brief = self.memory.brief(slug, vendor.name, month)
        self.memories = ([brief_text(self.brief)] if self.brief else []) + \
            [m.text for m in self.memory.recall(slug, vendor.name, month)]
        self.history, self.final_claims, self.opening, self.pending = [], [], 0, None
        self.accepted_final = False
        return "vendor", f"START {slug} {month} {self.spec.seed}"

    def on_vendor(self, text: str) -> list[tuple[str, str]]:
        """Vendor reply to outgoing messages: a list of (to, message), where to is vendor or human."""
        tag = TAG.search(text)
        if not tag or self.spec is None:
            return []
        ask, final, round_no, status = int(tag.group(1)), tag.group(2) == "1", int(tag.group(3)), tag.group(4)
        self.opening = self.opening or ask
        vendor = VENDOR_BY_SLUG[self.spec.vendor_slug]
        if status != "walked":
            for claim in self.final_claims:
                claim[2] = min(claim[2], ask)
        if status == "walked":
            self._remember(self._record(ask, "vendor_walked", round_no))
            spec, self.spec = self.spec, None
            return [("human", f"{vendor.name} walked away at round {round_no}. Deal {spec.deal_id} is lost and remembered.")]
        if status == "deal":
            self.pending = self._record(ask, "closed", round_no)
            return [("human", f"Deal agreed with {vendor.name} at ₹{ask:,} after {round_no} rounds. "
                              "Reply approve or reject.")]
        if final:
            self.final_claims.append([round_no, ask, ask])
        self.last_final = final
        display = TAG.sub("", text).strip()
        self.history.append(f"Vendor: {display}")
        state = DealState(vendor.name, self.spec.month, ask, round_no, final, display, list(self.history),
                          lost_deal_price())
        decision = self.brain.decide(state, self.memories, self.spec.seed)
        if decision.action == "accept":
            self.accepted_final = final
            return [("vendor", f"ACCEPT. {decision.message}")]
        self.history.append(f"You: offered ₹{decision.price:,}. {decision.message}")
        return [("vendor", f"OFFER {decision.price}. {decision.message}")]

    def on_human(self, text: str) -> list[tuple[str, str]]:
        text = strip_mentions(text)
        if self.pending and APPROVE.match(text):
            record, self.pending, self.spec = self.pending, None, None
            self._remember(record)
            return [("human", f"Approved and remembered: {record.deal_id}.")]
        if self.pending and REJECT.match(text):
            record, self.pending, self.spec = self.pending, None, None
            return [("human", f"Rejected. {record.deal_id} was not remembered.")]
        if self.pending:
            return [("human", f"Deal {self.pending.deal_id} is waiting. Reply exactly approve or reject.")]
        started = self.start(text)
        if started:
            n = len(self.memories)
            advice = f" {self.brief['advice']}" if self.brief.get("advice") else ""
            return [("human", f"Starting. I found {n} relevant memories for this vendor.{advice}"), started]
        return [("human", 'Tell me "negotiate apex month 9" (apex, bharat or nova, month 1-12).')]

    def _record(self, price: int, outcome: str, round_no: int) -> DealRecord:
        spec = self.spec
        vendor = VENDOR_BY_SLUG[spec.vendor_slug]
        return DealRecord(spec.deal_id, vendor.slug, vendor.name, spec.month, spec.year, self.opening, price,
                          outcome, round_no, tuple(tuple(c) for c in self.final_claims), (),
                          self.accepted_final and outcome == "closed")

    def _remember(self, record: DealRecord) -> None:
        self.memory.retain(record)
