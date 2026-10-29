"""Hybrid AI Assistant — desktop UI ("Premium Dark Workspace").

Threading model (the important part):
    Tkinter is single-threaded: only the main thread may touch widgets.
    The LLM call runs in a worker thread and pushes events into a
    queue.Queue. The main thread polls that queue every 40 ms with
    `after()`, drains everything that arrived, and updates the UI once per
    batch. That keeps the window responsive and avoids scheduling one
    Tk callback per token.

Privacy:
    Chats, settings and API keys live in the private data folder
    (%LOCALAPPDATA%\\HybridAI, see config.py), never in the project folder.
    Every file the assistant reads passes access.AccessGate first.
"""
import itertools
import json
import os
import queue
import threading
import time
import urllib.request
import uuid

import customtkinter as ctk

import config

# Move data that older versions kept in the project folder BEFORE anything
# reads it (router.py loads the API keys on import).
try:
    MIGRATION_NOTES = config.migrate_legacy()
except Exception as _e:  # never stop the app over this; report it in the log
    MIGRATION_NOTES = [f"could not move old data: {_e}"]

import attachments  # noqa: E402
import router  # noqa: E402
from access import AccessGate  # noqa: E402
from attachments import AttachError  # noqa: E402
from router import stream_chat  # noqa: E402
from settings import Settings  # noqa: E402
from storage import Store  # noqa: E402
from ui import dialogs  # noqa: E402
from ui.chat_view import ChatView  # noqa: E402
from ui.composer import Composer  # noqa: E402
from ui.inspector import Inspector  # noqa: E402
from ui.sidebar import Sidebar  # noqa: E402
from ui.theme import C, Fonts, model_label, where  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POLL_MS = 40
HEALTH_EVERY_MS = 60_000
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # bypass system proxies
DB_PATH = os.getenv("HYBRID_AI_DB", config.DB_PATH)
# Models the app needs; the vision model is optional (only for images).
REQUIRED_LOCAL_MODELS = [m for k, (m, c) in router.MODELS.items()
                         if c is router.local_client and k != "local-vision"]
