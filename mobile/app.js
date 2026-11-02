/* Hybrid AI — phone app. Talks to the phone server inside the desktop app. */
"use strict";

const $ = (id) => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
};

const S = {
  token: null, convId: null, models: [], model: null, busy: false, abort: null,
  uploads: [],   // {id, name, kind, detail} uploaded and waiting to be sent
  attach: [],    // {link, name} files picked from /find results
};

// ---------------------------------------------------------------- pairing
function takeTokenFromUrl() {
  const m = location.hash.match(/token=([\w-]+)/);
  if (m) {
    store.set("hai_token", m[1]);
    history.replaceState(null, "", location.pathname + location.search);
  }
}

function showPairing(msg) {
  $("app").hidden = true;
  $("pair").hidden = false;
  if (msg) { $("pair-error").textContent = msg; $("pair-error").hidden = false; }
}

$("pair-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("pair-input").value.trim();
  const m = v.match(/token=([\w-]+)/);
  if (!m) { showPairing("That link has no token. Copy the whole pairing link."); return; }
  try {
    const u = new URL(v);
    if (u.origin !== location.origin) { location.href = v; return; }  // pairing link for another address
  } catch (err) { /* not a URL: token only */ }
  store.set("hai_token", m[1]);
  start();
});

$("unpair").addEventListener("click", () => {
  if (!confirm("Disconnect this phone from the laptop?")) return;
  store.del("hai_token");
  location.reload();
});

// ---------------------------------------------------------------- api
async function api(path, opts = {}) {
  const headers = Object.assign({ Authorization: "Bearer " + S.token }, opts.headers || {});
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (res.status === 401) { showPairing("This phone isn't paired (or was disconnected). Pair again."); throw new Error("unpaired"); }
  return res;
}

async function ping() {
  try {
    const r = await api("/api/ping");
    const d = await r.json();
    $("conn-dot").className = "dot ok";
    $("conn-text").textContent = d.busy ? `${d.host} · busy` : `${d.host} · online`;
    $("host").textContent = d.host;
    S.models = d.models;
    S.cloudFiles = d.cloud_files;
    checkReminders();
    if (!S.model) S.model = store.get("hai_model") || d.model;
    if (!S.models.some((m) => m.key === S.model)) S.model = d.model;
    showModel();
    return true;
  } catch (e) {
    if (e.message !== "unpaired") {
      $("conn-dot").className = "dot err";
      $("conn-text").textContent = "laptop offline";
    }
    return false;
  }
}

// ---------------------------------------------------------------- models
function modelInfo(key) { return S.models.find((m) => m.key === key) || { name: key, where: "local" }; }

function showModel() {
  const m = modelInfo(S.model);
  const b = $("model-btn");
  b.textContent = m.name;
  b.className = "model " + (m.where === "cloud" ? "cloud" : "local");
}

$("model-btn").addEventListener("click", () => {
  const list = $("model-list");
  list.innerHTML = "";
  for (const m of S.models) {
    const b = document.createElement("button");
    b.className = "model-opt" + (m.key === S.model ? " sel" : "");
    b.innerHTML = `<span>${esc(m.name)}</span><span class="place ${m.where}">${m.where === "cloud" ? "Cloud" : "On laptop"}</span>`;
    b.onclick = () => { S.model = m.key; store.set("hai_model", m.key); showModel(); $("sheet").hidden = true; };
    list.appendChild(b);
  }
  $("sheet").hidden = false;
});
$("sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") $("sheet").hidden = true; });

// ---------------------------------------------------------------- markdown (small and safe)
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function safeUrl(u) {
  if (u.startsWith("/api/file?") || /^https?:\/\//i.test(u)) return u;
  return null;
}

function inline(s) {
  // s is already escaped
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, url) => {
    const u = safeUrl(url.replace(/&amp;/g, "&"));
    return u ? `<img alt="${alt}" src="${esc(u)}" loading="lazy">` : m;
  });
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) => {
    const raw = url.replace(/&amp;/g, "&");
    if (raw.startsWith("attach:")) {
      return `<button class="ask-file" data-attach="${esc(raw)}">${text === "ask about it" ? "Ask about it" : text}</button>`;
    }
    const u = safeUrl(raw);
    if (!u) return text;
    const ext = /^https?:/i.test(u) ? ' target="_blank" rel="noopener noreferrer"' : "";
    return `<a href="${esc(u)}"${ext}>${text}</a>`;
  });
  return s;
}

