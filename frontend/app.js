"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  sessionId: localStorage.getItem("clawmind.session") || newSessionId(),
  busy: false,
  status: null,
  lastNotification: Number(localStorage.getItem("clawmind.lastNotification") || 0),
};
localStorage.setItem("clawmind.session", state.sessionId);

function newSessionId() {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

// ---------- API ----------

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(path, options = {}) {
  const opts = { credentials: "same-origin", headers: {}, ...options };
  if (opts.body !== undefined && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new ApiError("Can't reach the ClawMind server. Is it still running?", 0);
  }
  if (res.status === 401) showLogin();
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    let message = (data && data.error) || `Request failed (${res.status})`;
    if (data && data.details && data.details.length) {
      message += " " + data.details.map((d) => `${d.field}: ${d.problem}`).join("; ");
    }
    throw new ApiError(message, res.status);
  }
  return data;
}

// ---------- small helpers ----------

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function toast(message, isError = false) {
  const node = el("div", "toast" + (isError ? " error" : ""), message);
  $("toasts").appendChild(node);
  setTimeout(() => node.remove(), isError ? 7000 : 5000);
}

function formatDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// Minimal, safe markdown: everything is escaped first, then a few patterns are formatted.
function inlineMarkdown(text) {
  let s = escapeHtml(text);
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
  return s;
}

function renderMarkdown(text) {
  const lines = String(text).split("\n");
  let html = "";
  let list = null;
  let paragraph = [];
  let code = null;

  const flushParagraph = () => {
    if (paragraph.length) html += `<p>${paragraph.map(inlineMarkdown).join("<br>")}</p>`;
    paragraph = [];
  };
  const closeList = () => {
    if (list) html += `</${list}>`;
    list = null;
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (code === null) {
        flushParagraph();
        closeList();
        code = [];
      } else {
        html += `<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`;
        code = null;
      }
      continue;
    }
    if (code !== null) {
      code.push(line);
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);

    if (heading) {
      flushParagraph();
      closeList();
      const level = Math.min(heading[1].length + 2, 6);
      html += `<h${level}>${inlineMarkdown(heading[2])}</h${level}>`;
    } else if (bullet || numbered) {
      flushParagraph();
      const type = bullet ? "ul" : "ol";
      if (list !== type) {
        closeList();
        html += `<${type}>`;
        list = type;
      }
      html += `<li>${inlineMarkdown((bullet || numbered)[1])}</li>`;
    } else if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      flushParagraph();
      closeList();
      html += "<hr>";
    } else if (!line.trim()) {
      flushParagraph();
      closeList();
    } else {
      closeList();
      paragraph.push(line);
    }
  }
  if (code !== null) html += `<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`;
  flushParagraph();
  closeList();
  return html;
}

// ---------- navigation ----------

const titles = { chat: "Chat", memory: "Memory", tasks: "Scheduled tasks", settings: "Settings" };

