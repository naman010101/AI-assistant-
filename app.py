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
import copy
import itertools
import json
import logging
import os
import queue
import threading
import time
import urllib.request
import uuid

import customtkinter as ctk

import config
import log_setup

log_setup.setup_safe()

log = logging.getLogger("hybridai")

# Move data that older versions kept in the project folder BEFORE anything
# reads it (router.py loads the API keys on import).
try:
    MIGRATION_NOTES = config.migrate_legacy()
except Exception as _e:  # never stop the app over this; report it in the log
    log.exception("couldn't move old data out of the project folder")
    MIGRATION_NOTES = [f"could not move old data: {_e}"]

import attachments  # noqa: E402
import context  # noqa: E402
import editor  # noqa: E402
import memory  # noqa: E402
import quickask  # noqa: E402
import router  # noqa: E402
import access  # noqa: E402
from access import AccessGate  # noqa: E402
from attachments import AttachError  # noqa: E402
from router import stream_chat  # noqa: E402
from settings import Settings  # noqa: E402
from storage import Store  # noqa: E402
from ui import dialogs  # noqa: E402
from ui.chat_view import ChatView  # noqa: E402
from ui.composer import Composer  # noqa: E402
from ui.diff_view import DiffWindow  # noqa: E402
from ui.memory_view import MemoryWindow  # noqa: E402
from ui.code_view import CodeWindow  # noqa: E402
from ui.quickask_view import QuickAskPopup  # noqa: E402
from ui.reminders_view import BriefWindow, NotifyPopup, RemindersWindow  # noqa: E402
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
                 created=None, meta=None):
        self.id = conv_id or uuid.uuid4().hex
        self.title = title
        self.history = history or []  # the list router.stream_chat reads and appends to
        self.turns = turns or []      # what the UI shows: dicts with role, text, route, meta, stopped
        self.created = created        # set by the store; None until first saved
        self.meta = meta or {}        # digest of older messages: {"digest": str, "digest_upto": int}


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
            log.exception("couldn't set the window icon")  # .ico only works on Windows

        # ---- state ----
        self.settings = Settings()
        self.gate = AccessGate(self.settings)
        self.model = self.settings.get("model")
        if self.model not in router.MODELS:
            self.model = router.DEFAULT_MODEL
        self.pending = []            # attachments waiting in the composer
        self.ollama_models = None    # names Ollama reported at the last health check
        self._edit_msgs = {}         # (conv id, turn index) -> message widget showing edit buttons
        # memory
        self.mem = None
        self.embed = memory.ollama_embedder()
        self.complete = memory.ollama_completer(router.MODELS["local-general"][0])
        self.complete_text = memory.ollama_completer(router.MODELS["local-general"][0], json_mode=False)
        self.digesting = set()       # chats whose older messages are being summarised
        self.hotkey = None           # Quick Ask's global hotkey
        self.phone = None            # phone server (Settings → Phone)
        self.rem = None              # reminders + timetable
        self.quick_popup = None
        self._qa_busy = False
        self.summarising = set()     # chats waiting for / being summarised
        self.summary_failures = {}   # conv id -> failed attempts this run
        self._summary_lock = threading.Lock()  # one summary at a time
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
        self._open_memory_store()
        self._open_reminders_store()
        self.new_chat()
        self._show_model()
        for note in MIGRATION_NOTES:
            self.inspector.add_log(note)
        self.inspector.set_recent_edits(editor.recent_edits(8), self._undo_record)
        self._refresh_sidebar()
        self.after(600, self._memory_startup)
        self.after(800, self._start_hotkey)
        self.after(900, self._start_phone)
        self.after(2000, self._check_reminders)

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
                               on_delete_chat=self.delete_chat, icon_path=os.path.join(BASE_DIR, "icon.png"),
                               on_open_memory=lambda: self.open_memory(),
                               on_select_summary=lambda sid: self.open_memory(sid),
                               on_open_code=lambda: self.open_code(),
                               on_open_reminders=lambda: self.open_reminders())
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
                                   data_dir=config.DATA_DIR, on_open_memory=lambda: self.open_memory(),
                                   on_pair=lambda: self.open_pairing(),
                                   on_reset_phone=lambda: self.reset_phone_token())
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
        if self.gen or conv_id in self.summarising or conv_id not in self.conversations:
            return
        previous = self.active
        self.active = self.conversations[conv_id]
        self._render_active()
        self._refresh_sidebar()
        self.composer.focus()
        if previous is not None and previous is not self.active and previous.id in self.conversations:
            self._end_session(previous)  # switching away = that session is over

    def clear_chat(self):
        if self.gen or not self.active:
            return
        self.active.history.clear()
        self.active.turns.clear()
        self.active.title = "New conversation"
        self.active.meta = {}
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
            conv = Conversation(c["id"], c["title"], c["history"], c["turns"], c["created"], c.get("meta"))
            self.conversations[conv.id] = conv
            self.order.append(conv.id)
        if saved:
            self.inspector.add_log(f"loaded {len(saved)} saved conversation"
                                   f"{'s' if len(saved) != 1 else ''}")

    def _save(self, conv):
        if not self.store:
            return
        try:
            self.store.save(conv.id, conv.title, conv.history, conv.turns, conv.created, conv.meta)
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
        # The open chat stays saved; it is summarised the next time the app starts.
        if self.hotkey:
            self.hotkey.stop()
        if self.phone:
            self.phone.stop()
        if self.gen:
            self.gen["stop"].set()
        if self.store:
            self.store.close()
        if self.mem:
            self.mem.close()
        if self.rem:
            self.rem.close()
        self.destroy()

    def _render_active(self):
        conv = self.active
        self.chat.show_meta = self.settings.get("show_meta", True)
        if not conv.turns:
            self.chat.show_empty_state()
        else:
            self.chat.clear()
            self._edit_msgs = {}
            for i, t in enumerate(conv.turns):
                if t["role"] == "note":
                    self.chat.add_note(t["text"])
                elif t["role"] == "user":
                    self.chat.add_user(t["text"], t.get("attachments"), t.get("notes"))
                else:
                    msg = self.chat.add_assistant()
                    msg.append(t["text"])
                    msg.set_done(t["route"], t["meta"], t.get("stopped", False))
                    if t.get("edits"):
                        self._show_edits(conv, i, msg)
                    if t.get("memory"):
                        msg.set_memory(t["memory"], self.open_memory)
            self.after(120, self.chat.go_bottom)  # after every message has its final height
        self._update_header()

    def _update_header(self):
        conv = self.active
        n = len([t for t in conv.turns if t["role"] in ("user", "assistant")])
        models = {t["route"].split(" ")[0] for t in conv.turns if t["role"] == "assistant"}
        sub = f"{n} message{'s' if n != 1 else ''}"
        if models:
            sub += f" · {len(models)} model{'s' if len(models) != 1 else ''} used"
        self.chat.set_title(conv.title, sub if n else "Start by typing below")

    def _refresh_sidebar(self):
        chats = [(cid, self.conversations[cid].title, "pending" if cid in self.summarising else "open")
                 for cid in self.order]
        past = []
        if self.mem:
            for sm in self.mem.summaries(limit=40):
                past.append((sm["id"], sm["title"],
                             time.strftime("%d %b", time.localtime(sm.get("started") or sm["created"]))))
        self.sidebar.set_chats(chats, self.active.id if self.active else None, past)

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
        if key == "quickask":
            self._start_hotkey()
        if key == "phone":
            self._start_phone()
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
        if self.phone and self.phone.busy.locked():
            self.composer.show_notice("Your phone is getting a reply right now; send again in a moment.", "info")
            return
        import reminders
        if reminders.is_reminder_request(text) and not self.pending:
            self._remind_in_chat(text)
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
        self.composer.clear()
        self.pending = []
        self._show_pending()
        self._generate(self.active, text, model, content=content, meta=[a.meta() for a in atts],
                       notes=notes, query=text or " ".join(a.name for a in atts))

    def _generate(self, conv, text, model, content=None, meta=None, notes=None, query=None,
                  build=None, use_memory=True, on_finished=None, title=None):
        """Start a reply in `conv`. `build` (optional) runs in the worker
        thread and returns the content to send (for slow preparation such as
        embedding a project). `on_finished(conv, turn)` runs after the reply."""
        meta, notes = meta or [], notes or []
        if not conv.turns:
            first = title or text or (meta[0]["name"] if meta else "New conversation")
            conv.title = first if len(first) <= 34 else first[:33].rstrip() + "…"
            self._refresh_sidebar()
        conv.turns.append({"role": "user", "text": text, "at": time.time(),
                           "attachments": meta, "notes": notes})
        self.chat.add_user(text, meta, notes)
        for n in notes:
            self.inspector.add_log(n, "warn")

        msg = self.chat.add_assistant()
        msg.set_waiting(model)
        self.inspector.set_route(model_label(model), "Selected manually" if model == self.model else
                                 ("Switched to the vision model for this message" if model == VISION_KEY
                                  else "Chosen for this message"))
        self.inspector.set_stats("…", "…", "…")

        gen_id = next(self.gen_ids)
        stop_event = threading.Event()
        self.gen = {"id": gen_id, "msg": msg, "conv": conv, "route": None,
                    "start": time.perf_counter(), "first": None, "chunks": 0,
                    "text": "", "stop": stop_event, "on_finished": on_finished}
        self.composer.set_generating(True)
        self.sidebar.set_enabled(False)
        self._update_header()

        q = self.events
        edit_files = self._editable_files(conv)
        system = editor.instructions(edit_files) if edit_files else None
        self.gen["edit_files"], self.gen["user_text"] = edit_files, text
        mem_cfg = self.settings.get("memory", {})
        use_memory = use_memory and bool(self.mem) and mem_cfg.get("enabled", True) and (
            where(model) == "Local" or mem_cfg.get("cloud", False))
        query = query or text

        fit_args = {"budget": int(self.settings.get("context_tokens", 8192)),
                    "digest": conv.meta.get("digest"), "digest_upto": conv.meta.get("digest_upto", 0)}

        def on_context(info):
            if info["dropped"] or info["digest_used"]:
                q.put(("log", f"context: {info['sent']} recent messages"
                              + (" + summary of earlier ones" if info["digest_used"] else "")
                              + (f", {info['dropped']} older left out" if info["dropped"] else "")
                              + f" · ~{info['tokens']} tokens (same for every model)"))

        def work():
            nonlocal content
            if build is not None:
                try:
                    content = build()
                except Exception as e:
                    log.exception("couldn't prepare the message for the model")
                    q.put((gen_id, "tok", f"Couldn't prepare this: {type(e).__name__}: {e}"))
                    q.put((gen_id, "done", model, "preparation failed", 0))
                    return
            sys_text = system
            if use_memory:
                sys_text = self._memory_context(query, mem_cfg, system, gen_id)
            try:
                stream_chat(conv.history, text, "manual",
                            on_token=lambda tok: q.put((gen_id, "tok", tok)),
                            on_done=lambda r, why, ms: q.put((gen_id, "done", r, why, ms)),
                            on_route=lambda r, why: q.put((gen_id, "route", r, why)),
                            model_override=model, should_stop=stop_event.is_set,
                            content=content, system=sys_text, fit_args=fit_args,
                            on_context=on_context)
            except Exception as e:  # anything stream_chat didn't catch itself
                log.exception("error before the model stream started")
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
                elif ev[0] == "log":
                    statuses.append(("__log__", ev[1], ev[2] if len(ev) > 2 else None))
                elif ev[0] == "summarised":
                    statuses.append(("__summarised__",) + tuple(ev[1:]))
                elif ev[0] == "summary_failed":
                    statuses.append(("__summary_failed__",) + tuple(ev[1:]))
                elif ev[0] == "call":
                    statuses.append(("__call__", ev[1]))
                elif ev[0] == "reminder_reply":
                    statuses.append(("__reminder_reply__",) + tuple(ev[1:]))
                elif ev[0] == "reminders_changed":
                    pass
                elif ev[0] in ("phone_changed", "phone_deleted"):
                    statuses.append(("__" + ev[0] + "__", ev[1]))
                elif ev[0] == "quickask":
                    statuses.append(("__quickask__",))
                elif ev[0] == "digested":
                    statuses.append(("__digested__",) + tuple(ev[1:]))
                elif self.gen and ev[0] == self.gen["id"]:
                    if ev[1] == "tok":
                        tokens.append(ev[2])
                    elif ev[1] == "route":
                        routed = ev[2:]
                    elif ev[1] == "memory":
                        self.gen["memory"] = ev[2]
                    else:
                        done = ev[2:]
                # events from an older generation are ignored
        except queue.Empty:
            log.debug("no more UI events this tick", exc_info=True)

        try:
            for st in statuses:
                if st[0] == "__log__":
                    self.inspector.add_log(st[1], st[2])
                elif st[0] == "__summarised__":
                    self._on_summarised(*st[1:])
                elif st[0] == "__call__":
                    st[1]()
                elif st[0] == "__reminder_reply__":
                    self._on_reminder_reply(*st[1:])
                elif st[0] == "__phone_changed__":
                    self._reload_conv(st[1])
                elif st[0] == "__phone_deleted__":
                    self._phone_deleted(st[1])
                elif st[0] == "__quickask__":
                    self._quick_ask()
                elif st[0] == "__digested__":
                    self._on_digested(*st[1:])
                elif st[0] == "__summary_failed__":
                    self._on_summary_failed(*st[1:])
                else:
                    self.sidebar.set_status(*st)
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
        turn = {"role": "assistant", "text": g["text"], "route": route_name,
                "meta": meta, "stopped": stopped, "at": time.time()}
        if g.get("edit_files") and not stopped:
            proposals = editor.extract(g["text"], g["edit_files"], g.get("user_text", ""))
            if proposals:
                turn["edits"] = proposals
                for e in proposals:
                    self.inspector.add_log(f"proposed change to {e['name']} (review it under the reply)")
        if g.get("memory"):
            turn["memory"] = g["memory"]
            g["msg"].set_memory(g["memory"], self.open_memory)
        conv.turns.append(turn)
        if turn.get("edits"):
            self._show_edits(conv, len(conv.turns) - 1, g["msg"])
        self._save(conv)  # the worker has finished touching conv.history by now
        self._maybe_digest(conv)
        if g.get("on_finished") and not stopped:
            try:
                g["on_finished"](conv, turn)
            except Exception as e:
                self.inspector.add_log(f"after-reply step failed: {e}", "warn")
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

    # ================= memory =================
    def _open_memory_store(self):
        try:
            self.mem = memory.MemoryStore(config.MEMORY_DB)
        except Exception as e:
            self.mem = None
            self.inspector.add_log(f"memory unavailable: {e}", "warn")

    def _memory_context(self, query, mem_cfg, system, gen_id):
        """Build the memory part of the system message (runs in the worker
        thread). Facts are always included, even if the search fails."""
        block, used = self._memory_block(query, mem_cfg)
        if used:
            self.events.put((gen_id, "memory", used))
        return "\n\n".join(x for x in (block, system) if x) or None

    def _memory_block(self, query, mem_cfg):
        """(memory notes text or None, [{id, title}] of summaries used).
        Thread-safe: used by desktop replies and by the phone server."""
        q = self.events
        k = int(mem_cfg.get("k", 3))
        threshold = float(mem_cfg.get("threshold", 0.5))
        try:
            facts = self.mem.user_facts()
        except Exception as e:
            facts = []
            q.put(("log", f"memory: couldn't read facts ({e})", "warn"))
        hits, best, note = [], None, ""
        try:
            scored = self.mem.search(query, self.embed, k=k, threshold=None)
            best = scored[0][0] if scored else None
            hits = [(sim, sm) for sim, sm in scored if sim >= threshold]
        except Exception as e:
            note = f"; search failed ({type(e).__name__}: {e})"
        recall = memory.is_recall_question(query)
        if recall:  # "do you remember…?" -> add the latest sessions too
            have = {sm["id"] for _, sm in hits}
            for sm in self.mem.recent():
                if sm["id"] not in have and len(hits) < max(k, memory.RECENT_FOR_RECALL):
                    hits.append((0.0, sm))
        block = memory.context_block(hits, facts)
        n_sum = self.mem.summaries()
        indexed = sum(1 for sm in n_sum if sm["vector"])
        q.put(("log", f"memory: {len(facts)} fact{'s' if len(facts) != 1 else ''}, "
                      f"{len(hits)} session{'s' if len(hits) != 1 else ''} sent"
                      + (f" (best match {best:.2f}, needs {threshold:.2f})" if best is not None else "")
                      + (" · recall question: added recent sessions" if recall else "")
                      + f" · {indexed}/{len(n_sum)} summaries searchable" + note,
               "warn" if note or (n_sum and not indexed) else None))
        return block, [{"id": sm["id"], "title": sm["title"]} for _, sm in hits]

    def _kept(self, conv_id):
        return conv_id in set(self.settings.get("memory_keep_ids", []))

    def _memory_startup(self):
        """Chats saved before this start are finished sessions: summarise them.
        The very first time, ask about chats from before memory existed."""
        if not self.mem:
            return
        ended = [self.conversations[c] for c in self.order
                 if self.conversations[c] is not self.active and self.conversations[c].turns
                 and not self._kept(c)]
        if not self.settings.get("memory_setup_done") and ended:
            n = len(ended)
            choice = dialogs.ask(
                self, "Turn your saved chats into memory?",
                f"You have {n} chat{'s' if n != 1 else ''} saved from before memory existed. Each can be "
                f"replaced by a short summary the assistant remembers (the full chat is then deleted), "
                f"or kept as a full chat that is never summarised.",
                [("summarise", "Summarise them"), ("keep", "Keep them as full chats")], primary="summarise")
            if choice != "summarise":
                self.settings.set("memory_keep_ids", [c.id for c in ended])
                self.inspector.add_log(f"kept {n} older chats in full (they won't be summarised)")
                ended = []
        self.settings.set("memory_setup_done", True)
        for conv in ended:
            self._end_session(conv)

    def _end_session(self, conv):
        """A chat is finished: summarise it, then delete the full chat. Kept
        chats and the open chat are left alone."""
        if (conv is self.active or conv.id in self.summarising or self._kept(conv.id)
                or conv.id not in self.conversations or not self.mem):
            return
        if not conv.turns:
            self._drop_conv(conv.id)
            return
        if not memory.worth_summarising(conv.turns):
            self._forget(conv.id)
            self._drop_conv(conv.id)
            self.inspector.add_log(f"“{conv.title}” was too short to remember; deleted")
            return
        self.summarising.add(conv.id)
        self._refresh_sidebar()
        turns = copy.deepcopy(conv.turns)
        started = next((t.get("at") for t in turns if t.get("at")), None)
        q, conv_id, title = self.events, conv.id, conv.title

        def job():
            with self._summary_lock:
                try:
                    summary = memory.summarize(turns, self.complete)
                    sid = self.mem.add_summary(conv_id, summary, self.embed, started=started,
                                               embed_model=getattr(self.embed, "model", None))
                    q.put(("summarised", conv_id, sid, summary["title"]))
                except Exception as e:
                    q.put(("summary_failed", conv_id, title, f"{type(e).__name__}: {e}"))

        threading.Thread(target=job, daemon=True).start()

    def _on_summarised(self, conv_id, sid, title):
        self.summarising.discard(conv_id)
        self.summary_failures.pop(conv_id, None)
        if conv_id in self.conversations and self.conversations[conv_id] is not self.active:
            self._forget(conv_id)  # only now, with the summary safely saved
            self._drop_conv(conv_id)
        self.inspector.add_log(f"remembered: “{title}” (full chat deleted)", "local")
        try:
            new = self.mem.pending_facts(source=sid)
        except Exception:
            new = []
        if new:
            self.inspector.add_log(f"{len(new)} new fact{'s' if len(new) != 1 else ''} about you to confirm "
                                   f"(Memory → Facts); they aren't used until you do", "warn")
            self.composer.show_notice(f"{len(new)} new thing{'s' if len(new) != 1 else ''} learned about you. "
                                      f"Check them in Memory → Facts (not used until you confirm).", "info", 12)
        self._refresh_sidebar()

    # ---------- digest of older messages in a long chat ----------
    def _maybe_digest(self, conv):
        budget = int(self.settings.get("context_tokens", 8192))
        upto = conv.meta.get("digest_upto", 0)
        if conv.id in self.digesting or not context.needs_digest(conv.history, upto, budget):
            return
        start, end = context.digest_range(conv.history, upto)
        if end <= start:
            return
        self.digesting.add(conv.id)
        prompt = context.digest_prompt(conv.meta.get("digest"), copy.deepcopy(conv.history[start:end]))
        q, conv_id = self.events, conv.id

        def job():
            try:
                text = context.clean_digest(self.complete_text(prompt))
                q.put(("digested", conv_id, text, end, None))
            except Exception as e:
                q.put(("digested", conv_id, None, end, f"{type(e).__name__}: {e}"))

        threading.Thread(target=job, daemon=True).start()

    def _on_digested(self, conv_id, text, end, error):
        self.digesting.discard(conv_id)
        conv = self.conversations.get(conv_id)
        if conv is None or len(conv.history) < end:  # chat cleared or deleted meanwhile
            return
        if error or not text:
            self.inspector.add_log(f"couldn't summarise older messages yet ({error}); they're still sent "
                                   f"while they fit", "warn")
            return
        conv.meta = {**conv.meta, "digest": text, "digest_upto": end}
        self._save(conv)
        self.inspector.add_log(f"long chat: the first {end} messages are now kept as a summary for every model")

    def _on_summary_failed(self, conv_id, title, error):
        self.summarising.discard(conv_id)
        self.summary_failures[conv_id] = self.summary_failures.get(conv_id, 0) + 1
        self.inspector.add_log(f"couldn't summarise “{title}” yet ({error}); the full chat is kept "
                               f"and it will be tried again", "warn")
        self._refresh_sidebar()

    def _retry_summaries(self):
        for cid in list(self.order):
            conv = self.conversations.get(cid)
            if (conv and conv is not self.active and conv.turns and cid not in self.summarising
                    and 0 < self.summary_failures.get(cid, 0) < 3):
                self._end_session(conv)

    def _check_idle(self):
        idle = float(self.settings.get("memory", {}).get("idle_minutes", 30))
        conv = self.active
        if self.gen or not conv or not conv.turns or idle <= 0:
            return
        last = max((t.get("at") or 0) for t in conv.turns)
        if last and time.time() - last > idle * 60:
            self.inspector.add_log(f"“{conv.title}” idle for {idle:.0f} min: session ended")
            self.new_chat()

    def _drop_conv(self, conv_id):
        self.conversations.pop(conv_id, None)
        if conv_id in self.order:
            self.order.remove(conv_id)

    # ================= phone =================
    def memory_system(self, query, model):
        """Memory notes for a message from the phone: (system text, used list)."""
        mem_cfg = self.settings.get("memory", {})
        if not (self.mem and mem_cfg.get("enabled", True) and (where(model) == "Local" or mem_cfg.get("cloud"))):
            return None, []
        block, used = self._memory_block(query, mem_cfg)
        return block, used

    def desktop_busy(self):
        return self.gen is not None

    def notify(self, event):
        self.events.put(event)

    def _start_phone(self):
        if self.phone:
            self.phone.stop()
            self.phone = None
        cfg = self.settings.get("phone", {})
        if not cfg.get("enabled"):
            self.sidebar.set_status("Phone", "unknown", "off")
            return
        import phone
        srv = phone.PhoneServer(self, port=int(cfg.get("port", 8765)), lan=cfg.get("lan", True))
        if srv.start():
            self.phone = srv
            self.sidebar.set_status("Phone", "ok", f"port {srv.port}")
            self.inspector.add_log(f"phone: listening on port {srv.port}"
                                   + (" (Wi-Fi + Tailscale)" if srv.lan else " (this laptop / tailscale serve only)"))
        else:
            self.sidebar.set_status("Phone", "err", "not started")
            self.inspector.add_log(f"phone: couldn't start: {srv.error}", "warn")

    def open_pairing(self):
        cfg = self.settings.get("phone", {})
        if not self.phone:
            dialogs.info(self, "Phone access is off", "Turn on “Let my phone connect” first.")
            return
        import webbrowser
        webbrowser.open(f"http://localhost:{int(cfg.get('port', 8765))}/pair")

    def reset_phone_token(self):
        if self.phone:
            self.phone.reset_token()
        else:
            import phone
            phone.load_token(new=True)
        self.inspector.add_log("phone: all paired phones disconnected (pair again with the new QR code)", "warn")

    def _reload_conv(self, conv_id):
        """A chat changed on the phone: take the saved version."""
        if not self.store or (self.gen and self.gen["conv"].id == conv_id):
            return
        data = self.store.get(conv_id)
        if data is None:
            return self._phone_deleted(conv_id)
        conv = self.conversations.get(conv_id)
        if conv is None:
            conv = Conversation(data["id"], data["title"], data["history"], data["turns"], data["created"],
                                data.get("meta"))
            self.conversations[conv_id] = conv
        else:
            conv.title, conv.history, conv.turns = data["title"], data["history"], data["turns"]
            conv.created, conv.meta = data["created"], data.get("meta") or {}
        if conv_id in self.order:
            self.order.remove(conv_id)
        self.order.insert(0, conv_id)
        if self.active is conv and not self.gen:
            self._render_active()
        self._refresh_sidebar()

    def _phone_deleted(self, conv_id):
        if conv_id not in self.conversations:
            return
        was_active = self.active is not None and self.active.id == conv_id
        self._drop_conv(conv_id)
        if was_active:
            self.active = None
            self.new_chat()
        else:
            self._refresh_sidebar()

    def _end_idle_others(self):
        """Chats you aren't in (e.g. from the phone) that have gone quiet are
        finished sessions: summarise them like any other."""
        idle = float(self.settings.get("memory", {}).get("idle_minutes", 30))
        if idle <= 0 or self.gen:
            return
        busy = self.phone.busy_conv if self.phone else None
        now = time.time()
        for cid in list(self.order):
            conv = self.conversations.get(cid)
            if not conv or conv is self.active or not conv.turns or cid == busy or cid in self.summarising:
                continue
            last = max((t.get("at") or 0) for t in conv.turns)
            if last and now - last > idle * 60 and not self.summary_failures.get(cid):
                self._end_session(conv)

    # ================= reminders + daily brief =================
    def _open_reminders_store(self):
        import reminders
        try:
            self.rem = reminders.ReminderStore(os.path.join(config.DATA_DIR, "reminders.db"))
        except Exception as e:
            self.rem = None
            self.inspector.add_log(f"reminders unavailable: {e}", "warn")

    def add_reminder_text(self, text, source="desktop"):
        """'remind me …' -> (ok, message). Plain words are parsed directly;
        anything unusual goes to the local model. Safe from any thread."""
        import reminders
        if not self.rem:
            return False, "Reminders aren't available (see the Log tab)."
        parsed = reminders.parse(text)
        if parsed is None:
            try:
                parsed = reminders.parse_model_reply(self.complete(reminders.model_prompt(text)))
            except Exception as e:
                return False, f"I couldn't work out when (the local model isn't reachable: {e}). Try “… on Friday 5pm”."
        if parsed is None:
            return False, "I couldn't work out when. Try something like “tomorrow 9am” or “Friday 5pm”."
        due, what, repeat = parsed
        self.rem.add(what, due, repeat, source)
        self.events.put(("reminders_changed",))
        return True, f"⏰ Reminder set: **{what}** — {reminders.describe(due, repeat)}."

    def _remind_in_chat(self, text):
        """'remind me …' typed in a desktop chat: handled here, no model reply needed
        unless the time is unusual (then the local model reads it, in the background)."""
        conv = self.active
        if not conv.turns:
            conv.title = text if len(text) <= 34 else text[:33].rstrip() + "…"
        now = time.time()
        conv.turns.append({"role": "user", "text": text, "at": now})
        self.chat.add_user(text)
        q, cid = self.events, conv.id

        def job():
            ok, msg = self.add_reminder_text(text, "chat")
            q.put(("reminder_reply", cid, ok, msg))

        threading.Thread(target=job, daemon=True).start()
        self.composer.clear()

    def _on_reminder_reply(self, conv_id, ok, msg):
        conv = self.conversations.get(conv_id)
        if conv is None:
            return
        turn = {"role": "assistant", "text": msg + ("\nSee or change it in Reminders." if ok else ""),
                "route": "laptop", "meta": "Reminders", "at": time.time()}
        conv.turns.append(turn)
        if self.active is conv:
            m = self.chat.add_assistant()
            m.append(turn["text"])
            m.set_done("laptop", "Reminders")
        self._save(conv)
        self._refresh_sidebar()

    def _check_reminders(self):
        """Every 30 s: show due reminders and, once a day, the brief."""
        try:
            if self.rem:
                import reminders
                for r in self.rem.due():
                    self.rem.fired(r)
                    when = reminders.describe(r["due_dt"], r["repeat"])
                    NotifyPopup(self, "Reminder", f"{r['text']}\n{when}",
                                buttons=[("Done", lambda rid=r["id"]: self.rem.complete(rid)),
                                         ("Snooze 10 min", lambda rid=r["id"]: self.rem.snooze(rid, 10))])
                    self.inspector.add_log(f"reminder: {r['text']}")
                self._maybe_brief()
        except Exception as e:
            self.inspector.add_log(f"reminder check failed: {e}", "warn")
        self.after(30_000, self._check_reminders)

    def _maybe_brief(self):
        cfg = self.settings.get("brief", {})
        if not cfg.get("enabled", True):
            return
        now = time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        if cfg.get("last") == today or time.strftime("%H:%M", now) < cfg.get("time", "08:00"):
            return
        self.settings.set("brief", {**cfg, "last": today})
        self.show_brief()

    def brief_text(self):
        import reminders
        changes = []
        try:
            import codeexplain
            for root in self.settings.get("code_projects", [])[:6]:
                if os.path.isdir(root):
                    p = codeexplain.Project(root)
                    ch = p.changes(lambda path: self.gate.blocked_reason(access._norm(path)))
                    if ch:
                        changes.append((p.name, len(ch)))
        except Exception:
            log.exception("couldn't check code projects for the brief")
        return reminders.brief(self.rem, mem=self.mem, project_changes=changes)

    def show_brief(self):
        if self.rem:
            BriefWindow(self, self.brief_text())

    def open_reminders(self):
        if not self.rem:
            dialogs.info(self, "Reminders unavailable", "See the Log tab.")
            return
        RemindersWindow(self, self.rem, self.settings, on_add_text=self.add_reminder_text,
                        on_read_timetable=self._read_timetable, on_show_brief=self.show_brief,
                        on_changed=lambda: None)

    def _read_timetable(self, path, done):
        """Ask the vision model for the classes in a timetable photo (background)."""
        import base64
        import io
        import reminders
        from PIL import Image
        q = self.events

        def job():
            try:
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((1600, 1600))
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=90)
                import classifier
                data = classifier._post("/api/chat", {
                    "model": router.MODELS[VISION_KEY][0], "stream": False, "format": "json",
                    "options": {"temperature": 0},
                    "messages": [{"role": "user", "content": reminders.TIMETABLE_PROMPT,
                                  "images": [base64.b64encode(buf.getvalue()).decode()]}]}, 300)
                rows = reminders.parse_timetable(data["message"]["content"])
                q.put(("call", lambda: done(rows, None)))
            except Exception as e:
                msg = f"Couldn't read it: {e}"
                if "not found" in str(e).lower():
                    msg = f"The vision model isn't installed: run  ollama pull {router.MODELS[VISION_KEY][0]}"
                q.put(("call", lambda: done([], msg)))

        threading.Thread(target=job, daemon=True).start()

    # ================= Quick Ask =================
    def _start_hotkey(self):
        """(Re)register the global hotkey from the settings."""
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None
        cfg = self.settings.get("quickask", {})
        if not cfg.get("enabled", True):
            return
        combo = cfg.get("hotkey") or quickask.DEFAULT_HOTKEY
        try:
            hk = quickask.HotkeyListener(combo, lambda: self.events.put(("quickask",)))
        except ValueError as e:
            self.inspector.add_log(f"Quick Ask hotkey “{combo}” isn't valid: {e}", "warn")
            return
        if hk.start():
            self.hotkey = hk
            self.inspector.add_log(f"Quick Ask: press {combo.title()} in any app")
        else:
            self.inspector.add_log(f"Quick Ask unavailable: {hk.error}", "warn")

    def _quick_ask(self):
        """The hotkey was pressed: capture the window and the selection, then
        open the popup. Runs on the main thread."""
        if self.quick_popup is not None and self.quick_popup.winfo_exists():
            self.quick_popup.lift()
            self.quick_popup.focus_force()
            return
        if self._qa_busy:
            return
        acc = self.settings.access
        cfg = self.settings.get("quickask", {})
        info = quickask.foreground()
        own = info.get("pid") == os.getpid()
        reason = "" if own else quickask.never_capture(info, cfg.get("never_apps") or quickask.DEFAULT_NEVER)
        notes, image = [], None
        if own:
            info = {**info, "app": "Hybrid AI"}
        elif not reason:
            if acc.get("screen_capture", True):
                image = quickask.capture(info, cfg.get("whole_screen", False))
            else:
                notes.append("Screen capture is off (Settings → Access).")
        ctx = {"info": info, "image": image, "selection": None, "blocked_reason": reason, "notes": notes}
        if own or reason:
            return self._open_quick(ctx)
        if not acc.get("clipboard", True):
            notes.append("Reading the selection is off (Settings → Access).")
            return self._open_quick(ctx)
        if quickask.clipboard_has_only_nontext():
            notes.append("Your clipboard holds an image or files, so the selection wasn't read "
                         "(it would have been overwritten).")
            return self._open_quick(ctx)
        # Read the selection: put a marker on the clipboard, press Ctrl+C in
        # that app, see what arrives, then put your clipboard back.
        try:
            old = self.clipboard_get()
        except Exception:
            old = None
        marker = f"⁣hybridai-{time.time()}"
        self.clipboard_clear()
        self.clipboard_append(marker)
        self.update()
        self._qa_busy = True
        quickask.send_copy()
        self.after(60, lambda: self._wait_copy(ctx, old, marker, 8))

    def _wait_copy(self, ctx, old, marker, tries):
        try:
            now = self.clipboard_get()
        except Exception:
            now = None
        if (now is None or now == marker) and tries > 0:
            self.after(50, lambda: self._wait_copy(ctx, old, marker, tries - 1))
            return
        sel = now if now and now != marker else None
        self.clipboard_clear()
        if old is not None:
            self.clipboard_append(old)
        self.update()
        self._qa_busy = False
        ctx["selection"] = sel.strip() if sel and sel.strip() else None
        self._open_quick(ctx)

    def _open_quick(self, ctx):
        self.quick_popup = QuickAskPopup(
            self, ctx, ask_fn=lambda *a, **k: self.quick_ask_worker(ctx, *a, **k),
            on_open_in_app=self.import_quick_chat, on_replace=self._replace_selection)

    def quick_ask_worker(self, ctx, history, question, action, use_screen, use_sel,
                         on_token, on_done, should_stop, on_model):
        """One Quick Ask answer (worker thread). Captured content (screen,
        selection) goes to a local model unless 'Files and the cloud' is set
        to Always send."""
        info = ctx.get("info") or {}
        first = not history
        sel = ctx.get("selection") if (use_sel and first) else None
        img = ctx.get("image") if (use_screen and first) else None
        model = self.model
        captured = bool(sel or img)
        if captured and where(model) == "Cloud" and self.settings.get("cloud_files", "ask") != "allow":
            model = router.DEFAULT_MODEL
            self.events.put(("log", "Quick Ask: captured content stays local, so a local model answered"))
        screen_text, image_att = None, None
        if img is not None:
            text = quickask.ocr(img)
            if text and text.strip():
                screen_text = text
            elif quickask.ocr.last_error:
                self.events.put(("log", f"Quick Ask: {quickask.ocr.last_error}", "warn"))
            vision_ready = self.ollama_models is None or router.MODELS[VISION_KEY][0] in self.ollama_models
            if (not screen_text or model in router.VISION_MODELS) and vision_ready:
                image_att = attachments.load_image(img, name="screen.png")
                if model not in router.VISION_MODELS:
                    model = VISION_KEY
            elif not screen_text:
                on_token("I couldn't read the screen: Windows OCR isn't set up and the vision model isn't "
                         f"installed. Run `ollama pull {router.MODELS[VISION_KEY][0]}` (see the Log tab for OCR).")
                on_done(model)
                return
        if first:
            prompt = quickask.build_prompt(question, sel, screen_text, info.get("app"), info.get("title"), action)
        else:
            prompt = question or (quickask.ACTIONS[action] + " (Apply it to the same text as before.)")
        content = prompt if image_att is None else [
            {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_att.image_url}}]
        system = None
        mem_cfg = self.settings.get("memory", {})
        if self.mem and mem_cfg.get("enabled", True) and (where(model) == "Local" or mem_cfg.get("cloud")):
            try:
                system = memory.context_block([], self.mem.user_facts())
            except Exception:
                system = None
        on_model(model)
        try:
            stream_chat(history, question or action or "Explain this", "manual", on_token=on_token,
                        on_done=lambda r, why, ms: on_done(r), model_override=model,
                        should_stop=should_stop, content=content, system=system,
                        fit_args={"budget": int(self.settings.get("context_tokens", 8192))})
        except Exception as e:
            on_token(f"\n\nError: {e}")
            on_done(model)

    def import_quick_chat(self, history, turns):
        """'Open in app': continue a Quick Ask chat in the main window."""
        if self.gen:
            return
        first = next((t["text"] for t in turns if t["role"] == "user"), "Quick Ask")
        conv = Conversation(title=("Quick Ask: " + first)[:40])
        conv.history = list(history)
        now = time.time()
        conv.turns = [{**t, "at": now} for t in turns]
        self.conversations[conv.id] = conv
        self.order.insert(0, conv.id)
        self.select_chat(conv.id)
        self._save(conv)

    def _replace_selection(self, text, ctx):
        """Put the answer where your selection was (paste it into that app)."""
        try:
            old = self.clipboard_get()
        except Exception:
            old = None
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        quickask.focus((ctx.get("info") or {}).get("hwnd"))

        def restore():
            self.clipboard_clear()
            if old is not None:
                self.clipboard_append(old)
            self.update()

        self.after(200, quickask.send_paste)
        self.after(900, restore)

    # ================= code explainer =================
    def open_code(self):
        CodeWindow(self, self.settings, self.gate, on_start=self.start_code_chat,
                   blocked=lambda p: self.gate.blocked_reason(access._norm(p)))

    def start_code_chat(self, title, display, build, project, journal=None):
        """A new chat about a project. `build` prepares the prompt in the
        worker thread (it may embed the project's functions first)."""
        if self.gen:
            self.composer.show_notice("Wait for the current reply to finish first.", "info")
            return
        conv = Conversation(title=title)
        self.conversations[conv.id] = conv
        self.order.insert(0, conv.id)
        self.select_chat(conv.id)
        model = "local-coder" if where(self.model) == "Local" else self.model
        meta = [{"name": project.name, "kind": "code", "detail": "project", "path": project.root,
                 "editable": False}]
        on_finished = None
        if journal:
            def on_finished(c, turn, project=project, journal=journal):
                project.mark_reviewed(changes=journal, explanation=turn["text"],
                                      blocked=lambda p: self.gate.blocked_reason(access._norm(p)))
                c.turns.append({"role": "note", "at": time.time(),
                                "text": "Saved to the change journal and marked as reviewed: next time only "
                                        "newer changes are explained."})
                if self.active is c:
                    self.chat.add_note(c.turns[-1]["text"])
                self._save(c)
        self._generate(conv, display, model, meta=meta, build=build, use_memory=False,
                       title=title, on_finished=on_finished)

    def open_facts(self):
        self.open_memory("facts")

    def open_memory(self, select=None):
        if not self.mem:
            dialogs.info(self, "Memory unavailable", "The memory database couldn't be opened; see the Log tab.")
            return
        MemoryWindow(self, self.mem, on_continue=self.continue_from, on_changed=self._refresh_sidebar,
                     on_save_summary=self._save_summary_edit, select=select)

    def _save_summary_edit(self, sid, title, data):
        try:
            self.mem.update_summary(sid, title, data, self.embed, getattr(self.embed, "model", None))
        except Exception as e:
            self.inspector.add_log(f"couldn't save the summary: {e}", "warn")
        self._refresh_sidebar()

    def continue_from(self, sid):
        """Start a new chat that carries one past session's summary."""
        if self.gen or not self.mem:
            return
        sm = self.mem.get_summary(sid)
        if not sm:
            return
        conv = Conversation(title=sm["title"])
        conv.history.append({"role": "system", "content": memory.context_block([(1.0, sm)], [])})
        conv.turns.append({"role": "note", "at": time.time(),
                           "text": f"Continuing from “{sm['title']}”. The assistant has that session's summary."})
        self.conversations[conv.id] = conv
        self.order.insert(0, conv.id)
        self.select_chat(conv.id)

    # ================= editing files =================
    def _editable_files(self, conv):
        """Files in this chat the model may propose changes to, with the
        fingerprint the file should have now (updated after Apply / Undo)."""
        if not self.settings.access.get("edit_files", True):
            return []
        files = {}
        for t in conv.turns:
            for a in t.get("attachments") or []:
                if a.get("editable") and a.get("path"):
                    files[a["path"]] = {"name": a["name"], "path": a["path"], "sha": a.get("sha")}
            for e in t.get("edits") or []:
                if e["path"] in files and e["status"] == "applied":
                    files[e["path"]]["sha"] = e.get("applied_sha")
                elif e["path"] in files and e["status"] == "undone":
                    files[e["path"]]["sha"] = e.get("restored_sha")
        return [f for f in files.values() if self.gate.check(f["path"], "edit")[0]]

    def _show_edits(self, conv, turn_idx, msg):
        self._edit_msgs[(conv.id, turn_idx)] = msg
        msg.set_edits(conv.turns[turn_idx]["edits"],
                      on_review=lambda k: self.review_edit(conv, turn_idx, k),
                      on_undo=lambda k: self.undo_edit(conv, turn_idx, k))

    def _refresh_edit_row(self, conv, turn_idx):
        msg = self._edit_msgs.get((conv.id, turn_idx))
        if msg is not None and self.active is conv:
            try:
                self._show_edits(conv, turn_idx, msg)
            except Exception:
                log.exception("couldn't refresh the edit list in a chat")
        self._save(conv)
        self.inspector.set_recent_edits(editor.recent_edits(8), self._undo_record)

    def review_edit(self, conv, turn_idx, k):
        if self.gen:
            return
        e = conv.turns[turn_idx]["edits"][k]
        ok, reason = self.gate.check(e["path"], "edit")
        if not ok:
            dialogs.info(self, "Can't edit this file", reason if reason != "outside" else
                         "This file is outside the allowed folders (Settings → Access).", detail=e["path"])
            return
        try:
            old, _, _ = editor.read_current(e["path"])
        except OSError as ex:
            dialogs.info(self, "Can't read the file", str(ex), detail=e["path"])
            return
        base = self._editable_files_sha(conv, e["path"], before_turn=turn_idx) or e.get("base_sha")
        warnings = editor.checks(e["path"], old, e["new_text"], base)
        rows = editor.diff_rows(old, e["new_text"])
        DiffWindow(self, e["path"], rows, editor.stats(old, e["new_text"]), warnings,
                   on_apply=lambda: self._apply_edit(conv, turn_idx, k),
                   on_discard=lambda: self._discard_edit(conv, turn_idx, k),
                   new_text=e["new_text"], title_name=e["name"])

    def _editable_files_sha(self, conv, path, before_turn):
        """The fingerprint the file had when this proposal was made."""
        sha = None
        for t in conv.turns[:before_turn]:
            for a in t.get("attachments") or []:
                if a.get("path") == path:
                    sha = a.get("sha")
            for e in t.get("edits") or []:
                if e["path"] == path and e["status"] == "applied":
                    sha = e.get("applied_sha")
                elif e["path"] == path and e["status"] == "undone":
                    sha = e.get("restored_sha")
        return sha

    def _apply_edit(self, conv, turn_idx, k):
        e = conv.turns[turn_idx]["edits"][k]
        ok, reason = self.gate.check(e["path"], "edit")
        if not ok:
            dialogs.info(self, "Can't edit this file", reason, detail=e["path"])
            return
        try:
            record = editor.apply(e["path"], e["new_text"], conv.id)
        except Exception as ex:
            dialogs.info(self, "Couldn't write the file", str(ex), detail=e["path"])
            return
        e.update(status="applied", record=record, applied_sha=record["applied_sha"])
        self.inspector.add_log(f"applied change to {e['name']} · backup: {record['backup']}", "local")
        self.composer.show_notice(f"Applied to {e['name']}. A backup was saved; Undo is under the reply.", "ok")
        self._refresh_edit_row(conv, turn_idx)

    def _discard_edit(self, conv, turn_idx, k):
        e = conv.turns[turn_idx]["edits"][k]
        e["status"] = "discarded"
        self.inspector.add_log(f"discarded change to {e['name']}")
        self._refresh_edit_row(conv, turn_idx)

    def undo_edit(self, conv, turn_idx, k):
        e = conv.turns[turn_idx]["edits"][k]
        if self._undo_record(e.get("record")):
            e["status"] = "undone"
            try:
                e["restored_sha"] = editor.sha_file(e["path"])
            except OSError:
                log.exception("couldn't read the restored file's hash after undo")
            self._refresh_edit_row(conv, turn_idx)

    def _undo_record(self, record):
        """Restore a backup. Returns True if it was restored."""
        if not record:
            return False
        try:
            editor.undo(record)
        except editor.EditError as ex:
            if str(ex) != "changed-since":
                dialogs.info(self, "Couldn't undo", str(ex), detail=record.get("path"))
                return False
            choice = dialogs.ask(
                self, "The file changed after this edit",
                "Undoing now puts back the version from before the edit, and your later changes to "
                "the file would be lost (the current version is backed up first).",
                [("undo", "Undo anyway"), ("cancel", "Cancel")], primary="cancel", detail=record["path"])
            if choice != "undo":
                return False
            try:
                editor.apply(record["path"], editor.read_current(record["path"])[0])  # back up current
                editor.undo(record, force=True)
            except Exception as ex2:
                dialogs.info(self, "Couldn't undo", str(ex2), detail=record.get("path"))
                return False
        self.inspector.add_log(f"undone: {os.path.basename(record['path'])} restored from backup", "warn")
        self.composer.show_notice(f"Undone: {os.path.basename(record['path'])} is back to before.", "ok")
        self.inspector.set_recent_edits(editor.recent_edits(8), self._undo_record)
        # mark the matching edit in any open chat as undone
        for c in self.conversations.values():
            for i, t in enumerate(c.turns):
                for e in t.get("edits") or []:
                    if (e.get("record") or {}).get("id") == record.get("id") and e["status"] == "applied":
                        e["status"] = "undone"
                        try:
                            e["restored_sha"] = editor.sha_file(e["path"])
                        except OSError:
                            log.exception("couldn't read the restored file's hash to update a chat's edit status")
                        self._refresh_edit_row(c, i)
        return True

    # ================= provider status =================
    def _health_check(self):
        q, host = self.events, self.ollama_host
        self._check_idle()
        self._retry_summaries()
        self._end_idle_others()
        mem, embed = self.mem, self.embed

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
            # (The router's classifier isn't used in manual mode, so it isn't checked.)
            if mem:  # summaries saved while the embedding model was unavailable
                try:
                    n = mem.embed_missing(embed, getattr(embed, "model", None))
                    if n:
                        q.put(("log", f"memory: indexed {n} summar{'ies' if n != 1 else 'y'} for search"))
                except Exception:
                    log.exception("couldn't index summaries saved without the embedding model")

        threading.Thread(target=work, daemon=True).start()
        self.after(HEALTH_EVERY_MS, self._health_check)


if __name__ == "__main__":
    app = HybridAIApp()
    app.mainloop()
