"""Общая память Jarvis: vision-паттерны, команды, макросы, история диалога."""
import json
import re
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import get

MEMORY_FILE = Path(__file__).resolve().parent / "jarvis_memory.json"
LEGACY_FILE = Path(__file__).resolve().parent / "vision_memory.json"
MAX_PATTERNS = 150

_last_click: Optional[dict] = None
_last_action: Optional[dict] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_legacy() -> None:
    if MEMORY_FILE.exists() or not LEGACY_FILE.exists():
        return
    try:
        data = json.loads(LEGACY_FILE.read_text(encoding="utf-8"))
        for p in data.get("patterns", []):
            p.setdefault("kind", "vision")
        MEMORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass


def _load_data() -> dict:
    _migrate_legacy()
    if not MEMORY_FILE.exists():
        return {"patterns": []}
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("patterns"), list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"patterns": []}


def _save_data(data: dict) -> None:
    patterns = data.get("patterns", [])
    if len(patterns) > MAX_PATTERNS:
        patterns.sort(
            key=lambda p: (p.get("success_count", 0), p.get("last_used", "")),
            reverse=True,
        )
        data["patterns"] = patterns[:MAX_PATTERNS]
    MEMORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zа-яё0-9]+", text.lower()) if len(w) > 1}


def _similarity(a: str, b: str) -> float:
    a_lower, b_lower = a.lower(), b.lower()
    if a_lower == b_lower:
        return 1.0
    if a_lower in b_lower or b_lower in a_lower:
        return 0.85
    tokens_a, tokens_b = _tokenize(a), _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _find_existing(data: dict, phrase: str, kind: Optional[str] = None) -> Optional[dict]:
    for p in data["patterns"]:
        if kind and p.get("kind") != kind:
            continue
        if _similarity(p.get("phrase", ""), phrase) >= 0.9:
            return p
        for alias in p.get("aliases", []):
            if _similarity(alias, phrase) >= 0.95:
                return p
    return None


def find_matching_patterns(
    target: str,
    limit: int = 3,
    min_score: float = 0.35,
    kind: Optional[str] = None,
) -> list[dict]:
    data = _load_data()
    scored = []
    for pattern in data["patterns"]:
        if kind and pattern.get("kind") != kind:
            continue
        phrase = pattern.get("phrase", "")
        score = _similarity(target, phrase)
        for alias in pattern.get("aliases", []):
            score = max(score, _similarity(target, alias))
        if score >= min_score:
            scored.append((score, pattern))
    scored.sort(key=lambda x: (x[0], x[1].get("success_count", 0)), reverse=True)
    return [p for _, p in scored[:limit]]


def resolve_stored_command(command: str, min_score: float = 0.88) -> Optional[list[str]]:
    """Если команда совпадает с сохранённым макросом/алиасом — вернуть шаги."""
    command = command.strip().lower()
    patterns = find_matching_patterns(command, limit=1, min_score=min_score, kind="command")
    if not patterns:
        patterns = find_matching_patterns(command, limit=1, min_score=min_score, kind="macro")
    if not patterns:
        return None

    pattern = patterns[0]
    action = pattern.get("action")

    data = _load_data()
    for p in data["patterns"]:
        if p.get("id") == pattern.get("id"):
            p["last_used"] = _now_iso()
            break
    _save_data(data)

    if isinstance(action, list) and action:
        return [str(s).strip().lower() for s in action if str(s).strip()]
    if isinstance(action, str) and action.strip():
        return [action.strip().lower()]
    return None


# --- last click / last action tracking ---

def set_last_click(
    target: str,
    coords: tuple[int, int],
    screen_w: int,
    screen_h: int,
    confidence: float = 0.0,
) -> None:
    global _last_click, _last_action
    x, y = coords
    _last_click = {
        "target": target,
        "coords": coords,
        "screen_w": screen_w,
        "screen_h": screen_h,
        "normalized_x": round(x / screen_w, 4) if screen_w else 0,
        "normalized_y": round(y / screen_h, 4) if screen_h else 0,
        "confidence": confidence,
    }
    _last_action = {"command": target, "kind": "vision", "result": "click"}


