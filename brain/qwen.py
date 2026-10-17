"""Мозг Джарвиса: агентный цикл «подумал → сделал → посмотрел» для любой просьбы."""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import requests

from core.config import get
from core.llm import chat_completion
from core.paths import BUS_DIR

SCRIPTS_DIR = BUS_DIR / "scripts"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}

SYSTEM_PROMPT = """Ты Джарвис — ИИ-ассистент как у Тони Старка. Управляешь Windows-компьютером пользователя и выполняешь ЛЮБУЮ просьбу: действия на ПК, вопросы, расчёты, поиск информации, файлы, учёба, тексты.

Каждый ход отвечай ТОЛЬКО JSON без текста вокруг:
{"thought":"очень кратко план","actions":[...],"done":true}

Инструменты (элементы actions):
- {"tool":"cmd","text":"открой discord"} — быстрая команда Джарвиса. Формы: "открой X" (программа/сайт), "закрой X", "поиск на ютуб X", "загугли X", "зайди на site.ru", "открой файл X", "открой папку документы", "создай файл имя.txt с текстом ...", "прочитай файл X", "найди файл X", "громче", "тише", "выключи звук", "пауза", "следующий трек", "новая вкладка", "закрой вкладку", "переключись на [окно]", "сверни окно", "посчитай [выражение]", "сделай дз [предмет] [на когда]", "открой дневник".
- {"tool":"type","text":"..."} — напечатать текст в активное окно.
- {"tool":"keys","keys":["ctrl","s"]} — клавиша или комбинация: enter, esc, tab, win, alt+tab, ctrl+l, f5...
- {"tool":"screen","task":"нажми на поле сообщения"} — одно действие по скриншоту: клик по кнопке/иконке/видео/полю.
- {"tool":"look","question":"что открыто на экране?"} — посмотреть на экран и получить ответ.
- {"tool":"web","query":"курс доллара сегодня"} — поиск в интернете, вернёт текст результатов.
- {"tool":"python","code":"print(...)"} — Python 3 на этом ПК (psutil, PIL, requests есть). Для файлов, папок, расчётов, конвертаций, системной информации. Результат — то, что напечатано print. Пути пиши ТОЛЬКО прямыми слешами: "C:/Users/PC/Downloads".
- {"tool":"wait","seconds":1}
- {"tool":"say","text":"..."} — ответ пользователю: коротко, по-русски, в стиле Джарвиса. Никогда не говори «сэр».

Правила:
1. Вопрос, болтовня, знания, совет, текст, перевод → сразу say и done=true.
2. Свежие данные (погода, курс, новости, цены, факты) → web с done=false, на следующем ходу say с ответом. Один web обычно хватает — не повторяй тот же запрос. Общий поиск — Яндекс: cmd «найди …». «загугли» только если пользователь просил Google.
3. Действия на ПК → cmd/type/keys/screen по порядку. Перед type в другом приложении сначала открой его и дай wait 1-2.
4. Если результат нужно узнать или проверить — done=false, и на следующем ходу ты получишь результаты.
5. say пиши только про то, что подтверждено результатами. Не ставь say в один ход с screen/look/web/python — сначала дождись результатов.
6. Удалить/переименовать/переместить файлы — только через python (пользователь подтвердит). Никаких del, rm, cd в cmd.
7. Если инструмент вернул ошибку — исправь причину, а не повторяй то же самое. После 2 неудач — честно скажи say.
8. Браузер только Яндекс Браузер. Не больше 6 действий за ход.
9. Не хватает данных (кому написать, какой файл, что именно) — спроси через say и done=true. Сообщения людям отправляй (enter) только когда через look/screen убедился, что открыт нужный чат.

Примеры:
«включи lofi на ютубе» → {"thought":"поиск и первое видео","actions":[{"tool":"cmd","text":"поиск на ютуб lofi"},{"tool":"wait","seconds":2.5},{"tool":"screen","task":"нажми на первое видео в результатах"}],"done":false}
«напиши маме в телеграме что буду в 8» → {"thought":"открыть чат","actions":[{"tool":"cmd","text":"открой telegram"},{"tool":"wait","seconds":2},{"tool":"screen","task":"нажми на поиск чатов"},{"tool":"type","text":"мама"},{"tool":"wait","seconds":1},{"tool":"screen","task":"нажми на чат мама в результатах"}],"done":false}
«сколько файлов в загрузках» → {"thought":"посчитать","actions":[{"tool":"python","code":"import os\\nprint(len(os.listdir('C:/Users/PC/Downloads')))"}],"done":false}
«кто такой Илон Маск» → {"thought":"знаю сам","actions":[{"tool":"say","text":"Илон Маск — предприниматель, глава Tesla и SpaceX."}],"done":true}"""

