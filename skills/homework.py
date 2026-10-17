"""Домашка: найти ДЗ (дневник, файлы, буфер, экран) и решить его локальной моделью."""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from core.config import get
from core.llm import chat_completion

HW_STEMS = ("домашк", "домашн", "домах", "домашек", "homework")
HW_EXACT = ("дз", "д/з", "дэзэ", "домашка", "уроки", "hw")
SOLVE_VERBS = ("сдела", "реши", "выполн", "помоги", "делай", "напиши", "сделай", "решай", "сгенер")
SHOW_VERBS = ("покажи", "какое", "какие", "что задали", "найди", "глянь", "посмотри", "проверь", "скажи")

SUBJECTS: dict[str, str] = {
    "алгебр": "алгебра", "геометр": "геометрия", "матем": "математика", "матеш": "математика",
    "русск": "русский язык", "русич": "русский язык", "литер": "литература",
    "англ": "английский язык", "инглиш": "английский язык", "немец": "немецкий язык",
    "франц": "французский язык", "физкульт": "физкультура", "физр": "физкультура",
    "физик": "физика", "физ": "физика", "хим": "химия",
    "биол": "биология", "истор": "история", "обществ": "обществознание", "общаг": "обществознание",
    "географ": "география", "информат": "информатика", "инф": "информатика",
    "вероятн": "вероятность и статистика", "статист": "вероятность и статистика",
    "обж": "ОБЗР", "обзр": "ОБЗР", "астроном": "астрономия",
}

WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среду": 2, "среда": 2, "четверг": 3,
    "пятницу": 4, "пятница": 4, "субботу": 5, "суббота": 5, "воскресенье": 6,
}
WEEKDAY_NAMES = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

