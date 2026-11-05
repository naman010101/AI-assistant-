"""Phone bridge: lets your Android app use this laptop's assistant.

It runs inside the desktop app as a background thread (Settings → Phone →
"Let my phone connect"). The phone app is served from the mobile/ folder.

What the phone gets
  - The same chats (chats.db), memory, models and access rules as the
    desktop. A chat started on the phone appears in the desktop sidebar.
  - Streaming replies (Server-Sent Events); Stop on the phone stops the laptop.
  - Laptop commands from tools.py (/find, /screenshot, /web, /status).
  - Uploads: photos and files from the phone, used like attachments.

Security
  - Every API call needs the pairing token (Authorization: Bearer …), kept in
    the private data folder. "Reset phone token" in Settings makes a new one.
  - Download/attach links and upload ids are signed with the token and expire
    after LINK_TTL seconds.
  - Files are only downloadable through links this server signed, and only
    if Settings → Access still allows them.
  - The pairing page (/pair) only opens on the laptop itself.
  - Reach it over your Wi-Fi or Tailscale; never open a router port to the internet.

Only the Python standard library is used.
"""
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import queue
import re
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import attachments
import config
import privacy
import reminders
import router
import tools
from storage import Store

log = logging.getLogger("hybridai")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MOBILE_DIR = os.path.join(BASE_DIR, "mobile")
TOKEN_FILE = os.path.join(config.DATA_DIR, "phone_token.txt")
UPLOAD_DIR = os.path.join(config.DATA_DIR, "phone_uploads")
SHOTS_DIR = os.path.join(config.DATA_DIR, "phone_shots")
LINK_TTL = 3600  # seconds a signed link or upload id stays valid
MAX_UPLOAD = 25 * 1024 * 1024
MODEL_NAMES = {"local-general": "Llama 3.1 8B", "local-coder": "Qwen Coder 7B", "local-vision": "Qwen 2.5 VL 7B",
               "cloud-gemini": "Gemini 3.8 Flash", "cloud-groq": "Groq · GPT-OSS 120B", "laptop": "Laptop"}

for ext, ctype in ((".js", "text/javascript"), (".css", "text/css"), (".webmanifest", "application/manifest+json"),
                   (".svg", "image/svg+xml"), (".png", "image/png"), (".json", "application/json")):
    mimetypes.add_type(ctype, ext)  # Windows' registry sometimes maps .js to text/plain


def load_token(new=False):
    token = "" if new else (open(TOKEN_FILE, encoding="utf-8").read().strip() if os.path.exists(TOKEN_FILE) else "")
    if not token:
        token = secrets.token_urlsafe(24)
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        tmp = TOKEN_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(token)
        os.replace(tmp, TOKEN_FILE)  # atomic: never a half-written token
    return token


def label(route_name):
    base = route_name.split(" ")[0]
    return MODEL_NAMES.get(base, base) + route_name[len(base):]


def place(route_name):
    return "cloud" if route_name.startswith("cloud") else "laptop" if route_name == "laptop" else "local"


def local_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError as _exc:
        log.warning("couldn't look up this laptop's IP addresses: %s", _exc)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError as _exc:
        log.warning("couldn't find the LAN IP address: %s", _exc)
    ips.discard("127.0.0.1")
    return sorted(ips, key=lambda ip: (not ip.startswith("100."), ip))  # Tailscale (100.x) first


def new_title(text):
    text = " ".join(text.split())
    return text if len(text) <= 34 else text[:33].rstrip() + "…"


