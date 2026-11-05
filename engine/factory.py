"""Chooses live services when keys are present, otherwise the clearly labelled rehearsal stand-ins."""
from dataclasses import dataclass, field
import os
import pathlib

from dotenv import load_dotenv

from .brains import DEFAULT_MODEL, Brain, LLMBrain, ScriptedBrain
from .memory import HindsightMemory, LocalMemory, Memory

load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
CLOUD_URL = "https://api.hindsight.vectorize.io"


@dataclass
class Stack:
    mode: str  # live | rehearsal
    memory: Memory
    brain_memory: Brain
    brain_twin: Brain
    missing: list = field(default_factory=list)


def missing_keys() -> list[str]:
    missing = [] if os.getenv("GROQ_API_KEY") else ["GROQ_API_KEY"]
    if not (os.getenv("HINDSIGHT_API_KEY") or os.getenv("HINDSIGHT_BASE_URL")):
        missing.append("HINDSIGHT_API_KEY")
    return missing


def build(mode: str = "auto") -> Stack:
    missing = missing_keys()
    if mode == "rehearsal" or missing:
        return Stack("rehearsal", LocalMemory(), ScriptedBrain(), ScriptedBrain(), missing)
    from groq import Groq  # deferred so rehearsal and tests need no live services
    from hindsight_client import Hindsight

    groq = Groq(api_key=os.environ["GROQ_API_KEY"], max_retries=5)
    model = os.getenv("COUNTERPARTY_MODEL", DEFAULT_MODEL)
    hindsight = Hindsight(base_url=os.getenv("HINDSIGHT_BASE_URL", CLOUD_URL),
                          api_key=os.getenv("HINDSIGHT_API_KEY"), timeout=120.0)
    return Stack("live", HindsightMemory(hindsight), LLMBrain(groq, model), LLMBrain(groq, model))
