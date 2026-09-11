import os
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import psutil
import webbrowser

from agent_orchestrator import get_actions
from keyboard_control import COMMANDS, hotkey, press, write
from memory import resolve_stored_command
from mouse_control import double_click, move_and_click, right_click, scroll
from search import SITES
from vision import find_screen_object
from skills.registry import run_skill_chain

START_MENU_PATHS = [
    Path(os.environ["PROGRAMDATA"]) / r"Microsoft\Windows\Start Menu\Programs",
    Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs",
]

ACTION_KEYWORDS = [
    "открой", "закрой", "найди", "напиши",
    "нажми на", "кликни по", "скопируй", "вставь",
    "вырежи", "выдели всё", "энтер", "пробел", "эскейп", "таб",
    "прочитай", "создай файл", "допиши", "покажи папку",
    "посчитай", "вычисли", "сколько будет",
    "выполни", "громче", "тише", "пауза", "раскладка", "запомни", "забудь",
]

SEQUENCE_MARKERS = [" и ", ", ", " потом ", " затем ", " после ", " а потом ", " далее "]

INTERACTIVE_MARKERS = [
    "включи", "нажми", "кликни", "выбери", "запусти",
    "скачай", "перейди", "открой перв", "первое видео", "первый результат",
]

ENTRY_MARKERS = ["зайди на", "зайди в", "загугли", "зайти на", "зайти в"]

# Команды, после которых нужна пауза (открытие приложений/сайтов)
SLOW_COMMANDS = ("открой", "закрой", "найди", "поиск на")

CLICK_PREFIXES = ("нажми на ", "кликни по ", "выбери ")

SCREEN_OBJECT_MARKERS = (
    "видео", "кнопк", "иконк", "вкладк", "поле", "картинк", "ссылк", "результат", "элемент",
)
ORDER_MARKERS = ("первое", "первый", "первая", "второе", "второй", "треть", "последн")


def find_app(name):
    name = name.lower()
    for path in START_MENU_PATHS:
        if not path.exists():
            continue
        for shortcut in path.rglob("*.lnk"):
            if name in shortcut.stem.lower():
                return shortcut
    return None


def is_complex_command(command: str) -> bool:
    from calculator import is_calculator_command

    if is_calculator_command(command):
        return False

    calc_keywords = {"посчитай", "вычисли", "сколько будет"}
    has_calc = any(kw in command for kw in calc_keywords)
    action_count = sum(
        1 for kw in ACTION_KEYWORDS
        if kw in command and kw not in calc_keywords
    )
    if has_calc:
        action_count += 1

    has_sequence = any(marker in command for marker in SEQUENCE_MARKERS)
    has_interactive = any(marker in command for marker in INTERACTIVE_MARKERS)
    has_entry = any(marker in command for marker in ENTRY_MARKERS)
    return (
        action_count >= 2
        or (action_count >= 1 and has_sequence)
        or has_interactive
        or has_entry
    )


def _step_delay(command: str):
    if command.startswith(SLOW_COMMANDS):
        time.sleep(2.5)


def _looks_like_screen_object(target: str) -> bool:
    """True, если «открой X» — это клик по объекту на экране, а не приложение/сайт."""
    if any(marker in target for marker in SCREEN_OBJECT_MARKERS):
        return True
    return any(m in target for m in ORDER_MARKERS) and any(
        m in target for m in ("видео", "кнопк", "результат", "элемент")
    )


def _extract_click_target(command: str) -> Optional[str]:
    """Извлекает описание объекта для экранного клика."""
    command = command.strip().lower()

    for prefix in CLICK_PREFIXES:
        if command.startswith(prefix):
            target = command[len(prefix):].strip().replace("пожалуйста", "").strip()
            return target or None

    if command.startswith("нажми ") and not command.startswith("нажми на "):
        target = command[len("нажми "):].strip().replace("пожалуйста", "").strip()
        if target and _looks_like_screen_object(target):
            return target

    if command.startswith("открой "):
        target = command[len("открой "):].strip().replace("пожалуйста", "").strip()
        if target and _looks_like_screen_object(target):
            return target

    return None


def _try_advanced_click(command: str) -> Optional[str]:
    """Двойной и правый клик по объекту на экране."""
    if command.startswith("двойной клик по "):
        target = command[len("двойной клик по "):].strip()
        result = find_screen_object(target)
        if result.get("coords") is None:
            return f"Не нашёл на экране {target}"
        double_click(*result["coords"])
        return f"Двойной клик по {target}"

    if command.startswith("правый клик по "):
        target = command[len("правый клик по "):].strip()
        result = find_screen_object(target)
        if result.get("coords") is None:
            return f"Не нашёл на экране {target}"
        right_click(*result["coords"])
        return f"Правый клик по {target}"
    return None