function render(md) {
  const out = [];
  const lines = String(md).split("\n");
  let i = 0, list = null, para = [];
  const flushPara = () => { if (para.length) { out.push("<p>" + para.map((l) => inline(esc(l))).join("<br>") + "</p>"); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>` + list.items.map((x) => "<li>" + x + "</li>").join("") + `</${list.tag}>`); list = null; } };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*```\s*([\w+#.:-]*)/);
    if (fence) {
      flushPara(); flushList();
      const lang = fence[1] || "";
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++;
      out.push(`<pre><div class="code-head"><span>${esc(lang)}</span><button data-copy>Copy</button></div><code>${esc(code.join("\n"))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    const li = line.match(/^\s*[-*•]\s+(.*)/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (h) { flushPara(); flushList(); out.push("<h3>" + inline(esc(h[2])) + "</h3>"); }
    else if (li || ol) {
      flushPara();
      const tag = li ? "ul" : "ol";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push(inline(esc((li || ol)[1])));
    } else if (/^\s{2,}\S/.test(line) && list) {
      list.items[list.items.length - 1] += "<br>" + inline(esc(line.trim()));
    } else if (!line.trim()) { flushPara(); flushList(); }
    else { flushList(); para.push(line.replace(/\s{2}$/, "")); }
    i++;
  }
  flushPara(); flushList();
  return out.join("");
}

// ---------------------------------------------------------------- messages
const KIND = { image: "IMG", pdf: "PDF", docx: "DOC", xlsx: "XLS", notebook: "NB", code: "</>", text: "TXT" };

function clearMessages() { $("messages").innerHTML = ""; $("empty").hidden = false; }

function addUser(text, atts) {
  $("empty").hidden = true;
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = (text ? `<div class="bubble">${esc(text)}</div>` : "") +
    (atts && atts.length ? `<div class="msg-atts">${atts.map((a) => `<span class="att"><b>${KIND[a.kind] || "FILE"}</b><span>${esc(a.name)}</span></span>`).join("")}</div>` : "");
  $("messages").appendChild(el);
  scrollDown(true);
}

function addAssistant() {
  $("empty").hidden = true;
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = `<div class="body"><span class="typing"><i></i><i></i><i></i></span></div><div class="meta"></div>`;
  $("messages").appendChild(el);
  scrollDown(true);
  return el;
}

function setMeta(el, where, label, meta, stopped, memory) {
  const badge = where === "cloud" ? "cloud" : where === "laptop" ? "laptop" : "local";
  const word = stopped ? "Stopped" : where === "cloud" ? "Cloud" : where === "laptop" ? "Laptop" : "Local";
  el.querySelector(".meta").innerHTML = `<span class="badge ${badge}">${word}</span><span>${esc(meta || label || "")}</span>`;
  if (memory && memory.length) {
    const m = document.createElement("div");
    m.className = "mem-chip";
    m.innerHTML = "◷ Used memory: " + memory.map((x) => `<span>${esc(x.title)}</span>`).join(", ");
    el.appendChild(m);
  }
}

function nearBottom() { const m = $("messages"); return m.scrollHeight - m.scrollTop - m.clientHeight < 120; }
function scrollDown(force) { const m = $("messages"); if (force || nearBottom()) m.scrollTop = m.scrollHeight; }

$("messages").addEventListener("click", (e) => {
  const copy = e.target.closest("[data-copy]");
  if (copy) {
    const code = copy.closest("pre").querySelector("code").textContent;
    navigator.clipboard && navigator.clipboard.writeText(code).then(() => toast("Copied"));
    return;
  }
  const att = e.target.closest("[data-attach]");
  if (att) {
    const link = att.getAttribute("data-attach");
    const name = decodeURIComponent(((link.match(/p=([^&]+)/) || [, "file"])[1]).replace(/\+/g, " ")).split(/[\\/]/).pop();
    if (!S.attach.some((a) => a.link === link)) S.attach.push({ link, name });
    showAttachChips();
    $("input").focus();
    toast(`${name} attached — ask your question`);
  }
});

// ---------------------------------------------------------------- attachments
function showAttachChips() {
  const box = $("attach-chips");
  const items = S.uploads.map((u, i) => ({ ...u, i, t: "u" })).concat(S.attach.map((a, i) => ({ name: a.name, kind: "file", i, t: "a" })));
  box.hidden = !items.length;
  box.innerHTML = items.map((x) =>
    `<span class="att${x.busy ? " busy" : ""}"><b>${KIND[x.kind] || "FILE"}</b><span>${esc(x.name)}${x.busy ? " · uploading…" : ""}</span>` +
    (x.busy ? "" : `<button data-rm="${x.t}${x.i}" aria-label="Remove">✕</button>`) + `</span>`).join("");
}

$("attach-chips").addEventListener("click", (e) => {
  const b = e.target.closest("[data-rm]");
  if (!b) return;
  const key = b.getAttribute("data-rm");
  const idx = Number(key.slice(1));
  if (key[0] === "u") S.uploads.splice(idx, 1); else S.attach.splice(idx, 1);
  showAttachChips();
});

$("attach-btn").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", async (e) => {
  for (const f of e.target.files) await upload(f);
  e.target.value = "";
});

async function upload(file) {
  const entry = { name: file.name || "file", kind: (file.type || "").startsWith("image/") ? "image" : "file", busy: true };
  S.uploads.push(entry);
  showAttachChips();
  try {
    const r = await api("/api/upload", { method: "POST", body: file, headers: { "X-File-Name": encodeURIComponent(entry.name) } });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "upload failed");
    Object.assign(entry, d, { busy: false });
  } catch (err) {
    S.uploads.splice(S.uploads.indexOf(entry), 1);
    if (err.message !== "unpaired") toast(`Couldn't send ${entry.name}: ${err.message}`);
  }
  showAttachChips();
}

// ---------------------------------------------------------------- sending
async function send(text, confirmCloud = false) {
  text = (text || "").trim();
  if (S.busy) return;
  if (S.uploads.some((u) => u.busy)) { toast("Wait for the upload to finish"); return; }
  if (!text && !S.uploads.length && !S.attach.length) return;
  const uploads = S.uploads.slice(), attach = S.attach.slice();
  const body = { conv_id: S.convId, text, model: S.model, uploads: uploads.map((u) => u.id),
                 attach: attach.map((a) => a.link), confirm_cloud: confirmCloud };
  S.busy = true;
  setBusy(true);
  const ctl = new AbortController();
  S.abort = ctl;
  let res;
  try {
    res = await api("/api/chat", { method: "POST", body: JSON.stringify(body), signal: ctl.signal,
                                   headers: { "Content-Type": "application/json" } });
  } catch (e) {
    S.busy = false; setBusy(false);
    if (e.message !== "unpaired") toast("Can't reach the laptop. Is the desktop app open?");
    return;
  }
  if (res.status === 428) {
    const d = await res.json();
    S.busy = false; setBusy(false);
    if (confirm(`Your attachments will be sent to ${d.provider} (a cloud model). Send anyway?`)) send(text, true);
    return;
  }
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    S.busy = false; setBusy(false);
    toast(d.error || `Error ${res.status}`);
    return;
  }
  // accepted: clear the composer
  $("input").value = ""; autosize();
  S.uploads = []; S.attach = []; showAttachChips();
  addUser(text, uploads.concat(attach.map((a) => ({ name: a.name, kind: "file" }))));
  const el = addAssistant();
  const bodyEl = el.querySelector(".body");
  let answer = "", started = false;
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, k); buf = buf.slice(k + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.type === "conv") { S.convId = ev.id; $("title").textContent = ev.title; store.set("hai_conv", ev.id); }
        else if (ev.type === "notice") { const n = document.createElement("div"); n.className = "notice"; n.textContent = "⚠ " + ev.text; el.prepend(n); }
        else if (ev.type === "route") { el.querySelector(".meta").innerHTML = `<span>${esc(ev.label)} · thinking…</span>`; }
        else if (ev.type === "token") {
          answer += ev.text;
          if (!started) { started = true; }
          bodyEl.innerHTML = render(answer);
          bodyEl.classList.add("caret");
          scrollDown(false);
        } else if (ev.type === "done") {
          bodyEl.classList.remove("caret");
          bodyEl.innerHTML = render(answer) || "<p class='muted'>(no answer)</p>";
          setMeta(el, ev.where, ev.label, ev.meta, ev.stopped, ev.memory);
        }
      }
    }
  } catch (e) {
    bodyEl.classList.remove("caret");
    if (e.name === "AbortError") setMeta(el, "local", "", "Stopped", true);
    else setMeta(el, "local", "", "Connection lost — the reply may still be saved on the laptop", true);
  }
  S.busy = false; S.abort = null; setBusy(false);
}