DANGER_PATTERNS = (
    r"\brmtree\b", r"\bos\.remove\b", r"\bos\.unlink\b", r"\.unlink\(", r"\brmdir\b", r"\bos\.system\b",
    r"\bsubprocess\b", r"\bwinreg\b", r"\bshutdown\b", r"\bctypes\b", r"send2trash",
    r"requests\.(?:post|put|delete|patch)", r"\bsmtplib\b", r"\bos\.replace\b", r"shutil\.move",
    r"\.rename\(",
)
CONFIRM_YES = ("да выполни", "выполняй", "да, выполни", "подтверждаю", "да давай", "да", "делай")
CONFIRM_NO = ("нет", "отмена", "не надо", "стоп")

_pending_code: Optional[str] = None
_messaging = False
MESSENGERS = ("дискорд", "discord", "телеграм", "telegram", "телег", "вк", "вконтакте", "whatsapp", "ватсап", "чат", "личк")
MESSAGE_VERBS = ("напиши", "отправь", "сообщ", "ответь", "скажи ему", "скажи ей", "передай")


def _parse_plan(answer: str) -> Optional[dict]:
    text = re.sub(r"<think>.*?</think>", "", answer or "", flags=re.S).strip()
    if "```" in text:
        inner = text.split("```", 2)[1]
        text = inner[4:] if inner.startswith("json") else inner
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    raw = text[start:end + 1]
    data = None
    for candidate in (raw, re.sub(r'([)\]\w])\}\]', r'\1"}]', raw), raw + "]}", raw + '"}]}'):
        try:
            data = json.loads(candidate, strict=False)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(data, dict):
        return None
    actions = data.get("actions")
    if isinstance(actions, dict):
        actions = [actions]
    if not isinstance(actions, list):
        actions = []
    data["actions"] = [a for a in actions if isinstance(a, dict)]
    return data


def _weather(query: str) -> str:
    m = re.search(r"погод\w*\s+(?:в|во)\s+([\w\- ]+)", query.lower())
    city = (m.group(1).strip() if m else str(get("brain.city", "Мытищи"))).split(" на ")[0]
    try:
        r = requests.get(f"https://wttr.in/{urllib.parse.quote(city)}", params={"format": "4", "lang": "ru", "m": ""}, timeout=6)
        now = r.text.strip()
        r2 = requests.get(f"https://wttr.in/{urllib.parse.quote(city)}", params={"format": "j1", "lang": "ru"}, timeout=6)
        days = r2.json().get("weather", [])[:3]
        lines = [now]
        for d in days:
            lines.append(f"{d.get('date')}: {d.get('mintempC')}…{d.get('maxtempC')}°C")
        return "\n".join(lines)
    except Exception as exc:
        return f"погода недоступна: {exc}"


CURRENCIES = {
    "доллар": "USD", "usd": "USD", "евро": "EUR", "eur": "EUR", "юан": "CNY", "фунт": "GBP",
    "иен": "JPY", "тенге": "KZT", "лир": "TRY", "франк": "CHF", "гривн": "UAH", "белорус": "BYN",
}
FRESH_MARKERS = (
    "новост", "сегодня", "сейчас", "цена", "стоит", "сколько стоит", "счет", "счёт", "матч", "расписани",
    "вышел", "выйдет", "релиз", "обновлени", "курс", "биткоин", "акци", "рейтинг", "топ", "2025", "2026",
)


def _currency(query: str) -> Optional[str]:
    low = query.lower()
    codes = [code for key, code in CURRENCIES.items() if key in low]
    if not codes and "биткоин" not in low:
        return None
    try:
        data = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=6).json()
    except Exception as exc:
        return f"курс недоступен: {exc}"
    lines = [f"Курс ЦБ РФ на {str(data.get('Date', ''))[:10]}:"]
    for code in dict.fromkeys(codes or ["USD", "EUR"]):
        v = data.get("Valute", {}).get(code)
        if v:
            lines.append(f"{v['Nominal']} {code} ({v['Name']}) = {v['Value']:.2f} руб. (вчера {v['Previous']:.2f})")
    return "\n".join(lines)