VISION_KEY = "local-vision"
FILE_TYPES = [
    ("Supported files", " ".join("*" + e for e in sorted(attachments.TEXT_EXT | attachments.IMAGE_EXT |
                                                        {".pdf", ".docx", ".xlsx", ".ipynb"}))),
    ("Images", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"),
    ("Documents", "*.pdf *.docx *.xlsx *.txt *.md"),
    ("All files", "*.*"),
]


class Conversation:
    def __init__(self, conv_id=None, title="New conversation", history=None, turns=None,
                 created=None):
        self.id = conv_id or uuid.uuid4().hex
        self.title = title
        self.history = history or []  # the list router.stream_chat reads and appends to
        self.turns = turns or []      # what the UI shows: dicts with role, text, route, meta, stopped
        self.created = created        # set by the store; None until first saved


class HybridAIApp(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("dark")
        super().__init__(fg_color=C["bg"])
        Fonts.init()

        self.title("Hybrid AI Assistant")
        self.geometry("1320x820")
        self.minsize(980, 620)
        try:
            self.iconbitmap(os.path.join(BASE_DIR, "icon.ico"))
        except Exception:
            pass  # .ico only works on Windows

        # ---- state ----
        self.settings = Settings()
        self.gate = AccessGate(self.settings)
        self.model = self.settings.get("model")
        if self.model not in router.MODELS:
            self.model = router.DEFAULT_MODEL
        self.pending = []            # attachments waiting in the composer
        self.ollama_models = None    # names Ollama reported at the last health check
        self.events = queue.Queue()
        self.gen = None              # info about the reply currently streaming
        self.gen_ids = itertools.count(1)
        self.conversations = {}
        self.order = []              # most recently used first
        self.store = None
        self.active = None
        self.panel_visible = True
        self.session = {"local": 0, "cloud": 0, "fallback": 0}

        self._build()
        self._load_saved()
        self.new_chat()
        self._show_model()
        for note in MIGRATION_NOTES:
            self.inspector.add_log(note)

        self.bind_all("<Control-n>", lambda e: self.new_chat())
        self.bind_all("<Escape>", lambda e: self.stop())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll)
        self.after(300, self._health_check)

    # ================= layout =================
    def _build(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.sidebar = Sidebar(self, on_new_chat=self.new_chat, on_select_chat=self.select_chat,
                               on_delete_chat=self.delete_chat, icon_path=os.path.join(BASE_DIR, "icon.png"))
        self.sidebar.grid(row=0, column=0, sticky="ns")
        ctk.CTkFrame(self, width=1, corner_radius=0, fg_color=C["border"]).grid(row=0, column=0, sticky="nse")

        center = ctk.CTkFrame(self, corner_radius=0, fg_color=C["bg"])
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_rowconfigure(0, weight=1)
        center.grid_columnconfigure(0, weight=1)
        self.chat = ChatView(center, on_toggle_panel=self.toggle_panel,
                             on_suggestion=lambda t: self.composer.set_text(t))
        self.chat.grid(row=0, column=0, sticky="nsew")
        self.composer = Composer(center, on_send=self.send, on_stop=self.stop,
                                 on_attach=self.pick_files, on_paste=self.paste_from_clipboard,
                                 on_remove_attachment=self.remove_attachment)
        self.composer.grid(row=1, column=0, sticky="ew")

        ollama_host = str(router.local_client.base_url).replace("/v1/", "").rstrip("/")
        env_info = [
            ("Ollama", ollama_host.replace("http://", "")),
            ("Gemini key", "set" if os.getenv("GEMINI_API_KEY") else "missing"),
            ("Groq key", "set" if os.getenv("GROQ_API_KEY") else "missing"),
        ]
        self.ollama_host = ollama_host
        self.inspector = Inspector(self, on_model=self._set_model,
                                   on_setting=self._set_setting, on_clear=self.clear_chat,
                                   env_info=env_info, settings=self.settings, gate=self.gate,
                                   data_dir=config.DATA_DIR)
        self.inspector.grid(row=0, column=2, sticky="ns")
        self.panel_border = ctk.CTkFrame(self, width=1, corner_radius=0, fg_color=C["border"])
        self.panel_border.grid(row=0, column=2, sticky="nsw")

    def toggle_panel(self):
        self.panel_visible = not self.panel_visible
        if self.panel_visible:
            self.inspector.grid()
            self.panel_border.grid()
        else:
            self.inspector.grid_remove()
            self.panel_border.grid_remove()
        self.chat.set_panel_visible(self.panel_visible)

    # ================= conversations =================
    def new_chat(self):
        if self.gen:
            return
        if self.active and not self.active.turns:
            self.composer.focus()  # current chat is already empty, reuse it
            return
        conv = Conversation()
        self.conversations[conv.id] = conv
        self.order.insert(0, conv.id)
        self.select_chat(conv.id)

    def select_chat(self, conv_id):
        if self.gen:
            return
        self.active = self.conversations[conv_id]
        self._render_active()
        self._refresh_sidebar()
        self.composer.focus()

    def clear_chat(self):
        if self.gen or not self.active:
            return
        self.active.history.clear()
        self.active.turns.clear()
        self.active.title = "New conversation"
        self._forget(self.active.id)  # an empty chat isn't kept on disk
        self._render_active()
        self._refresh_sidebar()

    def delete_chat(self, conv_id):
        if self.gen or conv_id not in self.conversations:
            return
        self._forget(conv_id)
        del self.conversations[conv_id]
        self.order.remove(conv_id)
        if self.active and self.active.id == conv_id:
            self.active = None
            if self.order:
                self.select_chat(self.order[0])
            else:
                self.new_chat()
        else:
            self._refresh_sidebar()

    # ================= saving to disk =================
    def _load_saved(self):
        try:
            self.store = Store(DB_PATH)
            saved = self.store.load_all()
        except Exception as e:
            self.store = None
            self.inspector.add_log(f"could not open chat history: {e}", "warn")
            return
        for c in saved:
            conv = Conversation(c["id"], c["title"], c["history"], c["turns"], c["created"])
            self.conversations[conv.id] = conv
            self.order.append(conv.id)
        if saved:
            self.inspector.add_log(f"loaded {len(saved)} saved conversation"
                                   f"{'s' if len(saved) != 1 else ''}")

    def _save(self, conv):
        if not self.store:
            return
        try:
            self.store.save(conv.id, conv.title, conv.history, conv.turns, conv.created)
            conv.created = conv.created or time.time()
        except Exception as e:
            self.inspector.add_log(f"save failed: {e}", "warn")

    def _forget(self, conv_id):
        if self.store:
            try:
                self.store.delete(conv_id)
            except Exception as e:
                self.inspector.add_log(f"delete failed: {e}", "warn")

    def _on_close(self):
        if self.gen:
            self.gen["stop"].set()
        if self.store:
            self.store.close()
        self.destroy()

    def _render_active(self):
        conv = self.active
        self.chat.show_meta = self.settings.get("show_meta", True)
        if not conv.turns:
            self.chat.show_empty_state()
        else:
            self.chat.clear()
            for t in conv.turns:
                if t["role"] == "user":
                    self.chat.add_user(t["text"], t.get("attachments"), t.get("notes"))
                else:
                    msg = self.chat.add_assistant()
                    msg.append(t["text"])
                    msg.set_done(t["route"], t["meta"], t.get("stopped", False))
            self.after(120, self.chat.go_bottom)  # after every message has its final height
        self._update_header()

    def _update_header(self):
        conv = self.active
        n = len(conv.turns)
        models = {t["route"].split(" ")[0] for t in conv.turns if t["role"] == "assistant"}
        sub = f"{n} message{'s' if n != 1 else ''}"
        if models:
            sub += f" · {len(models)} model{'s' if len(models) != 1 else ''} used"
        self.chat.set_title(conv.title, sub if n else "Start by typing below")

    def _refresh_sidebar(self):
        chats = [(cid, self.conversations[cid].title) for cid in self.order]
        self.sidebar.set_chats(chats, self.active.id if self.active else None)

    # ================= settings from the panel =================
    def _show_model(self):
        self.inspector.set_model(self.model)
        self.chat.set_model(self.model)

    def _set_model(self, key):
        if key == self.model or key not in router.MODELS:
            return
        self.model = key
        self.settings.set("model", key)
        self.chat.set_model(key)
        self.inspector.add_log(f"model → {model_label(key)} ({where(key).lower()})",
                               "local" if where(key) == "Local" else "cloud")

    def _set_setting(self, key, value):
        self.settings.set(key, value)
        if key == "show_meta" and not self.gen:
            self._render_active()

    # ================= attachments =================
    def pick_files(self):
        if self.gen:
            return
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(parent=self, title="Attach files", filetypes=FILE_TYPES)
        for p in paths:
            self.attach_path(os.path.normpath(p))
        self.composer.focus()

    def paste_from_clipboard(self):
        """Ctrl+V: attach a copied image or copied files. Returns True if it
        handled the paste (otherwise the normal text paste happens)."""
        if self.gen:
            return False
        try:
            from PIL import ImageGrab
            clip = ImageGrab.grabclipboard()
        except Exception:
            return False
        if clip is None:
            return False
        if isinstance(clip, list):  # files copied in Explorer
            files = [p for p in clip if isinstance(p, str) and os.path.isfile(p)]
            for p in files:
                self.attach_path(p, source="paste")
            return bool(files)
        try:
            att = attachments.load_image(clip)
        except Exception as e:
            self.composer.show_notice(f"Couldn't paste the image: {e}", "err")
            return True
        self._add_pending(att)
        return True

    def attach_path(self, path, source="file"):
        """Access check (asking if needed), then read the file. Returns True if attached."""
        if any(a.path and os.path.normcase(a.path) == os.path.normcase(os.path.abspath(path))
               for a in self.pending):
            return True
        ok, reason = self.gate.check(path, "read")
        if not ok and self.gate.needs_prompt(reason):
            choice = dialogs.ask(
                self, "Allow access to this file?",
                "This file is outside the folders the assistant may read. Allow it?",
                [("once", "Allow once"), ("always", "Always allow this folder"), ("deny", "Deny")],
                primary="once", detail=path)
            if choice == "once":
                self.gate.allow_once(path)
            elif choice == "always":
                self.gate.allow_always(path)
                self.inspector.refresh_access()
                self.inspector.add_log(f"access: added {os.path.dirname(path)}")
            else:
                self.composer.show_notice(f"Not attached: {os.path.basename(path)} (access denied)", "info")
                return False
            ok, reason = self.gate.check(path, "read")
        if not ok:
            self.composer.show_notice(f"Not attached: {reason}", "err")
            self.inspector.add_log(f"blocked {os.path.basename(path)}: {reason}", "warn")
            return False
        self.composer.show_notice(f"Reading {os.path.basename(path)}…", "info", seconds=0)
        self.update_idletasks()
        try:
            att = attachments.load_file(path, source=source)
        except AttachError as e:
            self.composer.show_notice(str(e), "err", seconds=12)
            return False
        except Exception as e:
            self.composer.show_notice(f"Couldn't read {os.path.basename(path)}: {e}", "err", seconds=12)
            return False
        self.composer.hide_notice()
        self._add_pending(att)
        return True

    def _add_pending(self, att):
        self.pending.append(att)
        self._show_pending()
        self.inspector.add_log(f"attached {att.label()}")

    def remove_attachment(self, index):
        if 0 <= index < len(self.pending) and not self.gen:
            self.pending.pop(index)
            self._show_pending()

    def _show_pending(self):
        self.composer.set_attachments([(a.label(), a.kind) for a in self.pending])

    def _check_before_send(self, atts):
        """Decide which model gets this message, asking where needed.
        Returns the model key, or None to cancel."""
        model = self.model
        images = [a for a in atts if a.is_image]
        if images and model not in router.VISION_MODELS:
            choice = dialogs.ask(
                self, "This model can't see images",
                f"{model_label(model)} reads text only. Send this message to "
                f"{model_label(VISION_KEY)} (on this laptop) instead?",
                [("vision", f"Use {model_label(VISION_KEY)}"), ("cancel", "Cancel")], primary="vision")
            if choice != "vision":
                return None
            model = VISION_KEY
        if model == VISION_KEY and self.ollama_models is not None:
            vision_id = router.MODELS[VISION_KEY][0]
            if vision_id not in self.ollama_models:
                dialogs.info(self, "Vision model not installed",
                             "Install it once in a terminal, then send again:",
                             detail=f"ollama pull {vision_id}")
                return None
        if atts and where(model) == "Cloud":
            policy = self.settings.get("cloud_files", "ask")
            provider = "Google (Gemini)" if "gemini" in model else "Groq"
            if images and model not in router.VISION_MODELS:
                return None  # (unreachable: handled above)
            if policy == "block":
                self.composer.show_notice("Attachments only go to local models (Settings → Files and "
                                          "the cloud). Pick a local model or change the setting.", "err", 10)
                return None
            if policy == "ask":
                names = ", ".join(a.name for a in atts)
                choice = dialogs.ask(
                    self, f"Send files to {provider}?",
                    f"{model_label(model)} is a cloud model. The contents of these attachments will "
                    f"leave this laptop:", [("send", "Send"), ("cancel", "Cancel")],
                    primary="cancel", detail=names)
                if choice != "send":
                    return None
        return model

    # ================= sending & streaming =================
    def send(self, text):
        if self.gen:
            return
        # Full file paths typed or pasted into the message are attached too.
        for p in attachments.find_paths(text):
            self.attach_path(p, source="path")
        atts = list(self.pending)
        if not text and not atts:
            return
        model = self._check_before_send(atts)
        if model is None:
            return  # cancelled; the text and attachments stay in the composer

        content, notes = (attachments.build_message(text, atts, self.settings.get("context_tokens", 8192))
                          if atts else (None, []))
        conv = self.active
        self.composer.clear()
        self.pending = []
        self._show_pending()
        if not conv.turns:
            first = text or atts[0].name
            conv.title = first if len(first) <= 34 else first[:33].rstrip() + "…"
            self._refresh_sidebar()
        meta = [a.meta() for a in atts]
        conv.turns.append({"role": "user", "text": text, "at": time.time(),
                           "attachments": meta, "notes": notes})
        self.chat.add_user(text, meta, notes)
        for n in notes:
            self.inspector.add_log(n, "warn")

        msg = self.chat.add_assistant()
        msg.set_waiting(model)
        self.inspector.set_route(model_label(model), "Selected manually" if model == self.model else
                                 "Switched to the vision model for this message")
        self.inspector.set_stats("…", "…", "…")

        gen_id = next(self.gen_ids)
        stop_event = threading.Event()
        self.gen = {"id": gen_id, "msg": msg, "conv": conv, "route": None,
                    "start": time.perf_counter(), "first": None, "chunks": 0,
                    "text": "", "stop": stop_event}
        self.composer.set_generating(True)
        self.sidebar.set_enabled(False)
        self._update_header()

        q = self.events

        def work():
            try:
                stream_chat(conv.history, text, "manual",
                            on_token=lambda tok: q.put((gen_id, "tok", tok)),
                            on_done=lambda r, why, ms: q.put((gen_id, "done", r, why, ms)),
                            on_route=lambda r, why: q.put((gen_id, "route", r, why)),
                            model_override=model, should_stop=stop_event.is_set,
                            content=content)
            except Exception as e:  # anything stream_chat didn't catch itself
                q.put((gen_id, "tok", f"\n\nError: {e}"))
                q.put((gen_id, "done", "local-general", "error before streaming", 0))

        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.gen and not self.gen["stop"].is_set():
            self.gen["stop"].set()
            self.inspector.add_log("stop requested", "warn")

    def _poll(self):
        """Runs on the main thread every POLL_MS: apply everything the worker sent."""
        tokens, done, statuses, routed = [], None, [], None
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "status":
                    statuses.append(ev[1:])
                elif ev[0] == "models":
                    self.ollama_models = ev[1]
                elif self.gen and ev[0] == self.gen["id"]:
                    if ev[1] == "tok":
                        tokens.append(ev[2])
                    elif ev[1] == "route":
                        routed = ev[2:]
                    else:
                        done = ev[2:]
                # events from an older generation are ignored
        except queue.Empty:
            pass

        try:
            for name, state, text in statuses:
                self.sidebar.set_status(name, state, text)
            if routed:
                self._on_route(*routed)
            if tokens:
                self._on_tokens("".join(tokens), len(tokens))
            if done:
                self._on_done(*done)
        finally:
            self.after(POLL_MS, self._poll)  # keep polling even if an update failed

    def _on_route(self, route_name, reason):
        g = self.gen
        g["route"] = route_name
        g["route_ms"] = (time.perf_counter() - g["start"]) * 1000
        g["msg"].set_waiting(route_name)
        self.inspector.set_route(model_label(route_name), reason)
        self.inspector.add_log(f"route → {route_name}  [{g['route_ms']:.0f} ms]  {reason}",
                               "local" if where(route_name) == "Local" else "cloud")

    def _on_tokens(self, text, n):
        g = self.gen
        now = time.perf_counter()
        # The router's "[Cloud unavailable…]" notice isn't model output, so it
        # mustn't count as the first token.
        is_notice_only = text.startswith("[Cloud unavailable") and text.endswith("\n\n")
        if g["first"] is None and not is_notice_only:
            g["first"] = now
            g["msg"].set_streaming(g["route"])
        g["text"] += text
        follow = self.settings.get("autoscroll", True) and self.chat.is_near_bottom()
        g["msg"].append(text)
        if follow:
            self.chat.scroll_to_bottom(force=True)
        if g["first"] is None:
            return
        g["chunks"] += n
        ttft = g["first"] - g["start"]
        span = now - g["first"]
        speed = f"{g['chunks'] / span:.0f}" if span > 0.3 else "…"
        self.inspector.set_stats(f"{ttft:.1f} s", speed, f"{now - g['start']:.1f} s")

    def _on_done(self, route_name, reason, elapsed_ms):
        g = self.gen
        stopped = g["stop"].is_set()
        ttft = (g["first"] - g["start"]) if g["first"] else None
        span = (time.perf_counter() - g["first"]) if g["first"] else 0
        speed = g["chunks"] / span if span > 0.3 else None

        parts = [model_label(route_name)]
        if ttft is not None:
            parts.append(f"{ttft:.1f} s to first token")
        parts.append(f"{elapsed_ms / 1000:.1f} s total")
        meta = " · ".join(parts)

        g["msg"].set_done(route_name, meta, stopped)
        conv = g["conv"]
        conv.turns.append({"role": "assistant", "text": g["text"], "route": route_name,
                           "meta": meta, "stopped": stopped, "at": time.time()})
        self._save(conv)  # the worker has finished touching conv.history by now
        if self.order and self.order[0] != conv.id:  # most recently used goes to the top
            self.order.remove(conv.id)
            self.order.insert(0, conv.id)

        self.inspector.set_route(model_label(route_name), reason)
        self.inspector.set_stats(f"{ttft:.1f} s" if ttft is not None else "—",
                                 f"{speed:.0f}" if speed else "—", f"{elapsed_ms / 1000:.1f} s")
        if "fallback" in route_name:
            self.session["fallback"] += 1
            self.inspector.add_log(f"cloud failed → fell back to {route_name.split(' ')[0]}", "warn")
        self.session["local" if where(route_name) == "Local" else "cloud"] += 1
        s = self.session
        self.inspector.set_session(f"This session: {s['local']} local · {s['cloud']} cloud · "
                                   f"{s['fallback']} fallback")
        self.inspector.add_log(f"done {elapsed_ms} ms" + (f" · ttft {ttft:.2f}s" if ttft else "")
                               + (" · stopped" if stopped else ""))

        self.gen = None
        self.composer.set_generating(False)
        self._refresh_sidebar()  # also re-enables the chat buttons
        self._update_header()
        self.composer.focus()

    # ================= provider status =================
    def _health_check(self):
        q, host = self.events, self.ollama_host

        def work():
            try:
                with LOCAL_HTTP.open(f"{host}/api/tags", timeout=2) as r:
                    names = {m["name"] for m in json.load(r).get("models", [])}
                q.put(("models", names))
                missing = [m for m in REQUIRED_LOCAL_MODELS if m not in names]
                if missing:
                    q.put(("status", "Ollama", "warn", f"missing {missing[0]}"))
                else:
                    q.put(("status", "Ollama", "ok", f"online · {len(REQUIRED_LOCAL_MODELS)} models"))
            except Exception:
                q.put(("status", "Ollama", "err", "offline"))
            for name, var in (("Gemini", "GEMINI_API_KEY"), ("Groq", "GROQ_API_KEY")):
                ok = bool(os.getenv(var))
                q.put(("status", name, "ok" if ok else "err", "key set" if ok else "no key"))
            # Manual mode: the router's classifier isn't used, so it isn't loaded.
            q.put(("status", "Router", "ok", "manual"))

        threading.Thread(target=work, daemon=True).start()
        self.after(HEALTH_EVERY_MS, self._health_check)


if __name__ == "__main__":
    app = HybridAIApp()
    app.mainloop()
