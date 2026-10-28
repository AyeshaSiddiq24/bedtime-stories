"""Thin wrapper around the Anthropic API.

Every LLM call in the project goes through here, so swapping models,
adding retries, or logging token usage only ever happens in one place.
"""

from __future__ import annotations

import json
import os

import anthropic

MODEL = "claude-haiku-5-5"

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it in your shell or put it in a .env file."
            )
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


def _call(prompt: str, system: str, max_tokens: int) -> tuple[str, str]:
    """Make one API call and return (text, stop_reason)."""
    kwargs = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    resp = _get_client().messages.create(**kwargs)
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text").strip()
    return text, resp.stop_reason


def call_model(prompt: str, system: str = "", max_tokens: int = 3000) -> str:
    """Send one user message (plus an optional system prompt) and return the text reply.

    No temperature is sent: Claude Haiku 5.5 rejects it. Each agent's behaviour (strict judge vs.
    imaginative storyteller) is set by its system prompt instead.
    """
    text, stop_reason = _call(prompt, system, max_tokens)
    if stop_reason == "max_tokens":  # cut off mid-reply: try once more with double the room
        text, _ = _call(prompt, system, max_tokens * 2)
    return text


def _extract_json(text: str) -> dict:
    """Pull the first {...} object out of a reply, tolerating code fences or stray prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    return json.loads(text[start : end + 1])


def call_model_json(prompt: str, system: str = "", max_tokens: int = 3000, retries: int = 2) -> dict:
    """Call the model and parse its reply as JSON.

    If the reply was cut off, retry with more room; if it wasn't valid JSON, retry with a nudge.
    """
    text = ""
    last_error = None
    for _ in range(retries + 1):
        text, stop_reason = _call(prompt, system, max_tokens)
        try:
            return _extract_json(text)
        except (ValueError, json.JSONDecodeError) as err:
            last_error = err
            if stop_reason == "max_tokens":
                max_tokens *= 2
            else:
                prompt = prompt + "\n\nIMPORTANT: Your last reply was not valid JSON. Reply with ONLY the JSON object."
    preview = text[:300].replace("\n", " ") if text else "(empty reply)"
    raise RuntimeError(f"Model did not return valid JSON ({last_error}). Start of reply: {preview}")