def _wikipedia(query: str) -> str:
    api = "https://ru.wikipedia.org/w/api.php"
    try:
        found = requests.get(api, params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 2}, headers=UA, timeout=6).json()
        titles = [x["title"] for x in found.get("query", {}).get("search", [])]
        if not titles:
            return ""
        pages = requests.get(api, params={
            "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
            "titles": "|".join(titles), "format": "json",
        }, headers=UA, timeout=6).json()
    except Exception:
        return ""
    out = []
    for page in pages.get("query", {}).get("pages", {}).values():
        text = (page.get("extract") or "").strip()
        if text:
            out.append(f"Википедия «{page.get('title')}»: {text[:1500]}")
    return "\n\n".join(out)


def _plain(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head|nav|footer).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def _headless_dom(url: str) -> str:
    import tempfile

    from skills.search import chrome_exe

    exe = chrome_exe()
    if not exe:
        return ""
    profile = Path(tempfile.gettempdir()) / "jarvis-headless"
    r = subprocess.run(
        [exe, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-agent={UA['User-Agent']}",
         f"--user-data-dir={profile}", "--lang=ru", "--dump-dom", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=int(get("brain.headless_timeout_sec", 20)),
    )
    return r.stdout


def _ddg_search(query: str) -> str:
    url = "https://html.duckduckgo.com/html/?kl=ru-ru&q=" + urllib.parse.quote(query)
    try:
        dom = _headless_dom(url)
    except Exception as exc:
        print("headless:", exc)
        return ""
    blocks = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', dom, re.S
    )
    results = []
    for href, title, snippet in blocks[:6]:
        if "uddg=" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [href])[0]
        results.append((href, _plain(title), _plain(snippet)))
    if not results:
        return ""
    out = [f"- {t}: {s}" for _, t, s in results]
    for href, _, _ in results[:2]:
        try:
            page = requests.get(href, headers=UA, timeout=5)
            page.encoding = page.apparent_encoding or page.encoding
            text = _plain(page.text)
            if len(text) > 300:
                out.append(f"\nСтраница {href}:\n{text[:1500]}")
                break
        except Exception:
            continue
    return "\n".join(out)


def _yandex_search(query: str) -> str:
    url = "https://yandex.ru/search/?text=" + urllib.parse.quote(query)
    try:
        dom = _headless_dom(url)
    except Exception as exc:
        print("яндекс:", exc)
        return ""
    if not dom or "SmartCaptcha" in dom or "captcha" in dom[:4000].lower():
        return ""
    blocks = re.findall(
        r'OrganicTitle-Link[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?OrganicText[^>]*>(.*?)</div>',
        dom, re.S,
    )
    if not blocks:
        blocks = re.findall(r'class="OrganicTitle[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', dom, re.S)
        blocks = [(href, title, "") for href, title in blocks]
    results = []
    for href, title, snippet in blocks[:6]:
        if href.startswith("/") or "yabs.yandex" in href:
            continue
        results.append((href, _plain(title), _plain(snippet)))
    if not results:
        return ""
    return "\n".join(f"- {title}: {snippet}".rstrip(": ") for _href, title, snippet in results)


def _browser_search(query: str) -> str:
    from skills.search import default_engine, open_platform_search

    engine = default_engine()
    open_platform_search(engine, query)
    time.sleep(float(get("brain.browser_wait_sec", 3)))
    label = "Яндекса" if engine == "яндекс" else "Google"
    return f"Из {label} (с экрана): " + look(
        f"Это результаты поиска {label} по запросу «{query}». Выпиши суть ответа: факты, числа, даты, заголовки. До 8 строк."
    )


def web_search(query: str) -> str:
    low = query.lower()
    if "погод" in low:
        return _weather(query)
    cur = _currency(query) if "курс" in low or any(k in low for k in CURRENCIES) else None
    if cur:
        return cur
    limit = int(get("brain.web_chars", 3500))
    fresh = any(m in low for m in FRESH_MARKERS)
    if not fresh:
        wiki = _wikipedia(query)
        if wiki:
            return wiki[:limit]
    found = _yandex_search(query) or _ddg_search(query)
    if found:
        return found[:limit]
    if get("brain.browser_search", True):
        return _browser_search(query)
    return "ничего не нашёл"