def get_last_click() -> Optional[dict]:
    return _last_click


def set_last_action(command: str, result: str = "", kind: str = "command") -> None:
    global _last_action
    _last_action = {"command": command.strip().lower(), "result": result, "kind": kind}


def get_last_action() -> Optional[dict]:
    return _last_action


# --- pattern CRUD ---

def add_pattern(
    phrase: str,
    kind: str = "vision",
    action: Optional[str | list[str]] = None,
    custom_hint: str = "",
    object_type: str = "",
    area_hint: str = "",
    position_hint: str = "",
    order_hint: str = "",
    click_hint: str = "",
    normalized_x: Optional[float] = None,
    normalized_y: Optional[float] = None,
    aliases: Optional[list[str]] = None,
) -> str:
    phrase = phrase.strip().lower()
    if not phrase:
        return "Не указана фраза для запоминания"

    data = _load_data()
    existing = _find_existing(data, phrase, kind=kind)

    if existing:
        pattern = existing
        pattern["last_used"] = _now_iso()
    else:
        pattern = {
            "id": str(uuid.uuid4())[:8],
            "kind": kind,
            "phrase": phrase,
            "success_count": 0,
            "fail_count": 0,
            "created": _now_iso(),
            "last_used": _now_iso(),
        }
        data["patterns"].append(pattern)

    if action is not None:
        if isinstance(action, str):
            steps = _split_steps(action)
            pattern["action"] = steps if len(steps) > 1 else steps[0]
        else:
            pattern["action"] = [s.strip().lower() for s in action if s.strip()]
    if custom_hint:
        pattern["custom_hint"] = custom_hint
    if object_type:
        pattern["object_type"] = object_type
    if area_hint:
        pattern["area_hint"] = area_hint
    if position_hint:
        pattern["position_hint"] = position_hint
    if order_hint:
        pattern["order_hint"] = order_hint
    if click_hint:
        pattern["click_hint"] = click_hint
    if normalized_x is not None:
        pattern["normalized_x"] = normalized_x
    if normalized_y is not None:
        pattern["normalized_y"] = normalized_y
    if aliases:
        pattern["aliases"] = [a.strip().lower() for a in aliases if a.strip()]

    _save_data(data)
    kind_label = {"vision": "vision", "command": "команду", "macro": "макрос"}.get(kind, kind)
    return f"Запомнил {kind_label}: «{phrase}»"


def _split_steps(text: str) -> list[str]:
    text = text.strip()
    for sep in (" и ", ", ", " потом ", " затем ", " а потом "):
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            if len(parts) > 1:
                return parts
    return [text]


def save_last_click_as_pattern(custom_hint: str = "") -> str:
    ctx = get_last_click()
    if not ctx:
        return "Нет последнего клика для запоминания"
    hint = custom_hint or "кликать в эту зону экрана"
    return add_pattern(
        phrase=ctx["target"],
        kind="vision",
        custom_hint=hint,
        click_hint="центр объекта в сохранённой зоне",
        normalized_x=ctx.get("normalized_x"),
        normalized_y=ctx.get("normalized_y"),
    )


def save_last_action_as_pattern(phrase: str = "") -> str:
    ctx = get_last_action()
    if not ctx:
        return "Нет последнего действия для запоминания"
    cmd = ctx["command"]
    if not phrase:
        phrase = cmd
    if ctx.get("kind") == "vision":
        return save_last_click_as_pattern()
    return add_pattern(phrase=phrase, kind="command", action=cmd)


