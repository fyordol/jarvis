import json

from llm_client import chat_completion
from memory import build_planner_memory_context, format_dialog_for_prompt

SYSTEM_PROMPT = """
Ты планировщик задач для голосового ассистента Джарвис.
Пользователь даёт задачу на естественном языке — разбей её на последовательность ПРОСТЫХ команд.

Допустимые команды (строго в таком формате):

Открытие и закрытие:
- "открой [имя]" — только имя, без слов «приложение», «сайт», «программу»
  Примеры: "открой discord", "открой блокнот", "открой ютуб" (НЕ "открой приложение discord")
- "открой файл [путь или имя]"
- "открой папку [рабочий стол / документы / jarvis / джарвис]"
- "закрой [имя]" — тоже без слова «приложение»: "закрой discord", "закрой хром"

Поиск:
- "найди [запрос]" — общий поиск в Яндексе
- "поиск на [сайт] [запрос]"
- "найди файл [имя]"

Файлы:
- "прочитай файл [путь]"
- "создай файл [имя] с текстом [текст]"
- "допиши в файл [имя] [текст]"
- "переименуй файл X в Y"
- "скопируй файл X в Y"
- "перемести файл X в Y"
- "удали файл [имя]" (требует подтверждения)

Ввод и клики:
- "напиши [текст]"
- "нажми на [элемент]" / "кликни по [элемент]"
- "двойной клик по [элемент]"
- "правый клик по [элемент]"
- "энтер" / "пробел" / "эскейп" / "таб"
- "скопируй" / "вставь" / "вырежи" / "выдели всё"

Медиа, окна, браузер:
- "громче" / "тише" / "выключи звук"
- "пауза" / "следующий трек"
- "переключи раскладку"
- "новая вкладка" / "закрой вкладку"
- "переключись на [окно]"

Калькулятор:
- "посчитай [выражение]"

Терминал:
- "выполни [команда]"

Правила:
1. Отвечай ТОЛЬКО JSON-массивом строк.
2. Каждый шаг — одна команда из списка выше.
3. Не выдумывай другие типы команд.
4. Пути: "рабочий стол", "документы", "jarvis", "джарвис".
5. Имена сайтов и программ пиши как есть: discord, ютуб, блокнот, хром, telegram — без префиксов.
6. Если нужно открыть программу или сайт — команда всегда "открой X", где X — короткое имя.
7. Одна простая задача — один шаг. «Посчитай 5 процентов от 10» → ["посчитай 5 процентов от 10"], не разбивай.
8. НЕ копируй команды из истории диалога — только то, что просит пользователь СЕЙЧАС.
9. Запрещены шаблоны в квадратных скобках: пиши "нажми на кнопку отправить", а не "нажми на [элемент]".
"""


def _sanitize_steps(steps: list[str], original: str) -> list[str]:
    cleaned = []
    for step in steps:
        if "[" in step and "]" in step:
            continue
        cleaned.append(step)
    if not cleaned:
        return [original.strip().lower()]
    return cleaned


def decompose_command(complex_command: str) -> list[str]:
    from calculator import is_calculator_command

    if is_calculator_command(complex_command):
        return [complex_command.strip().lower()]

    memory_ctx = build_planner_memory_context(complex_command)
    dialog_ctx = format_dialog_for_prompt(3)
    user_content = complex_command
    if memory_ctx:
        user_content += f"\n\n{memory_ctx}"
    if dialog_ctx:
        user_content += f"\n\n{dialog_ctx}"

    answer = chat_completion(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        role="planner",
        temperature=0.1,
        timeout=60,
    )
    print("Планировщик разбил команду на:", answer)

    if "```json" in answer:
        answer = answer.split("```json")[1].split("```")[0]
    elif "```" in answer:
        answer = answer.split("```")[1].split("```")[0]

    steps = json.loads(answer.strip())
    if not isinstance(steps, list):
        raise ValueError("Планировщик вернул не массив")

    result = [str(step).strip().lower() for step in steps if str(step).strip()]
    return _sanitize_steps(result, complex_command)
