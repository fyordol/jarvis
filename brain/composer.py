"""Сложные команды: Composer 2.5 думает, руки Jarvis выполняют."""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from core.config import get
from core.paths import REPO_ROOT

_agent = None
_agent_lock = threading.Lock()
_execute_step: Optional[Callable[[str], str]] = None
_busy = False

SYSTEM = """Ты мозг Джарвиса на Windows-ПК пользователя.

Простые команды («открой ютуб», «громче») уже отработаны без тебя. Тебя зовут на сложное: несколько шагов, экран, сообщения, поиск по смыслу, ДЗ, вопросы по фактам с проверкой.

Действуй ТОЛЬКО инструментами Jarvis. Не правь файлы репозитория, не вызывай shell/терминал Cursor, не пиши код проекта. Браузер — Яндекс Браузер. Не говори «сэр».

Инструменты:
- jarvis_cmd: быстрые команды. Формы: открой X, закрой X, поиск на ютуб X, найди X (Яндекс), зайди на site.ru, открой файл/папку, громче, тише, пауза, новая вкладка, переключись на [окно], посчитай [выражение], сделай дз …
- jarvis_type: напечатать текст в активное окно
- jarvis_keys: клавиши, например ["ctrl","l"] или ["enter"]
- jarvis_screen: одно действие по скриншоту (клик по кнопке/видео/полю)
- jarvis_look: посмотреть экран и ответить на вопрос
- jarvis_web: поиск в интернете, вернёт текст
- jarvis_python: короткий Python, пути только прямыми слешами C:/Users/PC/...
- jarvis_wait: пауза, секунды, максимум 5

Правила:
1. Сначала открой нужное окно, потом type/keys/screen.
2. Сообщения людям: enter только когда jarvis_look подтвердил нужный чат.
3. Удаление/перемещение файлов — только jarvis_python, пользователь подтвердит.
4. Если не хватает данных — спроси текстом, не угадывай.
5. Ответ пользователю — коротко по-русски в конце, только по фактам инструментов.
"""


def _key() -> str:
    raw = str(get("composer.api_key", "") or "").strip()
    if raw:
        return raw
    env = REPO_ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, val = line.partition("=")
            if name.strip() == "CURSOR_API_KEY":
                return val.strip().strip('"').strip("'")
    return str(os.environ.get("CURSOR_API_KEY") or "").strip()


def enabled() -> bool:
    return bool(get("composer.enabled", True)) and bool(_key())


def _hard(command: str) -> bool:
    if get("composer.every_command", False):
        return True
    low = command.lower()
    if any(w in low for w in HARD_ALWAYS):
        return True
    try:
        from skills.message import is_message_command

        if is_message_command(low) and len(low.split()) >= 5:
            return True
    except Exception:
        pass
    words = [w for w in low.replace(",", " ").split() if w]
    if len(words) >= int(get("composer.min_words", 8)):
        return True
    joins = sum(low.count(m) for m in (" и ", " потом ", " затем ", " после ", ", "))
    if joins >= 2:
        return True
    return False


HARD_ALWAYS = (
    "напиши в ", "отправь ", "сообщ", "разберись", "исправь", "настрой",
    "объясни как", "почему", "заполни", "переведи", "скачай",
    "удали файл", "переименуй", "перемести",
)


def should_handle(command: str, *, routed_kind: str = "") -> bool:
    if not enabled():
        return False
    return _hard(command)


def _tool(name: str, **kwargs: Any) -> str:
    from brain.qwen import _Stop, _do

    try:
        obs, spoken = _do({"tool": name, **kwargs}, _execute_step or (lambda s: s))
    except _Stop as stop:
        return stop.message
    if spoken:
        return spoken if not obs else f"{spoken}\n{obs}"
    return obs or "готово"