def record_feedback(success: bool) -> str:
    ctx = get_last_action() or (
        {"command": get_last_click()["target"], "kind": "vision"} if get_last_click() else None
    )
    if not ctx:
        return "Нет последнего действия для оценки"

    target = ctx["command"]
    kind = ctx.get("kind", "command")
    patterns = find_matching_patterns(target, limit=1, min_score=0.5, kind=kind)
    if not patterns and kind == "vision":
        patterns = find_matching_patterns(target, limit=1, min_score=0.5)

    data = _load_data()
    if patterns:
        pattern = patterns[0]
    else:
        pattern = {
            "id": str(uuid.uuid4())[:8],
            "kind": kind,
            "phrase": target,
            "success_count": 0,
            "fail_count": 0,
            "created": _now_iso(),
        }
        if kind == "command" and ctx.get("kind") == "command":
            pattern["action"] = target
        data["patterns"].append(pattern)

    if success:
        pattern["success_count"] = pattern.get("success_count", 0) + 1
        click = get_last_click()
        if click and click.get("target") == target:
            pattern["normalized_x"] = click.get("normalized_x")
            pattern["normalized_y"] = click.get("normalized_y")
        msg = f"Запомнил успешное действие для «{target}»"
    else:
        pattern["fail_count"] = pattern.get("fail_count", 0) + 1
        msg = f"Отметил неудачное действие для «{target}»"

    pattern["last_used"] = _now_iso()
    _save_data(data)
    return msg


def delete_pattern(phrase: str) -> str:
    phrase = phrase.strip().lower()
    data = _load_data()
    before = len(data["patterns"])
    data["patterns"] = [
        p for p in data["patterns"]
        if _similarity(p.get("phrase", ""), phrase) < 0.9
        and all(_similarity(a, phrase) < 0.9 for a in p.get("aliases", []))
    ]
    _save_data(data)
    removed = before - len(data["patterns"])
    if removed:
        return f"Удалил {removed} паттерн(ов) для «{phrase}»"
    return f"Паттерн «{phrase}» не найден"


def list_patterns(kind: Optional[str] = None) -> str:
    data = _load_data()
    patterns = data["patterns"]
    if kind:
        patterns = [p for p in patterns if p.get("kind") == kind]
    if not patterns:
        return "Память пуста" if not kind else f"Нет паттернов типа «{kind}»"

    kind_icons = {"vision": "[vision]", "command": "[cmd]", "macro": "[macro]"}
    lines = []
    for p in patterns:
        k = p.get("kind", "vision")
        icon = kind_icons.get(k, "•")
        line = f"  {icon} «{p.get('phrase', '')}»"
        if p.get("action"):
            act = p["action"]
            if isinstance(act, list):
                line += f" → {' → '.join(act)}"
            else:
                line += f" → {act}"
        elif p.get("custom_hint"):
            line += f" — {p['custom_hint']}"
        succ = p.get("success_count", 0)
        fail = p.get("fail_count", 0)
        if succ or fail:
            line += f" (+{succ}/-{fail})"
        lines.append(line)
    print("\n".join(lines))
    return f"Паттернов в памяти: {len(patterns)}"


def build_memory_hints(target: str) -> str:
    """Подсказки vision из памяти."""
    patterns = find_matching_patterns(target, kind="vision")
    if not patterns:
        patterns = find_matching_patterns(target)
        patterns = [p for p in patterns if p.get("kind", "vision") == "vision"]
    if not patterns:
        return ""

    lines = ["Память пользователя (используй как приоритетные подсказки):"]
    for p in patterns:
        parts = [f'Команда «{p.get("phrase", "")}»']
        if p.get("custom_hint"):
            parts.append(p["custom_hint"])
        if p.get("object_type"):
            parts.append(f"тип: {p['object_type']}")
        if p.get("area_hint"):
            parts.append(f"область: {p['area_hint']}")
        if p.get("position_hint"):
            parts.append(f"позиция: {p['position_hint']}")
        if p.get("order_hint"):
            parts.append(f"порядок: {p['order_hint']}")
        if p.get("click_hint"):
            parts.append(f"клик: {p['click_hint']}")
        if p.get("normalized_x") is not None and p.get("normalized_y") is not None:
            parts.append(f"зона: x={p['normalized_x']}, y={p['normalized_y']}")
        lines.append("- " + "; ".join(parts))
    return "\n".join(lines)


