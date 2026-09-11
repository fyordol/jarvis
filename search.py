"""Поиск на сайтах и открытие URL."""
import re
import urllib.parse
import webbrowser
from typing import Optional

# Сайты для «открой ютуб» и т.п.
SITES = {
    "ютуб": "https://youtube.com",
    "youtube": "https://youtube.com",
    "яндекс": "https://yandex.ru",
    "гугл": "https://google.com",
    "вк": "https://vk.com",
    "вконтакте": "https://vk.com",
    "дискорд": "https://discord.com",
    "discord": "https://discord.com",
    "твич": "https://twitch.tv",
    "чатгпт": "https://chat.openai.com",
    "github": "https://github.com",
    "стим": "https://store.steampowered.com",
}

SEARCH_URLS = {
    "ютуб": "https://www.youtube.com/results?search_query={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "яндекс": "https://yandex.ru/search/?text={q}",
    "гугл": "https://www.google.com/search?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "вк": "https://vk.com/search?c[q]={q}&c[section]=auto",
    "вконтакте": "https://vk.com/search?c[q]={q}&c[section]=auto",
    "твич": "https://www.twitch.tv/search?term={q}",
    "twitch": "https://www.twitch.tv/search?term={q}",
    "github": "https://github.com/search?q={q}",
    "стим": "https://store.steampowered.com/search/?term={q}",
    "дискорд": "https://discord.com/channels/@me",
}

# Все варианты «на ютубе», «в youtube» → ключ из SEARCH_URLS
PLATFORM_MARKERS: list[tuple[str, str]] = []
for key in SEARCH_URLS:
    PLATFORM_MARKERS.append((f"на {key}", key))
    PLATFORM_MARKERS.append((f"в {key}", key))
    PLATFORM_MARKERS.append((f"на {key}е", key))
    PLATFORM_MARKERS.append((f"в {key}е", key))
# Частые разговорные формы
PLATFORM_MARKERS.extend([
    ("на ютубе", "ютуб"),
    ("в ютубе", "ютуб"),
    ("на youtube", "youtube"),
    ("в youtube", "youtube"),
    ("в гугле", "гугл"),
    ("в google", "google"),
    ("на яндексе", "яндекс"),
    ("в яндексе", "яндекс"),
])
# Длинные маркеры первыми
PLATFORM_MARKERS.sort(key=lambda x: len(x[0]), reverse=True)

SEARCH_VERBS = ("найди", "найти", "поищи", "искать", "поиск")


def _clean_query(text: str) -> str:
    text = text.strip()
    for word in ("пожалуйста", "видео", "ролик", "канал", "музыку", "песню", "фильм"):
        text = re.sub(rf"\b{word}\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.")
    return text


def parse_platform_search(command: str) -> Optional[tuple[str, str]]:
    """Возвращает (платформа, запрос) или None."""
    command = command.lower().strip()

    # «поиск на ютуб котики» / «ищи на яндекс рецепт»
    for prefix in ("поиск на ", "ищи на ", "искать на "):
        if command.startswith(prefix):
            rest = command[len(prefix):]
            for site_key in sorted(SEARCH_URLS, key=len, reverse=True):
                if rest == site_key or rest.startswith(site_key + " "):
                    query = rest[len(site_key):].strip()
                    if query:
                        return site_key, _clean_query(query)
                    return None

    if not any(v in command for v in SEARCH_VERBS):
        return None

    for marker, site_key in PLATFORM_MARKERS:
        if marker not in command:
            continue

        before, after = command.split(marker, 1)
        before = before.strip()
        after = after.strip()

        for verb in SEARCH_VERBS:
            before = before.replace(verb, "", 1).strip()

        # «найди котиков на ютубе» — запрос до маркера
        query = _clean_query(before) if before else _clean_query(after)
        if query:
            return site_key, query

    return None


def open_platform_search(platform: str, query: str) -> str:
    template = SEARCH_URLS.get(platform)
    if not template:
        return f"Не знаю, как искать на {platform}"

    url = template.format(q=urllib.parse.quote(query))
    webbrowser.open(url)
    return f"Ищу «{query}» на {platform}"


def try_platform_search(command: str) -> Optional[str]:
    parsed = parse_platform_search(command)
    if not parsed:
        return None
    platform, query = parsed
    return open_platform_search(platform, query)
