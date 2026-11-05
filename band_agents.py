"""Runs the buyer and vendor as Band agents, so they negotiate live in a shared room.

Usage: python band_agents.py buyer   and, in another terminal: python band_agents.py vendor
Credentials live in agent_config.yaml under the keys counterparty_buyer and counterparty_vendor.
"""
import asyncio
import logging
import sys
from typing import Any, ClassVar, Unpack

from band import Agent, Capability, Emit, FeatureKwargs, configure_logging
from band.core import AgentToolsProtocol, HistoryConverter, SimpleAdapter
from band.core.types import PlatformMessage

from engine.factory import build
from engine.room import BuyerRoom, VendorRoom

log = logging.getLogger("counterparty.band")
VENDOR_NAME_HINT = "vendor"


class Ignored(HistoryConverter[list]):
    """Both agents keep their own deal state, so platform history is not needed."""
    def convert(self, raw: list[dict[str, Any]]) -> list:
        return []


class RoomAdapter(SimpleAdapter[list]):
    SUPPORTED_EMIT: ClassVar[frozenset[Emit]] = frozenset()
    SUPPORTED_CAPABILITIES: ClassVar[frozenset[Capability]] = frozenset()

    def __init__(self, **features: Unpack[FeatureKwargs]) -> None:
        super().__init__(history_converter=Ignored(), **features)


class VendorAdapter(RoomAdapter):
    def __init__(self, **features: Unpack[FeatureKwargs]) -> None:
        super().__init__(**features)
        self.rooms: dict[str, VendorRoom] = {}

    async def on_message(self, msg: PlatformMessage, tools: AgentToolsProtocol, history: list,
                         participants_msg: str | None, contacts_msg: str | None, *,
                         is_session_bootstrap: bool, room_id: str) -> None:
        reply = self.rooms.setdefault(room_id, VendorRoom()).handle(msg.content)
        if reply:
            await tools.send_message(reply, mentions=[msg.sender_id])
        else:
            await tools.no_reply("No open negotiation for this message.")


class BuyerAdapter(RoomAdapter):
    def __init__(self, stack, **features: Unpack[FeatureKwargs]) -> None:
        super().__init__(**features)
        self.stack = stack
        self.rooms: dict[str, BuyerRoom] = {}
        self.humans: dict[str, str] = {}

    async def _vendor_id(self, tools: AgentToolsProtocol) -> str | None:
        for person in await tools.get_participants():
            if VENDOR_NAME_HINT in f"{person.name or ''} {person.handle or ''}".lower():
                return person.id
        return None

    async def on_message(self, msg: PlatformMessage, tools: AgentToolsProtocol, history: list,
                         participants_msg: str | None, contacts_msg: str | None, *,
                         is_session_bootstrap: bool, room_id: str) -> None:
        room = self.rooms.setdefault(room_id, BuyerRoom(self.stack.brain_memory, self.stack.memory))
        vendor_id = await self._vendor_id(tools)
        if vendor_id is None:
            await tools.send_message("Please add the vendor agent to this room first.", mentions=[msg.sender_id])
            return
        if msg.sender_id == vendor_id:
            outgoing = await asyncio.to_thread(room.on_vendor, msg.content)
        else:
            self.humans[room_id] = msg.sender_id
            outgoing = await asyncio.to_thread(room.on_human, msg.content)
        if not outgoing:
            await tools.no_reply("Nothing to do for this message.")
        for to, text in outgoing:
            target = vendor_id if to == "vendor" else self.humans.get(room_id, msg.sender_id)
            await tools.send_message(text, mentions=[target])


async def main(role: str) -> None:
    configure_logging()
    if role == "vendor":
        agent = Agent.from_config("counterparty_vendor", adapter=VendorAdapter())
    else:
        stack = build("auto")
        log.info("Buyer running in %s mode", stack.mode)
        agent = Agent.from_config("counterparty_buyer", adapter=BuyerAdapter(stack))
    await agent.run()


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"buyer", "vendor"}:
        sys.exit("Usage: python band_agents.py buyer|vendor")
    asyncio.run(main(sys.argv[1]))