def build_planner_memory_context(command: str) -> str:
    """Контекст памяти для планировщика."""
    patterns = find_matching_patterns(command, limit=5, min_score=0.4)
    if not patterns:
        return ""

    lines = ["Известные пользователю паттерны (учитывай при разборе):"]
    for p in patterns:
        k = p.get("kind", "vision")
        if k in ("command", "macro") and p.get("action"):
            act = p["action"]
            steps = act if isinstance(act, list) else [act]
            lines.append(f'- «{p["phrase"]}» → {steps}')
        elif p.get("custom_hint"):
            lines.append(f'- «{p["phrase"]}» → {p["custom_hint"]}')
    return "\n".join(lines)


def _extract_hints_from_text(text: str) -> dict:
    text_lower = text.lower()
    result = {"custom_hint": text.strip()}

    type_map = {"видео": "video_card", "кнопк": "button", "иконк": "icon", "вкладк": "tab", "поле": "input_field"}
    for key, val in type_map.items():
        if key in text_lower:
            result["object_type"] = val
            break

    for word in ("справа", "слева", "сверху", "снизу", "в центре", "по центру"):
        if word in text_lower:
            result["position_hint"] = word
            break

    for word in ("в ленте", "в списке", "в меню", "на панели", "в браузере"):
        if word in text_lower:
            result["area_hint"] = word
            break

    for word in ("первое", "первый", "второе", "второй", "третье", "последнее"):
        if word in text_lower:
            result["order_hint"] = word
            break

    if "превью" in text_lower:
        result["click_hint"] = "центр превью"
    elif "кнопк" in text_lower:
        result["click_hint"] = "центр кнопки"

    return result