def look(question: str) -> str:
    from desktop.vision import screenshot_for_vision

    b64, *_ = screenshot_for_vision(force=True)
    return chat_completion(
        [{
            "role": "user",
            "content": [
                {"type": "text", "text": f"Посмотри на скриншот экрана и ответь кратко по-русски: {question}"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        role="vision",
        temperature=0,
        timeout=int(get("vision.timeout_sec", 45)),
        max_tokens=300,
    ).strip()


def is_dangerous(code: str) -> bool:
    return any(re.search(p, code) for p in DANGER_PATTERNS)


def run_python(code: str) -> str:
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCRIPTS_DIR / f"task_{int(time.time() * 1000)}.py"
    path.write_text(code, encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run(
            [sys.executable, str(path)], cwd=str(Path.home()), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=int(get("brain.python_timeout_sec", 60)), env=env,
        )
    except subprocess.TimeoutExpired:
        return "таймаут скрипта"
    out = r.stdout.strip()
    if r.returncode:
        err = "\n".join(r.stderr.strip().splitlines()[-4:])
        out = f"{out}\nОШИБКА: {err}".strip()
    return (out or "(ничего не напечатано)")[:2500]


def try_confirm_code(command: str) -> Optional[str]:
    global _pending_code
    if _pending_code is None:
        return None
    c = command.strip().lower().strip(".!")
    if c in CONFIRM_YES:
        code, _pending_code = _pending_code, None
        out = run_python(code)
        print("Скрипт:", out)
        return f"Выполнил. {out[:300]}"
    if c in CONFIRM_NO:
        _pending_code = None
        return "Отменил"
    return None


class _Stop(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _do(act: dict, execute_step: Callable[[str], str]) -> tuple[str, str]:
    """Выполняет одно действие. Возвращает (наблюдение, сказанное)."""
    global _pending_code
    from control.keyboard import hotkey, write

    tool = str(act.get("tool") or act.get("action") or "").lower()
    if tool == "say":
        return "", str(act.get("text") or "").strip()
    if tool == "cmd":
        text = str(act.get("text") or "").strip().lower()
        if not text:
            return "пустая команда", ""
        if re.search(r"&&|\|\||^(?:cd|del|rm|rmdir|erase|format)\b", text):
            return "ошибка: shell-команды в cmd запрещены, используй python", ""
        return execute_step(text) or "команда не распознана", ""
    if tool == "type":
        text = str(act.get("text") or "")
        write(text)
        return f"напечатал {len(text)} символов", ""
    if tool == "keys":
        keys = act.get("keys") or []
        if isinstance(keys, str):
            keys = re.split(r"[+\s]+", keys)
        keys = [str(k).strip().lower() for k in keys if str(k).strip()]
        if keys in (["enter"], ["return"]) and _messaging and get("brain.confirm_messages", True):
            import desktop.screen as screen_agent

            screen_agent._pending_send = True
            raise _Stop("Сообщение набрано. Проверь чат и скажи «отправить» или «нет»")
        if keys:
            hotkey(*keys)
        return f"нажал {'+'.join(keys)}", ""
    if tool == "screen":
        from desktop.screen import run_screen_agent

        return run_screen_agent(str(act.get("task") or "")), ""
    if tool == "look":
        return look(str(act.get("question") or "что на экране?")), ""
    if tool == "web":
        return web_search(str(act.get("query") or "")), ""
    if tool == "wait":
        time.sleep(min(float(act.get("seconds") or 1), 5))
        return "подождал", ""
    if tool == "python":
        code = str(act.get("code") or "")
        if not code.strip():
            return "пустой код", ""
        if is_dangerous(code) and get("brain.confirm_risky_code", True):
            _pending_code = code
            print("Скрипт ждёт подтверждения:\n" + code)
            raise _Stop("Скрипт меняет или удаляет файлы/систему. Выполнить? Скажи «да, выполни» или «нет»")
        return run_python(code), ""
    return f"нет инструмента {tool}", ""


def _context_block() -> str:
    from core.memory import format_session_context

    now = datetime.now()
    days = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
    parts = [
        f"Сейчас: {now.strftime('%d.%m.%Y %H:%M')}, {days[now.weekday()]}.",
        f"Город пользователя: {get('brain.city', 'Мытищи')}. Папки: {(Path.home() / 'Desktop').as_posix()}, "
        f"{(Path.home() / 'Documents').as_posix()}, {(Path.home() / 'Downloads').as_posix()}.",
    ]
    session = format_session_context(6)
    if session:
        parts.append(session)
    return "\n".join(parts)


def think(command: str, *, execute_step: Callable[[str], str]) -> tuple[str, list[str]]:
    """Решает задачу циклом. Возвращает (ответ, список cmd-шагов для кэша)."""
    global _messaging
    low = command.lower()
    _messaging = any(m in low for m in MESSENGERS) and any(v in low for v in MESSAGE_VERBS)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{_context_block()}\n\nЗадача: {command}"},
    ]
    said: list[str] = []
    observations: list[str] = []
    cmd_steps: list[str] = []
    only_cmds = True
    bad_json = 0
    max_rounds = int(get("brain.max_rounds", 6))

    for round_no in range(max_rounds):
        started = time.perf_counter()
        answer = chat_completion(messages, role="brain", temperature=0.2, timeout=int(get("brain.timeout_sec", 90)))
        print(f"Мозг, ход {round_no + 1} ({time.perf_counter() - started:.1f} с): {answer}")
        plan = _parse_plan(answer)
        if plan is None:
            clean = re.sub(r"<think>.*?</think>", "", answer, flags=re.S).strip()
            if round_no == 0 and clean and "{" not in clean:
                return clean, []
            bad_json += 1
            if bad_json > 1:
                break
            messages.append({"role": "assistant", "content": answer})
            messages.append({"role": "user", "content": "Это был невалидный JSON. Повтори ход строго валидным JSON."})
            continue

        actions = plan["actions"][:6]
        probes = {str(a.get("tool") or "") for a in actions} & PROBE_TOOLS
        held_say = probes and any(str(a.get("tool")) == "say" for a in actions)
        if held_say:
            actions = [a for a in actions if str(a.get("tool")) != "say"]
            plan["done"] = False

        if round_no == 0 and plan.get("done") is False:
            _narrate_start(actions)

        round_obs: list[str] = []
        try:
            for act in actions:
                obs, spoken = _do(act, execute_step)
                tool = str(act.get("tool") or "")
                if tool == "cmd":
                    if _failed(obs):
                        only_cmds = False
                    else:
                        cmd_steps.append(str(act.get("text") or "").strip().lower())
                elif tool not in ("say", "wait"):
                    only_cmds = False
                if spoken:
                    said.append(spoken)
                if obs:
                    round_obs.append(f"{tool}: {obs}")
        except _Stop as stop:
            return stop.message, []

        observations.extend(round_obs)
        if plan.get("done", True) is not False:
            break
        messages.append({"role": "assistant", "content": answer})
        feedback = "\n".join(round_obs) or "действия выполнены"
        hint = " Проверь результаты — подтвердилось ли то, что ты собирался сказать." if held_say else ""
        messages.append({"role": "user", "content": f"Результаты:\n{feedback[:3500]}\n\nПродолжай.{hint} Ответ — JSON."})
        if len(messages) > 8:
            messages = messages[:2] + messages[-6:]

    if said:
        reply = " ".join(said)
    elif observations:
        reply = _summarize(command, observations)
    else:
        reply = "Не смог довести задачу до конца"
    return reply, cmd_steps if only_cmds and cmd_steps else []


PROBE_TOOLS = {"screen", "look", "web", "python"}
NARRATION = (
    ("web", "Ищу в сети"),
    ("python", "Секунду"),
    ("look", "Смотрю на экран"),
    ("screen", "Выполняю"),
    ("cmd", "Выполняю"),
)


def _narrate_start(actions: list[dict]) -> None:
    tools = {str(a.get("tool") or "") for a in actions}
    for tool, phrase in NARRATION:
        if tool in tools:
            try:
                from voice.tts import narrate

                narrate(phrase)
            except Exception:
                pass
            return


def _failed(obs: str) -> bool:
    low = (obs or "").lower()
    return any(m in low for m in ("не нашёл", "не распознана", "ошибка", "не удалось", "не уверен", "пустой план"))


def _summarize(command: str, observations: list[str]) -> str:
    facts = "\n".join(observations[-6:])[:3000]
    try:
        return chat_completion(
            [
                {"role": "system", "content": "Ты Джарвис. Одной-двумя фразами по-русски скажи пользователю итог задачи по фактам. Если были ошибки — честно скажи, что не вышло."},
                {"role": "user", "content": f"Задача: {command}\nФакты:\n{facts}"},
            ],
            role="brain", temperature=0.2, timeout=40, max_tokens=150,
        ).strip()
    except Exception:
        return observations[-1].split(": ", 1)[-1][:200]
