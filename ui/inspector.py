"""Right panel: Run (mode, model, why-this-route, timings), Log, Settings."""
import time

import customtkinter as ctk

from .theme import C, Fonts, MODEL_INFO, section_label

MODE_HINTS = {
    "auto": "Each prompt is classified (embeddings, with keyword rules as backup). "
            "Code → Qwen Coder, complex → Gemini, simple → Llama. Personal data always stays local.",
    "local": "Everything stays on this laptop: code → Qwen Coder, everything else → Llama 3.1 8B.",
    "cloud": "Complex → Gemini, quick questions and code → Groq. Falls back to Llama 3.1 8B on errors.",
}


class Inspector(ctk.CTkFrame):
    def __init__(self, master, on_mode, on_model, on_setting, on_clear, env_info):
        super().__init__(master, width=330, corner_radius=0, fg_color=C["panel"])
        self.grid_propagate(False)
        self.on_mode = on_mode
        self.on_model = on_model
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # --- tabs ---
        tabs = ctk.CTkFrame(self, fg_color="transparent")
        tabs.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
        self._tab_btns = {}
        for i, name in enumerate(("Run", "Log", "Settings")):
            b = ctk.CTkButton(tabs, text=name, width=76, height=38, corner_radius=0,
                              fg_color="transparent", hover_color=C["panel"], font=Fonts.ui(13, "bold"),
                              command=lambda n=name: self.show_tab(n))
            b.grid(row=0, column=i, padx=(0, 4))
            line = ctk.CTkFrame(tabs, width=76, height=2, corner_radius=0, fg_color="transparent")
            line.grid(row=1, column=i, sticky="ew", padx=(0, 4))
            self._tab_btns[name] = (b, line)
        ctk.CTkFrame(self, height=1, fg_color=C["border"]).grid(row=1, column=0, sticky="ew")

        self._pages = {
            "Run": self._build_run(),
            "Log": self._build_log(),
            "Settings": self._build_settings(on_setting, on_clear, env_info),
        }
        self.show_tab("Run")

    # ================= tabs =================
    def show_tab(self, name):
        for n, page in self._pages.items():
            b, line = self._tab_btns[n]
            active = n == name
            b.configure(text_color=C["text"] if active else C["muted"])
            line.configure(fg_color=C["accent"] if active else "transparent")
            if active:
                page.grid(row=2, column=0, sticky="nsew")
            else:
                page.grid_remove()

    def _page(self):
        return ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0,
                                      scrollbar_button_color=C["border2"],
                                      scrollbar_button_hover_color=C["faint"])

    # ================= Run =================
    def _build_run(self):
        p = self._page()
        pad = {"padx": 12, "sticky": "ew"}
        p.grid_columnconfigure(0, weight=1)

        section_label(p, "Mode").grid(row=0, column=0, pady=(14, 6), **pad)
        self.mode_btn = ctk.CTkSegmentedButton(
            p, values=["Auto", "Local", "Cloud"], height=36, corner_radius=8,
            font=Fonts.ui(13, "bold"), fg_color=C["bg"], selected_color=C["accent_sel"],
            selected_hover_color=C["accent_sel"], unselected_color=C["bg"],
            unselected_hover_color=C["raised"], text_color=C["accent_hi"],
            command=lambda v: self.on_mode(v.lower()))
        self.mode_btn.grid(row=1, column=0, **pad)
        self.mode_btn.set("Auto")
        self.mode_hint = ctk.CTkLabel(p, text=MODE_HINTS["auto"], font=Fonts.ui(12),
                                      text_color=C["muted"], wraplength=280, justify="left", anchor="w")
        self.mode_hint.grid(row=2, column=0, pady=(8, 0), **pad)

        section_label(p, "Model").grid(row=3, column=0, pady=(20, 6), **pad)
        self.model_var = ctk.StringVar(value="auto")
        self._model_rows = {}
        options = [("auto", "Let the router decide", "Follows the mode's rules", "")]
        options += [(k, v["name"], v["desc"], v["where"]) for k, v in MODEL_INFO.items()]
        for i, (key, name, desc, where) in enumerate(options):
            row = ctk.CTkFrame(p, fg_color=C["card"], corner_radius=10, border_width=1,
                               border_color=C["border"])
            row.grid(row=4 + i, column=0, pady=3, **pad)
            row.grid_columnconfigure(0, weight=1)
            rb = ctk.CTkRadioButton(
                row, text=name, value=key, variable=self.model_var, font=Fonts.ui(13),
                text_color=C["text"], text_color_disabled=C["faint"], fg_color=C["accent"],
                hover_color=C["accent_hi"], border_color=C["faint"], radiobutton_width=16,
                radiobutton_height=16, border_width_unchecked=2, border_width_checked=5,
                command=lambda k=key: self._pick_model(k))
            rb.grid(row=0, column=0, sticky="w", padx=12, pady=(9, 0))
            d = ctk.CTkLabel(row, text=desc, font=Fonts.ui(11), text_color=C["muted"], height=14)
            d.grid(row=1, column=0, sticky="w", padx=(40, 12), pady=(0, 9))
            if where:
                local = where == "Local"
                ctk.CTkLabel(row, text=f" {where} ", height=20, corner_radius=5, font=Fonts.ui(11),
                             fg_color=C["local_bg"] if local else C["cloud_bg"],
                             text_color=C["local_fg"] if local else C["cloud_fg"]
                             ).grid(row=0, column=1, rowspan=2, padx=12)
            self._model_rows[key] = (row, rb, where)

        section_label(p, "Why this route").grid(row=10, column=0, pady=(20, 6), **pad)
        card = ctk.CTkFrame(p, fg_color=C["card"], corner_radius=12, border_width=1,
                            border_color=C["border"])
        card.grid(row=11, column=0, **pad)
        card.grid_columnconfigure((0, 1, 2), weight=1)
        self.route_model = ctk.CTkLabel(card, text="No messages yet", font=Fonts.ui(13, "bold"),
                                        text_color=C["accent_hi"], anchor="w")
        self.route_model.grid(row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(12, 0))
        self.route_reason = ctk.CTkLabel(card, text="Send a prompt to see which model handles it and why.",
                                         font=Fonts.ui(12), text_color="#DADDE2", wraplength=270,
                                         justify="left", anchor="w")
        self.route_reason.grid(row=1, column=0, columnspan=3, sticky="w", padx=14, pady=(4, 10))
        self._stats = {}
        for col, (key, label) in enumerate([("ttft", "first token"), ("speed", "≈ tokens/s"),
                                            ("total", "total")]):
            box = ctk.CTkFrame(card, fg_color="transparent")
            box.grid(row=2, column=col, sticky="w", padx=(14, 0), pady=(0, 12))
            v = ctk.CTkLabel(box, text="—", font=Fonts.ui(15, "bold"), text_color=C["text"],
                             anchor="w", height=20)
            v.pack(anchor="w")
            ctk.CTkLabel(box, text=label, font=Fonts.ui(11), text_color=C["muted"], anchor="w",
                         height=14).pack(anchor="w")
            self._stats[key] = v

        self.session_lbl = ctk.CTkLabel(p, text="", font=Fonts.ui(11), text_color=C["muted"], anchor="w")
        self.session_lbl.grid(row=12, column=0, pady=(10, 14), **pad)
        return p

    def _pick_model(self, key):
        self.on_model(None if key == "auto" else key)

    def set_mode(self, mode):
        """Grey out models the mode doesn't allow, and reset an invalid pick."""
        self.mode_btn.set(mode.capitalize())
        self.mode_hint.configure(text=MODE_HINTS[mode])
        for key, (row, rb, where) in self._model_rows.items():
            allowed = (not where or mode == "auto" or
                       (mode == "local" and where == "Local") or (mode == "cloud" and where == "Cloud"))
            rb.configure(state="normal" if allowed else "disabled")
            row.configure(border_color=C["border"])
            if not allowed and self.model_var.get() == key:
                self.model_var.set("auto")
                self._pick_model("auto")
        self._highlight_model()

    def _highlight_model(self):
        sel = self.model_var.get()
        for key, (row, _rb, _w) in self._model_rows.items():
            row.configure(border_color=C["accent_border"] if key == sel else C["border"],
                          fg_color=C["accent_bg"] if key == sel else C["card"])

    def set_route(self, model_text, reason):
        self._highlight_model()
        self.route_model.configure(text=model_text)
        self.route_reason.configure(text=reason)

    def set_stats(self, ttft=None, speed=None, total=None):
        for key, val in (("ttft", ttft), ("speed", speed), ("total", total)):
            self._stats[key].configure(text=val if val is not None else "—")

    def set_session(self, text):
        self.session_lbl.configure(text=text)

    # ================= Log =================
    def _build_log(self):
        p = ctk.CTkFrame(self, fg_color="transparent")
        p.grid_columnconfigure(0, weight=1)
        p.grid_rowconfigure(1, weight=1)
        section_label(p, "Routing log").grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        self.log = ctk.CTkTextbox(p, wrap="word", font=Fonts.mono(12), fg_color=C["code_bg"],
                                  text_color="#A9B6C4", border_width=1, border_color=C["border"],
                                  corner_radius=10)
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.log.tag_config("time", foreground=C["faint"])
        self.log.tag_config("local", foreground=C["local_fg"])
        self.log.tag_config("cloud", foreground=C["cloud_fg"])
        self.log.tag_config("warn", foreground=C["warn"])
        self.log.configure(state="disabled")
        return p

    def add_log(self, text, kind=None):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S") + "  ", "time")
        self.log.insert("end", text + "\n", kind or "")
        self.log.configure(state="disabled")
        self.log.see("end")

    # ================= Settings =================
    def _build_settings(self, on_setting, on_clear, env_info):
        p = self._page()
        p.grid_columnconfigure(0, weight=1)
        r = 0

        def switch(label, desc, key, default=True):
            nonlocal r
            row = ctk.CTkFrame(p, fg_color="transparent")
            row.grid(row=r, column=0, sticky="ew", padx=12, pady=(12, 0))
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=label, font=Fonts.ui(13), text_color=C["text"], anchor="w",
                         height=18).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(row, text=desc, font=Fonts.ui(11), text_color=C["muted"], anchor="w",
                         height=14).grid(row=1, column=0, sticky="w")
            var = ctk.BooleanVar(value=default)
            ctk.CTkSwitch(row, text="", variable=var, width=44, progress_color=C["accent"],
                          button_color=C["text"], fg_color=C["border2"],
                          command=lambda: on_setting(key, var.get())).grid(row=0, column=1, rowspan=2)
            r += 1

        section_label(p, "Display").grid(row=r, column=0, sticky="ew", padx=12, pady=(14, 0)); r += 1
        switch("Show routing details", "Model, place and timing above each reply", "show_meta")
        switch("Auto-scroll", "Follow the reply while it streams", "autoscroll")

        section_label(p, "Connections").grid(row=r, column=0, sticky="ew", padx=12, pady=(22, 4)); r += 1
        for label, value in env_info:
            row = ctk.CTkFrame(p, fg_color="transparent")
            row.grid(row=r, column=0, sticky="ew", padx=12, pady=3)
            ctk.CTkLabel(row, text=label, font=Fonts.ui(12), text_color=C["text2"]).pack(side="left")
            ctk.CTkLabel(row, text=value, font=Fonts.mono(11), text_color=C["muted"]).pack(side="right")
            r += 1
        ctk.CTkLabel(p, text="Keys are read from .env. Cloud requests fall back to the local model on errors.",
                     font=Fonts.ui(11), text_color=C["muted"], wraplength=280, justify="left",
                     anchor="w").grid(row=r, column=0, sticky="ew", padx=12, pady=(6, 0)); r += 1

        section_label(p, "Conversation").grid(row=r, column=0, sticky="ew", padx=12, pady=(22, 6)); r += 1
        ctk.CTkButton(p, text="Clear this conversation", height=36, corner_radius=8,
                      fg_color="transparent", hover_color=C["stop_bg"], border_width=1,
                      border_color=C["stop_border"], text_color=C["stop_fg"], font=Fonts.ui(13),
                      command=on_clear).grid(row=r, column=0, sticky="ew", padx=12, pady=(0, 14))
        return p
