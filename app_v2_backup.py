"""Hybrid AI Assistant — desktop UI ("Premium Dark Workspace").

Threading model (the important part):
    Tkinter is single-threaded: only the main thread may touch widgets.
    The LLM call runs in a worker thread and pushes events into a
    queue.Queue. The main thread polls that queue every 40 ms with
    `after()`, drains everything that arrived, and updates the UI once per
    batch. That keeps the window responsive and avoids scheduling one
    Tk callback per token.
"""
import itertools
import json
import os
import queue
import threading
import time
import urllib.request

import customtkinter as ctk

import router
from router import stream_chat
from ui.chat_view import ChatView
from ui.composer import Composer
from ui.inspector import Inspector
from ui.sidebar import Sidebar
from ui.theme import C, Fonts, model_label, where

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POLL_MS = 40
HEALTH_EVERY_MS = 60_000
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # bypass system proxies
REQUIRED_LOCAL_MODELS = [m for m, c in router.MODELS.values() if c is router.local_client]


class Conversation:
    def __init__(self, conv_id):
        self.id = conv_id
        self.title = "New conversation"
        self.history = []  # the list router.stream_chat reads and appends to
        self.turns = []    # what the UI shows: dicts with role, text, route, meta, stopped


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
        self.mode = "auto"
        self.model_override = None
        self.settings = {"show_meta": True, "autoscroll": True}
        self.events = queue.Queue()
        self.gen = None              # info about the reply currently streaming
        self.gen_ids = itertools.count(1)
        self.conv_ids = itertools.count(1)
        self.conversations = {}
        self.order = []              # newest first
        self.active = None
        self.panel_visible = True
        self.session = {"local": 0, "cloud": 0, "fallback": 0}

        self._build()
        self.new_chat()
        self._set_mode("auto")

        self.bind_all("<Control-n>", lambda e: self.new_chat())
        self.bind_all("<Escape>", lambda e: self.stop())
        self.after(POLL_MS, self._poll)
        self.after(300, self._health_check)

    # ================= layout =================
    def _build(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.sidebar = Sidebar(self, on_new_chat=self.new_chat, on_select_chat=self.select_chat,
                               icon_path=os.path.join(BASE_DIR, "icon.png"))
        self.sidebar.grid(row=0, column=0, sticky="ns")
        ctk.CTkFrame(self, width=1, corner_radius=0, fg_color=C["border"]).grid(row=0, column=0, sticky="nse")

        center = ctk.CTkFrame(self, corner_radius=0, fg_color=C["bg"])
        center.grid(row=0, column=1, sticky="nsew")
        center.grid_rowconfigure(0, weight=1)
        center.grid_columnconfigure(0, weight=1)
        self.chat = ChatView(center, on_toggle_panel=self.toggle_panel,
                             on_suggestion=lambda t: self.composer.set_text(t))
        self.chat.grid(row=0, column=0, sticky="nsew")
        self.composer = Composer(center, on_send=self.send, on_stop=self.stop)
        self.composer.grid(row=1, column=0, sticky="ew")

        ollama_host = str(router.local_client.base_url).replace("/v1/", "").rstrip("/")
        env_info = [
            ("Ollama", ollama_host.replace("http://", "")),
            ("Gemini key", "set" if os.getenv("GEMINI_API_KEY") else "missing"),
            ("Groq key", "set" if os.getenv("GROQ_API_KEY") else "missing"),
        ]
        self.ollama_host = ollama_host
        self.inspector = Inspector(self, on_mode=self._set_mode, on_model=self._set_model,
                                   on_setting=self._set_setting, on_clear=self.clear_chat,
                                   env_info=env_info)
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
        conv = Conversation(next(self.conv_ids))
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
        self._render_active()
        self._refresh_sidebar()

    def _render_active(self):
        conv = self.active
        self.chat.show_meta = self.settings["show_meta"]
        if not conv.turns:
            self.chat.show_empty_state()
        else:
            self.chat.clear()
            for t in conv.turns:
                if t["role"] == "user":
                    self.chat.add_user(t["text"])
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
    def _set_mode(self, mode):
        self.mode = mode
        self.inspector.set_mode(mode)
        self.chat.set_mode(mode)

    def _set_model(self, key):
        if key == self.model_override:
            return
        self.model_override = key
        self.inspector.add_log(f"model → {model_label(key) if key else 'router decides'}")

    def _set_setting(self, key, value):
        self.settings[key] = value
        if key == "show_meta" and not self.gen:
            self._render_active()

    # ================= sending & streaming =================
    def send(self, text):
        if self.gen:
            return
        conv = self.active
        self.composer.clear()
        if not conv.turns:
            conv.title = text if len(text) <= 34 else text[:33].rstrip() + "…"
            self._refresh_sidebar()
        conv.turns.append({"role": "user", "text": text})
        self.chat.add_user(text)

        # Routing may call the embedding model, so it runs in the worker thread
        # and reports back through on_route instead of blocking the UI here.
        msg = self.chat.add_assistant()
        msg.set_waiting(None)
        self.inspector.set_route("Routing…", "Classifying the prompt.")
        self.inspector.set_stats("…", "…", "…")

        gen_id = next(self.gen_ids)
        stop_event = threading.Event()
        self.gen = {"id": gen_id, "msg": msg, "conv": conv, "route": None,
                    "start": time.perf_counter(), "first": None, "chunks": 0,
                    "text": "", "stop": stop_event}
        self.composer.set_generating(True)
        self.sidebar.set_enabled(False)
        self._update_header()

        q, mode, override = self.events, self.mode, self.model_override

        def work():
            try:
                stream_chat(conv.history, text, mode,
                            on_token=lambda tok: q.put((gen_id, "tok", tok)),
                            on_done=lambda r, why, ms: q.put((gen_id, "done", r, why, ms)),
                            on_route=lambda r, why: q.put((gen_id, "route", r, why)),
                            model_override=override, should_stop=stop_event.is_set)
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
        follow = self.settings["autoscroll"] and self.chat.is_near_bottom()
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
        g["conv"].turns.append({"role": "assistant", "text": g["text"], "route": route_name,
                                "meta": meta, "stopped": stopped})

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
        self.sidebar.set_enabled(True)
        self._update_header()
        self.composer.focus()

    # ================= provider status =================
    def _health_check(self):
        q, host = self.events, self.ollama_host

        def work():
            try:
                with LOCAL_HTTP.open(f"{host}/api/tags", timeout=2) as r:
                    names = {m["name"] for m in json.load(r).get("models", [])}
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
            # Embed the router's examples now, so the first prompt isn't slow.
            if router.warmup():
                q.put(("status", "Router", "ok", "embeddings"))
            else:
                q.put(("status", "Router", "warn", "keywords only"))

        threading.Thread(target=work, daemon=True).start()
        self.after(HEALTH_EVERY_MS, self._health_check)


if __name__ == "__main__":
    app = HybridAIApp()
    app.mainloop()