def _try_screen_click(command: str) -> Optional[str]:
    """Клик по визуальному объекту на экране."""
    target = _extract_click_target(command)
    if not target:
        return None

    result = find_screen_object(target)
    coords = result["coords"]
    reason = result.get("reason", "")

    if coords is None:
        if reason:
            return f"Не уверен, что нашёл нужный объект: {reason}"
        return f"Не нашёл на экране {target}"

    x, y = coords
    move_and_click(x, y)
    return f"Нажимаю на {target}. Скажи «да» или «нет», если нужно запомнить результат"


def execute_simple(command: str, *, skip_memory: bool = False) -> Optional[str]:
    """Выполняет одну простую команду. None — если не распознана."""
    command = command.lower().strip()

    if not skip_memory:
        stored_steps = resolve_stored_command(command)
        if stored_steps:
            if len(stored_steps) == 1:
                return execute_simple(stored_steps[0], skip_memory=True)
            results = []
            for step in stored_steps:
                print(f"-> Макрос: {step}")
                result = execute_simple(step, skip_memory=True)
                if result is None:
                    result = execute_via_agent(step)
                results.append(result)
                _step_delay(step)
            return "Выполнил макрос: " + " → ".join(results)

    skill_result = run_skill_chain(command, execute_simple)
    if skill_result is not None:
        return skill_result

    adv = _try_advanced_click(command)
    if adv is not None:
        return adv

    click_result = _try_screen_click(command)
    if click_result is not None:
        return click_result

    if "открой" in command and not command.startswith(("открой файл ", "открой папку ")):
        target = command.split("открой", 1)[1].strip().replace("пожалуйста", "").strip()
        for prefix in ("приложение ", "программу ", "программа ", "сайт "):
            if target.startswith(prefix):
                target = target[len(prefix):].strip()
        in_browser = "в браузере" in target
        if in_browser:
            target = target.replace("в браузере", "").strip()

        if not in_browser:
            shortcut = find_app(target)
            if shortcut:
                os.startfile(shortcut)
                return f"Открываю {target}"

        for name, url in SITES.items():
            if name in target:
                webbrowser.open_new_tab(url)
                return f"Открываю {name} в браузере"

        if target.startswith("http"):
            webbrowser.open_new_tab(target)
            return "Открываю ссылку"
        if "." in target:
            webbrowser.open_new_tab("https://" + target)
            return "Открываю сайт"
        return f"Не нашёл {target}"

    if "закрой" in command:
        app = command.split("закрой", 1)[1].strip().replace("пожалуйста", "").strip()
        for prefix in ("приложение ", "программу ", "программа "):
            if app.startswith(prefix):
                app = app[len(prefix):].strip()
        found = False
        for proc in psutil.process_iter(["name"]):
            try:
                process = proc.info["name"]
                if process and app.lower() in process.lower():
                    proc.kill()
                    found = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return f"Закрываю {app}" if found else f"Не нашёл запущенное приложение {app}"

    if "найди" in command:
        query = command.split("найди", 1)[1].strip()
        if query:
            url = "https://yandex.ru/search/?text=" + urllib.parse.quote(query)
            webbrowser.open(url)
            return f"Ищу {query}"
        return "Что найти?"

    for phrase, action in COMMANDS.items():
        if command.startswith(phrase):
            action()
            return f"Выполняю {phrase}"

    return None


def execute_via_agent(command: str) -> str:
    try:
        actions = get_actions(command)
        for act in actions:
            action_type = act.get("action")

            if action_type == "write":
                write(act["text"])
            elif action_type == "press":
                press(act["key"])
            elif action_type == "hotkey":
                hotkey(*act["keys"])
            elif action_type == "scroll":
                scroll(act["amount"])

        return "Выполнил через ИИ-агент"
    except Exception as e:
        print("Ошибка ИИ-агента:", e)
        return "Команда не найдена"


def _run_steps(steps: list[str]) -> str:
    results = []
    for step in steps:
        print(f"-> Шаг: {step}")
        result = execute_simple(step, skip_memory=True)
        if result is None:
            result = execute_via_agent(step)
        results.append(result)
        _step_delay(step)
    return "Выполнил: " + " → ".join(results)


def execute(command: str) -> str:
    from agent_orchestrator import orchestrate
    command = command.lower().strip()
    print("Обработка команды:", command)
    return orchestrate(command, execute_simple_fn=execute_simple, run_steps_fn=_run_steps)
