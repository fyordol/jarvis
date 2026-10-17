"""Поиск на сайтах и открытие URL."""
import os
import re
import subprocess
import urllib.parse
import webbrowser
from typing import Optional

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
YANDEX_PATHS = [
    r"C:\Program Files\Yandex\YandexBrowser\Application\browser.exe",
    r"C:\Program Files (x86)\Yandex\YandexBrowser\Application\browser.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Yandex\YandexBrowser\Application\browser.exe"),
]


def chrome_exe() -> Optional[str]:
    for path in CHROME_PATHS:
        if os.path.isfile(path):
            return path
    return None


def yandex_exe() -> Optional[str]:
    for path in YANDEX_PATHS:
        if os.path.isfile(path):
            return path
    return None


def browser_exe() -> Optional[str]:
    from core.config import get

    raw = str(get("search.browser", "yandex")).strip().lower()
    if raw in ("chrome", "хром", "google"):
        return chrome_exe()
    return yandex_exe() or chrome_exe()


def browser_name() -> str:
    from core.config import get

    raw = str(get("search.browser", "yandex")).strip().lower()
    if raw in ("chrome", "хром", "google"):
        return "Chrome"
    return "Яндекс Браузер"


def open_app_window(url: str) -> bool:
    """Окно Jarvis: Chrome в режиме приложения со своим профилем."""
    chrome = chrome_exe()
    if not chrome:
        return False
    from core.paths import REPO_ROOT

    profile = str(REPO_ROOT / ".chrome-app")
    os.makedirs(profile, exist_ok=True)
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 1
    subprocess.Popen(
        [chrome, f"--app={url}", f"--user-data-dir={profile}", "--window-size=440,780",
         "--no-first-run", "--no-default-browser-check"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=si,
    )
    _raise_window_later("JARVIS")
    return True


def _raise_window_later(title: str, wait_sec: float = 6.0) -> None:
    import threading
    import time

    def run() -> None:
        from control.media import _focus_win32

        end = time.time() + wait_sec
        while time.time() < end:
            time.sleep(0.4)
            if _focus_win32(title, exact=True):
                return

    threading.Thread(target=run, daemon=False, name="jarvis-raise").start()


def open_url(url: str, new_tab: bool = True) -> None:
    exe = browser_exe()
    if exe:
        args = [exe]
        if new_tab:
            args.append("--new-tab")
        args.append(url)
        subprocess.Popen(args)
        return
    webbrowser.open(url)

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
    "гитхаб": "https://github.com",
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
    "гитхаб": "https://github.com/search?q={q}",
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

_FILLER_WORDS = (
    "пожалуйста", "видео", "ролик", "ролики", "канал", "канале",
    "музыку", "песню", "фильм", "фильмы", "клип", "клипы",
    "про", "найди", "найти", "поищи", "искать",
)
_FILLER_PREFIXES = (
    "видео про ", "ролик про ", "клип про ", "фильм про ",
    "видео о ", "что такое ", "что-нибудь про ", "что нибудь про ",
)

_SEARCH_REWRITE_PROMPT = """\
Из команды пользователя извлеки поисковый запрос для поля поиска.

Правила:
- Верни только запрос, без кавычек и пояснений.
- Убери «найди», «поищи», «видео», «ролик», «на ютубе», «в гугле», «про», «пожалуйста».
- Оставь то, что человек вбил бы сам: тема, имя, точное название.
- «как …» / «рецепт …» оставляй, если это суть запроса.

Примеры:
найди видео про космос на ютубе → космос
поищи на ютубе клип the weeknd blinding lights → the weeknd blinding lights
найди в гугле как почистить SSD → как почистить SSD
найди рецепт борща на яндексе → рецепт борща
"""


def _clean_query(text: str) -> str:
    text = (text or "").strip().lower()
    for prefix in _FILLER_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
    for word in _FILLER_WORDS:
        text = re.sub(rf"\b{re.escape(word)}\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.")
    text = re.sub(r"^(про|об|ото)\s+", "", text)
    return text.strip(" ,.")


def _sanitize_llm_query(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0].strip().strip(" «»\"'`")
    if len(text) > 80:
        text = text[:80].rsplit(" ", 1)[0]
    lowered = text.lower()
    if not lowered or lowered.startswith(("найди", "найти", "поищи", "искать", "поиск")):
        return ""
    return text


def rewrite_search_query(command: str, *, platform: str, fallback: str) -> str:
    fallback = _clean_query(fallback) or _clean_query(command) or fallback.strip()
    from core.config import get

    if fallback and len(fallback.split()) <= int(get("search.llm_rewrite_over_words", 4)):
        return fallback
    try:
        from core.llm import chat_completion

        answer = chat_completion(
            [
                {"role": "system", "content": _SEARCH_REWRITE_PROMPT},
                {
                    "role": "user",
                    "content": f"Платформа: {platform}\nКоманда: {command.strip()}",
                },
            ],
            role="search",
            temperature=0,
            timeout=8,
        )
        query = _clean_query(_sanitize_llm_query(answer))
        if query:
            print(f"Поисковый запрос [{platform}]: «{query}»")
            return query
    except Exception as exc:
        print("Переписывание поискового запроса:", exc)
    return fallback


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


def default_engine() -> str:
    from core.config import get

    raw = str(get("search.engine", "yandex")).strip().lower()
    if raw in ("google", "гугл"):
        return "гугл"
    return "яндекс"


def open_platform_search(platform: str, query: str) -> str:
    template = SEARCH_URLS.get(platform)
    if not template:
        return f"Не знаю, как искать на {platform}"

    query = _clean_query(query) or query.strip()
    url = template.format(q=urllib.parse.quote(query))
    open_url(url)
    return f"Ищу «{query}» на {platform}"


def try_entry_command(command: str) -> Optional[str]:
    command = command.strip().lower()
    if command.startswith("загугли "):
        raw = command[len("загугли "):]
        if not raw.strip():
            return "Что загуглить?"
        query = rewrite_search_query(command, platform="гугл", fallback=raw)
        if not query:
            return "Что загуглить?"
        return open_platform_search("гугл", query)

    for prefix in ("зайди на ", "зайди в ", "зайти на ", "зайти в "):
        if command.startswith(prefix):
            target = command[len(prefix):].strip()
            if not target:
                return "Куда зайти?"
            for name, url in SITES.items():
                if name in target:
                    open_url(url)
                    return f"Открываю {name} в {browser_name()}"
            if target.startswith("http"):
                open_url(target)
                return "Открываю ссылку"
            if "." in target:
                open_url("https://" + target)
                return "Открываю сайт"
            return None
    return None


def try_platform_search(command: str) -> Optional[str]:
    parsed = parse_platform_search(command)
    if not parsed:
        return None
    platform, query = parsed
    query = rewrite_search_query(command, platform=platform, fallback=query)
    return open_platform_search(platform, query)
