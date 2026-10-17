"""Умный токенизатор: чистит речь, чинит опечатки STT и маршрутизирует команду без LLM."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

LEAD_FILLERS = (
    "не мог бы ты", "не могли бы вы", "будь добр", "будь другом", "если не сложно",
    "джарвис", "жарвис", "jarvis", "слушай", "эй", "ну", "давай", "так", "короче",
    "можешь", "можете", "сможешь", "пожалуйста", "плиз", "пж", "быстро", "быстренько",
    "срочно", "сейчас", "щас", "мне", "нам", "а", "ок", "окей", "хорошо", "теперь",
)
ANY_FILLERS = ("пожалуйста", "плиз", "пжлст", "пж", "please")

VERBS: dict[str, tuple[str, ...]] = {
    "открой": (
        "открой", "откройте", "открыть", "открою", "открывай", "запусти", "запустить",
        "запускай", "вруби", "врубай", "врубить",
    ),
    "закрой": ("закрой", "закройте", "закрыть", "закрывай", "выруби", "вырубай", "заверши", "убей"),
    "найди": ("найди", "найти", "поищи", "поискать", "ищи", "искать", "отыщи"),
    "загугли": ("загугли", "погугли", "гугли", "нагугли"),
    "напиши": ("напиши", "написать", "напечатай", "набери", "введи", "пиши"),
    "посчитай": ("посчитай", "посчитать", "подсчитай", "вычисли", "сосчитай"),
    "прочитай": ("прочитай", "прочти", "зачитай"),
}
SOFT_OPEN = ("включи", "включить", "врубни")

PHRASES: dict[str, tuple[str, ...]] = {
    "пауза": ("поставь на паузу", "на паузу", "поставь паузу", "сними с паузы", "продолжи музыку", "стоп музыка"),
    "громче": ("сделай громче", "погромче", "прибавь звук", "звук громче", "громкость выше"),
    "тише": ("сделай тише", "потише", "убавь звук", "звук тише", "громкость ниже"),
    "выключи звук": ("выруби звук", "отключи звук", "без звука", "мут", "замьють"),
    "следующий трек": ("следующую песню", "некст трек", "переключи трек", "следующую"),
    "предыдущий трек": ("предыдущую песню", "прошлый трек", "прошлую песню"),
    "новая вкладка": ("открой вкладку", "новую вкладку", "открой новую вкладку"),
    "закрой вкладку": ("закрой эту вкладку", "закрыть вкладку"),
    "сверни окно": ("сверни", "сверни это", "сверни все"),
    "разверни окно": ("разверни", "на весь экран"),
    "переключи раскладку": ("смени язык", "поменяй раскладку", "поменяй язык", "смени раскладку"),
    "выдели всё": ("выдели все", "выделить все", "выдели весь текст"),
    "энтер": ("нажми энтер", "нажми enter", "ентер", "enter"),
    "скопируй": ("скопировать", "копируй"),
    "вставь": ("вставить", "вставь текст"),
}

APP_ALIASES: dict[str, str] = {
    "дискорд": "discord", "дискорт": "discord", "дис": "discord",
    "телеграм": "telegram", "телеграмм": "telegram", "телега": "telegram", "тг": "telegram",
    "стим": "steam", "стимчик": "steam",
    "хром": "хром", "гугл хром": "хром", "chrome": "хром",
    "браузер": "яндекс браузер", "яндекс браузер": "яндекс браузер",
    "спотифай": "spotify", "вскод": "visual studio code", "вс код": "visual studio code",
    "vs code": "visual studio code", "курсор": "cursor", "обс": "obs", "осу": "osu",
    "ютубчик": "ютуб", "ютюб": "ютуб", "ютьюб": "ютуб", "youtube": "ютуб",
    "вконтакте": "вк", "контакт": "вк", "гитхаб": "github", "твитч": "твич",
}
OBJECT_WORDS = ("приложение", "программу", "программа", "сайт", "игру", "мне", "нам")

CANON_PREFIXES = (
    "открой ", "закрой ", "найди ", "поиск на ", "ищи на ", "загугли ", "зайди на ", "зайди в ",
    "напиши ", "нажми ", "кликни ", "двойной клик", "правый клик", "прочитай ", "создай файл ",
    "допиши в файл ", "запиши в файл ", "переименуй файл ", "скопируй файл ", "перемести файл ",
    "удали файл ", "покажи папку ", "список файлов ", "выполни ", "посчитай ", "вычисли ",
    "сколько будет ", "переключись на ", "активируй ", "громче", "тише",
)
RISKY_FOR_CHAIN = ("напиши в ", "нажми на ", "кликни ", "выбери ")
PRONOUNS = ("там", "туда", "его", "её", "ее", "это", "этот", "эту", "ему", "в нем", "на нем", "в нём", "на нём")

_SPLIT_RE = re.compile(
    r"(\s*,?\s*(?:и\s+|а\s+)?(?:потом|затем|после этого|далее)\s+|\s*,\s*|\s+и\s+)",
    re.IGNORECASE,
)


@dataclass
class Route:
    steps: list[str]
    kind: str
    safe_fallback: bool = False
    notes: list[str] = field(default_factory=list)


def fold(text: str) -> str:
    return text.lower().replace("ё", "е")


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text)]


def stem(word: str) -> str:
    w = fold(word)
    for suf in (
        "иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ией", "ой", "ей",
        "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ов", "ев", "ам", "ям", "ах", "ях",
        "ом", "ем", "ую", "юю", "ть", "ти", "а", "я", "о", "е", "ы", "и", "у", "ю", "ь",
    ):
        if len(w) - len(suf) >= 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _vowel_fold(text: str) -> str:
    return text.translate(str.maketrans("аеёэяыю", "оиииииу"))


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, fold(a), fold(b)).ratio()


def _strip_fillers(text: str) -> str:
    t = f" {text.lower().strip()} "
    for w in ANY_FILLERS:
        t = re.sub(rf"\s{re.escape(w)}(?=[\s,.!?])", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.!?;:")
    changed = True
    while changed and t:
        changed = False
        for f in sorted(LEAD_FILLERS, key=len, reverse=True):
            if t == f:
                return ""
            if t.startswith(f + " ") or t.startswith(f + ","):
                t = t[len(f):].lstrip(" ,")
                changed = True
                break
    return re.sub(r"-ка\b", "", t).strip(" ,.!?;:")


def normalize(text: str) -> str:
    return _strip_fillers(re.sub(r"\s+", " ", (text or "").lower()).strip())


def _canon_verb(word: str) -> Optional[str]:
    w = fold(word)
    for canon, variants in VERBS.items():
        if w in (fold(v) for v in variants):
            return canon
    if len(w) < 5:
        return None
    best, best_score = None, 0.0
    for canon, variants in VERBS.items():
        for v in variants:
            if _vowel_fold(fold(v)[:2]) != _vowel_fold(w[:2]):
                continue
            score = similar(_vowel_fold(w), _vowel_fold(fold(v)))
            if score > best_score:
                best, best_score = canon, score
    if best_score >= 0.84:
        return best
    return None


def _canon_phrase(text: str) -> Optional[str]:
    t = fold(text)
    best, best_score = None, 0.0
    for canon, variants in PHRASES.items():
        for v in (canon, *variants):
            score = similar(t, v)
            if score > best_score:
                best, best_score = canon, score
    if best_score >= 0.88:
        return best
    return None


def canon_target(target: str) -> str:
    t = f" {target.strip()} "
    for w in OBJECT_WORDS:
        t = t.replace(f" {w.strip()} ", " ")
    t = re.sub(r"\s+", " ", t).strip(" ,.!?")
    if t in APP_ALIASES:
        return APP_ALIASES[t]
    if " " not in t:
        for alias, real in APP_ALIASES.items():
            if " " not in alias and len(alias) >= 4 and stem(alias) == stem(t):
                return real
    if len(t) >= 4 and " " not in t:
        best, best_score = None, 0.0
        for alias, real in APP_ALIASES.items():
            score = similar(t, alias)
            if score > best_score:
                best, best_score = real, score
        if best_score >= 0.84 and best:
            return best
    words = t.split()
    if words and words[0] in APP_ALIASES and len(words) <= 3:
        return " ".join([APP_ALIASES[words[0]], *words[1:]])
    return t


def _known_openable(target: str) -> bool:
    from skills.search import SITES

    if target in APP_ALIASES.values() or target in SITES:
        return True
    try:
        from desktop.commands import find_app

        return find_app(target) is not None
    except Exception:
        return False


def _rewrite_verb(cmd: str) -> Optional[str]:
    words = cmd.split(" ", 1)
    head = words[0]
    rest = words[1].strip() if len(words) > 1 else ""

    if fold(head) in SOFT_OPEN and rest:
        target = canon_target(rest)
        if _known_openable(target):
            return f"открой {target}"
        return None

    verb = _canon_verb(head)
    if verb is None:
        return None
    if verb in ("открой", "закрой"):
        if not rest:
            return None
        if verb == "открой" and rest.startswith(("файл ", "папку ")):
            return f"открой {rest}"
        if verb == "закрой" and rest.startswith(("вкладку", "окно")):
            return f"закрой {rest}"
        return f"{verb} {canon_target(rest)}"
    if not rest:
        return None
    return f"{verb} {rest}"


def _is_homework(cmd: str) -> bool:
    try:
        from skills.homework import is_homework_command

        return is_homework_command(cmd)
    except Exception:
        return False


def _route_one(cmd: str, *, in_chain: bool) -> Optional[str]:
    cmd = normalize(cmd)
    if not cmd:
        return None
    if _is_homework(cmd):
        return cmd

    phrase = _canon_phrase(cmd)
    if phrase:
        return phrase

    rewritten = _rewrite_verb(cmd) or cmd
    if in_chain and rewritten.startswith(RISKY_FOR_CHAIN):
        return None
    if rewritten.startswith(CANON_PREFIXES) or rewritten in PHRASES:
        return rewritten

    from control.keyboard import COMMANDS

    if rewritten in COMMANDS:
        return rewritten
    if not in_chain:
        from desktop.screen import needs_screen_agent

        if needs_screen_agent(rewritten):
            return rewritten
    return None


def _split_parts(cmd: str) -> list[str]:
    pieces = _SPLIT_RE.split(cmd)
    parts: list[str] = [pieces[0]]
    i = 1
    while i < len(pieces) - 1:
        sep, chunk = pieces[i], pieces[i + 1]
        head = normalize(chunk).split(" ", 1)[0] if normalize(chunk) else ""
        is_new = bool(head) and (
            _canon_verb(head) is not None
            or fold(head) in SOFT_OPEN
            or _canon_phrase(normalize(chunk)) is not None
            or normalize(chunk).startswith(CANON_PREFIXES)
        )
        bare_app = canon_target(normalize(chunk))
        prev_verb = normalize(parts[-1]).split(" ", 1)[0]
        if not is_new and _canon_verb(prev_verb) in ("открой", "закрой") and len(bare_app.split()) <= 2:
            parts.append(f"{_canon_verb(prev_verb)} {bare_app}")
        elif is_new:
            parts.append(chunk)
        else:
            parts[-1] = parts[-1] + sep + chunk
        i += 2
    return [p.strip() for p in parts if p.strip()]


def _bind_context(steps: list[str]) -> Optional[list[str]]:
    from skills.search import SEARCH_URLS

    out: list[str] = []
    last_site = ""
    for step in steps:
        padded = f" {step} "
        has_pronoun = any(f" {p} " in padded for p in PRONOUNS)
        if step.startswith("найди ") and last_site and (has_pronoun or len(out) > 0):
            query = padded
            for p in PRONOUNS:
                query = query.replace(f" {p} ", " ")
            query = query.strip()[len("найди"):].strip()
            if not query:
                return None
            out.append(f"поиск на {last_site} {query}")
            continue
        if has_pronoun and not step.startswith(("напиши ", "найди файл")):
            return None
        if step.startswith("открой "):
            target = step[len("открой "):].strip()
            last_site = target if target in SEARCH_URLS else ""
        out.append(step)
    return out


def route(command: str) -> Optional[Route]:
    """Маршрут без LLM или None, если нужна думалка."""
    cmd = normalize(command)
    if not cmd:
        return None

    if _is_homework(cmd):
        return Route([cmd], "homework")

    try:
        from core.memory import resolve_stored_command

        if resolve_stored_command(cmd):
            return Route([cmd], "macro")
    except Exception:
        pass

    parts = _split_parts(cmd)
    if len(parts) == 1:
        step = _route_one(parts[0], in_chain=False)
        if step is None:
            return None
        return Route([step], "single", safe_fallback=step.startswith("открой "))

    steps: list[str] = []
    for part in parts:
        step = _route_one(part, in_chain=True)
        if step is None:
            return None
        steps.append(step)
    bound = _bind_context(steps)
    if not bound:
        return None
    return Route(bound, "chain")