class PhoneServer:
    """host must provide: settings, gate, memory_system(query, model) ->
    (system, used), desktop_busy() -> bool, notify(event tuple), ollama_models."""

    def __init__(self, host, port=8765, lan=True, db_path=None):
        self.host, self.port, self.lan = host, port, lan
        self.token = load_token()
        self.store = Store(db_path or config.DB_PATH, upgrade=False)  # only the desktop app upgrades (1.5)
        self.busy = threading.Lock()          # one phone reply at a time
        self.busy_conv = None
        self.httpd = None
        self.thread = None
        self.error = None

    # ---------- lifecycle ----------
    def start(self):
        handler = type("H", (Handler,), {"srv": self})
        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0" if self.lan else "127.0.0.1", self.port), handler)
        except OSError as e:
            self.error = f"port {self.port} is in use ({e.strerror or e})"
            return False
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.store.close()

    def reset_token(self):
        self.token = load_token(new=True)
        return self.token

    def links(self):
        out = [f"http://{ip}:{self.port}/#token={self.token}" for ip in (local_ips() if self.lan else [])]
        public = (self.host.settings.get("phone", {}) or {}).get("public_url", "").rstrip("/")
        if public:
            out.insert(0, f"{public}/#token={self.token}")
        return out

    # ---------- signing ----------
    def sign(self, path, kind, exp):
        msg = f"v2|{kind}|{exp}|{path}"
        return hmac.new(self.token.encode(), msg.encode("utf-8"), hashlib.sha256).hexdigest()

    def make_sig(self, path, kind, now=None):
        exp = int((time.time() if now is None else now) + LINK_TTL)
        return exp, self.sign(path, kind, exp)

    def check_link(self, path, kind, exp, sig, now=None):
        """"ok", "expired" or "invalid". The signature is checked before the time."""
        exp = str(exp)
        if not (exp.isascii() and exp.isdigit()):
            return "invalid"
        if not hmac.compare_digest(self.sign(path, kind, exp), str(sig)):
            return "invalid"
        return "expired" if (time.time() if now is None else now) > int(exp) else "ok"

    def file_link(self, path):
        path = os.path.abspath(path)
        exp, sig = self.make_sig(path, "f")
        return "/api/file?" + urllib.parse.urlencode({"p": path, "e": exp, "s": sig})

    def ask_link(self, path):
        path = os.path.abspath(path)
        exp, sig = self.make_sig(path, "a")
        return "attach:" + urllib.parse.urlencode({"p": path, "e": exp, "s": sig})

    def file_allowed(self, path):
        """Downloads: our own shots/uploads, or files Access allows right now."""
        p = os.path.normcase(os.path.abspath(path))
        for own in (SHOTS_DIR, UPLOAD_DIR):
            if p.startswith(os.path.normcase(os.path.abspath(own)) + os.sep):
                return True
        ok, _ = self.host.gate.check(path, "read")
        return ok


class _Ctx:
    def __init__(self, srv):
        self.host = srv.host
        self.link, self.ask_link = srv.file_link, srv.ask_link
        self.gate, self.settings, self.shots_dir = srv.host.gate, srv.host.settings, SHOTS_DIR


