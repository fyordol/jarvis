"""Единый оркестратор: быстрый токенизатор → выученные разборы → LLM-планировщик."""
import json
import time
from typing import Optional

from control.keyboard import hotkey, press, write
from core.llm import chat_completion
from core.memory import _FAIL_MARKERS, add_dialog_history, lookup_learned_parse, remember_parse, set_last_action
from control.mouse import scroll
from brain.planner import decompose_command

_AGENT_PROMPT = """
Ты управляешь компьютером.

Доступные действия:
write(text) - напечатать текст
press(key) - нажать кнопку (enter, space, tab, esc...)
hotkey(keys) - комбинация (ctrl+c, alt+tab...)
scroll(amount) - прокрутка (отрицательное = вниз)

Отвечай только JSON массивом. Никакого текста кроме JSON.
"""


def get_actions(command: str) -> list[dict]:
    answer = chat_completion(
        [
            {"role": "system", "content": _AGENT_PROMPT},
            {"role": "user", "content": command},
        ],
        role="agent",
        temperature=0,
        timeout=30,
    )
    print("LLM ответ:", answer)
    if "```json" in answer:
        answer = answer.split("```json")[1].split("```")[0]
    elif "```" in answer:
        answer = answer.split("```")[1].split("```")[0]
    return json.loads(answer)


def _execute_step(step: str, *, execute_simple_fn) -> str:
    from desktop.screen import needs_screen_agent, run_screen_agent

    if needs_screen_agent(step):
        return run_screen_agent(step)
    result = execute_simple_fn(step)
    if result is not None:
        return result
    return _agent_fallback(step)


def orchestrate(command: str, *, execute_simple_fn, run_steps_fn) -> str:
    from skills.calculator import is_calculator_command, try_calculator
    from desktop.screen import try_confirm_send

    command = command.lower().strip()
    if not command:
        return "Пустая команда"

    from brain.qwen import try_confirm_code

    confirm = try_confirm_send(command)
    if confirm is None:
        confirm = try_confirm_code(command)
    if confirm is not None:
        set_last_action(command, confirm)
        add_dialog_history(command, confirm)
        return confirm

    remembered = None
    if command.startswith(("запомни", "забудь", "покажи паттерн", "покажи команд", "покажи память", "мои паттерн", "мои команд")):
        from core.memory import parse_remember_command
        remembered = parse_remember_command(command)
    if remembered is not None:
        set_last_action(command, remembered)
        add_dialog_history(command, remembered)
        return remembered

    if is_calculator_command(command):
        result = try_calculator(command)
        if result:
            set_last_action(command, result)
            add_dialog_history(command, result)
            return result

    from core.config import get
    import brain.composer as composer_brain

    skip_fast = composer_brain._hard(command) and composer_brain.enabled()
    if not skip_fast:
        fast = _fast_path(command, execute_simple_fn=execute_simple_fn, run_steps_fn=run_steps_fn)
        if fast is not None:
            set_last_action(command, fast)
            add_dialog_history(command, fast)
            return fast

    if composer_brain.should_handle(command):
        print("Сложная команда → Composer 2.5")
        try:
            result = composer_brain.think(
                command, execute_step=lambda s: _brain_step(s, execute_simple_fn=execute_simple_fn)
            )
        except Exception as e:
            print("Composer упал, локальный мозг:", e)
            result = ""
        if result:
            set_last_action(command, result)
            add_dialog_history(command, result)
            return result
        print("Composer пусто, локальный путь")
        if skip_fast:
            fast = _fast_path(command, execute_simple_fn=execute_simple_fn, run_steps_fn=run_steps_fn)
            if fast is not None:
                set_last_action(command, fast)
                add_dialog_history(command, fast)
                return fast

    if get("brain.enabled", True):
        from brain.qwen import think

        try:
            result, cmd_steps = think(
                command, execute_step=lambda s: _brain_step(s, execute_simple_fn=execute_simple_fn)
            )
            if cmd_steps:
                remember_parse(command, cmd_steps, result)
            set_last_action(command, result)
            add_dialog_history(command, result)
            return result
        except Exception as e:
            print("Мозг упал, перехожу на планировщик:", e)

    try:
        steps = decompose_command(command)
    except Exception as e:
        print("Планировщик:", e)
        steps = [command]

    if not steps:
        steps = [command]

    print("Шаги:", steps)
    result = _run(steps, execute_simple_fn=execute_simple_fn, run_steps_fn=run_steps_fn)

    remember_parse(command, steps, result)
    set_last_action(command, result)
    add_dialog_history(command, result)
    return result


def _brain_step(step: str, *, execute_simple_fn) -> str:
    from brain.router import route
    from desktop.screen import needs_screen_agent, run_screen_agent

    routed = route(step)
    if routed is not None and len(routed.steps) == 1:
        step = routed.steps[0]
    if needs_screen_agent(step):
        return run_screen_agent(step)
    result = execute_simple_fn(step)
    if result is None:
        return "команда не распознана — используй type/keys/screen/python"
    return result


def _run(steps: list[str], *, execute_simple_fn, run_steps_fn) -> str:
    if len(steps) == 1:
        return _execute_step(steps[0], execute_simple_fn=execute_simple_fn)
    return run_steps_fn(steps)


def _failed(result: str) -> bool:
    low = (result or "").lower()
    return any(m in low for m in _FAIL_MARKERS)


def _fast_path(command: str, *, execute_simple_fn, run_steps_fn) -> Optional[str]:
    """Токенизатор и выученные разборы — без LLM-планировщика."""
    from brain.router import route

    started = time.perf_counter()
    try:
        routed = route(command)
    except Exception as e:
        print("Роутер:", e)
        routed = None

    if routed is not None:
        print(f"Быстрый маршрут [{routed.kind}]: {routed.steps}")
        if routed.kind == "homework":
            from skills.homework import run_homework

            return run_homework(routed.steps[0])
        result = _run(routed.steps, execute_simple_fn=execute_simple_fn, run_steps_fn=run_steps_fn)
        print(f"Быстрый маршрут: {time.perf_counter() - started:.2f} с")
        if not (routed.safe_fallback and _failed(result)):
            return result
        print("Быстрый маршрут не попал, зову планировщик")
        return None

    learned = lookup_learned_parse(command)
    if learned:
        print(f"Выученный разбор: {learned}")
        result = _run(learned, execute_simple_fn=execute_simple_fn, run_steps_fn=run_steps_fn)
        if not _failed(result):
            return result
    return None


def _agent_fallback(command: str) -> str:
    try:
        for act in get_actions(command):
            t = act.get("action")
            if t == "write":
                write(act["text"])
            elif t == "press":
                press(act["key"])
            elif t == "hotkey":
                hotkey(*act["keys"])
            elif t == "scroll":
                scroll(act["amount"])
        return "Выполнил через ИИ-агент"
    except Exception as e:
        print("Ошибка ИИ-агента:", e)
        return "Команда не найдена"