function showView(name) {
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  document.querySelectorAll("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $("view-title").textContent = titles[name];
  closeSidebar();
  if (name === "memory") loadMemories();
  if (name === "tasks") loadTasks();
  if (name === "settings") loadSettings();
  if (name === "chat") $("input").focus();
}

function closeSidebar() {
  $("sidebar").classList.remove("open");
  $("backdrop").classList.remove("show");
}

// ---------- chat ----------

function scrollToBottom() {
  const box = $("messages");
  box.scrollTop = box.scrollHeight;
}

function hideEmptyState() {
  $("empty-state").classList.add("hidden");
}

function addMessage(role, content, extra = {}) {
  hideEmptyState();
  const wrap = el("div", `msg ${role}`);
  const bubble = el("div", "bubble");

  if (role === "user") {
    bubble.textContent = content;
  } else {
    if (extra.plan && extra.plan.length) {
      const details = el("details", "plan");
      details.appendChild(el("summary", "", `Plan (${extra.plan.length} steps)`));
      const ol = el("ol");
      extra.plan.forEach((step) => ol.appendChild(el("li", "", step)));
      details.appendChild(ol);
      bubble.appendChild(details);
    }
    const body = el("div");
    body.innerHTML = renderMarkdown(content);
    bubble.appendChild(body);
  }
  wrap.appendChild(bubble);

  if (extra.tools && extra.tools.length) {
    const meta = el("div", "meta");
    meta.appendChild(el("span", "", "Tools:"));
    extra.tools.forEach((t) => meta.appendChild(el("span", "chip", t)));
    wrap.appendChild(meta);
  }

  if (extra.pending) {
    const actions = el("div", "confirm");
    const yes = el("button", "btn btn-primary btn-small", "Yes, continue");
    const no = el("button", "btn btn-small", "Cancel");
    yes.type = no.type = "button";
    const answer = async (approve) => {
      yes.disabled = no.disabled = true;
      try {
        const result = await api("/api/confirm", {
          method: "POST",
          body: { run_id: extra.pending.run_id, approve, session_id: state.sessionId },
        });
        actions.remove();
        addMessage("assistant", result.response, { tools: result.tools_used });
      } catch (err) {
        yes.disabled = no.disabled = false;
        toast(err.message, true);
      }
    };
    yes.addEventListener("click", () => answer(true));
    no.addEventListener("click", () => answer(false));
    actions.append(yes, no);
    bubble.appendChild(actions);
  }

  $("messages").appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function addActivity() {
  hideEmptyState();
  const wrap = el("div", "msg activity");
  const ul = el("ul");
  wrap.appendChild(ul);
  $("messages").appendChild(wrap);
  const update = (text) => {
    ul.querySelectorAll("li.current").forEach((li) => li.classList.replace("current", "done"));
    if (text === "Done.") return;
    ul.appendChild(el("li", "current", text));
    scrollToBottom();
  };
  update("Thinking...");
  return { update, remove: () => wrap.remove() };
}

function setBusy(busy) {
  state.busy = busy;
  $("send-btn").disabled = busy;
  $("input").disabled = busy;
  if (!busy) $("input").focus();
}

async function sendMessage(text) {
  if (state.busy || !text.trim()) return;
  setBusy(true);
  addMessage("user", text);
  const activity = addActivity();

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: state.sessionId }),
    });
    if (res.status === 401) {
      showLogin();
      throw new Error("Please log in first.");
    }
    if (!res.ok) {
      let message = `Request failed (${res.status})`;
      try {
        const data = await res.json();
        message = data.error || message;
      } catch {
        // keep the default message
      }
      throw new Error(message);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result = null;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.type === "activity") activity.update(event.text);
        if (event.type === "result") result = event;
        if (event.type === "error") throw new Error(event.error);
      }
    }
    activity.remove();
    if (!result) throw new Error("The server closed the connection before answering.");
    addMessage("assistant", result.response, {
      tools: result.tools_used,
      plan: result.plan,
      pending: result.pending_confirmation,
    });
  } catch (err) {
    activity.remove();
    addMessage("assistant", `⚠️ ${err.message}`);
  } finally {
    setBusy(false);
  }
}

async function loadHistory() {
  try {
    const data = await api(`/api/sessions/${state.sessionId}/messages`);
    data.messages.forEach((m) => addMessage(m.role, m.content, { tools: m.tools_used }));
  } catch (err) {
    if (err.status !== 401) toast(err.message, true);
  }
}

function newChat() {
  state.sessionId = newSessionId();
  localStorage.setItem("clawmind.session", state.sessionId);
  $("messages").querySelectorAll(".msg").forEach((m) => m.remove());
  $("empty-state").classList.remove("hidden");
  showView("chat");
}

// ---------- memory ----------

