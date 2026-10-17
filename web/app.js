const logEl = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("input");
const send = document.getElementById("send");
const pill = document.getElementById("statusPill");
const modelLine = document.getElementById("modelLine");

let ready = false;
let busy = false;

const voiceLine = document.getElementById("voiceLine");
const voiceText = document.getElementById("voiceText");
let lastEvent = -1;

const VOICE_LABELS = {
  off: "микрофон выключен",
  armed: "зажми Esc и говори",
  listening: "запись… отпусти Esc",
  recognizing: "распознаю…",
  working: "выполняю",
  confirm: "нужно подтверждение",
  error: "ошибка микрофона",
};

function renderVoice(v) {
  if (!v) return;
  voiceLine.dataset.state = v.state;
  if (v.state === "armed") {
    voiceText.textContent = v.text || VOICE_LABELS.armed;
    return;
  }
  const label = VOICE_LABELS[v.state] || v.state;
  voiceText.textContent = v.text ? `${label}: ${v.text}` : label;
}

async function pollEvents() {
  try {
    const r = await fetch(`/api/events?since=${Math.max(lastEvent, 0)}`);
    const data = await r.json();
    if (lastEvent < 0) {
      lastEvent = data.last || 0;
    } else {
      for (const e of data.events) {
        addMsg(e.role, e.text, e.role === "you");
        lastEvent = e.id;
      }
    }
    renderVoice(data.voice);
  } catch {}
}

function addMsg(role, text, voiced = false) {
  const el = document.createElement("div");
  el.className = `msg ${role}${voiced ? " voiced" : ""}`;
  el.textContent = text;
  logEl.appendChild(el);
  logEl.scrollTop = logEl.scrollHeight;
}

function setBusy(on) {
  busy = on;
  const can = ready && !busy;
  input.disabled = !can;
  send.disabled = !can;
  pill.dataset.state = on ? "busy" : (ready ? "ready" : pill.dataset.state);
  pill.textContent = on ? "работаю" : (ready ? "онлайн" : pill.textContent);
}

async function refreshStatus() {
  try {
    const r = await fetch("/api/status");
    const data = await r.json();
    if (data.model_error) {
      ready = false;
      pill.dataset.state = "error";
      pill.textContent = "ошибка";
      modelLine.textContent = data.model_error;
      input.disabled = true;
      send.disabled = true;
      return;
    }
    ready = Boolean(data.model_ready);
    if (data.busy) {
      setBusy(true);
      return;
    }
    if (ready) {
      pill.dataset.state = "ready";
      pill.textContent = "онлайн";
      modelLine.textContent = data.model || "локальный ассистент";
      if (!busy) {
        input.disabled = false;
        send.disabled = false;
      }
    } else {
      pill.dataset.state = "boot";
      pill.textContent = "модель";
      modelLine.textContent = "загружаю нейросеть…";
      input.disabled = true;
      send.disabled = true;
    }
  } catch {
    pill.dataset.state = "boot";
    pill.textContent = "связь";
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || !ready || busy) return;
  input.value = "";
  addMsg("you", text);
  setBusy(true);
  try {
    const r = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await r.json();
    if (!r.ok || data.ok === false) {
      addMsg("bot", data.error || "Не получилось выполнить команду");
    } else {
      addMsg("bot", data.result || "Готово");
    }
  } catch {
    addMsg("bot", "Нет связи с Jarvis");
  } finally {
    setBusy(false);
    await refreshStatus();
    input.focus();
  }
});

document.getElementById("chips").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-cmd]");
  if (!btn || input.disabled) return;
  input.value = btn.dataset.cmd;
  input.focus();
});

addMsg("sys", "Системы в сети. Пиши команду — я выполню её на компьютере.");
refreshStatus();
setInterval(refreshStatus, 1500);
pollEvents();
setInterval(pollEvents, 700);
