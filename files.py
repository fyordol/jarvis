"""Работа с файлами — только в разрешённых папках."""
import os
import shutil
from pathlib import Path
from typing import Optional

JARVIS_DIR = Path(__file__).resolve().parent

ALLOWED_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
    JARVIS_DIR,
]

FOLDER_ALIASES = {
    "рабочий стол": Path.home() / "Desktop",
    "рабочем столе": Path.home() / "Desktop",
    "документы": Path.home() / "Documents",
    "документах": Path.home() / "Documents",
    "загрузки": Path.home() / "Downloads",
    "загрузках": Path.home() / "Downloads",
    "jarvis": JARVIS_DIR,
    "джарвис": JARVIS_DIR,
    "джарvis": JARVIS_DIR,
}

MAX_READ_LINES = 50
MAX_READ_CHARS = 4000
_pending_delete: Optional[Path] = None


def _sorted_aliases():
    return sorted(FOLDER_ALIASES.items(), key=lambda x: len(x[0]), reverse=True)


def _resolve_path(raw: str) -> Path:
    raw = raw.strip().strip('"').strip("'")
    raw_lower = raw.lower()

    for alias, folder in _sorted_aliases():
        if raw_lower == alias:
            return folder
        if raw_lower.startswith(alias + " ") or raw_lower.startswith(alias + "/") or raw_lower.startswith(alias + "\\"):
            rest = raw[len(alias):].strip(" /\\")
            return folder / rest if rest else folder
        if raw_lower.endswith(" " + alias) or raw_lower.endswith("/" + alias) or raw_lower.endswith("\\" + alias):
            rest = raw[: raw_lower.rfind(alias)].strip(" /\\")
            return folder / rest if rest else folder
        if f" {alias} " in f" {raw_lower} ":
            idx = raw_lower.index(alias)
            before = raw[:idx].strip(" /\\")
            after = raw[idx + len(alias):].strip(" /\\")
            rest = before or after
            return folder / rest if rest else folder

    path = Path(raw)
    if not path.is_absolute():
        path = Path.home() / "Documents" / path
    return path.resolve()


def _is_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in ALLOWED_ROOTS:
        try:
            root_resolved = root.resolve()
            if resolved == root_resolved or root_resolved in resolved.parents:
                return True
        except OSError:
            continue
    return False


def _guard(path: Path) -> Optional[str]:
    if not _is_allowed(path):
        return "Нет доступа. Разрешены: рабочий стол, документы, загрузки, jarvis"
    return None


def cmd_read_file(command: str) -> Optional[str]:
    for prefix in ("прочитай файл ", "прочитай "):
        if command.startswith(prefix):
            path = _resolve_path(command[len(prefix):])
            err = _guard(path)
            if err:
                return err
            if not path.is_file():
                return f"Файл не найден: {path}"
            text = path.read_text(encoding="utf-8", errors="replace")
            if len(text) > MAX_READ_CHARS:
                text = text[:MAX_READ_CHARS] + "\n... (обрезано)"
            lines = text.splitlines()
            if len(lines) > MAX_READ_LINES:
                text = "\n".join(lines[:MAX_READ_LINES]) + "\n... (обрезано)"
            print(text)
            return f"Прочитал {path.name} ({len(lines)} строк)"


def cmd_find_file(command: str) -> Optional[str]:
    for prefix in ("найди файл ", "где файл "):
        if not command.startswith(prefix):
            continue
        query = command[len(prefix):].strip()
        if not query:
            return "Какой файл искать?"
        found = []
        for root in ALLOWED_ROOTS:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and query.lower() in path.name.lower():
                    found.append(path)
                    if len(found) >= 10:
                        break
            if len(found) >= 10:
                break
        if not found:
            return f"Файл «{query}» не найден"
        for p in found:
            print(f"  {p}")
        return f"Нашёл {len(found)} файл(ов)"


def cmd_open_file(command: str) -> Optional[str]:
    if not command.startswith("открой файл "):
        return None
    path = _resolve_path(command[len("открой файл "):])
    err = _guard(path)
    if err:
        return err
    if not path.exists():
        return f"Файл не найден: {path}"
    os.startfile(path)
    return f"Открываю {path.name}"


