"""Удалённое управление — Telegram (опционально)."""
import threading
from typing import Optional

_bot_thread: Optional[threading.Thread] = None


def start_telegram_bot(execute_fn) -> bool:
    """Запускает Telegram-бота в фоне, если включён в config."""
    from config import get
    if not get("remote.telegram_enabled", False):
        return False
    token = get("remote.telegram_token", "")
    if not token:
        print("Telegram: token не задан в config.yaml")
        return False

    def _run():
        try:
            import requests
            offset = 0
            print("Telegram bot запущен")
            while True:
                resp = requests.get(
                    f"https://api.telegram.org/bot{token}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=35,
                )
                data = resp.json()
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message", {})
                    text = (msg.get("text") or "").strip().lower()
                    if not text:
                        continue
                    result = execute_fn(text)
                    chat_id = msg["chat"]["id"]
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": result[:4000]},
                        timeout=10,
                    )
        except Exception as e:
            print(f"Telegram bot error: {e}")

    global _bot_thread
    _bot_thread = threading.Thread(target=_run, daemon=True)
    _bot_thread.start()
    return True
