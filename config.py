"""Загрузка config.yaml."""
from pathlib import Path
from typing import Any

_CONFIG: dict | None = None
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _parse_simple_yaml(text: str) -> dict:
    """Минимальный YAML-парсер для flat/nested ключей без зависимостей."""
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]

    for line in text.splitlines():
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip())
        key_val = stripped.strip()
        if ":" not in key_val:
            continue
        key, _, val = key_val.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if not val:
            node: dict = {}
            parent[key] = node
            stack.append((indent, node))
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            parent[key] = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()] if inner else []
        elif val.lower() in ("true", "false"):
            parent[key] = val.lower() == "true"
        else:
            try:
                parent[key] = int(val)
            except ValueError:
                try:
                    parent[key] = float(val)
                except ValueError:
                    parent[key] = val
    return root


def load_config() -> dict:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG
    if CONFIG_PATH.exists():
        _CONFIG = _parse_simple_yaml(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        _CONFIG = {}
    return _CONFIG


def get(path: str, default: Any = None) -> Any:
    cfg = load_config()
    node = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def model_for(role: str) -> str:
    models = get("llm.models", {}) or {}
    if isinstance(models, dict) and role in models:
        return str(models[role])
    return str(get("llm.default_model", "qwen"))