TEXT_EXTS = {".txt", ".md", ".rtf", ".html", ".htm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
DOC_EXTS = {".docx", ".pdf"}
FILE_KEYWORDS = ("дз", "домаш", "homework", "hw", "задани", "упражн", "задач", "контрольн", "самостоят")
SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", "AppData", ".chrome-app", "AI", "Jarvis ДЗ"}

DEFAULT_DIARY_URL = "https://authedu.mosreg.ru/diary/homeworks/homeworks"

EXTRACT_PROMPT = """Ты читаешь скриншот. Найди на нём ДОМАШНИЕ ЗАДАНИЯ школьника.
Верни ТОЛЬКО JSON:
{"login_required": false, "tasks": [{"subject": "алгебра", "date": "18.10", "task": "полный текст задания как написано"}]}

Правила:
- Переписывай задание дословно: номера, упражнения, страницы, параграфы, текст задачи.
- Если на экране форма входа / Госуслуги / капча — {"login_required": true, "tasks": []}.
- Если заданий не видно — {"login_required": false, "tasks": []}.
- Никакого текста вне JSON."""

SOLVE_PROMPT = """Ты Джарвис — личный репетитор-отличник. Реши домашнее задание школьника полностью и правильно.

Оформление:
- Каждый номер/упражнение отдельно, подпиши номер.
- Математика, физика, химия: Дано → Решение по шагам → Ответ. Формулы обычным текстом: x^2, sqrt(2), a/b.
- Русский, литература, языки: выполни задание целиком (вставь буквы, разборы, перевод, сочинение) и коротко поясни правило.
- Перед ответом проверь вычисления.
- Если задание ссылается на учебник (№ 245, упр. 112, с. 40), а текста задачи нет — напиши «НУЖЕН ТЕКСТ: …» и что именно прислать, затем дай краткий план решения такого типа.
- Пиши по-русски, как в аккуратной тетради, без воды."""


@dataclass
class HomeworkTask:
    subject: str
    task: str
    when: str = ""
    image_b64: str = ""
    source: str = ""


@dataclass
class HomeworkRequest:
    mode: str
    subject: str = ""
    day: date = field(default_factory=date.today)
    source: str = "auto"
    path_hint: str = ""


def _low(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zа-я0-9/]+", _low(text))


def _has_hw_word(text: str) -> bool:
    words = _words(text)
    if any(w in HW_EXACT for w in words):
        return True
    return any(w.startswith(HW_STEMS) for w in words) or "домашнее задание" in _low(text)


def is_homework_command(command: str) -> bool:
    low = _low(command)
    if low in ("открой дневник", "открой школьный портал", "дневник", "открой электронный дневник"):
        return True
    if re.search(r"\bчто (?:нам |мне )?задали\b", low) or re.search(r"\bзадали (?:на|по)\b", low):
        return True
    if not _has_hw_word(low):
        return False
    if "уроки" in _words(low) and not any(v in low for v in SOLVE_VERBS + SHOW_VERBS):
        return False
    return True


def _detect_subject(text: str) -> str:
    for w in _words(text):
        for stem, name in sorted(SUBJECTS.items(), key=lambda x: len(x[0]), reverse=True):
            if w.startswith(stem):
                return name
    return ""


def _skip_sunday(d: date) -> date:
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _detect_day(text: str) -> date:
    low = _low(text)
    today = date.today()
    if "послезавтра" in low:
        return _skip_sunday(today + timedelta(days=2))
    if "сегодня" in low:
        return today
    if "вчера" in low:
        return today - timedelta(days=1)
    for name, idx in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", low):
            delta = (idx - today.weekday()) % 7 or 7
            return today + timedelta(days=delta)
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})\b", low)
    if m:
        try:
            return date(today.year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return _skip_sunday(today + timedelta(days=1))


def parse_request(command: str) -> HomeworkRequest:
    low = _low(command)
    mode = "solve"
    if any(v in low for v in SHOW_VERBS) and not any(v in low for v in SOLVE_VERBS):
        mode = "show"
    if low.startswith(("открой дневник", "открой школьный портал", "открой электронный дневник")) or low == "дневник":
        mode = "diary"

    source = "auto"
    path_hint = ""
    if "буфер" in low or "скопировал" in low:
        source = "clipboard"
    elif "экран" in low or "что открыто" in low:
        source = "screen"
    elif "дневник" in low or "портал" in low or "мэш" in low:
        source = "diary"
    m = re.search(r"(?:из файла|файл)\s+(.+)$", low)
    if m:
        source = "file"
        path_hint = m.group(1).strip(" .")

    return HomeworkRequest(mode=mode, subject=_detect_subject(low), day=_detect_day(low), source=source, path_hint=path_hint)


def _image_to_b64(img, max_size: int = 1600) -> str:
    from PIL import Image

    img = img.convert("RGB")
    if max(img.size) > max_size:
        scale = max_size / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _screen_b64() -> str:
    import pyautogui

    return _image_to_b64(pyautogui.screenshot(), int(get("homework.shot_max_size", 1600)))


def _parse_json(answer: str) -> dict:
    text = answer.strip()
    if "```" in text:
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_from_image(image_b64: str, req: HomeworkRequest, source: str) -> tuple[list[HomeworkTask], bool]:
    focus = f"Нужна дата: {req.day.strftime('%d.%m')} ({WEEKDAY_NAMES[req.day.weekday()]})."
    if req.subject:
        focus += f" Предмет: {req.subject}."
    if source != "diary":
        focus += " Если дата не указана — бери все задания с экрана."
    answer = chat_completion(
        [
            {"role": "system", "content": EXTRACT_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": focus},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            },
        ],
        role="vision",
        temperature=0,
        timeout=int(get("homework.extract_timeout_sec", 90)),
        max_tokens=int(get("homework.extract_max_tokens", 900)),
    )
    print("ДЗ, распознал:", answer)
    data = _parse_json(answer)
    tasks = []
    for item in data.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("task") or "").strip()
        if not text:
            continue
        subject = str(item.get("subject") or req.subject or "задание").strip()
        if req.subject and _detect_subject(subject) not in ("", req.subject):
            continue
        keep_image = source != "diary"
        tasks.append(HomeworkTask(subject, text, str(item.get("date") or ""), image_b64 if keep_image else "", source))
    return tasks, bool(data.get("login_required"))


def _read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages[:10])


def _tasks_from_file(path: Path, req: HomeworkRequest) -> list[HomeworkTask]:
    ext = path.suffix.lower()
    subject = req.subject or _detect_subject(path.stem) or "задание"
    if ext in IMAGE_EXTS:
        from PIL import Image

        b64 = _image_to_b64(Image.open(path))
        tasks, _ = _extract_from_image(b64, req, "file")
        if tasks:
            return tasks
        return [HomeworkTask(subject, "Задание на фото — реши всё, что на нём есть.", "", b64, "file")]
    if ext == ".docx":
        text = _read_docx(path)
    elif ext == ".pdf":
        text = _read_pdf(path)
        if not text.strip():
            return []
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    text = text.strip()[: int(get("homework.max_task_chars", 6000))]
    if not text:
        return []
    return [HomeworkTask(subject, text, "", "", "file")]


