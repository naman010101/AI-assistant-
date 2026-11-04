"""Quick Ask popup: a small window over the app you're working in.

It shows what it captured (screen, selected text) as chips you can switch
off, an ask box with one-click actions, and streams the answer. You can copy
the answer, put it in place of your selection, or move the chat into the
main window to continue there.
"""
import logging
import queue
import threading

import customtkinter as ctk

import quickask

from .theme import C, Fonts, model_label, where

log = logging.getLogger("hybridai")


class QuickAskPopup(ctk.CTkToplevel):
    def __init__(self, master, ctx, ask_fn, on_open_in_app, on_replace):
        """ctx: dict(info, image, selection, blocked_reason, notes)
        ask_fn(history, question, action, use_screen, use_selection, on_token, on_done, should_stop, on_model)
            runs in a worker thread (see app.quick_ask_worker)
        on_open_in_app(history, turns)   on_replace(text, ctx)"""
        super().__init__(master, fg_color=C["panel"])
        self.ctx, self.ask_fn = ctx, ask_fn
        self.on_open_in_app, self.on_replace = on_open_in_app, on_replace
        self.history, self.turns = [], []
        self.events = queue.Queue()
        self.busy = False
        self.stop_flag = threading.Event()
        self.answer = ""

        self.title("Quick Ask")
        self.attributes("-topmost", True)
        self.resizable(True, True)
        self.minsize(520, 300)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda e: self._escape())
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        info = ctx.get("info") or {}
        # ---- what was captured ----
        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        app = info.get("app") or "this window"
        ctk.CTkLabel(chips, text=f"◎  {app}", font=Fonts.ui(12, "bold"), text_color=C["text2"]).pack(side="left")
        self.use_screen = ctk.BooleanVar(value=ctx.get("image") is not None)
        self.use_sel = ctk.BooleanVar(value=bool(ctx.get("selection")))
        if ctx.get("image") is not None:
            ctk.CTkCheckBox(chips, text="Screen", variable=self.use_screen, font=Fonts.ui(12),
                            text_color=C["text2"], fg_color=C["accent"], hover_color=C["accent_hi"],
                            checkbox_width=16, checkbox_height=16, border_color=C["faint"]).pack(side="left", padx=(14, 0))
        if ctx.get("selection"):
            sel = " ".join(ctx["selection"].split())
            short = sel if len(sel) <= 46 else sel[:45] + "…"
            ctk.CTkCheckBox(chips, text=f"Selection: “{short}”", variable=self.use_sel, font=Fonts.ui(12),
                            text_color=C["text2"], fg_color=C["accent"], hover_color=C["accent_hi"],
                            checkbox_width=16, checkbox_height=16, border_color=C["faint"]).pack(side="left", padx=(14, 0))
        notes = list(ctx.get("notes") or [])
        if ctx.get("blocked_reason"):
            notes.insert(0, f"Nothing captured: {ctx['blocked_reason']}.")
        if notes:
            ctk.CTkLabel(self, text="  ".join(notes), font=Fonts.ui(11), text_color=C["warn"], anchor="w",
                         wraplength=560, justify="left").grid(row=1, column=0, sticky="ew", padx=16)

        # ---- ask ----
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 4))
        row.grid_columnconfigure(0, weight=1)
        self.entry = ctk.CTkEntry(row, placeholder_text="Ask about this…", height=36, corner_radius=10,
                                  fg_color=C["card"], border_color=C["border2"], text_color=C["text"],
                                  font=Fonts.ui(14))
        self.entry.grid(row=0, column=0, sticky="ew")
        self.entry.bind("<Return>", lambda e: self._ask())
        self.ask_btn = ctk.CTkButton(row, text="Ask", width=70, height=36, corner_radius=10, font=Fonts.ui(13, "bold"),
                                     fg_color=C["accent"], hover_color=C["accent_hi"], text_color=C["on_accent"],
                                     command=self._ask)
        self.ask_btn.grid(row=0, column=1, padx=(8, 0))
        acts = ctk.CTkFrame(self, fg_color="transparent")
        acts.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 6))
        for name in quickask.ACTIONS:
            ctk.CTkButton(acts, text=name, height=28, width=10, corner_radius=8, font=Fonts.ui(12),
                          fg_color="transparent", hover_color=C["raised"], border_width=1,
                          border_color=C["border2"], text_color=C["text2"],
                          command=lambda a=name: self._ask(action=a)).pack(side="left", padx=(0, 6))

        # ---- answer ----
        self.box = ctk.CTkTextbox(self, wrap="word", font=Fonts.ui(13), fg_color=C["card"], border_width=1,
                                  border_color=C["border"], text_color="#DADDE2", corner_radius=10)
        self.box.grid(row=4, column=0, sticky="nsew", padx=14)
        self.box.configure(state="disabled")
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=5, column=0, sticky="ew", padx=14, pady=10)
        self.model_lbl = ctk.CTkLabel(bar, text="", font=Fonts.ui(11), text_color=C["muted"])
        self.model_lbl.pack(side="left")
        self._mk(bar, "Open in app", self._open).pack(side="right")
        if ctx.get("selection") and (info.get("hwnd")):
            self.replace_btn = self._mk(bar, "Replace selection", self._replace)
            self.replace_btn.pack(side="right", padx=(0, 6))
        self._mk(bar, "Copy", self._copy).pack(side="right", padx=(0, 6))

        self._place()
        self.after(40, self._poll)
        self.after(80, lambda: (self.lift(), self.focus_force(), self.entry.focus_set()))

    # ---------- layout ----------
    def _mk(self, master, text, cmd):
        return ctk.CTkButton(master, text=text, height=30, width=10, corner_radius=8, font=Fonts.ui(12),
                             fg_color="transparent", hover_color=C["raised"], border_width=1,
                             border_color=C["border2"], text_color=C["text2"], command=cmd)

    def _place(self):
        w, h = 640, 460
        x, y = quickask.cursor_pos()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(10, min(x - w // 2, sw - w - 10))
        y = max(10, min(y + 20, sh - h - 60))
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- asking ----------
    def _ask(self, action=None):
        if self.busy:
            return
        q = self.entry.get().strip()
        if not q and not action:
            if not (self.use_sel.get() or self.use_screen.get()):
                return
        self.entry.delete(0, "end")
        self.busy = True
        self.stop_flag.clear()
        self.ask_btn.configure(state="disabled")
        self.answer = ""
        shown = q or action or "Explain this"
        self._write(("\n\n" if self.turns else "") + f"› {shown}\n\n", replace=not self.turns)
        self.turns.append({"role": "user", "text": shown})
        ev = self.events

        def work():
            self.ask_fn(self.history, q, action, self.use_screen.get(), self.use_sel.get(),
                        on_token=lambda t: ev.put(("tok", t)),
                        on_done=lambda route: ev.put(("done", route)),
                        should_stop=self.stop_flag.is_set,
                        on_model=lambda m: ev.put(("model", m)))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():
            return
        toks = []
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "tok":
                    toks.append(ev[1])
                elif ev[0] == "model":
                    self.model_lbl.configure(text=f"{model_label(ev[1])} · {where(ev[1]).lower()}")
                elif ev[0] == "done":
                    if toks:
                        self._write("".join(toks))
                        toks = []
                    self.busy = False
                    self.ask_btn.configure(state="normal")
                    self.turns.append({"role": "assistant", "text": self.answer, "route": ev[1],
                                       "meta": f"{model_label(ev[1])} · Quick Ask"})
                    self.entry.focus_set()
        except queue.Empty:
            log.debug("no more Quick Ask events this tick", exc_info=True)
        if toks:
            self._write("".join(toks))
        self.after(40, self._poll)

    def _write(self, text, replace=False):
        self.box.configure(state="normal")
        if replace:
            self.box.delete("1.0", "end")
        self.box.insert("end", text)
        self.box.see("end")
        self.box.configure(state="disabled")
        if not text.startswith(("› ", "\n\n› ")):
            self.answer += text

    # ---------- buttons ----------
    def _copy(self):
        if self.answer.strip():
            self.clipboard_clear()
            self.clipboard_append(self.answer.strip())
            self.model_lbl.configure(text="Copied.")

    def _replace(self):
        if self.answer.strip() and not self.busy:
            text = self.answer.strip()
            self.destroy()
            self.on_replace(text, self.ctx)

    def _open(self):
        if self.turns and not self.busy:
            self.on_open_in_app(self.history, self.turns)
            self.destroy()

    def _escape(self):
        if self.busy:
            self.stop_flag.set()
        else:
            self._close()

    def _close(self):
        self.stop_flag.set()
        self.destroy()