def _tools() -> dict:
    from cursor_sdk import CustomTool

    def wrap(name: str, execute):
        def run(args, _ctx):
            print(f"Composer → {name} {args}")
            return execute(args)

        return run

    return {
        "jarvis_cmd": CustomTool(
            description="Быстрая команда Jarvis: открой/закрой/найди/поиск на/громче/пауза/файл.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            execute=wrap("cmd", lambda a: _tool("cmd", text=a.get("text") or "")),
        ),
        "jarvis_type": CustomTool(
            description="Напечатать текст в активное окно.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            execute=wrap("type", lambda a: _tool("type", text=a.get("text") or "")),
        ),
        "jarvis_keys": CustomTool(
            description="Нажать клавиши. Пример: keys=['ctrl','l'] или keys=['enter'].",
            input_schema={
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["keys"],
            },
            execute=wrap("keys", lambda a: _tool("keys", keys=a.get("keys") or [])),
        ),
        "jarvis_screen": CustomTool(
            description="Одно действие по скриншоту: клик по кнопке, видео, полю, иконке.",
            input_schema={
                "type": "object",
                "properties": {"task": {"type": "string"}},
                "required": ["task"],
            },
            execute=wrap("screen", lambda a: _tool("screen", task=a.get("task") or "")),
        ),
        "jarvis_look": CustomTool(
            description="Посмотреть скриншот экрана и ответить на вопрос.",
            input_schema={
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
            execute=wrap("look", lambda a: _tool("look", question=a.get("question") or "что на экране?")),
        ),
        "jarvis_web": CustomTool(
            description="Поиск в интернете, вернёт краткий текст результатов.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            execute=wrap("web", lambda a: _tool("web", query=a.get("query") or "")),
        ),
        "jarvis_python": CustomTool(
            description="Короткий Python 3. Результат — то, что напечатал print. Пути: C:/Users/PC/...",
            input_schema={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
            execute=wrap("python", lambda a: _tool("python", code=a.get("code") or "")),
        ),
        "jarvis_wait": CustomTool(
            description="Подождать секунды, максимум 5.",
            input_schema={
                "type": "object",
                "properties": {"seconds": {"type": "number"}},
                "required": ["seconds"],
            },
            execute=wrap("wait", lambda a: _tool("wait", seconds=a.get("seconds") or 1)),
        ),
    }


def _local_opts():
    from cursor_sdk import LocalAgentOptions

    return LocalAgentOptions(cwd=str(REPO_ROOT), custom_tools=_tools())


def _options():
    from cursor_sdk import AgentOptions

    return AgentOptions(
        api_key=_key(),
        model=str(get("composer.model", "composer-2.5")),
        local=_local_opts(),
        tools=[],
    )


def _get_agent():
    global _agent
    from cursor_sdk import Agent, CursorAgentError

    with _agent_lock:
        if _agent is not None:
            return _agent
        try:
            _agent = Agent.create(_options())
        except TypeError:
            _agent = Agent.create(
                model=str(get("composer.model", "composer-2.5")),
                api_key=_key(),
                local=_local_opts(),
                tools=[],
            )
        except CursorAgentError:
            raise
        print("Composer 2.5 подключён")
        return _agent


def close() -> None:
    global _agent
    with _agent_lock:
        agent = _agent
        _agent = None
    if agent is None:
        return
    try:
        agent.close()
    except Exception:
        pass


def think(command: str, execute_step: Callable[[str], str]) -> str:
    global _execute_step, _busy
    from brain.qwen import _context_block
    from cursor_sdk import CursorAgentError

    if _busy:
        return "Composer уже выполняет другую команду"
    _execute_step = execute_step
    _busy = True
    try:
        from ui.app import set_voice_state

        set_voice_state("working", "Composer думает")
    except Exception:
        pass
    prompt = f"{SYSTEM}\n\n{_context_block()}\n\nЗадача: {command}"
    try:
        agent = _get_agent()
        run = agent.send(prompt)
        print("Composer run", getattr(run, "id", ""))
        text = ""
        if hasattr(run, "text"):
            text = (run.text() or "").strip()
        result = run.wait() if hasattr(run, "wait") else None
        if result is not None and getattr(result, "status", "") == "error":
            return f"Composer не доделал: {getattr(result, 'result', '') or text or 'ошибка'}"
        return text or (getattr(result, "result", None) if result is not None else "") or "Готово"
    except CursorAgentError as exc:
        print("Composer старт:", exc)
        close()
        return ""
    except Exception as exc:
        print("Composer:", exc)
        close()
        return ""
    finally:
        _busy = False
        _execute_step = None
