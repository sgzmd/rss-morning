"""OpenRouter client helper for structured completions."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.0-flash-001"


class OpenRouterError(RuntimeError):
    """Raised when an OpenRouter API request fails."""


def complete_structured(
    messages: List[Dict[str, str]],
    json_schema: Dict[str, Any],
    schema_name: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Call OpenRouter Chat Completions with strict structured JSON schema output."""
    resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not resolved_key:
        raise OpenRouterError("OPENROUTER_API_KEY environment variable is not set.")

    resolved_model = model or os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL

    headers = {
        "Authorization": f"Bearer {resolved_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/sgzmd/rss-morning",
        "X-Title": "RSS Morning",
    }

    payload = {
        "model": resolved_model,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": json_schema,
            },
        },
        "provider": {
            "require_parameters": True,
        },
    }

    masked_key = f"...{resolved_key[-4:]}" if len(resolved_key) >= 4 else "****"
    logger.debug("Calling OpenRouter model=%s with key=%s", resolved_model, masked_key)

    try:
        response = requests.post(
            OPENROUTER_API_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout as exc:
        raise OpenRouterError(f"OpenRouter request timed out after {timeout}s") from exc
    except requests.RequestException as exc:
        raise OpenRouterError(f"OpenRouter network request failed: {exc}") from exc

    if response.status_code != 200:
        error_msg = f"OpenRouter returned HTTP {response.status_code}"
        try:
            err_data = response.json()
            if "error" in err_data:
                err_detail = err_data["error"]
                if isinstance(err_detail, dict):
                    error_msg += f": {err_detail.get('message', err_detail)}"
                else:
                    error_msg += f": {err_detail}"
        except Exception:
            error_msg += f": {response.text[:200]}"
        raise OpenRouterError(error_msg)

    try:
        resp_data = response.json()
        content = resp_data["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise OpenRouterError(
            f"Failed to parse structured response from OpenRouter: {exc}"
        ) from exc
