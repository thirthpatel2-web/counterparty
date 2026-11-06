"""Vendor simulator: hidden traits are the ground truth a buyer discovers over repeated deals.

Vendor decisions are deterministic given a seed and never use an LLM, so a deal's price
is an objective score. An LLM can phrase the buyer's words, but it cannot move vendor numbers.
Each vendor has tendencies, not constants: the bluff and walk-away rounds vary from deal to deal.
"""
from dataclasses import dataclass
import random

LIST_PRICE = 100_000
LOST_DEAL_PREMIUM = 1.05  # losing a deal means an emergency purchase at list price + 5%
FINAL_TOLERANCE = 0.01    # a vendor within 1% of its floor truthfully says "final"
CLOSE_ENOUGH = 0.01       # at its patience limit, a vendor takes a bid within 1% of its ask instead of walking


@dataclass(frozen=True)
class VendorTraits:
    name: str
    slug: str
    floor_ratio: float           # lowest acceptable price / list price, outside quarter-end
    concession_rate: float       # share of the remaining gap conceded per round
    false_final_at_round: int    # usual round of a "final" bluff (0 = never bluffs)
    quarter_end_discount: float  # extra floor reduction in the last month of a quarter
    walks_after_rounds: int      # usual patience: the vendor ends talks at this round
    pressure_claim: str = ""     # "scarcity" or "competitor_price" (always false), or none


VENDORS = [
    VendorTraits("Apex Circuits", "apex", 0.80, 0.35, 2, 0.05, 6, "competitor_price"),
    VendorTraits("Bharat PCB", "bharat", 0.90, 0.80, 0, 0.00, 3),
    VendorTraits("Nova Components", "nova", 0.84, 0.25, 3, 0.08, 7, "scarcity"),
]
VENDOR_BY_SLUG = {v.slug: v for v in VENDORS}


@dataclass
class VendorReply:
    price: int
    says_final: bool = False
    deal: bool = False
    walked: bool = False
    claim: str = ""      # enum only: free text from a vendor never reaches memory
    message: str = ""    # untrusted display text


def is_quarter_end(month: int) -> bool:
    return month % 3 == 0


class Negotiation:
    def __init__(self, vendor: VendorTraits, month: int, seed: int):
        if not 1 <= month <= 12:
            raise ValueError("month must be 1-12")
        self.vendor = vendor
        self.month = month
        rng = random.Random(f"{vendor.slug}:{month}:{seed}")
        discount = vendor.quarter_end_discount if is_quarter_end(month) else 0.0
        self.floor = LIST_PRICE * (vendor.floor_ratio - discount + rng.uniform(-0.01, 0.01))
        self.ask = LIST_PRICE * rng.uniform(0.97, 1.0)
        # Tendencies, not constants: a third of deals run one round longer.
        self.bluff_round = vendor.false_final_at_round + (rng.random() < 1 / 3) if vendor.false_final_at_round else 0
        self.walk_round = vendor.walks_after_rounds + (rng.random() < 1 / 3)
        self.round = 0
        self.closed = False

    def opening(self) -> VendorReply:
        return VendorReply(round(self.ask), message=f"Our price for this lot is ₹{round(self.ask):,}.")

    def counter(self, bid: float) -> VendorReply:
        if self.closed:
            raise ValueError("negotiation already closed")
        self.round += 1
        v = self.vendor
        if bid >= self.ask:
            self.closed = True
            return VendorReply(round(self.ask), deal=True, message="Agreed. Sending the invoice.")
        if self.round >= self.walk_round:
            self.closed = True
            if bid >= self.floor and self.ask - bid <= CLOSE_ENOUGH * self.ask:
                return VendorReply(round(bid), deal=True, message="Fine, we'll meet you there. Sending the invoice.")
            return VendorReply(round(self.ask), walked=True,
                               message="We're too far apart. Withdrawing this quote.")
        # Bids below the floor still pull the ask down, but never past the floor.
        self.ask = max(self.floor, self.ask - v.concession_rate * (self.ask - max(bid, self.floor)))
        at_floor = self.ask <= self.floor + FINAL_TOLERANCE * LIST_PRICE
        price = round(self.ask)
        says_final = at_floor or self.round == self.bluff_round
        claim = v.pressure_claim if self.round == 1 else ""
        return VendorReply(price, says_final=says_final, claim=claim,
                           message=self._message(price, says_final, claim))

    def accept(self) -> VendorReply:
        self.closed = True
        return VendorReply(round(self.ask), deal=True, message="Agreed. Sending the invoice.")

    @staticmethod
    def _message(price: int, says_final: bool, claim: str) -> str:
        parts = {"scarcity": "Two other buyers are waiting on this lot, and the price rises tomorrow.",
                 "competitor_price": "Your competitor already paid this price last week."}
        text = [parts[claim]] if claim else []
        text.append(f"This is my final price: ₹{price:,}." if says_final else f"I can do ₹{price:,}.")
        return " ".join(text)


def lost_deal_price() -> int:
    return round(LIST_PRICE * LOST_DEAL_PREMIUM)