def _search_roots() -> list[Path]:
    roots = [Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Downloads"]
    roots.append(Path.home() / "Downloads" / "Telegram Desktop")
    extra = get("homework.folders", [])
    if isinstance(extra, list):
        roots.extend(Path(os.path.expandvars(str(p))) for p in extra if p)
    seen, out = set(), []
    for r in roots:
        if r.is_dir() and str(r).lower() not in seen:
            seen.add(str(r).lower())
            out.append(r)
    return out


def find_homework_files(req: HomeworkRequest, limit: int = 3) -> list[Path]:
    max_age = float(get("homework.max_age_days", 4)) * 86400
    budget = time.time() + float(get("homework.scan_budget_sec", 1.5))
    now = time.time()
    scored: list[tuple[float, Path]] = []
    allowed_exts = TEXT_EXTS | IMAGE_EXTS | DOC_EXTS

    for root in _search_roots():
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() > budget:
                break
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            if len(Path(dirpath).parts) - base_depth >= 3:
                dirnames[:] = []
            folder_low = _low(dirpath)
            folder_hit = any(k in folder_low for k in FILE_KEYWORDS) or bool(_detect_subject(Path(dirpath).name))
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix.lower() not in allowed_exts:
                    continue
                try:
                    age = now - p.stat().st_mtime
                except OSError:
                    continue
                if age > max_age:
                    continue
                low = _low(p.stem)
                score = 0.0
                if any(k in low for k in FILE_KEYWORDS):
                    score += 3
                if folder_hit:
                    score += 2
                subj = _detect_subject(p.stem) or _detect_subject(Path(dirpath).name)
                if subj:
                    score += 2
                if req.subject and subj and subj != req.subject:
                    continue
                if req.subject and subj == req.subject:
                    score += 2
                score += max(0.0, 1.0 - age / max_age)
                if score >= 3:
                    scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


def _from_clipboard(req: HomeworkRequest) -> list[HomeworkTask]:
    try:
        from PIL import ImageGrab

        clip = ImageGrab.grabclipboard()
    except Exception:
        clip = None
    if clip is not None and not isinstance(clip, list):
        b64 = _image_to_b64(clip)
        tasks, _ = _extract_from_image(b64, req, "clipboard")
        if tasks:
            return tasks
        return [HomeworkTask(req.subject or "задание", "Задание на картинке — реши всё.", "", b64, "clipboard")]
    if isinstance(clip, list):
        for raw in clip:
            p = Path(raw)
            if p.is_file():
                tasks = _tasks_from_file(p, req)
                if tasks:
                    return tasks
    try:
        import pyperclip

        text = (pyperclip.paste() or "").strip()
    except Exception:
        text = ""
    if len(text) >= 8:
        return [HomeworkTask(req.subject or _detect_subject(text) or "задание", text[:6000], "", "", "clipboard")]
    return []


def diary_url() -> str:
    return str(get("homework.diary_url", DEFAULT_DIARY_URL))


def _from_diary(req: HomeworkRequest) -> tuple[list[HomeworkTask], bool]:
    from skills.search import open_url

    open_url(diary_url())
    time.sleep(float(get("homework.diary_wait_sec", 5)))
    return _extract_from_image(_screen_b64(), req, "diary")


def _from_screen(req: HomeworkRequest) -> list[HomeworkTask]:
    tasks, _ = _extract_from_image(_screen_b64(), req, "screen")
    return tasks


def collect_tasks(req: HomeworkRequest) -> tuple[list[HomeworkTask], str]:
    """Пробует источники по очереди. Возвращает задания и заметку для пользователя."""
    if req.source == "file":
        from skills.files import _guard, _resolve_path

        path = _resolve_path(req.path_hint)
        if _guard(path) or not path.is_file():
            return [], f"Не нашёл файл {req.path_hint}"
        return _tasks_from_file(path, req), ""
    if req.source == "clipboard":
        tasks = _from_clipboard(req)
        return tasks, "" if tasks else "В буфере нет задания — скопируй текст или скриншот"
    if req.source == "screen":
        tasks = _from_screen(req)
        return tasks, "" if tasks else "На экране не вижу домашнего задания"
    if req.source == "diary":
        tasks, login = _from_diary(req)
        if login:
            return [], "Дневник просит вход. Войди в Школьный портал в Яндекс Браузере один раз — дальше я сам"
        return tasks, "" if tasks else "В дневнике не нашёл заданий на эту дату"

    for path in find_homework_files(req):
        print("ДЗ, файл-кандидат:", path)
        tasks = _tasks_from_file(path, req)
        if tasks:
            return tasks, f"нашёл в файле {path.name}"

    if get("homework.use_diary", True):
        tasks, login = _from_diary(req)
        if tasks:
            return tasks, "взял из электронного дневника"
        if login:
            return [], "Дневник просит вход. Войди в Школьный портал в Яндекс Браузере один раз — дальше я сам"

    tasks = _from_screen(req)
    if tasks:
        return tasks, "взял с экрана"
    return [], "Не нашёл ДЗ: ни в файлах, ни в дневнике, ни на экране"


def _gdz_hint(task: HomeworkTask) -> str:
    m = re.search(r"(?:№|номер|упр\.?|упражнение)\s*(\d+[\d,.\s\-–и]*)", _low(task.task))
    if not m:
        return ""
    grade = str(get("homework.grade", "") or "")
    books = get("homework.textbooks", {}) or {}
    book = ""
    if isinstance(books, dict):
        book = str(books.get(_low(task.subject).split()[0], "") or "")
    parts = ["гдз", task.subject]
    if grade:
        parts.append(f"{grade} класс")
    if book:
        parts.append(book)
    parts.append(f"номер {m.group(1).strip()}")
    return " ".join(parts)


def solve_task(task: HomeworkTask) -> str:
    grade = get("homework.grade", "")
    header = f"Предмет: {task.subject}."
    if grade:
        header += f" Класс: {grade}."
    text = f"{header}\nЗадание:\n{task.task}"
    if task.image_b64:
        content = [
            {"type": "text", "text": text + "\nЗадание также на картинке — учти рисунки и условия с неё."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{task.image_b64}"}},
        ]
    else:
        content = text
    return chat_completion(
        [{"role": "system", "content": SOLVE_PROMPT}, {"role": "user", "content": content}],
        role="homework",
        temperature=0.2,
        timeout=int(get("homework.solve_timeout_sec", 300)),
    ).strip()


def _out_dir() -> Path:
    raw = get("homework.output_dir", "")
    out = Path(os.path.expandvars(str(raw))) if raw else Path.home() / "Documents" / "Jarvis ДЗ"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "", text).strip()[:60] or "дз"


def save_solutions(req: HomeworkRequest, solved: list[tuple[HomeworkTask, str]]) -> Path:
    subjects = sorted({t.subject for t, _ in solved})
    title = ", ".join(subjects) if len(subjects) <= 3 else f"{len(subjects)} предметов"
    path = _out_dir() / _safe_name(f"{req.day.isoformat()} {title}.md")
    lines = [f"# ДЗ на {req.day.strftime('%d.%m')} ({WEEKDAY_NAMES[req.day.weekday()]})", ""]
    lines.append(f"_Сделал Джарвис {datetime.now().strftime('%d.%m %H:%M')}_")
    for task, answer in solved:
        lines += ["", f"## {task.subject.capitalize()}", "", "**Задание:**", "", task.task, "", "**Решение:**", "", answer]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _announce(text: str) -> None:
    print(text)
    try:
        from voice.tts import speak

        speak(text)
    except Exception:
        pass


def _format_list(tasks: list[HomeworkTask]) -> str:
    return "; ".join(f"{t.subject}: {t.task[:120]}" for t in tasks)


def run_homework(command: str) -> str:
    req = parse_request(command)
    if req.mode == "diary":
        from skills.search import open_url

        open_url(diary_url())
        return "Открываю электронный дневник"

    tasks, note = collect_tasks(req)
    if not tasks:
        return note
    tasks = tasks[: int(get("homework.max_tasks", 6))]

    if req.mode == "show":
        return f"ДЗ на {req.day.strftime('%d.%m')}: {_format_list(tasks)}"

    subjects = ", ".join(dict.fromkeys(t.subject for t in tasks))
    _announce(f"Нашёл ДЗ ({subjects}), {note}. Решаю")

    solved: list[tuple[HomeworkTask, str]] = []
    gdz_queries: list[str] = []
    for i, task in enumerate(tasks, 1):
        print(f"ДЗ {i}/{len(tasks)}: {task.subject} — {task.task[:80]}")
        try:
            answer = solve_task(task)
        except Exception as exc:
            answer = f"Не смог решить: {exc}"
        solved.append((task, answer))
        if "НУЖЕН ТЕКСТ" in answer.upper():
            q = _gdz_hint(task)
            if q:
                gdz_queries.append(q)

    path = save_solutions(req, solved)
    try:
        from core.memory import remember_file

        remember_file(path, "создал")
    except Exception:
        pass
    os.startfile(path)

    msg = f"ДЗ готово: {subjects}. Решение открыл — {path.name}"
    if gdz_queries and get("homework.open_gdz", True):
        from skills.search import open_platform_search

        for q in gdz_queries[:2]:
            open_platform_search("яндекс", q)
        msg += ". Для номеров из учебника нужен текст — открыл поиск, скинь фото и скажи «сделай дз из буфера»"
    return msg


def try_homework(command: str) -> Optional[str]:
    if not is_homework_command(command):
        return None
    return run_homework(command)