async function loadMemories() {
  const q = $("memory-search").value.trim();
  const list = $("memory-list");
  try {
    const data = await api("/api/memory" + (q ? `?q=${encodeURIComponent(q)}` : ""));
    list.innerHTML = "";
    if (!data.memories.length) {
      list.appendChild(el("li", "empty-note", q ? "No matching memories." : "No memories yet."));
      return;
    }
    data.memories.forEach((m) => {
      const li = el("li");
      const main = el("div", "item-main");
      main.appendChild(el("div", "", m.content));
      main.appendChild(el("div", "item-sub", `#${m.id} · ${formatDate(m.created_at)}`));
      const del = el("button", "btn btn-small btn-danger", "Delete");
      del.type = "button";
      del.addEventListener("click", async () => {
        if (!confirm(`Delete this memory?\n\n${m.content}`)) return;
        try {
          await api(`/api/memory/${m.id}`, { method: "DELETE" });
          loadMemories();
        } catch (err) {
          toast(err.message, true);
        }
      });
      li.append(main, del);
      list.appendChild(li);
    });
  } catch (err) {
    toast(err.message, true);
  }
}

// ---------- tasks ----------

async function loadTasks() {
  const list = $("task-list");
  try {
    const data = await api("/api/tasks");
    list.innerHTML = "";
    if (!data.tasks.length) {
      list.appendChild(el("li", "empty-note", "No scheduled tasks."));
      return;
    }
    data.tasks.forEach((t) => {
      const li = el("li");
      const main = el("div", "item-main");
      const title = el("div", "", t.title);
      title.appendChild(el("span", "badge", t.action === "agent" ? "agent task" : "reminder"));
      if (t.status !== "active") title.appendChild(el("span", "badge", t.status));
      main.appendChild(title);
      let sub = t.schedule;
      if (t.next_run) sub += ` · next: ${formatDate(t.next_run)}`;
      if (t.last_run) sub += ` · last ran: ${formatDate(t.last_run)}`;
      main.appendChild(el("div", "item-sub", sub));
      const del = el("button", "btn btn-small btn-danger", t.status === "active" ? "Cancel" : "Remove");
      del.type = "button";
      del.addEventListener("click", async () => {
        if (!confirm(`Delete the task "${t.title}"?`)) return;
        try {
          await api(`/api/tasks/${t.id}`, { method: "DELETE" });
          loadTasks();
        } catch (err) {
          toast(err.message, true);
        }
      });
      li.append(main, del);
      list.appendChild(li);
    });
  } catch (err) {
    toast(err.message, true);
  }
}

// ---------- settings ----------