def cmd_create_file(command: str) -> Optional[str]:
    if not command.startswith("создай файл "):
        return None
    rest = command[len("создай файл "):].strip()
    if " с текстом " in rest:
        path_part, content = rest.split(" с текстом ", 1)
        path = _resolve_path(path_part)
        err = _guard(path)
        if err:
            return err
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Создал {path.name} с текстом"
    path = _resolve_path(rest)
    err = _guard(path)
    if err:
        return err
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return f"Создал {path.name}"


def cmd_append_file(command: str) -> Optional[str]:
    for prefix in ("допиши в файл ", "запиши в файл "):
        if not command.startswith(prefix):
            continue
        rest = command[len(prefix):].strip()
        parts = rest.split(" ", 1)
        if len(parts) < 2:
            return "Укажи файл и текст"
        path = _resolve_path(parts[0])
        err = _guard(path)
        if err:
            return err
        with path.open("a", encoding="utf-8") as f:
            f.write(parts[1] + "\n")
        return f"Дописал в {path.name}"


def cmd_rename_file(command: str) -> Optional[str]:
    if not command.startswith("переименуй файл "):
        return None
    rest = command[len("переименуй файл "):].strip()
    if " в " not in rest:
        return "Формат: переименуй файл X в Y"
    old_part, new_part = rest.split(" в ", 1)
    old_path = _resolve_path(old_part.strip())
    new_path = _resolve_path(new_part.strip())
    err = _guard(old_path) or _guard(new_path)
    if err:
        return err
    if not old_path.exists():
        return f"Файл не найден: {old_path}"
    old_path.rename(new_path)
    return f"Переименовал {old_path.name} → {new_path.name}"


def cmd_copy_file(command: str) -> Optional[str]:
    if not command.startswith("скопируй файл "):
        return None
    rest = command[len("скопируй файл "):].strip()
    if " в " not in rest:
        return "Формат: скопируй файл X в Y"
    src_part, dst_part = rest.split(" в ", 1)
    src = _resolve_path(src_part.strip())
    dst = _resolve_path(dst_part.strip())
    err = _guard(src) or _guard(dst)
    if err:
        return err
    if not src.exists():
        return f"Не найден: {src}"
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return f"Скопировал {src.name} → {dst}"


def cmd_move_file(command: str) -> Optional[str]:
    if not command.startswith("перемести файл "):
        return None
    rest = command[len("перемести файл "):].strip()
    if " в " not in rest:
        return "Формат: перемести файл X в Y"
    src_part, dst_part = rest.split(" в ", 1)
    src = _resolve_path(src_part.strip())
    dst = _resolve_path(dst_part.strip())
    err = _guard(src) or _guard(dst)
    if err:
        return err
    if not src.exists():
        return f"Не найден: {src}"
    shutil.move(str(src), str(dst))
    return f"Переместил {src.name} → {dst}"


def cmd_delete_file(command: str) -> Optional[str]:
    global _pending_delete
    if command in ("да удали", "подтверди удаление", "да удалить"):
        if _pending_delete is None:
            return "Нет файла, ожидающего удаления"
        path = _pending_delete
        _pending_delete = None
        if not path.exists():
            return "Файл уже не существует"
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return f"Удалил {path.name}"

    if command.startswith("удали файл "):
        path = _resolve_path(command[len("удали файл "):])
        err = _guard(path)
        if err:
            return err
        if not path.exists():
            return f"Не найден: {path}"
        _pending_delete = path
        return f"Удалить {path.name}? Скажи «да удали» для подтверждения"
    return None


def cmd_list_folder(command: str) -> Optional[str]:
    for prefix in ("покажи папку ", "список файлов ", "содержимое папки ", "открой папку "):
        if not command.startswith(prefix):
            continue
        path = _resolve_path(command[len(prefix):])
        err = _guard(path)
        if err:
            return err
        if not path.is_dir():
            return f"Папка не найдена: {path}"
        os.startfile(path)
        return f"Открываю папку {path.name}"


def try_files_command(command: str) -> Optional[str]:
    for handler in (
        cmd_delete_file,
        cmd_find_file,
        cmd_read_file,
        cmd_open_file,
        cmd_create_file,
        cmd_append_file,
        cmd_rename_file,
        cmd_copy_file,
        cmd_move_file,
        cmd_list_folder,
    ):
        result = handler(command)
        if result is not None:
            return result
    return None
