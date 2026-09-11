"""Единый оркестратор: маршрутизация команд через skills и LLM."""
import json

from keyboard_control import hotkey, press, write
from llm_client import chat_completion
from memory import add_dialog_history, set_last_action
from mouse_control import scroll
from planner import decompose_command

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
        timeout=60,
    )
    print("LLM ответ:", answer)
    if "```json" in answer:
        answer = answer.split("```json")[1].split("```")[0]
    elif "```" in answer:
        answer = answer.split("```")[1].split("```")[0]
    return json.loads(answer)


def orchestrate(command: str, *, execute_simple_fn, run_steps_fn) -> str:
    from calculator import is_calculator_command, try_calculator
    from commands import is_complex_command

    command = command.lower().strip()
    if not command:
        return "Пустая команда"

    if is_calculator_command(command):
        result = try_calculator(command)
        if result:
            set_last_action(command, result)
            add_dialog_history(command, result)
            return result

    if is_complex_command(command):
        print("Составная команда, передаю планировщику...")
        try:
            steps = decompose_command(command)
            if steps:
                result = run_steps_fn(steps)
                set_last_action(command, result)
                add_dialog_history(command, result)
                return result
        except Exception as e:
            print("Планировщик:", e)

    result = execute_simple_fn(command)
    if result is not None:
        set_last_action(command, result)
        add_dialog_history(command, result)
        return result

    try:
        steps = decompose_command(command)
        if len(steps) > 1:
            result = run_steps_fn(steps)
            add_dialog_history(command, result)
            return result
        if len(steps) == 1 and steps[0] != command:
            return orchestrate(steps[0], execute_simple_fn=execute_simple_fn, run_steps_fn=run_steps_fn)
    except Exception as e:
        print("Planner fallback:", e)

    result = _agent_fallback(command)
    set_last_action(command, result)
    add_dialog_history(command, result)
    return result


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
