"""Безопасный калькулятор для голосовых команд."""
import ast
import operator
import re
from typing import Optional

from llm_client import chat_completion

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"(\d+)\s*%\s*от", r"\1 процентов от", text)
    text = re.sub(r"(\d+)\s*%", r"\1 процентов", text)
    replacements = [
        (r"\bплюс\b", "+"),
        (r"\bминус\b", "-"),
        (r"\bумнож(ить|енное)?\s*(на)?\b", "*"),
        (r"\bраздел(ить|ённое)?\s*(на)?\b", "/"),
        (r"\bдел(ить|ённое)?\s*(на)?\b", "/"),
        (r"\bв\s+степени\b", "**"),
        (r",", "."),
        (r"\s+", ""),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    return text


def _parse_percent(expr: str) -> Optional[tuple[float, float]]:
    """5% от 1000, 5 процентов от 1000, сколько будет 5 процентов от 10."""
    expr = expr.replace(",", ".")
    patterns = [
        r"(\d+(?:\.\d+)?)\s*%\s*от\s+(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*процент(?:а|ов)?\s+от\s+(\d+(?:\.\d+)?)",
    ]
    for pat in patterns:
        m = re.search(pat, expr.strip())
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def is_calculator_command(command: str) -> bool:
    """True, если команда — один запрос к калькулятору (не составная задача)."""
    command = command.strip().lower()
    prefixes = ("посчитай ", "вычисли ", "сколько будет ", "калькулятор ")
    if any(command.startswith(p) for p in prefixes):
        tail = command
        for prefix in prefixes:
            if tail.startswith(prefix):
                tail = tail[len(prefix):].strip()
                break
        if tail.startswith("сколько будет "):
            tail = tail[len("сколько будет "):].strip()
        other_actions = (
            "открой", "закрой", "найди", "напиши", "нажми", "кликни",
            "прочитай", "создай файл", "выполни", "громче", "тише",
        )
        if any(kw in tail for kw in other_actions):
            return False
        if any(m in command for m in (" и ", ", ", " потом ", " затем ", " после ")):
            return False
        return True
    return ("процент" in command or "%" in command) and " от " in command


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    raise ValueError("Недопустимое выражение")


def _eval_expr(expr: str) -> float:
    expr = _normalize(expr)
    if not expr:
        raise ValueError("Пустое выражение")
    tree = ast.parse(expr, mode="eval")
    return _safe_eval(tree)


def _format_result(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.4g}"


def _ask_llm_to_calculate(expr: str) -> Optional[str]:
    try:
        answer = chat_completion(
            [
                {
                    "role": "system",
                    "content": "Ты калькулятор. Ответь СТРОГО только финальным числом.",
                },
                {"role": "user", "content": f"Вычисли: {expr}"},
            ],
            role="calculator",
            temperature=0.0,
            timeout=30,
        )
        match = re.search(r"[-+]?\d*\.?\d+", answer.replace(",", "."))
        return match.group(0) if match else None
    except Exception as e:
        print(f"LLM калькулятор ошибка: {e}")
        return None


def try_calculator(command: str) -> Optional[str]:
    command = command.strip().lower()

    prefixes = ("посчитай ", "вычисли ", "сколько будет ", "калькулятор ")
    expr = None
    for prefix in prefixes:
        if command.startswith(prefix):
            expr = command[len(prefix):].strip()
            break

    if expr is None:
        if "процент" in command or "%" in command:
            if " от " in command:
                expr = command
            else:
                return None
        else:
            return None

    if expr.startswith("сколько будет "):
        expr = expr[len("сколько будет "):].strip()

    pct_vals = _parse_percent(expr)
    if pct_vals:
        pct, base = pct_vals
        result = base * pct / 100
        formatted = _format_result(result)
        return f"{pct} процентов от {base} — это {formatted}"

    try:
        result = _eval_expr(expr)
        return f"Ответ: {_format_result(result)}"
    except Exception:
        pass

    llm_answer = _ask_llm_to_calculate(expr)
    if llm_answer:
        return f"Ответ: {llm_answer}"

    return "Не могу посчитать это выражение."
