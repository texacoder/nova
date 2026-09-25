// NOVA web interface. Plain JavaScript, no libraries, works offline.
"use strict";

const TOKEN = document.querySelector('meta[name="nova-token"]').content;
const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const inputEl = $("input");
const sendBtn = $("send");
const coreEl = $("core");
const coreLabel = $("core-label");
const statePill = $("state-pill");

let busy = false;
let speakReplies = false;
try { speakReplies = localStorage.getItem("nova-speak") === "1"; } catch (e) { /* storage blocked */ }

// ---------- server calls ----------

async function api(path, body) {
  const options = { method: body ? "POST" : "GET", headers: { "X-Nova-Token": TOKEN } };
  if (body) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok && !data.text) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

// ---------- visual state ----------

function setState(state, label) {
  coreEl.className = "core " + state;
  coreLabel.textContent = label;
  statePill.className = "pill" + (state === "waiting" ? " warn" : state === "offline" ? " bad" : "");
  statePill.textContent = {
    idle: "ONLINE", thinking: "PROCESSING", waiting: "AWAITING APPROVAL",
    offline: "BRAIN OFFLINE", listening: "LISTENING",
  }[state] || state.toUpperCase();
}

let brainOk = true;
function idleState() {
  if (brainOk) setState("idle", "Standing by");
  else setState("offline", "Brain offline - see System");
}

function tickClock() {
  const now = new Date();
  $("clock").textContent = now.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) +
    "  " + now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
setInterval(tickClock, 1000);
tickClock();