function setBusy(on) {
  $("send-btn").hidden = on;
  $("stop-btn").hidden = !on;
}

$("stop-btn").addEventListener("click", () => { if (S.abort) S.abort.abort(); });
$("form").addEventListener("submit", (e) => { e.preventDefault(); send($("input").value); });
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && window.matchMedia("(pointer: fine)").matches) { e.preventDefault(); send($("input").value); }
});
function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 140) + "px"; }
$("input").addEventListener("input", autosize);

document.addEventListener("click", (e) => {
  const s = e.target.closest("[data-send]");
  if (s) { send(s.getAttribute("data-send")); return; }
  const f = e.target.closest("[data-fill]");
  if (f) { $("input").value = f.getAttribute("data-fill"); autosize(); $("input").focus(); }
});

// ---------------------------------------------------------------- chats + memory drawer
function openDrawer(open) {
  $("drawer").classList.toggle("open", open);
  $("drawer").setAttribute("aria-hidden", String(!open));
  $("scrim").hidden = !open;
  if (open) { loadChats(); loadMemory(); }
}
$("menu-btn").addEventListener("click", () => openDrawer(true));
$("scrim").addEventListener("click", () => openDrawer(false));

async function loadChats() {
  const list = $("chat-list");
  try {
    const items = await (await api("/api/conversations")).json();
    list.innerHTML = items.length ? "" : `<div class="list-empty">No chats yet.</div>`;
    for (const c of items) {
      const row = document.createElement("div");
      row.className = "chat-item" + (c.id === S.convId ? " active" : "");
      row.innerHTML = `<button class="open"><div class="t">${esc(c.title)}</div><div class="l">${esc((c.last || "").replace(/[*#`>_]|\[|\]\([^)]*\)/g, "").slice(0, 80))}</div></button>
        <button class="del" aria-label="Delete"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button>`;
      row.querySelector(".open").onclick = () => { openChat(c.id); openDrawer(false); };
      row.querySelector(".del").onclick = async () => {
        if (!confirm(`Delete “${c.title}”?`)) return;
        await api("/api/conversations/" + c.id, { method: "DELETE" });
        if (c.id === S.convId) newChat();
        loadChats();
      };
      list.appendChild(row);
    }
  } catch (e) { list.innerHTML = `<div class="list-empty">Can't load chats.</div>`; }
}

