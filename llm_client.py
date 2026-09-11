"""Единый LLM-клиент с retry и выбором модели."""
import time
from typing import Any, Optional

import requests

from config import get, model_for

DEFAULT_API_URL = "http://127.0.0.1:8080/v1/chat/completions"


def api_url() -> str:
    return str(get("llm.api_url", DEFAULT_API_URL))


def chat_completion(
    messages: list[dict],
    *,
    role: str = "agent",
    model: Optional[str] = None,
    temperature: float = 0.0,
    timeout: int = 120,
) -> str:
    """POST /chat/completions с retry."""
    payload = {
        "model": model or model_for(role),
        "messages": messages,
        "temperature": temperature,
    }
    retries = int(get("llm.retry_count", 2))
    delay = float(get("llm.retry_delay_sec", 1.0))
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
