"""Runs the buyer and vendor through Band Desktop, so they negotiate live in a Band room. No API keys needed.

Band Desktop must be installed and signed in (`band whoami`). This script:
  1. creates (once) the agents counterparty-buyer and counterparty-vendor in your Band account,
  2. creates a room with both agents and you, or reuses the one passed with --room,
  3. relays room messages to the same BuyerRoom and VendorRoom logic that band_agents.py uses.

Usage: python band_desktop.py vendor     and, in a second terminal:  python band_desktop.py buyer [--room CHAT_ID]
Then, in the Band Desktop room, send:  negotiate apex month 9   and reply  approve  or  reject  when asked.
"""
import argparse
import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
import time

from engine.factory import build
from engine.room import BuyerRoom, VendorRoom

log = logging.getLogger("counterparty.band_desktop")
BUYER, VENDOR = "counterparty-buyer", "counterparty-vendor"
ROOM_BANK = "counterparty-band-room"  # one lasting Hindsight bank, so every approved deal teaches the next one
DESCRIPTIONS = {BUYER: "AI procurement buyer that negotiates using Hindsight memory",
                VENDOR: "Simulated supplier for Counterparty negotiations"}


def band_exe() -> str:
    local = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Band" / "band.exe"
    return str(local) if local.exists() else (shutil.which("band") or "band")


def band(*args: str, check: bool = True) -> str:
    done = subprocess.run([band_exe(), *args], capture_output=True, text=True, encoding="utf-8")
    if check and done.returncode != 0:
        raise RuntimeError(f"band {args[0]} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip()


def owner_handle() -> str:
    # whoami prints: "Name @handle <email> [user] <id>"
    return next(w[1:] for w in band("whoami").split() if w.startswith("@"))


def go_online(name: str) -> str:
    """Creates the agent on first run, then attaches it to this process so Band can deliver to it."""
    owner = owner_handle()
    if f"{owner}/{name}" not in band("list"):
        band("agent", "create", "--identity-only", "--name", name, "--session", name,
             "--description", DESCRIPTIONS[name])
    band("onboard", "--session", name, "--name", name, "--host-pid", str(os.getpid()))
    return f"{owner}/{name}"


def make_room(buyer: str, vendor: str, owner: str) -> str:
    room = band("chat", "new", "--as", buyer).splitlines()[-1].strip()
    band("chat", "add", "--as", buyer, room, owner, vendor)
    band("chat", "rename", "--as", buyer, room, "Counterparty negotiation", check=False)
    return room


def inbox(handle: str) -> list[dict]:
    out = band("inbox", "--as", handle, "--json", check=False)
    try:
        return json.loads(out) if out.startswith("[") else []
    except json.JSONDecodeError:
        return []


def say(handle: str, room: str, to: str, text: str) -> None:
    band("send", "--as", handle, room, f"@{to} {text}")
    log.info("%s -> @%s: %s", handle.split("/")[-1], to, text)


def run_vendor(owner: str) -> None:
    vendor, buyer = go_online(VENDOR), f"{owner}/{BUYER}"
    log.info("Vendor is online as %s. Start the buyer in another terminal.", vendor)
    rooms: dict[str, VendorRoom] = {}
    while True:
        for msg in inbox(vendor):
            if msg.get("sender_handle") == buyer:
                reply = rooms.setdefault(msg["chat_id"], VendorRoom()).handle(msg["content"])
                if reply:
                    say(vendor, msg["chat_id"], buyer, reply)
            band("ack", "--as", vendor, msg["message_id"], check=False)
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("role", choices=["buyer", "vendor"])
    parser.add_argument("--room", help="buyer only: reuse an existing Band room id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    owner = owner_handle()
    if args.role == "vendor":
        run_vendor(owner)
        return 0
    vendor = f"{owner}/{VENDOR}"
    buyer = go_online(BUYER)
    room = args.room or make_room(buyer, vendor, owner)
    stack = build("auto")
    if hasattr(stack.memory, "use_bank"):
        stack.memory.use_bank(os.getenv("COUNTERPARTY_ROOM_BANK", ROOM_BANK))
    log.info("Buyer memory mode: %s, bank %s", stack.mode, getattr(stack.memory, "bank_id", "local"))
    log.info("Room %s is live. In Band Desktop, send:  negotiate apex month 9", room)

    buyer_rooms: dict[str, BuyerRoom] = {}
    humans: dict[str, str] = {}
    while True:
        for msg in inbox(buyer):
            chat, sender = msg["chat_id"], msg.get("sender_handle", "")
            talk = buyer_rooms.setdefault(chat, BuyerRoom(stack.brain_memory, stack.memory))
            if sender == vendor:
                outgoing = talk.on_vendor(msg["content"])
            elif msg.get("sender_type") == "User":
                humans[chat] = sender
                outgoing = talk.on_human(msg["content"])
            else:
                outgoing = []
            band("ack", "--as", buyer, msg["message_id"], check=False)
            for to, text in outgoing:
                say(buyer, chat, vendor if to == "vendor" else humans.get(chat, owner), text)
        time.sleep(1.5)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
