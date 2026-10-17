"""Экранный агент: один скриншот → план с координатами → клик без второго LLM."""
import json
import time
from typing import Optional

from core.config import get
from control.keyboard import hotkey, press, write
from core.llm import chat_completion
from core.memory import build_memory_hints, set_last_action
from control.mouse import move_and_click, scroll
from desktop.ui_intents import vision_intent_hint
from desktop.ui_tree import find_ui_element
from desktop.vision import find_screen_object, invalidate_shot_cache, screenshot_for_vision, to_screen_coords

SCREEN_AGENT_MARKERS = (
    "отправь", "отправить", "сообщение", "комментар",
    "на экране", "что на экране", "прочитай экран",
    "иконк", "кнопк", "поле ввода", "чат", "напиши в",
    "кликни", "нажми", "выбери", "перейди",
    "скач", "download", "сохран", "установ",
    "лайк", "подписк", "поделись", "share",
    "play", "плей", "воспроизв",
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

SYSTEM_PROMPT = """Ты экранный агент Jarvis. Видишь скриншот и выполняешь задачу пользователя.

Верни ТОЛЬКО JSON-массив, без текста вокруг.

Доступные action:
- click: {"action":"click","x":123,"y":456,"target":"что кликнул"}
- click_object: {"action":"click_object","target":"описание"}  // только если координаты совсем неясны
- write: {"action":"write","text":"..."}
- press: {"action":"press","key":"enter"}
- hotkey: {"action":"hotkey","keys":["ctrl","l"]}
- scroll: {"action":"scroll","amount":-3}
- wait: {"action":"wait","seconds":0.4}

Правила:
1. Для кликов почти всегда используй click с x,y — центр кликабельной области на ЭТОМ скриншоте.
2. Ищи по НАЗНАЧЕНИЮ, не по точному тексту. «скачай» = иконка загрузки / стрелка вниз / Save, даже без слова Download.
3. Перед write кликни поле ввода.
4. Не более 6 шагов. Не добавляй wait без нужды.
"""

_pending_send: bool = False


def needs_screen_agent(command: str) -> bool:
    command = command.strip().lower()
    if not command:
        return False
    if any(command.startswith(s) or command == s for s in SIMPLE_SKIP):
        return False
    key_tail = command[6:].strip() if command.startswith("нажми ") else ""
    if key_tail in ("enter", "энтер", "escape", "эскейп", "tab", "таб", "пробел", "space"):
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


def get_screen_plan(command: str, *, error_context: str = "") -> tuple[list[dict], tuple[int, int, int, int]]:
    image_b64, img_w, img_h, screen_w, screen_h = screenshot_for_vision(force=True)
    memory_hints = build_memory_hints(command)
    err_block = f"\nПредыдущая ошибка: {error_context}\nСкорректируй план." if error_context else ""
    intent = vision_intent_hint(command)

    user_text = f"""Задача пользователя: {command}
Размер скриншота: {img_w}x{img_h}. Координаты click — пиксели этого скриншота.
{intent}
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
        timeout=int(get("vision.timeout_sec", 45)),
    )
    print(f"Экранный агент, план: {answer}")
    return _parse_actions(answer), (img_w, img_h, screen_w, screen_h)


def _after_click_pause() -> None:
    time.sleep(float(get("timing.after_click", 0.12)))


def _click_xy(x: float, y: float, dims: tuple[int, int, int, int], target: str = "") -> None:
    img_w, img_h, screen_w, screen_h = dims
    sx, sy = to_screen_coords(float(x), float(y), img_w, img_h, screen_w, screen_h)
    print(f"Клик {target or ''} -> ({sx},{sy})")
    move_and_click(sx, sy)
    invalidate_shot_cache()
    _after_click_pause()


def execute_screen_plan(
    actions: list[dict],
    *,
    require_send_confirm: bool = False,
    command: str = "",
    dims: tuple[int, int, int, int] = (1280, 720, 1920, 1080),
) -> tuple[str, Optional[str]]:
    global _pending_send
    results = []

    for act in actions[:6]:
        atype = act.get("action")
        print(f"-> Экранный шаг: {act}")

        if atype in ("click", "click_xy"):
            if act.get("x") is None or act.get("y") is None:
                atype = "click_object"
            else:
                _click_xy(act["x"], act["y"], dims, str(act.get("target", "")))
                results.append(f"клик: {act.get('target', 'xy')}")
                continue

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
            invalidate_shot_cache()
            _after_click_pause()
            results.append(f"клик: {target}")

        elif atype == "write":
            text = act.get("text", "")
            if text:
                write(text)
                results.append(f"ввод: {text[:30]}")

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
            results.append("прокрутка")

        elif atype == "wait":
            time.sleep(min(float(act.get("seconds", 0.4)), 3))

    if not results:
        return "Экранный агент не выполнил шагов", "пустой план"
    return "Выполнил на экране: " + " → ".join(results), None


def run_screen_agent(command: str) -> str:
    require_confirm = any(w in command for w in ("отправь", "отправить"))
    if not any(w in command for w in ("напиши", "введи", " потом ", " затем ")):
        uia = find_ui_element(command)
        if uia and uia.get("coords"):
            move_and_click(*uia["coords"])
            invalidate_shot_cache()
            msg = f"Нажал: {command}"
            set_last_action(command, msg)
            return msg

    max_replan = int(get("vision.replan_count", 1))
    error_ctx = ""
    result = "Не удалось выполнить на экране"

    for attempt in range(max_replan + 1):
        try:
            actions, dims = get_screen_plan(command, error_context=error_ctx)
        except Exception as e:
            return f"Не смог составить план: {e}"

        if not actions:
            return "Экранный агент вернул пустой план"

        result, err = execute_screen_plan(
            actions, require_send_confirm=require_confirm, command=command, dims=dims
        )
        if err is None:
            set_last_action(command, result)
            return result
        error_ctx = err
        print(f"Replan {attempt + 1}: {err}")

    set_last_action(command, result)
    return result
