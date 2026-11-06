"""Chooses live services when keys are present, otherwise the clearly labelled rehearsal stand-ins."""
from dataclasses import dataclass, field
import os
import pathlib

from dotenv import load_dotenv

from .brains import DEFAULT_MODEL, Brain, LLMBrain, ScriptedBrain
from .memory import HindsightMemory, LocalMemory, Memory

load_dotenv(pathlib.Path(__file__).resolve().parents[1] / ".env")
CLOUD_URL = "https://api.hindsight.vectorize.io"

# Any OpenAI-compatible chat API works. These are presets; LLM_BASE_URL and LLM_API_KEY cover the rest.
PROVIDERS = {
    "groq": {"key": "GROQ_API_KEY", "model": DEFAULT_MODEL},
    "nvidia": {"key": "NVIDIA_API_KEY", "base_url": "https://integrate.api.nvidia.com/v1",
               "model": "nvidia/llama-3.3-nemotron-super-49b-v1.5"},
    "openai": {"key": "LLM_API_KEY", "model": ""},
}


@dataclass
class Stack:
    mode: str  # live | rehearsal
    memory: Memory
    brain_memory: Brain
    brain_twin: Brain
    missing: list = field(default_factory=list)
    model: str = ""


def provider() -> str:
    name = os.getenv("LLM_PROVIDER", "groq").lower()
    if name not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {', '.join(PROVIDERS)}")
    return name


def missing_keys() -> list[str]:
    preset = PROVIDERS[provider()]
    missing = [] if os.getenv(preset["key"]) else [preset["key"]]
    if provider() == "openai" and not os.getenv("LLM_BASE_URL"):
        missing.append("LLM_BASE_URL")
    if not (os.getenv("HINDSIGHT_API_KEY") or os.getenv("HINDSIGHT_BASE_URL")):
        missing.append("HINDSIGHT_API_KEY")
    return missing


def llm_client():
    name, preset = provider(), PROVIDERS[provider()]
    if name == "groq":
        from groq import Groq  # deferred so rehearsal and tests need no live services
        return Groq(api_key=os.environ[preset["key"]], max_retries=5)
    from openai import OpenAI
    base_url = os.getenv("LLM_BASE_URL") or preset["base_url"]
    return OpenAI(base_url=base_url, api_key=os.environ[preset["key"]], max_retries=5)


def build(mode: str = "auto") -> Stack:
    missing = missing_keys()
    if mode == "rehearsal" or missing:
        return Stack("rehearsal", LocalMemory(), ScriptedBrain(), ScriptedBrain(), missing)
    from hindsight_client import Hindsight

    client = llm_client()
    model = os.getenv("COUNTERPARTY_MODEL") or PROVIDERS[provider()]["model"]
    if not model:
        raise ValueError("Set COUNTERPARTY_MODEL for this provider")
    tool_choice = os.getenv("LLM_TOOL_CHOICE", "required")
    hindsight = Hindsight(base_url=os.getenv("HINDSIGHT_BASE_URL", CLOUD_URL),
                          api_key=os.getenv("HINDSIGHT_API_KEY"), timeout=120.0)
    return Stack("live", HindsightMemory(hindsight), LLMBrain(client, model, tool_choice=tool_choice),
                 LLMBrain(client, model, tool_choice=tool_choice), model=model)
