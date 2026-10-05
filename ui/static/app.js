// JARVIS web interface. Plain JavaScript, no libraries, works offline.
"use strict";

const TOKEN = document.querySelector('meta[name="jarvis-token"]').content;
const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const inputEl = $("input");
const sendBtn = $("send");
const coreEl = $("core");
const coreLabel = $("core-label");
const statePill = $("state-pill");

let busy = false;
let NAME = document.querySelector(".brand-name").textContent.trim();

function loadSetting(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback; } catch (e) { return fallback; }
}
function saveSetting(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* storage blocked */ }
}
let speakReplies = loadSetting("jarvis-speak", "0") === "1";

// ---------- server calls ----------

async function api(path, body) {
  const options = { method: body ? "POST" : "GET", headers: { "X-Jarvis-Token": TOKEN } };
  if (body) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok && !data.text) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

// Streamed request: the server sends one JSON object per line while JARVIS works.
async function streamRequest(path, body, onEvent) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "X-Jarvis-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let reply = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.type === "done") reply = event.reply;
      else onEvent(event);
    }
  }
  if (!reply) throw new Error("the reply was cut off");
  return reply;
}

// ---------- visual state ----------

function setState(state, label) {
  coreEl.className = "core " + state;
  coreLabel.textContent = label;
  statePill.className = "pill" + (state === "waiting" ? " warn" : state === "offline" ? " bad" : "");
  statePill.textContent = {
    idle: "ONLINE", thinking: "PROCESSING", waiting: "AWAITING APPROVAL",
    offline: "BRAIN OFFLINE", listening: "LISTENING", speaking: "SPEAKING",
  }[state] || state.toUpperCase();
}