function applyTheme(theme) {
  if (theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}

function updateNotifyStatus() {
  const btn = $("notify-btn");
  if (!("Notification" in window)) {
    $("notify-status").textContent = "This browser doesn't support desktop notifications.";
    btn.classList.add("hidden");
    return;
  }
  const labels = {
    granted: "Desktop notifications are on. Reminders also appear in the chat.",
    denied: "Notifications are blocked in your browser settings. Reminders still appear in the chat.",
    default: "Reminders appear in the chat. You can also get desktop notifications.",
  };
  $("notify-status").textContent = labels[Notification.permission];
  btn.classList.toggle("hidden", Notification.permission !== "default");
}

async function loadStatus() {
  try {
    state.status = await api("/api/status");
  } catch (err) {
    if (err.status !== 401) $("model-badge").textContent = "Server unreachable";
    return null;
  }
  const s = state.status;
  const badge = $("model-badge");
  badge.innerHTML = "";
  badge.appendChild(el("span", "dot " + (s.llm_configured ? "on" : "off")));
  badge.appendChild(document.createTextNode(s.llm_configured ? `Model: ${s.llm_model}` : "No AI model configured"));
  return s;
}

async function loadSettings() {
  updateNotifyStatus();
  const s = await loadStatus();
  if (!s) return;
  const rows = [
    ["AI model", s.llm_configured ? `${s.llm_model} (${s.llm_host})` : "Not configured — offline commands only"],
    ["Semantic memory", s.embeddings ? "Embeddings enabled" : "Keyword search"],
    ["Web search", s.search_provider],
    ["Python tool", s.python_tool ? "Enabled" : "Disabled"],
    ["Browser tool", s.browser_tool ? "Enabled" : "Disabled (ENABLE_BROWSER=false)"],
    ["Authentication", s.auth_enabled ? "Password required" : "Off (local-only mode)"],
    ["Agent limits", `${s.max_agent_steps} steps, ${s.max_tool_calls} tool calls`],
    ["Version", s.version],
  ];
  const dl = $("status-list");
  dl.innerHTML = "";
  rows.forEach(([k, v]) => dl.append(el("dt", "", k), el("dd", "", v)));

  const tools = $("tool-list");
  tools.innerHTML = "";
  s.tools.forEach((t) => {
    const li = el("li", t.enabled ? "" : "off");
    li.append(el("span", "", t.name), el("span", "badge", t.enabled ? t.permission : "disabled"));
    tools.appendChild(li);
  });
  $("logout-card").classList.toggle("hidden", !s.auth_enabled);
}

// ---------- notifications ----------

async function pollNotifications(initial = false) {
  try {
    const data = await api(`/api/notifications?after=${state.lastNotification}`);
    for (const n of data.notifications) {
      state.lastNotification = Math.max(state.lastNotification, n.id);
      if (initial) continue;
      if (n.session_id === state.sessionId) addMessage("notification", n.content);
      toast(n.content);
      if ("Notification" in window && Notification.permission === "granted") {
        new Notification("ClawMind", { body: n.content.slice(0, 200) });
      }
    }
    localStorage.setItem("clawmind.lastNotification", String(state.lastNotification));
  } catch {
    // the server may be restarting; try again on the next poll
  }
}

// ---------- login ----------

function showLogin() {
  $("login").classList.remove("hidden");
  $("login-password").focus();
}

async function checkAuth() {
  try {
    const data = await api("/api/auth");
    if (data.logged_in) return true;
  } catch {
    // fall through to the login form
  }
  showLogin();
  return false;
}

async function start() {
  loadHistory();
  loadStatus();
  await pollNotifications(state.lastNotification === 0);
  setInterval(() => pollNotifications(), 20000);
}

// ---------- wiring ----------

document.addEventListener("DOMContentLoaded", async () => {
  const theme = localStorage.getItem("clawmind.theme") || "auto";
  $("theme-select").value = theme;
  applyTheme(theme);

  $("theme-select").addEventListener("change", (e) => {
    localStorage.setItem("clawmind.theme", e.target.value);
    applyTheme(e.target.value);
  });

  document.querySelectorAll("#nav button").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  $("new-chat").addEventListener("click", newChat);
  $("menu-btn").addEventListener("click", () => {
    $("sidebar").classList.add("open");
    $("backdrop").classList.add("show");
  });
  $("backdrop").addEventListener("click", closeSidebar);

  const input = $("input");
  const autosize = () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  };
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("chat-form").requestSubmit();
    }
  });
  $("chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value;
    input.value = "";
    autosize();
    sendMessage(text);
  });
  $("examples").addEventListener("click", (e) => {
    if (e.target.tagName !== "BUTTON") return;
    input.value = e.target.textContent;
    autosize();
    input.focus();
  });

  $("memory-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const field = $("memory-input");
    try {
      await api("/api/memory", { method: "POST", body: { content: field.value } });
      field.value = "";
      toast("Memory saved.");
      loadMemories();
    } catch (err) {
      toast(err.message, true);
    }
  });
  let searchTimer;
  $("memory-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadMemories, 250);
  });

  $("task-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const task = await api("/api/tasks", {
        method: "POST",
        body: {
          title: $("task-title").value,
          when: $("task-when").value,
          action: $("task-action").value,
          session_id: state.sessionId,
        },
      });
      $("task-title").value = "";
      $("task-when").value = "";
      toast(`Scheduled: ${task.title} (${task.schedule})`);
      loadTasks();
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("notify-btn").addEventListener("click", async () => {
    await Notification.requestPermission();
    updateNotifyStatus();
  });

  $("logout-btn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: {} }).catch(() => {});
    location.reload();
  });

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("login-error").textContent = "";
    try {
      await api("/api/login", { method: "POST", body: { password: $("login-password").value } });
      $("login").classList.add("hidden");
      $("login-password").value = "";
      start();
    } catch (err) {
      $("login-error").textContent = err.message;
    }
  });

  if (await checkAuth()) start();
});