async function loadMemory() {
  const list = $("mem-list");
  try {
    const items = await (await api("/api/memory")).json();
    list.innerHTML = items.length ? "" : `<div class="list-empty">Finished chats are summarised here.</div>`;
    for (const m of items) {
      const row = document.createElement("div");
      row.className = "mem-item";
      row.innerHTML = `<div class="t">◷ ${esc(m.title)}</div>`;
      row.onclick = () => {
        const open = row.classList.toggle("open");
        if (open && !row.querySelector("ul")) row.insertAdjacentHTML("beforeend", "<ul>" + m.bullets.map((b) => "<li>" + esc(b) + "</li>").join("") + "</ul>");
        else if (!open) { const u = row.querySelector("ul"); if (u) u.remove(); }
      };
      list.appendChild(row);
    }
  } catch (e) { list.innerHTML = ""; }
}

async function openChat(id) {
  const r = await api("/api/conversations/" + id);
  if (!r.ok) { toast("That chat is gone (it may have been summarised into memory)."); newChat(); return; }
  const c = await r.json();
  S.convId = c.id; store.set("hai_conv", c.id);
  $("title").textContent = c.title;
  clearMessages();
  for (const t of c.turns) {
    if (t.role === "user") addUser(t.text, t.attachments);
    else if (t.role === "assistant") {
      const el = addAssistant();
      el.querySelector(".body").innerHTML = render(t.text);
      const where = (t.route || "").startsWith("cloud") ? "cloud" : t.route === "laptop" ? "laptop" : "local";
      setMeta(el, where, "", t.meta, t.stopped, t.memory);
    } else if (t.role === "note") {
      const n = document.createElement("div"); n.className = "notice"; n.textContent = "◷ " + t.text;
      $("messages").appendChild(n);
    }
  }
  scrollDown(true);
}

