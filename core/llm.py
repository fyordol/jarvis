"""Единый LLM-клиент с retry и выбором модели."""
import time
from typing import Optional

import requests

from core.config import get, model_for

DEFAULT_API_URL = "http://127.0.0.1:8080/v1/chat/completions"


def api_url() -> str:
    return str(get("llm.api_url", DEFAULT_API_URL))


def _max_tokens_for(role: str) -> Optional[int]:
    raw = get(f"llm.max_tokens.{role}")
    if raw is None:
        raw = get("llm.max_tokens_default")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def chat_completion(
    messages: list[dict],
    *,
    role: str = "agent",
    model: Optional[str] = None,
    temperature: float = 0.0,
    timeout: int = 60,
    max_tokens: Optional[int] = None,
) -> str:
    payload = {
        "model": model or model_for(role),
        "messages": messages,
        "temperature": temperature,
    }
    mt = max_tokens if max_tokens is not None else _max_tokens_for(role)
    if mt:
        payload["max_tokens"] = mt

    retries = int(get("llm.retry_count", 1))
    delay = float(get("llm.retry_delay_sec", 0.4))
    last_error: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            response = requests.post(api_url(), json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last_error = e
            if attempt < retries:
                print(f"LLM retry {attempt + 1}/{retries}: {e}")
                time.sleep(delay * (attempt + 1))
    raise last_error  # type: ignore[misc]
