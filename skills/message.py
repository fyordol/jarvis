"""«напиши другу …» — сообщение человеку, не печать слова «другу»."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from core.config import get

_WRITE = ("напиши ", "написать ", "отправь ", "отправить ", "передай ", "пиши ")
_TYPE_ONLY = ("напечатай ", "набери ", "введи ")

RECIPIENTS: dict[str, str] = {
    "другу": "друг", "друга": "друг", "друган": "друг", "другану": "друг",
    "бро": "друг", "корешу": "друг", "кенту": "друг", "приятелю": "друг",
    "маме": "мама", "мами": "мама", "мамой": "мама", "маман": "мама",
    "папе": "папа", "папи": "папа", "отцу": "папа",
    "брату": "брат", "братухе": "брат", "сестре": "сестра", "сестрёнке": "сестра",
    "бабушке": "бабушка", "дедушке": "дедушка", "деду": "дедушка",
    "девушке": "девушка", "деву": "девушка", "парню": "парень",
    "жене": "жена", "мужу": "муж",
    "учителю": "учитель", "преподу": "препод",
    "маме в": "мама",
}

MESSENGERS: list[tuple[str, str]] = [
    ("в телеграме", "telegram"), ("в телеграмме", "telegram"), ("в телеге", "telegram"),
    ("в тг", "telegram"), ("в telegram", "telegram"), ("телеге", "telegram"),
    ("в дискорде", "discord"), ("в дискорд", "discord"), ("в discord", "discord"),
    ("в вк", "вк"), ("во вконтакте", "вк"), ("в контакте", "вк"),
    ("в ватсапе", "whatsapp"), ("в вотсапе", "whatsapp"), ("в whatsapp", "whatsapp"),
]


@dataclass
class Message:
    who: str
    text: str
    messenger: str
    who_raw: str


def parse_message(command: str) -> Optional[Message]:
    cmd = (command or "").strip().lower()
    if not cmd or cmd.startswith(_TYPE_ONLY):
        return None
    rest = ""
    for prefix in _WRITE:
        if cmd.startswith(prefix):
            rest = cmd[len(prefix):].strip()
            break
    if not rest:
        return None

    messenger = ""
    for marker, app in MESSENGERS:
        if marker in rest:
            messenger = app
            rest = rest.replace(marker, " ", 1)
            break
    rest = " ".join(rest.split())
    if rest.startswith("в "):
        rest = rest[2:].strip()

    words = rest.split()
    if not words:
        return None

    who = ""
    who_raw = ""
    idx = -1
    for i, word in enumerate(words):
        key = word.strip(".,!?")
        if key in RECIPIENTS:
            who = RECIPIENTS[key]
            who_raw = key
            idx = i
            break
    if not who:
        return None

    body = words[:idx] + words[idx + 1:]
    if body and body[0] in ("что", "чтобы", "шо"):
        body = body[1:]
    text = " ".join(body).strip(" .,!")
    return Message(who=who, text=text, messenger=messenger, who_raw=who_raw)


def is_message_command(command: str) -> bool:
    return parse_message(command) is not None


def _pick_messenger(preferred: str) -> str:
    if preferred:
        return preferred
    from control.media import focus_running

    for name in ("telegram", "discord", "вк"):
        if focus_running(name):
            return name
    return str(get("message.app", "telegram") or "telegram")


def try_message(command: str) -> Optional[str]:
    parsed = parse_message(command)
    if parsed is None:
        return None
    if not parsed.text:
        hint = parsed.who_raw or parsed.who
        return f"Кому: {parsed.who}. Что написать {hint}? Скажи «напиши {hint} …» и текст"

    from desktop.commands import execute_simple
    from desktop.screen import run_screen_agent

    app = _pick_messenger(parsed.messenger)
    opened = execute_simple(f"открой {app}", skip_memory=True, allow_vision=False)
    time.sleep(float(get("timing.after_open", 0.8)))
    task = (
        f"открой чат {parsed.who}: нажми поиск чатов, введи «{parsed.who}», "
        f"открой этот чат и в поле сообщения напиши: {parsed.text}. enter не нажимай"
    )
    result = run_screen_agent(task)
    import desktop.screen as screen_agent

    screen_agent._pending_send = True
    who = parsed.who_raw or parsed.who
    prefix = opened or f"Открыл {app}"
    return (
        f"{prefix}. {result}. Сообщение {who} набрано, не отправлено. "
        f"Скажи «отправить» или «нет»"
    )
