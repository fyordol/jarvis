"""Намерения UI: ищем действие, а не точный текст на кнопке."""
import re
from typing import Optional

_FILLER = (
    "кнопку", "кнопка", "кнопке", "иконку", "иконка", "иконке",
    "ссылку", "ссылка", "элемент", "элемента",
    "пожалуйста", "нужно", "надо", "давай", "просто",
    "нажми", "нажать", "кликни", "кликнуть", "выбери", "выбрать",
)

INTENTS: list[dict] = [
    {
        "name": "download",
        "triggers": (
            "скач", "download", "загруз", "сгруз",
            "save as", "сохранить как", "сохрани файл", "сохранить файл",
        ),
        "queries": (
            "download", "downloads", "скачать", "скачивание", "загрузить",
            "загрузка", "save", "save as", "сохранить", "получить файл",
            "export", "экспорт",
        ),
        "hint": (
            "Намерение: СКАЧАТЬ. Кликай элемент, который начинает загрузку: "
            "иконка стрелки вниз в лоток, облако со стрелкой, дискетка, "
            "кнопка Download/Save/Скачать/Сохранить/Export. "
            "Текст НЕ обязателен — голая иконка загрузки тоже цель. "
            "Не путай с папкой «Загрузки» и историей загрузок, если на экране есть кнопка скачивания контента."
        ),
    },
    {
        "name": "save",
        "triggers": ("сохран", "save"),
        "queries": ("save", "сохранить", "сохранить как", "save as", "floppy", "диск"),
        "hint": (
            "Намерение: СОХРАНИТЬ. Иконка дискеты, Save, Сохранить — текст не обязателен."
        ),
    },
    {
        "name": "play",
        "triggers": ("play", "плей", "воспроизв", "смотри", "запусти видео", "играй"),
        "queries": ("play", "воспроизвести", "смотреть", "пуск"),
        "hint": (
            "Намерение: PLAY. Большой треугольник / круглая кнопка воспроизведения, не соседний текст."
        ),
    },
    {
        "name": "pause",
        "triggers": ("pause", "пауза видео", "останови видео"),
        "queries": ("pause", "пауза", "остановить"),
        "hint": "Намерение: ПАУЗА. Две вертикальные полоски на плеере.",
    },
    {
        "name": "close",
        "triggers": ("крестик", "закрой окно", "close tab", "закрыть вкладку"),
        "queries": ("close", "закрыть", "dismiss", "×", "x"),
        "hint": "Намерение: ЗАКРЫТЬ. Крестик окна/модалки/вкладки.",
    },
    {
        "name": "search",
        "triggers": ("лупа", "найди на странице", "поиск на странице"),
        "queries": ("search", "найти", "поиск"),
        "hint": "Намерение: ПОИСК. Лупа или поле Search.",
    },
    {
        "name": "send",
        "triggers": ("отправ", "send", "самолет", "самолёт"),
        "queries": ("send", "отправить", "submit", "post"),
        "hint": (
            "Намерение: ОТПРАВИТЬ. Бумажный самолётик, стрелка вправо, Send/Отправить — текст не обязателен."
        ),
    },
    {
        "name": "share",
        "triggers": ("поделись", "share", "шарен", "перешли"),
        "queries": ("share", "поделиться", "copy link"),
        "hint": "Намерение: ПОДЕЛИТЬСЯ. Иконка share (три точки / стрелка из коробки).",
    },
    {
        "name": "like",
        "triggers": ("лайк", "like", "сердеч", "класс"),
        "queries": ("like", "нравится", "heart"),
        "hint": "Намерение: ЛАЙК. Сердце или палец вверх, не счётчик рядом.",
    },
    {
        "name": "subscribe",
        "triggers": ("подписк", "subscribe", "фоллов", "follow"),
        "queries": ("subscribe", "подписаться", "follow", "following"),
        "hint": "Намерение: ПОДПИСАТЬСЯ. Кнопка Subscribe/Подписаться/Follow.",
    },
    {
        "name": "install",
        "triggers": ("установ", "install", "постарь", "поставить приложение"),
        "queries": ("install", "установить", "get", "добавить"),
        "hint": "Намерение: УСТАНОВИТЬ. Install/Получить/Установить, иногда только иконка магазина.",
    },
    {
        "name": "settings",
        "triggers": ("настройк", "шестерен", "шестерён", "settings", "gear"),
        "queries": ("settings", "настройки", "options", "preferences"),
        "hint": "Намерение: НАСТРОЙКИ. Шестерёнка, даже без текста.",
    },
    {
        "name": "login",
        "triggers": ("войти", "логин", "login", "sign in"),
        "queries": ("log in", "login", "sign in", "войти"),
        "hint": "Намерение: ВОЙТИ. Кнопка Log in / Sign in / Войти.",
    },
    {
        "name": "confirm",
        "triggers": ("подтверд", "ок ", "agree", "принять", "continue", "далее"),
        "queries": ("ok", "okay", "continue", "далее", "принять", "согласен", "confirm", "yes"),
        "hint": "Намерение: ПОДТВЕРДИТЬ. OK / Далее / Continue / Принять.",
    },
]


def strip_filler(text: str) -> str:
    t = f" {text.lower()} "
    for w in _FILLER:
        t = t.replace(f" {w} ", " ")
    return re.sub(r"\s+", " ", t).strip(" .,:;!?")


def detect_intent(target: str) -> Optional[dict]:
    t = target.lower()
    best = None
    best_len = 0
    for intent in INTENTS:
        for trig in intent["triggers"]:
            if trig in t and len(trig) > best_len:
                best = intent
                best_len = len(trig)
    return best


def expand_queries(target: str) -> list[str]:
    raw = strip_filler(target)
    seen: set[str] = set()
    out: list[str] = []

    def add(q: str) -> None:
        q = q.strip().lower()
        if len(q) < 2 or q in seen:
            return
        seen.add(q)
        out.append(q)

    add(raw)
    add(target)
    stop = {"это", "то", "вот", "там", "тут", "мне", "его", "её", "их", "the", "a"}
    for part in re.findall(r"[a-zа-яё0-9]{2,}", raw):
        if part not in stop:
            add(part)

    intent = detect_intent(target)
    if intent:
        for q in intent["queries"]:
            add(q)

    return out


def vision_intent_hint(target: str) -> str:
    intent = detect_intent(target)
    if not intent:
        return (
            "Ищи по НАЗНАЧЕНИЮ, не по точному тексту. "
            "Кликабельна иконка без подписи, если она явно делает нужное действие."
        )
    return intent["hint"]


def uia_skip_reason(target: str) -> Optional[str]:
    t = target.lower()
    visual = (
        "видео", "превью", "карточка", "карточки", "лент", "результат",
        "перв", "втор", "треть", "последн", "снизу", "сверху",
    )
    if any(w in t for w in visual):
        return "layout"
    return None
