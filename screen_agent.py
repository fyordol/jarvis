"""Экранный агент: LLM + скриншот → план → verify → replan."""
import json
import time
from typing import Optional

from config import get
from keyboard_control import hotkey, press, write
from llm_client import chat_completion
from memory import build_memory_hints, set_last_action
from mouse_control import move_and_click, scroll
from vision import find_screen_object, screenshot_for_vision

SCREEN_AGENT_MARKERS = (
    "отправь", "отправить", "сообщение", "комментар",
    "на экране", "что на экране", "прочитай экран",
    "иконк", "кнопк", "поле ввода", "чат", "напиши в",
    "кликни", "нажми", "выбери", "перейди",
)

SIMPLE_SKIP = (
    "громче", "тише", "пауза", "посчитай", "вычисли", "сколько будет",
    "покажи папку", "выполни", "запомни", "забудь", "покажи память",
    "покажи паттерны", "громче на", "тише на",
)

UI_OPEN_WORDS = (
    "комментар", "вкладк", "иконк", "кнопк", "меню", "чат",
    "поле", "видео", "результат", "настройк", "профил", "уведомлен",
)

SYSTEM_PROMPT = """Ты экранный агент Jarvis. Видишь скриншот рабочего стола и выполняешь задачу пользователя.

Верни план действий — ТОЛЬКО JSON-массив, без текста вокруг.

Доступные action:
- click_object: {"action":"click_object","target":"описание объекта на экране"}
- write: {"action":"write","text":"текст для ввода"}
- press: {"action":"press","key":"enter"}
- hotkey: {"action":"hotkey","keys":["ctrl","l"]}
- scroll: {"action":"scroll","amount":-3}
- wait: {"action":"wait","seconds":1}

Правила:
1. Для кликов используй click_object с понятным описанием.
2. Перед write обычно нужен click_object по полю ввода.
3. Не более 8 шагов.
"""

_pending_send: bool = False


def needs_screen_agent(command: str) -> bool:
    command = command.strip().lower()
    if not command:
        return False
    if any(command.startswith(s) or command == s for s in SIMPLE_SKIP):
        return False
    if any(m in command for m in SCREEN_AGENT_MARKERS):
        return True
    if command.startswith("открой "):
        target = command[len("открой "):].strip()
        if any(w in target for w in UI_OPEN_WORDS):
            return True
        if target and not any(name in target for name in ("http", ".com", ".ru")):
            if len(target.split()) <= 3 and any(
                w in target for w in ("перв", "втор", "верх", "низ", "справа", "слева")
            ):
                return True
    return False


def try_confirm_send(command: str) -> Optional[str]:
    global _pending_send
    if not _pending_send:
        return None
    if command.strip().lower() in ("да", "отправить", "отправь", "да отправь", "enter", "энтер"):
        _pending_send = False
        press("enter")
        return "Отправил сообщение"
    if command.strip().lower() in ("нет", "отмена", "не отправляй"):
        _pending_send = False
        return "Отправка отменена"
    return None


def _parse_actions(answer: str) -> list[dict]:
    text = answer.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("Нет JSON-массива в ответе")
    actions = json.loads(text[start : end + 1])
    if not isinstance(actions, list):
        raise ValueError("Ответ не массив")
    return actions


def get_screen_plan(command: str, *, error_context: str = "") -> list[dict]:
    image_b64, img_w, img_h, _, _ = screenshot_for_vision()
    memory_hints = build_memory_hints(command)
    err_block = f"\nПредыдущая ошибка: {error_context}\nСкорректируй план." if error_context else ""

    user_text = f"""Задача пользователя: {command}
Размер скриншота: {img_w}x{img_h}.
{memory_hints}{err_block}
Верни JSON-массив действий."""

    answer = chat_completion(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            },
        ],
        role="screen_agent",
        temperature=0.1,
        timeout=120,
    )
    print(f"Экранный агент, план: {answer}")
    return _parse_actions(answer)


def _verify_click(target: str) -> bool:
    """Повторный скриншот — объект всё ещё на месте? (упрощённая verify)."""
    if not get("vision.verify_clicks", True):
        return True
    result = find_screen_object(target)
    return result.get("coords") is not None


def execute_screen_plan(
    actions: list[dict],
    *,
    require_send_confirm: bool = False,
    command: str = "",
) -> tuple[str, Optional[str]]:
    """Возвращает (result_message, error_for_replan)."""
    global _pending_send
    results = []

    for act in actions[:8]:
        atype = act.get("action")
        print(f"-> Экранный шаг: {act}")

        if atype == "click_object":
            target = act.get("target", "").strip()
            if not target:
                continue
            result = find_screen_object(target)
            coords = result.get("coords")
            if coords is None:
                reason = result.get("reason", "")
                return f"Не нашёл «{target}»" + (f": {reason}" if reason else ""), f"не найден {target}"
            move_and_click(*coords)
            time.sleep(0.4)
            if not _verify_click(target):
                return f"Клик по «{target}» возможно не сработал", f"verify failed {target}"
            results.append(f"клик: {target}")

        elif atype == "write":
            text = act.get("text", "")
            if text:
                write(text)
                results.append(f"ввод: {text[:30]}")
                time.sleep(0.2)

        elif atype == "press":
            key = act.get("key", "enter")
            if require_send_confirm and key in ("enter", "return"):
                _pending_send = True
                msg = " → ".join(results) if results else "Готово"
                return f"{msg}. Сообщение готово. Скажи «отправить» или «да».", None
            press(key)
            results.append(f"нажал {key}")

        elif atype == "hotkey":
            keys = act.get("keys", [])
            if keys:
                hotkey(*keys)
                results.append(f"hotkey: {'+'.join(keys)}")

        elif atype == "scroll":
            scroll(int(act.get("amount", -3)))
            results.append(f"прокрутка")

        elif atype == "wait":
            time.sleep(min(float(act.get("seconds", 1)), 5))

    if not results:
        return "Экранный агент не выполнил шагов", "пустой план"
    return "Выполнил на экране: " + " → ".join(results), None


def run_screen_agent(command: str) -> str:
    require_confirm = any(w in command for w in ("отправь", "отправить"))
    max_replan = 2
    error_ctx = ""

    for attempt in range(max_replan + 1):
        try:
            actions = get_screen_plan(command, error_context=error_ctx)
        except Exception as e:
            return f"Не смог составить план: {e}"

        if not actions:
            return "Экранный агент вернул пустой план"

        result, err = execute_screen_plan(actions, require_send_confirm=require_confirm, command=command)
        if err is None:
            set_last_action(command, result)
            return result
        error_ctx = err
        print(f"Replan {attempt + 1}: {err}")

    set_last_action(command, result)
    return result