// ---------- rendering messages safely ----------

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Minimal formatting: ```code blocks```, `inline code`, **bold**, and links.
function formatText(raw) {
  const parts = raw.split(/```/);
  return parts.map((part, i) => {
    if (i % 2 === 1) {
      const code = part.replace(/^[\w+-]*\n/, "");  // drop the language name line
      return `<pre><code>${escapeHtml(code)}</code></pre>`;
    }
    let html = escapeHtml(part);
    html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
    return `<span class="text">${html}</span>`;
  }).join("");
}

function addMessage(who, text, steps) {
  const wrapper = document.createElement("div");
  wrapper.className = "msg " + who;
  const label = document.createElement("div");
  label.className = "who";
  label.textContent = who === "user" ? "YOU" : who === "nova" ? document.title.split(" ")[0] : "SYSTEM";
  wrapper.appendChild(label);

  if (steps && steps.length) wrapper.appendChild(renderSteps(steps));

  if (text) {
    const body = document.createElement("div");
    body.className = "body";
    body.innerHTML = formatText(text);
    wrapper.appendChild(body);
  }
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrapper;
}

function renderSteps(steps) {
  const box = document.createElement("div");
  box.className = "steps";
  for (const step of steps) {
    const details = document.createElement("details");
    details.className = "step";
    const summary = document.createElement("summary");
    const mark = document.createElement("span");
    mark.className = "mark-" + step.status;
    mark.textContent = step.status === "ok" ? "✓ " : step.status === "declined" ? "⊘ " : "✗ ";
    summary.appendChild(mark);
    summary.appendChild(document.createTextNode(`${step.tool}  ${shortArgs(step.arguments)}`));
    const pre = document.createElement("pre");
    pre.textContent = step.result;
    details.appendChild(summary);
    details.appendChild(pre);
    box.appendChild(details);
  }
  return box;
}

function shortArgs(args) {
  const text = Object.entries(args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" ");
  return text.length > 90 ? text.slice(0, 90) + "…" : text;
}

function showTyping() {
  const el = document.createElement("div");
  el.className = "msg nova";
  el.innerHTML = '<div class="body typing"><span></span><span></span><span></span></div>';
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

// ---------- conversation flow ----------

async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  addMessage("user", text);
  await run(() => api("/api/message", { text }));
}

async function answerApproval(approve) {
  $("confirm").hidden = true;
  addMessage("system", approve ? "Action approved." : "Action denied.");
  await run(() => api("/api/confirm", { approve }));
}

async function run(request) {
  busy = true;
  sendBtn.disabled = true;
  setState("thinking", "Processing");
  const typing = showTyping();
  try {
    const reply = await request();
    typing.remove();
    handleReply(reply);
  } catch (error) {
    typing.remove();
    addMessage("system", "Lost contact with NOVA. Is the program still running? (" + error.message + ")");
    setState("offline", "Connection lost");
  } finally {
    busy = false;
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

function handleReply(reply) {
  if (reply.pending) {
    if (reply.steps && reply.steps.length) addMessage("nova", "", reply.steps);
    $("confirm-text").textContent = reply.pending.description;
    $("confirm").hidden = false;
    setState("waiting", "Awaiting your approval");
    $("approve").focus();
    return;
  }
  addMessage("nova", reply.text, reply.steps);
  if (reply.text) speak(reply.text);
  if (reply.text && reply.text.startsWith("Setting up my brain")) {
    showSetupCard(true);
    pollSetup();
  }
  if (reply.exit) {
    addMessage("system", "NOVA has shut down. You can close this tab.");
    setState("offline", "Shut down");
    return;
  }
  idleState();
  refreshStatus();
}

async function refreshStatus() {
  try {
    const data = await api("/api/status");
    brainOk = data.brain_ok;
    const list = $("status-list");
    list.innerHTML = "";
    for (const [key, value] of Object.entries(data.status)) {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      list.append(dt, dd);
    }
    if (!busy) idleState();
    return data;
  } catch (error) {
    brainOk = false;
    setState("offline", "Cannot reach NOVA");
    return null;
  }
}

// ---------- automatic brain setup ----------

let setupPolling = false;

function showSetupCard(show) {
  $("setup").hidden = !show;
}

function renderSetup(data) {
  const card = $("setup");
  const bar = $("setup-bar");
  const progress = $("setup-progress");
  const button = $("setup-btn");
  if (data.state === "running") {
    card.className = "setup-card working";
    card.querySelector("h2").textContent = "Setting up brain";
    $("setup-text").textContent = data.message || "Working...";
    progress.hidden = false;
    const known = typeof data.progress === "number";
    progress.classList.toggle("indeterminate", !known);
    bar.style.width = known ? Math.round(data.progress * 100) + "%" : "";
    button.hidden = true;
    setState("thinking", "Installing brain");
  } else if (data.state === "error") {
    card.className = "setup-card";
    card.querySelector("h2").textContent = "Setup problem";
    $("setup-text").textContent = data.message;
    progress.hidden = true;
    button.hidden = false;
    button.textContent = "Try again";
  }
}

async function pollSetup() {
  if (setupPolling) return;
  setupPolling = true;
  try {
    while (true) {
      const data = await api("/api/setup");
      if (data.state === "done") {
        showSetupCard(false);
        addMessage("nova", data.message + " I'm fully online now. How can I help?");
        break;
      }
      renderSetup(data);
      if (data.state !== "running") break;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } catch (error) {
    addMessage("system", "Lost contact with NOVA during setup: " + error.message);
  } finally {
    setupPolling = false;
    refreshStatus();
  }
}

async function startSetup() {
  $("setup-btn").hidden = true;
  await api("/api/setup", {});
  pollSetup();
}

// ---------- voice ----------

function speak(text) {
  if (!speakReplies || !("speechSynthesis" in window)) return;
  const clean = text.replace(/```[\s\S]*?```/g, " (code shown on screen) ").replace(/https?:\/\/\S+/g, "link").slice(0, 1200);
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.rate = 1.02;
  utterance.pitch = 0.95;
  window.speechSynthesis.speak(utterance);
}

function setupVoice() {
  const speakBtn = $("speak");
  speakBtn.setAttribute("aria-pressed", String(speakReplies));
  if (!("speechSynthesis" in window)) speakBtn.hidden = true;
  speakBtn.addEventListener("click", () => {
    speakReplies = !speakReplies;
    speakBtn.setAttribute("aria-pressed", String(speakReplies));
    try { localStorage.setItem("nova-speak", speakReplies ? "1" : "0"); } catch (e) { /* ignore */ }
    if (!speakReplies && "speechSynthesis" in window) window.speechSynthesis.cancel();
  });

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const micBtn = $("mic");
  if (!Recognition) {
    micBtn.hidden = true;  // e.g. Firefox: no built-in speech recognition
    return;
  }
  const recognition = new Recognition();
  recognition.lang = navigator.language || "en-US";
  recognition.interimResults = true;
  let listening = false;

  recognition.onresult = (event) => {
    let transcript = "";
    for (const result of event.results) transcript += result[0].transcript;
    inputEl.value = transcript;
    autoGrow();
    if (event.results[event.results.length - 1].isFinal) {
      recognition.stop();
      send(inputEl.value);
      inputEl.value = "";
      autoGrow();
    }
  };
  recognition.onend = () => {
    listening = false;
    micBtn.classList.remove("recording");
    if (!busy) idleState();
  };
  recognition.onerror = (event) => {
    if (event.error !== "no-speech" && event.error !== "aborted") {
      addMessage("system", "Voice input error: " + event.error);
    }
  };
  micBtn.addEventListener("click", () => {
    if (listening) { recognition.stop(); return; }
    listening = true;
    micBtn.classList.add("recording");
    setState("listening", "Listening");
    recognition.start();
  });
}

// ---------- input ----------

function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + "px";
}

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = inputEl.value;
  inputEl.value = "";
  autoGrow();
  send(text);
});
inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
inputEl.addEventListener("input", autoGrow);
document.querySelectorAll(".quick button").forEach((button) => {
  button.addEventListener("click", () => send(button.dataset.cmd));
});
$("approve").addEventListener("click", () => answerApproval(true));
$("setup-btn").addEventListener("click", startSetup);
$("deny").addEventListener("click", () => answerApproval(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("confirm").hidden) answerApproval(false);
});

// ---------- start ----------

(async function start() {
  setupVoice();
  const data = await refreshStatus();
  if (!data) return;
  const name = data.name;
  if (data.brain_ok) {
    addMessage("nova", `${name} online. How can I help?`);
  } else {
    showSetupCard(true);
    const setup = await api("/api/setup").catch(() => null);
    if (setup && setup.state === "running") pollSetup();
    else if (setup && setup.state === "error") renderSetup(setup);
  }
  if (data.pending) {
    handleReply({ pending: data.pending, steps: [], text: "" });
  }
  inputEl.focus();
})();