function newChat() {
  S.convId = null; store.del("hai_conv");
  $("title").textContent = "New conversation";
  clearMessages();
}
$("new-btn").addEventListener("click", newChat);
$("drawer-new").addEventListener("click", () => { newChat(); openDrawer(false); });

// ---------------------------------------------------------------- shared from other apps
async function takeShared() {
  if (!/[?&]shared=1/.test(location.search) || !("caches" in window)) return;
  history.replaceState(null, "", location.pathname);
  try {
    const cache = await caches.open("hai-share");
    const keys = await cache.keys();
    let text = "";
    for (const req of keys) {
      const res = await cache.match(req);
      if (req.url.endsWith("/shared-text")) {
        const d = await res.json();
        text = [d.title, d.text, d.url].filter(Boolean).join("\n");
      } else {
        const blob = await res.blob();
        const name = decodeURIComponent(res.headers.get("X-File-Name") || "shared");
        await upload(new File([blob], name, { type: blob.type }));
      }
      await cache.delete(req);
    }
    newChat();
    $("input").value = text ? "Summarise this:\n" + text : "Summarise this";
    autosize();
    toast("Shared to your laptop — edit the request and send");
  } catch (e) { toast("Couldn't read what was shared"); }
}

// ---------------------------------------------------------------- reminders -> notifications
async function checkReminders() {
  try {
    const d = await (await api("/api/reminders")).json();
    let seen = [];
    try { seen = JSON.parse(store.get("hai_seen") || "[]"); } catch (e) { seen = []; }
    for (const f of d.fired) {
      if (seen.includes(f.id)) continue;
      seen.push(f.id);
      toast("⏰ " + f.text);
      if ("Notification" in window && Notification.permission === "granted" && navigator.serviceWorker) {
        const reg = await navigator.serviceWorker.getRegistration();
        if (reg) reg.showNotification("Reminder", { body: f.text, icon: "icon-192.png", tag: f.id });
      }
    }
    store.set("hai_seen", JSON.stringify(seen.slice(-50)));
  } catch (e) { /* laptop offline */ }
}

$("notify-btn").addEventListener("click", async () => {
  if (!("Notification" in window)) { toast("Notifications need the installed app (HTTPS via Tailscale)."); return; }
  const p = await Notification.requestPermission();
  toast(p === "granted" ? "Reminders will show as notifications while the app is open" : "Notifications are off");
});

// ---------------------------------------------------------------- misc
let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3500);
}

async function start() {
  S.token = store.get("hai_token");
  if (!S.token) { showPairing(); return; }
  started = true;
  $("pair").hidden = true;
  $("app").hidden = false;
  const ok = await ping();
  if (ok && store.get("hai_conv")) openChat(store.get("hai_conv")).catch(() => newChat());
  takeShared();
  setInterval(ping, 20000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) ping(); });
}

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* still works without it */ });
}
let started = false;
window.addEventListener("hashchange", () => {
  // a pairing link opened while the app is already open
  if (/token=/.test(location.hash)) { takeTokenFromUrl(); if (!started) start(); else location.reload(); }
});
takeTokenFromUrl();
start();