def parse_remember_command(command: str) -> Optional[str]:
    command = command.strip().lower()

    if command in ("да", "верно", "правильно", "точно", "именно так"):
        return record_feedback(success=True)

    if command in ("нет", "не туда", "неправильно", "мимо", "не то"):
        return record_feedback(success=False)

    if command in ("запомни клик", "запомни последний клик", "запомни этот клик"):
        return save_last_click_as_pattern()

    if command in ("запомни действие", "запомни последнее действие", "запомни это действие"):
        return save_last_action_as_pattern()

    if command.startswith("забудь паттерн ") or command.startswith("забудь "):
        phrase = command.replace("забудь паттерн ", "", 1).replace("забудь ", "", 1).strip()
        return delete_pattern(phrase)

    if command in ("покажи паттерны", "список паттернов", "мои паттерны", "память паттернов", "покажи память"):
        return list_patterns()

    if command in ("покажи команды", "мои команды", "покажи макросы"):
        return list_patterns(kind="command") or list_patterns(kind="macro")

    # запомни команду X: Y
    match = re.match(r"запомни\s+(?:команду|макрос)\s+(.+?)\s*[:\-—]\s*(.+)", command)
    if match:
        phrase, action_text = match.group(1).strip(), match.group(2).strip()
        steps = _split_steps(action_text)
        kind = "macro" if len(steps) > 1 else "command"
        return add_pattern(phrase=phrase, kind=kind, action=steps if len(steps) > 1 else steps[0])

    # запомни когда я говорю X выполняй Y
    match = re.match(
        r"запомни[:\s,]+когда я говорю\s+(.+?)\s*,?\s*(?:выполняй|делай|запускай)\s+(.+)",
        command,
    )
    if match:
        phrase, action_text = match.group(1).strip(), match.group(2).strip()
        steps = _split_steps(action_text)
        kind = "macro" if len(steps) > 1 else "command"
        return add_pattern(phrase=phrase, kind=kind, action=steps if len(steps) > 1 else steps[0])

    # запомни: когда я говорю X, нажимай Y (vision)
    match = re.match(
        r"запомни[:\s,]+когда я говорю\s+(.+?)\s*,?\s*(?:это|нажимай|кликай|выбирай)\s+(.+)",
        command,
    )
    if match:
        phrase, hint_text = match.group(1).strip(), match.group(2).strip()
        hints = _extract_hints_from_text(hint_text)
        return add_pattern(phrase=phrase, kind="vision", **hints)

    # запомни что X значит Y
    match = re.match(r"запомни[:\s,]+что\s+(.+?)\s+(?:это|значит|означает)\s+(.+)", command)
    if match:
        phrase, hint_text = match.group(1).strip(), match.group(2).strip()
        if any(kw in hint_text for kw in ("открой", "найди", "покажи", "выполни", "громче", "напиши")):
            steps = _split_steps(hint_text)
            kind = "macro" if len(steps) > 1 else "command"
            return add_pattern(phrase=phrase, kind=kind, action=steps if len(steps) > 1 else steps[0])
        hints = _extract_hints_from_text(hint_text)
        return add_pattern(phrase=phrase, kind="vision", **hints)

    # запомни что X это команда Y (явно)
    match = re.match(r"запомни[:\s,]+(.+?)\s+(?:это|—|-)\s+(?:команда|действие)\s+(.+)", command)
    if match:
        phrase, action_text = match.group(1).strip(), match.group(2).strip()
        steps = _split_steps(action_text)
        kind = "macro" if len(steps) > 1 else "command"
        return add_pattern(phrase=phrase, kind=kind, action=steps if len(steps) > 1 else steps[0])

    match = re.match(r"запомни паттерн\s+(.+)", command)
    if match:
        phrase = match.group(1).strip()
        hints = _extract_hints_from_text(phrase)
        return add_pattern(phrase=phrase, kind="vision", **hints)

    if command.startswith("запомни"):
        rest = re.sub(r"^запомни[:\s,]+", "", command).strip()
        if rest:
            for sep in (" — ", " - ", " это ", " значит "):
                if sep in rest:
                    phrase, hint = rest.split(sep, 1)
                    phrase, hint = phrase.strip(), hint.strip()
                    if any(kw in hint for kw in ("открой", "найди", "покажи", "выполни", "громче", "напиши", "закрой")):
                        steps = _split_steps(hint)
                        kind = "macro" if len(steps) > 1 else "command"
                        return add_pattern(phrase=phrase, kind=kind, action=steps if len(steps) > 1 else steps[0])
                    hints = _extract_hints_from_text(hint)
                    return add_pattern(phrase=phrase, kind="vision", **hints)
            if any(kw in rest for kw in ("открой", "найди", "покажи", "выполни")):
                return add_pattern(phrase=rest, kind="command", action=rest)
            hints = _extract_hints_from_text(rest)
            return add_pattern(phrase=rest, kind="vision", **hints)

    return None


# --- История диалога ---

_dialog_history: deque[dict] = deque(maxlen=50)


def _dialog_max_size() -> int:
    return int(get("dialog.history_max", 20))


def add_dialog_history(user: str, assistant: str) -> None:
    global _dialog_history
    if _dialog_history.maxlen != _dialog_max_size():
        _dialog_history = deque(_dialog_history, maxlen=_dialog_max_size())
    _dialog_history.append({"user": user.strip(), "assistant": assistant.strip()})


def format_dialog_for_prompt(n: int = 5) -> str:
    items = list(_dialog_history)[-n:]
    if not items:
        return ""
    lines = ["Недавний диалог:"]
    for item in items:
        lines.append(f"Пользователь: {item['user']}")
        lines.append(f"Jarvis: {item['assistant']}")
    return "\n".join(lines)
