---
name: jarvis-hands
description: >-
  Drive the Windows desktop through Jarvis JSON plans, not Windows-MCP clicks.
  Use when opening apps/sites, searching YouTube, clicking UI, typing, or any
  computer-use on this PC. Jarvis executes instantly; this agent only writes
  the plan and reads a screenshot on «хз».
---

# Jarvis hands

This PC has Jarvis at `C:\Users\PC\Documents\jarvis`. It is the executor. You are the planner. Do **not** drive the desktop with `user-windows-mcp` (App/Click/Type/Screenshot loops). That is slow. Write a JSON plan, let Jarvis run it.

Web: Yandex Browser. Jarvis window itself stays a Chrome app.

## Send a plan

Write `bus/inbox.json` (this repo). Jarvis polls it (~60ms).

```json
{
  "id": "t1",
  "goal": "что просил пользователь",
  "steps": [
    "открой ютуб",
    "поиск на ютуб космос",
    "хз"
  ]
}
```

A raw array is also valid: `["открой discord"]`.

Then Read `bus/result.json`. If `id` is old or the file is missing, wait ~200ms and Read again (a few times). Do not busy-loop.

If `need_screen` is true, Read `bus/screen.jpg`, then send the next inbox with clicks/commands.

## Steps Jarvis runs fast

- `открой discord` / `открой ютуб` / `открой файл привет.txt` / `закрой …`
- `поиск на ютуб космос` / `найди …` / `загугли …` — query is the gist, no «про»
- `напиши текст` / `энтер` / `пробел` / `hotkey ctrl+l`
- `жди 0.4`
- `клик 412 180` — coordinates from **the screenshot you just saw** (image pixels). Jarvis scales to the screen.
- `двойной клик 412 180` / `правый клик 412 180`

Do not send vague `нажми на кнопку скачать`. If you cannot see the target, write `хз`.

## «хз»

Put `хз` when the next UI action is unknown (which thumbnail, which icon). Jarvis runs everything **before** it, then writes `bus/screen.jpg` and stops. You look at the shot and send the next plan (`клик x y`, `напиши …`, or another `хз`).

Unknown/unparsed steps are treated as `хз` too.

## HTTP alternative

Jarvis UI: `http://127.0.0.1:8765`

- `POST /api/plan` same JSON, waits until done
- `GET /api/bus` last `result.json`
- `GET /api/bus/screen` last JPEG

Prefer the file inbox from this workspace.

## Fallback

If `result.json` never appears (~3s) Jarvis is probably not running. Tell the user to start Jarvis. Only then use Windows-MCP.
