"""Быстрый поиск кликабельных элементов через Windows UI Automation."""
import time
from typing import Optional

from core.config import get
from desktop.ui_intents import expand_queries, uia_skip_reason

CLICKABLE = {
    "ButtonControl",
    "HyperlinkControl",
    "MenuItemControl",
    "SplitButtonControl",
    "TabItemControl",
    "ListItemControl",
    "EditControl",
    "ComboBoxControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "ImageControl",
    "TreeItemControl",
}

_warned = False


def _score_text(blob: str, queries: list[str]) -> float:
    if not blob:
        return 0.0
    best = 0.0
    for q in queries:
        if blob == q:
            return 1.0
        if q in blob:
            best = max(best, 0.9 if len(q) >= 4 else 0.75)
        elif blob in q and len(blob) >= 3:
            best = max(best, 0.7)
    return best


def _control_blob(ctrl) -> str:
    parts = []
    for attr in ("Name", "HelpText", "AutomationId", "ClassName"):
        try:
            val = getattr(ctrl, attr, "") or ""
        except Exception:
            val = ""
        if val:
            parts.append(str(val))
    return " ".join(parts).lower()


def _center(ctrl) -> Optional[tuple[int, int]]:
    try:
        rect = ctrl.BoundingRectangle
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 4 or h < 4:
            return None
        if rect.right < 0 or rect.bottom < 0:
            return None
        return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
    except Exception:
        return None


def _walk(ctrl, depth: int, acc: list, deadline: float, limit: int) -> None:
    if depth < 0 or len(acc) >= limit or time.time() > deadline:
        return
    try:
        ctype = ctrl.ControlTypeName
    except Exception:
        return
    if ctype in CLICKABLE:
        acc.append(ctrl)
    if depth == 0:
        return
    try:
        children = ctrl.GetChildren()
    except Exception:
        return
    for ch in children:
        _walk(ch, depth - 1, acc, deadline, limit)
        if len(acc) >= limit or time.time() > deadline:
            return


def find_ui_element(target: str) -> Optional[dict]:
    if not get("vision.uia_enabled", True):
        return None
    if uia_skip_reason(target):
        return None

    global _warned
    try:
        import uiautomation as auto
    except Exception:
        if not _warned:
            print("UI Automation недоступна (pip install uiautomation) — пропускаю быстрый путь")
            _warned = True
        return None

    queries = expand_queries(target)
    if not queries:
        return None

    timeout = float(get("vision.uia_timeout_sec", 0.7))
    deadline = time.time() + timeout
    auto.SetGlobalSearchTimeout(0.05)

    try:
        root = auto.GetForegroundControl()
        if root is None:
            root = auto.GetRootControl()
    except Exception as e:
        print(f"UIA foreground: {e}")
        return None

    controls: list = []
    _walk(root, int(get("vision.uia_depth", 12)), controls, deadline, 160)

    best = None
    best_score = 0.0
    for ctrl in controls:
        blob = _control_blob(ctrl)
        score = _score_text(blob, queries)
        if score < 0.7:
            continue
        coords = _center(ctrl)
        if coords is None:
            continue
        if score > best_score:
            best_score = score
            best = {"coords": coords, "confidence": min(0.95, 0.55 + score * 0.4), "reason": f"UIA {blob[:80]}"}

    if best:
        print(f"UIA нашёл «{target}»: {best['reason']} at {best['coords']}")
    return best
