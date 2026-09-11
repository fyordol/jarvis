"""Терминал: whitelist-команды и опциональный LLM-агент."""
import json
import re
import subprocess
from pathlib import Path
from typing import Optional

from config import get
from llm_client import chat_completion

DEFAULT_CWD = Path(__file__).resolve().parent
MAX_OUTPUT = 2000

BLOCKED_PATTERNS = [
    r"\brm\b", r"\bdel\b", r"\brmdir\b", r"\bformat\b", r"\bshutdown\b",
    r"\brestart\b", r"\breg\b", r"\bpowershell\b",
    r"\bcmd\s*/c\b", r"[;&|`$]", r"\.\.",
]

_LLM_SYSTEM = """Ты терминальный агент Jarvis. Предложи ОДНУ безопасную команду.
Разрешены: git, python, pip, dir, echo, type, where, pytest, node, npm.
Запрещено: del, rm, powershell, shutdown, цепочки (; | &).
Ответ — ТОЛЬКО JSON: {"command": "git status"} или {"command": null}."""


def _timeout() -> int:
    return int(get("terminal.timeout_sec", 30))


def _allowed_binaries() -> set[str]:
    raw = get("terminal.allowed_binaries", [])
    if isinstance(raw, list) and raw:
        return {str(x).lower() for x in raw}
    return {"git", "python", "pip", "py", "node", "npm", "dir", "echo", "type", "where", "pytest"}


def _is_safe(cmd: str) -> Optional[str]:
    if not get("terminal.enabled", True):
        return "Терминал отключён в config.yaml"
    cmd_lower = cmd.lower().strip()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_lower):
            return "Команда содержит запрещённые символы или операции"
    parts = cmd.strip().split()
    if not parts:
        return "Пустая команда"
    binary = parts[0].lower().removesuffix(".exe")
    allowed = _allowed_binaries()
    if binary not in allowed:
        return f"Команда «{parts[0]}» не в whitelist. Доступны: {', '.join(sorted(allowed))}"
    return None


def run_terminal_command(cmd: str, cwd: Optional[Path] = None) -> str:
    cmd = cmd.strip()
    err = _is_safe(cmd)
    if err:
        return err
    work_dir = cwd or DEFAULT_CWD
    if not work_dir.exists():
        work_dir = DEFAULT_CWD
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=str(work_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_timeout(),
        )
    except subprocess.TimeoutExpired:
        return f"Таймаут ({_timeout()} сек): {cmd}"
    except Exception as e:
        return f"Ошибка запуска: {e}"
    output = (result.stdout + result.stderr).strip()
    if len(output) > MAX_OUTPUT:
        output = output[:MAX_OUTPUT] + "\n... (обрезано)"
    if output:
        print(output)
    status = "OK" if result.returncode == 0 else f"код {result.returncode}"
    return f"Выполнил: {cmd} ({status})"


def _llm_suggest_command(task: str) -> Optional[str]:
    if not get("terminal.llm_agent_enabled", False):
        return None
    try:
        answer = chat_completion(
            [{"role": "system", "content": _LLM_SYSTEM}, {"role": "user", "content": task}],
            role="terminal_agent", temperature=0.0, timeout=30,
        )
        if "```" in answer:
            answer = answer.split("```", 1)[-1].split("```", 1)[0]
        start, end = answer.find("{"), answer.rfind("}")
        if start == -1:
            return None
        data = json.loads(answer[start : end + 1])
        cmd = data.get("command")
        return str(cmd).strip() if cmd else None
    except Exception as e:
        print(f"Terminal LLM: {e}")
        return None


def try_terminal_command(command: str) -> Optional[str]:
    for prefix in ("агент выполни ", "llm выполни ", "авто выполни "):
        if command.startswith(prefix):
            task = command[len(prefix):].strip()
            if not task:
                return "Какую задачу выполнить?"
            suggested = _llm_suggest_command(task)
            if not suggested:
                return "Не могу подобрать безопасную команду"
            print(f"LLM предложила: {suggested}")
            return run_terminal_command(suggested, cwd=DEFAULT_CWD)

    for prefix in ("выполни ", "запусти в терминале ", "запусти команду ", "терминал "):
        if command.startswith(prefix):
            cmd = command[len(prefix):].strip()
            if not cmd:
                return "Какую команду выполнить?"
            return run_terminal_command(cmd)
    return None