class Handler(BaseHTTPRequestHandler):
    server_version = "HybridAI"
    srv = None  # set on the subclass

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # ---------- plumbing ----------
    def _send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authed(self):
        auth = self.headers.get("Authorization", "")
        given = auth[7:] if auth.startswith("Bearer ") else ""
        return bool(given) and hmac.compare_digest(given.encode(), self.srv.token.encode())

    def _local_request(self):
        """From the laptop itself, not proxied (tailscale serve proxies from 127.0.0.1)."""
        proxied = any(h in self.headers for h in ("X-Forwarded-For", "Forwarded", "Tailscale-User-Login",
                                                   "X-Forwarded-Host"))
        return self.client_address[0] in ("127.0.0.1", "::1") and not proxied

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("too large")
        return json.loads(self.rfile.read(n) or b"{}")

    # ---------- routes ----------
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        if path == "/pair":
            return self._pair()
        if path == "/api/file":
            return self._file(qs)
        if path.startswith("/api/"):
            if not self._authed():
                return self._send(401, {"error": "not paired"})
            if path == "/api/ping":
                st = self.srv.host.settings
                return self._send(200, {
                    "ok": True, "host": socket.gethostname(), "busy": self.srv.busy.locked(),
                    "model": st.get("model"),
                    "models": [{"key": k, "name": label(k), "where": place(k)} for k in router.MODELS],
                    "cloud_files": st.get("cloud_files", "ask")})
            if path == "/api/conversations":
                return self._send(200, self.srv.store.list())
            if path.startswith("/api/conversations/"):
                conv = self.srv.store.get(path.rsplit("/", 1)[1])
                if not conv:
                    return self._send(404, {"error": "no such chat (it may have been summarised into memory)"})
                return self._send(200, {k: conv[k] for k in ("id", "title", "turns")})
            if path == "/api/reminders":
                rem = getattr(self.srv.host, "rem", None)
                if not rem:
                    return self._send(200, {"upcoming": [], "fired": []})
                now = time.time()
                items = rem.upcoming()
                return self._send(200, {
                    "upcoming": [{"id": r["id"], "text": r["text"], "due": r["due"],
                                  "when": reminders.describe(r["due_dt"], r["repeat"])} for r in items[:30]],
                    "fired": [{"id": f"{r['id']}:{int(r['fired'])}", "text": r["text"]} for r in items
                              if r.get("fired") and now - r["fired"] < 20 * 60]})
            if path == "/api/memory":
                mem = getattr(self.srv.host, "mem", None)
                if not mem:
                    return self._send(200, [])
                return self._send(200, [{"id": s["id"], "title": s["title"], "bullets": s["bullets"],
                                         "created": s["created"]} for s in mem.summaries(limit=40)])
            return self._send(404, {"error": "unknown endpoint"})
        return self._static(path)

    do_HEAD = do_GET

    def do_DELETE(self):
        if not self._authed():
            return self._send(401, {"error": "not paired"})
        if self.path.startswith("/api/conversations/"):
            cid = self.path.rsplit("/", 1)[1]
            self.srv.store.delete(cid)
            self.srv.host.notify(("phone_deleted", cid))
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "unknown endpoint"})

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"error": "not paired"})
        if self.path.startswith("/api/upload"):
            return self._upload()
        if self.path != "/api/chat":
            return self._send(404, {"error": "unknown endpoint"})
        try:
            body = self._json()
        except Exception:
            return self._send(400, {"error": "invalid request"})
        text = str(body.get("text", "")).strip()
        if not text and not body.get("uploads") and not body.get("attach"):
            return self._send(400, {"error": "empty message"})
        if self.srv.host.desktop_busy():
            return self._send(409, {"error": "The laptop is answering on the desktop right now. Try again in a moment."})
        if not self.srv.busy.acquire(blocking=False):
            return self._send(409, {"error": "The laptop is still answering another phone message."})
        try:
            self._chat(body, text)
        finally:
            self.srv.busy_conv = None
            self.srv.busy.release()

    # ---------- uploads ----------
    def _upload(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_UPLOAD:
            return self._send(413, {"error": f"files up to {MAX_UPLOAD // 1024 // 1024} MB"})
        name = urllib.parse.unquote(self.headers.get("X-File-Name") or "upload.bin")
        name = re.sub(r"[^\w.\- ()]+", "_", os.path.basename(name))[:120] or "upload.bin"
        day = os.path.join(UPLOAD_DIR, time.strftime("%Y-%m-%d"))
        os.makedirs(day, exist_ok=True)
        path = os.path.join(day, f"{time.strftime('%H%M%S')}_{name}")
        remaining = n
        with open(path, "wb") as f:
            while remaining:
                chunk = self.rfile.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        try:
            att = attachments.load_file(path, source="phone")
        except attachments.AttachError as e:
            os.remove(path)
            return self._send(415, {"error": str(e)})
        exp, sig = self.srv.make_sig(path, "u")
        return self._send(200, {"id": f"{sig}:{exp}:{os.path.basename(path)}",
                                "name": name, "kind": att.kind, "detail": att.detail})

    def _resolve_upload(self, uid):
        sig, _, rest = uid.partition(":")
        exp, _, base = rest.partition(":")
        for day in sorted(os.listdir(UPLOAD_DIR), reverse=True) if os.path.isdir(UPLOAD_DIR) else []:
            path = os.path.join(UPLOAD_DIR, day, base)
            if os.path.isfile(path) and self.srv.check_link(path, "u", exp, sig) == "ok":
                return path
        return None

    # ---------- chat (streamed as Server-Sent Events) ----------
    def _chat(self, body, text):
        srv, host = self.srv, self.srv.host
        st = host.settings
        conv = srv.store.get(body.get("conv_id") or "") or {
            "id": uuid.uuid4().hex, "title": None, "history": [], "turns": [], "created": None, "meta": {}}
        model = body.get("model") if body.get("model") in router.MODELS else st.get("model", router.DEFAULT_MODEL)
        if model not in router.MODELS:
            model = router.DEFAULT_MODEL

        # attachments: uploads from the phone + files picked from /find results
        atts, problems = [], []
        for uid in body.get("uploads") or []:
            path = self._resolve_upload(str(uid))
            if not path:
                problems.append("an upload expired")
                continue
            try:
                atts.append(attachments.load_file(path, source="phone"))
            except attachments.AttachError as e:
                problems.append(str(e))
        for link in body.get("attach") or []:
            q = urllib.parse.parse_qs(str(link).split(":", 1)[-1])
            path, sig = (q.get("p") or [""])[0], (q.get("s") or [""])[0]
            state = srv.check_link(path, "a", (q.get("e") or [""])[0], sig) if path else "invalid"
            if state != "ok":
                problems.append("a file link expired, run /find again" if state == "expired"
                                else "a file link wasn't valid")
                continue
            ok, reason = host.gate.check(path, "read")
            if not ok:
                problems.append(f"{os.path.basename(path)}: " + ("outside the allowed folders"
                                                                 if reason == "outside" else reason))
                continue
            try:
                atts.append(attachments.load_file(path, source="path"))
            except attachments.AttachError as e:
                problems.append(str(e))

        images = [a for a in atts if a.is_image]
        if images and model not in router.VISION_MODELS:
            model = "local-vision"
        if atts and model.startswith("cloud"):
            policy = st.get("cloud_files", "ask")
            if policy == "block" or (policy == "ask" and not body.get("confirm_cloud")):
                return self._send(428 if policy == "ask" else 403, {
                    "error": "confirm_cloud" if policy == "ask" else
                    "Files only go to local models (laptop setting). Pick a local model.",
                    "provider": "Google (Gemini)" if "gemini" in model else "Groq"})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        gone = threading.Event()

        def emit(ev):
            if gone.is_set():
                return
            try:
                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            except OSError:
                gone.set()

        if not conv["title"]:
            conv["title"] = new_title(text or (atts[0].name if atts else "Phone chat"))
        srv.busy_conv = conv["id"]
        emit({"type": "conv", "id": conv["id"], "title": conv["title"]})
        if problems:
            emit({"type": "notice", "text": "; ".join(problems)})
        content, notes = (attachments.build_message(text, atts, int(st.get("context_tokens", 8192)))
                          if atts else (None, []))
        conv["turns"].append({"role": "user", "text": text, "at": time.time(), "via": "phone",
                              "attachments": [a.meta() for a in atts], "notes": notes + problems})
        start = time.time()

        if reminders.is_reminder_request(text) and not atts and hasattr(host, "add_reminder_text"):
            emit({"type": "route", "model": "laptop", "label": "Reminders", "where": "laptop"})
            ok, answer = host.add_reminder_text(text, "phone")
            if ok:
                answer += "\nYou'll get it on the laptop, and here while this app is open."
            emit({"type": "token", "text": answer})
            route_name, used, stopped, meta = "laptop", [], False, "Reminders"
        elif text.startswith("/") and not atts:
            emit({"type": "route", "model": "laptop", "label": "Laptop", "where": "laptop"})
            answer = tools.run(text, _Ctx(srv))
            emit({"type": "token", "text": answer})
            route_name, used, stopped = "laptop", [], False
            meta = f"Laptop · {time.time() - start:.1f} s"
        else:
            system, used = None, []
            try:
                system, used = host.memory_system(text or " ".join(a.name for a in atts), model)
            except Exception:
                log.exception("couldn't build the memory context for a phone chat")
            events = queue.Queue()
            first = {"t": None}

            def work():
                try:
                    router.stream_chat(
                        conv["history"], text, "manual",
                        on_token=lambda t: events.put(("tok", t)),
                        on_route=lambda r, why: events.put(("route", r)),
                        on_done=lambda r, why, ms: events.put(("done", r, ms)),
                        model_override=model, should_stop=gone.is_set, content=content, system=system,
                        mask_cloud=bool(st.get("privacy_filter", True)),
                        on_masked=lambda r: events.put(("masked", r)),
                        fit_args={"budget": int(st.get("context_tokens", 8192)),
                                  "digest": (conv.get("meta") or {}).get("digest"),
                                  "digest_upto": (conv.get("meta") or {}).get("digest_upto", 0)})
                except Exception as e:
                    events.put(("tok", f"\n\nError: {e}"))
                    events.put(("done", model, 0))

            threading.Thread(target=work, daemon=True).start()
            answer, route_name, elapsed = "", model, 0
            while True:
                try:
                    ev = events.get(timeout=15)
                except queue.Empty:
                    emit({"type": "ping"})  # keeps mobile networks from dropping the stream
                    continue
                if ev[0] == "tok":
                    if first["t"] is None and not router.is_fallback_notice(ev[1]):
                        first["t"] = time.time()
                    answer += ev[1]
                    emit({"type": "token", "text": ev[1]})
                elif ev[0] == "route":
                    emit({"type": "route", "model": ev[1], "label": label(ev[1]), "where": place(ev[1])})
                elif ev[0] == "masked":
                    emit({"type": "notice", "text": "Masked before sending: " + privacy.describe(ev[1])})
                else:
                    route_name, elapsed = ev[1], ev[2]
                    break
            stopped = gone.is_set()
            parts = [label(route_name)]
            if first["t"]:
                parts.append(f"{first['t'] - start:.1f} s to first token")
            parts.append(f"{elapsed / 1000:.1f} s total")
            meta = " · ".join(parts)

        turn = {"role": "assistant", "text": answer, "route": route_name, "meta": meta, "stopped": stopped,
                "at": time.time()}
        if used:
            turn["memory"] = used
        conv["turns"].append(turn)
        srv.store.save(conv["id"], conv["title"], conv["history"], conv["turns"], conv["created"],
                       conv.get("meta"))
        host.notify(("phone_changed", conv["id"]))
        emit({"type": "done", "route": route_name, "label": label(route_name), "where": place(route_name),
              "meta": meta, "stopped": stopped, "memory": used})

    # ---------- files ----------
    def _file(self, qs):
        path, sig = (qs.get("p") or [""])[0], (qs.get("s") or [""])[0]
        state = self.srv.check_link(path, "f", (qs.get("e") or [""])[0], sig) if path else "invalid"
        if state == "expired":
            return self._send(403, {"error": "link expired - run the command again on the phone"})
        if state != "ok":
            return self._send(403, {"error": "link not valid"})
        if not os.path.isfile(path):
            return self._send(404, {"error": "file no longer exists"})
        if not self.srv.file_allowed(path):
            return self._send(403, {"error": "the laptop no longer allows this file (Settings → Access)"})
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        inline = ctype.startswith("image/") or ctype == "application/pdf"
        name = urllib.parse.quote(os.path.basename(path))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(os.path.getsize(path)))
        self.send_header("Content-Disposition", f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{name}")
        self.send_header("Cache-Control", "private, max-age=300")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            while chunk := f.read(256 * 1024):
                try:
                    self.wfile.write(chunk)
                except OSError:
                    return

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        if path == "/pair.html":
            return self._send(404, "Not found", "text/plain")
        root = os.path.abspath(MOBILE_DIR)
        full = os.path.abspath(os.path.join(root, path.lstrip("/")))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return self._send(404, "Not found", "text/plain")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        headers = {"Service-Worker-Allowed": "/"} if full.endswith("sw.js") else None
        self._send(200, data, ctype, headers)

    def _pair(self):
        if not self._local_request():
            return self._send(403, "Open this page on the laptop itself.", "text/plain")
        with open(os.path.join(MOBILE_DIR, "pair.html"), encoding="utf-8") as f:
            page = f.read().replace("__LINKS__", json.dumps(self.srv.links()))
        self._send(200, page, "text/html; charset=utf-8")
