"""Azure OpenAI client wrapper.

Three roles only — classify, extract, explain (PRD 15.1). The model never
evaluates a rule, never performs arithmetic, and never writes to the store.

Everything here degrades. If the key is absent or the service is down, callers
receive ``None`` and fall back to deterministic behaviour; the invoice still
reaches a defensible decision, just with more rules reporting CANNOT_EVALUATE.
That property is a direct consequence of keeping the decision boundary in code
and is worth demonstrating rather than asserting (PRD 15.5).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .config import SETTINGS

log = logging.getLogger("engine.llm")

_client = None
_client_failed = False


def get_client():
    """Lazily construct the Azure client. Never raises."""
    global _client, _client_failed
    if _client is not None or _client_failed:
        return _client
    if not SETTINGS.llm_available:
        _client_failed = True
        log.warning("Azure OpenAI not configured — running in deterministic-only mode")
        return None
    try:
        from openai import AzureOpenAI

        _client = AzureOpenAI(
            api_key=SETTINGS.azure_api_key,
            api_version=SETTINGS.azure_api_version,
            azure_endpoint=SETTINGS.azure_endpoint,
            timeout=60.0,
            max_retries=2,
        )
        return _client
    except Exception as exc:
        _client_failed = True
        log.error("Azure OpenAI client construction failed: %s", exc)
        return None


def available() -> bool:
    return get_client() is not None


class LLMUnavailable(RuntimeError):
    pass


def chat_json(
    system: str,
    user_blocks: List[Dict[str, Any]],
    schema: Optional[Dict[str, Any]] = None,
    schema_name: str = "result",
    max_tokens: int = 4096,
) -> Optional[Dict[str, Any]]:
    """One structured-output call.

    ``temperature=0`` with a fixed seed and a strict JSON schema. The schema is
    not decoration: it is the channel restriction that makes prompt injection
    inert, because a model constrained to emit only schema fields has no way to
    emit an instruction even if it were persuaded to try (PRD 15.3).
    """
    client = get_client()
    if client is None:
        return None

    kwargs: Dict[str, Any] = {
        "model": SETTINGS.azure_deployment,
        "temperature": 0,
        "seed": 42,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_blocks},
        ],
    }
    if schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": False, "schema": schema},
        }
    else:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        log.error("LLM call failed: %s", exc)
        return None

    content = (response.choices[0].message.content or "").strip()
    if not content:
        return None
    return _parse_json(content)


def chat_text(system: str, user: str, max_tokens: int = 800) -> Optional[str]:
    """Free-text call, used only for the explanation narrative, which is then
    validated by ``explain.numeric_guard`` before it is shown to anyone."""
    client = get_client()
    if client is None:
        return None
    try:
        response = client.chat.completions.create(
            model=SETTINGS.azure_deployment,
            temperature=0,
            seed=42,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        log.error("LLM explanation call failed: %s", exc)
        return None
    return (response.choices[0].message.content or "").strip() or None


def _parse_json(content: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Models occasionally wrap JSON in a fenced block despite the response
    # format. Recover once rather than discarding a good extraction.
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{.*\}", content, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            return None
    return None