let brainOk = true;
function idleState() {
  if (voice.active) setState("listening", "Voice chat on");
  else if (brainOk) setState("idle", "Standing by");
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

function messageShell(who) {
  const wrapper = document.createElement("div");
  wrapper.className = "msg " + who;
  const label = document.createElement("div");
  label.className = "who";
  label.textContent = who === "user" ? "YOU" : who === "jarvis" ? NAME.toUpperCase() : "SYSTEM";
  wrapper.appendChild(label);
  return wrapper;
}

function addMessage(who, text, steps) {
  const wrapper = messageShell(who);
  if (steps && steps.length) {
    const box = document.createElement("div");
    box.className = "steps";
    steps.forEach((step) => box.appendChild(renderStep(step)));
    wrapper.appendChild(box);
  }
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

function renderStep(step) {
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
  return details;
}

function shortArgs(args) {
  const text = Object.entries(args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" ");
  return text.length > 90 ? text.slice(0, 90) + "…" : text;
}

// A reply that fills in live while JARVIS is writing it.
function createLiveReply() {
  const wrapper = messageShell("jarvis");
  const steps = document.createElement("div");
  steps.className = "steps";
  const body = document.createElement("div");
  body.className = "body typing";
  body.innerHTML = "<span></span><span></span><span></span>";
  wrapper.append(steps, body);
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  let text = "";

  return {
    gotText: false,
    handle(event) {
      if (event.type === "token") {
        if (!text) {
          body.className = "body live";
          body.textContent = "";
          setState(voice.active || speakReplies ? "speaking" : "thinking", "Responding");
        }
        text += event.text;
        this.gotText = true;
        body.textContent = text;
        speaker.feed(text);
      } else if (event.type === "reset") {
        text = "";
        this.gotText = false;
        speaker.reset();
        body.className = "body typing";
        body.innerHTML = "<span></span><span></span><span></span>";
      } else if (event.type === "step") {
        steps.appendChild(renderStep(event));
      }
      messagesEl.scrollTop = messagesEl.scrollHeight;
    },
    remove() { wrapper.remove(); },
  };
}

// ---------- conversation flow ----------

async function send(text, options = {}) {
  text = text.trim();
  if (!text || busy) return;
  addMessage("user", text);
  await run("/api/message/stream", { text, voice: Boolean(options.voice) });
}

async function answerApproval(approve) {
  $("confirm").hidden = true;
  addMessage("system", approve ? "Action approved." : "Action denied.");
  await run("/api/confirm/stream", { approve });
}

async function run(path, body) {
  busy = true;
  sendBtn.disabled = true;
  setState("thinking", "Processing");
  speaker.reset();
  const live = createLiveReply();
  try {
    const reply = await streamRequest(path, body, (event) => live.handle(event));
    live.remove();
    handleReply(reply, live.gotText);
  } catch (error) {
    live.remove();
    speaker.stop();
    addMessage("system", "Lost contact with " + NAME + ". Is the program still running? (" + error.message + ")");
    setState("offline", "Connection lost");
    voice.stop();
  } finally {
    busy = false;
    sendBtn.disabled = false;
    if (!voice.active) inputEl.focus();
  }
}

function handleReply(reply, alreadySpoken) {
  if (reply.pending) {
    speaker.stop();
    if (reply.steps && reply.steps.length) addMessage("jarvis", "", reply.steps);
    $("confirm-text").textContent = reply.pending.description;
    $("confirm").hidden = false;
    setState("waiting", "Awaiting your approval");
    $("approve").focus();
    return;
  }
  addMessage("jarvis", reply.text, reply.steps);
  if (alreadySpoken) speaker.finish(reply.text);
  else speaker.speakAll(reply.text);
  if (reply.text && reply.text.startsWith("Setting up my brain")) {
    showSetupCard(true);
    pollSetup();
  }
  if (reply.restart) {
    voice.stop();
    setState("thinking", "Restarting");
    addMessage("system", "Restarting " + NAME + "... this page will reconnect by itself.");
    waitForRestart();
    return;
  }
  if (reply.exit) {
    voice.stop();
    addMessage("system", NAME + " has shut down. You can close this tab.");
    setState("offline", "Shut down");
    return;
  }
  if (!speaker.busy()) idleState();
  refreshStatus();
  // Listen for the next question once JARVIS has finished speaking (after this request settles).
  speaker.whenDone(() => setTimeout(() => voice.listenAgain(), 300));
}

async function refreshStatus() {
  try {
    const data = await api("/api/status");
    brainOk = data.brain_ok;
    applyName(data.name);
    const list = $("status-list");
    list.innerHTML = "";
    for (const [key, value] of Object.entries(data.status)) {
      const dt = document.createElement("dt");
      dt.textContent = key;
      const dd = document.createElement("dd");
      dd.textContent = value;
      list.append(dt, dd);
    }
    if (!busy && !speaker.busy() && !voice.listening) idleState();
    return data;
  } catch (error) {
    brainOk = false;
    setState("offline", "Cannot reach " + NAME);
    return null;
  }
}

// ---------- name (can change while running, e.g. "change your name to ...") ----------

function applyName(name) {
  if (!name || name === NAME) return;
  NAME = name;
  document.querySelector(".brand-name").textContent = name;
  document.title = name + " - Personal AI";
  inputEl.placeholder = "Talk to " + name + "...";
}

// ---------- restart ----------

async function waitForRestart() {
  await new Promise((resolve) => setTimeout(resolve, 1500));
  for (let attempt = 0; attempt < 120; attempt++) {
    try {
      const response = await fetch("/", { cache: "no-store" });
      if (response.ok) {
        location.reload();  // the restarted assistant has a new security token
        return;
      }
    } catch (error) { /* not up yet */ }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  addMessage("system", NAME + " didn't come back. Check its window on your PC.");
  setState("offline", "Restart failed");
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
        addMessage("jarvis", data.message + " I'm fully online now. How can I help?");
        break;
      }
      renderSetup(data);
      if (data.state !== "running") break;
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } catch (error) {
    addMessage("system", "Lost contact with " + NAME + " during setup: " + error.message);
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

// ---------- speaking replies (sentence by sentence, while JARVIS is still writing) ----------

// Turn reply text into something pleasant to hear: no code, links or markdown symbols.
function speakableText(text) {
  const parts = text.split("```");
  let result = "";
  parts.forEach((part, i) => {
    if (i % 2 === 0) result += part;
    else if (i < parts.length - 1) result += " The code is shown on screen. ";
    // an unfinished code block (still being written) is skipped for now
  });
  return result
    .replace(/https?:\/\/\S+/g, "the link on screen")
    .replace(/[*_#>`|]/g, "")
    .replace(/\[(\d+)\]/g, "");
}

const speaker = {
  supported: "speechSynthesis" in window,
  spokenUpTo: 0,
  queued: 0,
  doneCallbacks: [],

  enabled() { return this.supported && (speakReplies || voice.active); },

  reset() { this.spokenUpTo = 0; },

  stop() {
    if (this.supported) window.speechSynthesis.cancel();
    this.queued = 0;
    this.spokenUpTo = 0;
    this.doneCallbacks = [];
  },

  busy() { return this.queued > 0; },

  // Called with the full text so far: speak every complete sentence not spoken yet.
  feed(fullText) {
    if (!this.enabled()) return;
    const clean = speakableText(fullText);
    const rest = clean.slice(this.spokenUpTo);
    const match = rest.match(/^[\s\S]*[.!?:;](?=\s)|^[\s\S]*\n/);
    if (match && match[0].trim().length > 1) {
      this.say(match[0]);
      this.spokenUpTo += match[0].length;
    }
  },

  // The reply is complete: speak whatever is left.
  finish(fullText) {
    if (!this.enabled()) return;
    const rest = speakableText(fullText).slice(this.spokenUpTo);
    if (rest.trim()) this.say(rest);
    this.spokenUpTo = 0;
  },

  speakAll(text) {
    this.spokenUpTo = 0;
    this.finish(text || "");
  },

  say(text) {
    const utterance = new SpeechSynthesisUtterance(text.trim());
    const chosen = voicePicker.selectedVoice();
    if (chosen) {
      utterance.voice = chosen;
      utterance.lang = chosen.lang;
    }
    utterance.rate = Number(loadSetting("jarvis-rate", "1.05"));
    const finished = () => {
      this.queued = Math.max(0, this.queued - 1);
      if (this.queued === 0) {
        const callbacks = this.doneCallbacks;
        this.doneCallbacks = [];
        if (!busy) idleState();
        callbacks.forEach((callback) => callback());
      }
    };
    utterance.onend = finished;
    utterance.onerror = finished;
    this.queued += 1;
    if (!busy) setState("speaking", "Speaking");
    window.speechSynthesis.speak(utterance);
  },

  // Run `callback` once everything queued has been spoken (immediately if silent).
  whenDone(callback) {
    if (this.queued === 0) callback();
    else this.doneCallbacks.push(callback);
  },
};

// ---------- choosing the voice ----------

const voicePicker = {
  select: $("voice-select"),
  voices: [],

  load() {
    if (!speaker.supported) {
      $("voice-panel").hidden = true;
      return;
    }
    const all = window.speechSynthesis.getVoices();
    if (!all.length) return;  // Chrome loads voices a moment later
    const language = (navigator.language || "en").slice(0, 2);
    const score = (v) => (v.lang.startsWith(language) ? 4 : 0) + (/natural|neural/i.test(v.name) ? 2 : 0) +
      (/google|microsoft/i.test(v.name) ? 1 : 0);
    this.voices = all.slice().sort((a, b) => score(b) - score(a) || a.name.localeCompare(b.name));
    const saved = loadSetting("jarvis-voice", "");
    this.select.innerHTML = "";
    for (const v of this.voices) {
      const option = document.createElement("option");
      option.value = v.name;
      option.textContent = `${v.name} (${v.lang})`;
      this.select.appendChild(option);
    }
    this.select.value = this.voices.some((v) => v.name === saved) ? saved : this.voices[0].name;
    const natural = this.voices.some((v) => /natural|neural/i.test(v.name));
    $("voice-tip").hidden = natural;
    $("voice-rate").value = loadSetting("jarvis-rate", "1.05");
  },

  selectedVoice() {
    return this.voices.find((v) => v.name === this.select.value) || null;
  },

  setup() {
    this.load();
    if (speaker.supported) window.speechSynthesis.onvoiceschanged = () => this.load();
    this.select.addEventListener("change", () => saveSetting("jarvis-voice", this.select.value));
    $("voice-rate").addEventListener("change", (event) => saveSetting("jarvis-rate", event.target.value));
    $("voice-test").addEventListener("click", () => {
      speaker.stop();
      const wasEnabled = speakReplies;
      speakReplies = true;
      speaker.speakAll(`Hello. I'm ${NAME}. This is how I sound.`);
      speakReplies = wasEnabled;
    });
  },
};

// ---------- listening: dictation (mic button) and hands-free voice chat ----------

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

const voice = {
  active: false,       // voice chat mode is on
  listening: false,
  recognition: null,
  silentRounds: 0,
  dictation: false,    // one-off dictation from the mic button

  setup() {
    if (!Recognition) {
      $("mic").hidden = true;          // e.g. Firefox: no built-in speech recognition
      $("voice-chat").hidden = true;
      return;
    }
    const recognition = new Recognition();
    recognition.lang = navigator.language || "en-US";
    recognition.interimResults = true;
    recognition.continuous = false;
    this.recognition = recognition;
    let heard = "";

    recognition.onstart = () => {
      this.listening = true;
      heard = "";
      setState("listening", this.active ? "Listening... speak now" : "Listening");
    };
    recognition.onresult = (event) => {
      let transcript = "";
      for (const result of event.results) transcript += result[0].transcript;
      heard = transcript;
      inputEl.value = transcript;
      autoGrow();
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        addMessage("system", "Microphone access was blocked. Allow the microphone for this page " +
          "(click the icon in the address bar), then try again.");
        this.stop();
      } else if (event.error === "network") {
        addMessage("system", "Voice recognition needs an internet connection in this browser. You can still type.");
        this.stop();
      }
    };
    recognition.onend = () => {
      this.listening = false;
      $("mic").classList.remove("recording");
      const text = heard.trim();
      inputEl.value = "";
      autoGrow();
      if (text) {
        this.silentRounds = 0;
        this.dictation = false;
        send(text, { voice: this.active });
      } else if (this.active && this.silentRounds < 3) {
        this.silentRounds += 1;
        setTimeout(() => this.listenAgain(), 250);  // nothing heard yet: keep listening
      } else {
        if (this.active) addMessage("system", "Voice chat paused because I didn't hear anything. Click the voice chat button to continue.");
        this.stop();
      }
    };

    $("mic").addEventListener("click", () => {
      if (this.listening) { recognition.stop(); return; }
      speaker.stop();
      this.dictation = true;
      $("mic").classList.add("recording");
      this.start();
    });
    $("voice-chat").addEventListener("click", () => (this.active ? this.stop() : this.begin()));
    // Click the glowing core to interrupt JARVIS while it speaks.
    coreEl.addEventListener("click", () => {
      if (speaker.busy()) {
        speaker.stop();
        if (this.active) this.listenAgain();
        else idleState();
      }
    });
  },

  begin() {
    this.active = true;
    this.silentRounds = 0;
    $("voice-chat").classList.add("active");
    $("voice-chat").setAttribute("aria-pressed", "true");
    addMessage("system", "Voice chat on. Speak after the core turns green. Click the voice chat button again to stop, or the core to interrupt.");
    this.start();
  },

  stop() {
    const wasActive = this.active;
    this.active = false;
    $("voice-chat").classList.remove("active");
    $("voice-chat").setAttribute("aria-pressed", "false");
    if (this.listening && this.recognition) this.recognition.abort();
    if (wasActive) speaker.stop();
    if (!busy) idleState();
  },

  start() {
    if (!this.recognition || this.listening || busy) return;
    try { this.recognition.start(); } catch (error) { /* already starting */ }
  },

  // After JARVIS has finished replying (and speaking), listen for the next question.
  listenAgain() {
    if (this.active && !busy && $("confirm").hidden && !speaker.busy()) this.start();
  },
};

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
  if (event.key !== "Escape") return;
  if (!$("confirm").hidden) answerApproval(false);
  else if (voice.active) voice.stop();
  else if (speaker.busy()) { speaker.stop(); idleState(); }
});

const speakBtn = $("speak");
speakBtn.setAttribute("aria-pressed", String(speakReplies));
if (!speaker.supported) speakBtn.hidden = true;
speakBtn.addEventListener("click", () => {
  speakReplies = !speakReplies;
  speakBtn.setAttribute("aria-pressed", String(speakReplies));
  saveSetting("jarvis-speak", speakReplies ? "1" : "0");
  if (!speakReplies && !voice.active) speaker.stop();
});

// ---------- reminders & scheduled emails (notifications from the scheduler) ----------

const notifications = {
  // Remember what was shown, per JARVIS run (the token changes every start).
  lastSeen() {
    const [token, id] = loadSetting("jarvis-note-seen", "").split(":");
    return token === TOKEN.slice(0, 12) ? Number(id) || 0 : 0;
  },
  markSeen(id) { saveSetting("jarvis-note-seen", `${TOKEN.slice(0, 12)}:${id}`); },

  beep() {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      [0, 0.25].forEach((delay) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.15, ctx.currentTime + delay);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + delay + 0.2);
        osc.connect(gain).connect(ctx.destination);
        osc.start(ctx.currentTime + delay);
        osc.stop(ctx.currentTime + delay + 0.2);
      });
    } catch (e) { /* sound blocked */ }
  },

  show(note) {
    const wrapper = addMessage("system", `**${note.title}** (${note.time}): ${note.text}`);
    wrapper.classList.add("alert", "alert-" + note.kind);
    wrapper.querySelector(".who").textContent = note.kind === "reminder" ? "REMINDER" : "SCHEDULED";
    this.beep();
    if (speaker.supported) speaker.say(note.kind === "reminder" ? `Reminder: ${note.text}` : `${note.title}.`);
    if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
      try { new Notification(`${NAME}: ${note.title}`, { body: note.text }); } catch (e) { /* not allowed */ }
    }
  },

  async poll() {
    try {
      const data = await api(`/api/notifications?after=${this.lastSeen()}`);
      for (const note of data.notifications || []) {
        this.show(note);
        this.markSeen(note.id);
      }
    } catch (e) { /* JARVIS restarting; try again next time */ }
  },

  // Desktop pop-ups need permission, which browsers only let a click ask for.
  askPermissionOnce() {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  },
};
document.addEventListener("click", () => notifications.askPermissionOnce(), { once: true });
setInterval(() => notifications.poll(), 10000);

// ---------- start ----------

(async function start() {
  voice.setup();
  voicePicker.setup();
  const data = await refreshStatus();
  if (!data) return;
  const name = data.name;
  if (data.brain_ok) {
    addMessage("jarvis", `${name} online. How can I help?`);
  } else {
    showSetupCard(true);
    const setup = await api("/api/setup").catch(() => null);
    if (setup && setup.state === "running") pollSetup();
    else if (setup && setup.state === "error") renderSetup(setup);
  }
  if (data.pending) {
    handleReply({ pending: data.pending, steps: [], text: "" });
  }
  notifications.poll();
  inputEl.focus();
})();
