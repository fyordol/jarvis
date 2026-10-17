"""Цепочка быстрых skills до vision/LLM."""
from typing import Callable, Optional


def run_skill_chain(command: str, _execute_simple: Callable) -> Optional[str]:
    from skills.calculator import try_calculator
    from skills.files import try_files_command
    from skills.homework import try_homework
    from control.media import try_media_command
    from core.memory import parse_remember_command
    from skills.message import try_message
    from desktop.screen import try_confirm_send
    from skills.search import try_entry_command, try_platform_search
    from skills.terminal import try_terminal_command

    for handler in (
        try_confirm_send,
        try_message,
        try_homework,
        parse_remember_command,
        try_calculator,
        try_media_command,
        try_entry_command,
        try_platform_search,
        try_files_command,
        try_terminal_command,
    ):
        result = handler(command)
        if result is not None:
            return result
    return None
