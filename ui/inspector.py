"""Right panel: Run (model picker, last reply, timings), Log, Settings (incl. Access)."""
import os
import time
from tkinter import filedialog

import customtkinter as ctk

from .theme import C, Fonts, MODEL_INFO, section_label

MODEL_HINT = ("You choose the model; nothing is routed automatically. Local models run on "
              "this laptop. Cloud models send your message to Google or Groq.")


class Inspector(ctk.CTkFrame):
    def __init__(self, master, on_model, on_setting, on_clear, env_info, settings=None, gate=None,
                 data_dir=None):
        super().__init__(master, width=330, corner_radius=0, fg_color=C["panel"])
        self.grid_propagate(False)
        self.on_model = on_model
        self.on_setting = on_setting
        self.settings = settings
        self.gate = gate
        self.data_dir = data_dir
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

        section_label(p, "Model").grid(row=0, column=0, pady=(14, 6), **pad)
        ctk.CTkLabel(p, text=MODEL_HINT, font=Fonts.ui(12), text_color=C["muted"], wraplength=280,
                     justify="left", anchor="w").grid(row=1, column=0, pady=(0, 8), **pad)
        self.model_var = ctk.StringVar(value="local-general")
        self._model_rows = {}
        options = [(k, v["name"], v["desc"], v["where"]) for k, v in MODEL_INFO.items()]
        for i, (key, name, desc, where) in enumerate(options):
            row = ctk.CTkFrame(p, fg_color=C["card"], corner_radius=10, border_width=1,
                               border_color=C["border"])
            row.grid(row=2 + i, column=0, pady=3, **pad)
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
            local = where == "Local"
            ctk.CTkLabel(row, text=f" {where} ", height=20, corner_radius=5, font=Fonts.ui(11),
                         fg_color=C["local_bg"] if local else C["cloud_bg"],
                         text_color=C["local_fg"] if local else C["cloud_fg"]
                         ).grid(row=0, column=1, rowspan=2, padx=12)
            self._model_rows[key] = (row, rb, where)

        section_label(p, "Last reply").grid(row=10, column=0, pady=(20, 6), **pad)
        card = ctk.CTkFrame(p, fg_color=C["card"], corner_radius=12, border_width=1,
                            border_color=C["border"])
        card.grid(row=11, column=0, **pad)
        card.grid_columnconfigure((0, 1, 2), weight=1)
        self.route_model = ctk.CTkLabel(card, text="No messages yet", font=Fonts.ui(13, "bold"),
                                        text_color=C["accent_hi"], anchor="w")
        self.route_model.grid(row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(12, 0))
        self.route_reason = ctk.CTkLabel(card, text="Send a message to see which model answered and how fast.",
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
        self._highlight_model()
        self.on_model(key)

    def set_model(self, key):
        """Show `key` as selected (without calling on_model)."""
        self.model_var.set(key)
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
        self._settings_page = p
        self._r = 0
        st = self.settings

        def row_next():
            self._r += 1
            return self._r - 1

        def switch(label, desc, getter, setter):
            row = ctk.CTkFrame(p, fg_color="transparent")
            row.grid(row=row_next(), column=0, sticky="ew", padx=12, pady=(12, 0))
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=label, font=Fonts.ui(13), text_color=C["text"], anchor="w",
                         height=18).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(row, text=desc, font=Fonts.ui(11), text_color=C["muted"], anchor="w",
                         height=14, wraplength=230, justify="left").grid(row=1, column=0, sticky="w")
            var = ctk.BooleanVar(value=getter())
            ctk.CTkSwitch(row, text="", variable=var, width=44, progress_color=C["accent"],
                          button_color=C["text"], fg_color=C["border2"],
                          command=lambda: setter(var.get())).grid(row=0, column=1, rowspan=2)

        def label(text, pady=(22, 4)):
            section_label(p, text).grid(row=row_next(), column=0, sticky="ew", padx=12, pady=pady)

        def note(text):
            ctk.CTkLabel(p, text=text, font=Fonts.ui(11), text_color=C["muted"], wraplength=280,
                         justify="left", anchor="w").grid(row=row_next(), column=0, sticky="ew",
                                                         padx=12, pady=(6, 0))

        label("Display", pady=(14, 0))
        switch("Show routing details", "Model, place and timing above each reply",
               lambda: st.get("show_meta", True) if st else True, lambda v: on_setting("show_meta", v))
        switch("Auto-scroll", "Follow the reply while it streams",
               lambda: st.get("autoscroll", True) if st else True, lambda v: on_setting("autoscroll", v))

        # ---- Access ----
        if st and self.gate:
            label("Access")
            note("The assistant can only read files inside these folders. Secrets (.env, keys, "
                 ".ssh) and system folders are always blocked.")
            self._folder_frame = ctk.CTkFrame(p, fg_color="transparent")
            self._folder_frame.grid(row=row_next(), column=0, sticky="ew", padx=12, pady=(8, 0))
            self._folder_frame.grid_columnconfigure(0, weight=1)
            self._render_folders()
            ctk.CTkButton(p, text="+  Add folder", height=32, corner_radius=8, font=Fonts.ui(12),
                          fg_color="transparent", hover_color=C["raised"], border_width=1,
                          border_color=C["border2"], text_color=C["text2"],
                          command=self._add_folder).grid(row=row_next(), column=0, sticky="w",
                                                         padx=12, pady=(6, 0))
            switch("Read files", "Attach and read files from the allowed folders",
                   lambda: st.access.get("read_files", True), lambda v: st.set_access("read_files", v))
            switch("Edit files", "Let the assistant propose changes; nothing is written until you Apply",
                   lambda: st.access.get("edit_files", True), lambda v: st.set_access("edit_files", v))

            label("Recent edits")
            note("Every applied change is backed up first. Undo puts the old version back.")
            self._edits_frame = ctk.CTkFrame(p, fg_color="transparent")
            self._edits_frame.grid(row=row_next(), column=0, sticky="ew", padx=12, pady=(8, 0))
            self._edits_frame.grid_columnconfigure(0, weight=1)
            self.set_recent_edits([], None)

            label("Files and the cloud")
            note("When a message with files or images goes to Gemini or Groq:")
            seg = ctk.CTkSegmentedButton(
                p, values=["Ask each time", "Never send", "Always send"], height=32, corner_radius=8,
                font=Fonts.ui(12), fg_color=C["bg"], selected_color=C["accent_sel"],
                selected_hover_color=C["accent_sel"], unselected_color=C["bg"],
                unselected_hover_color=C["raised"], text_color=C["accent_hi"],
                command=lambda v: on_setting("cloud_files", {"Ask each time": "ask", "Never send": "block",
                                                             "Always send": "allow"}[v]))
            seg.grid(row=row_next(), column=0, sticky="ew", padx=12, pady=(6, 0))
            seg.set({"ask": "Ask each time", "block": "Never send", "allow": "Always send"}
                    .get(st.get("cloud_files", "ask"), "Ask each time"))

            label("Context size")
            note("How much text the local models can take in at once. Must match Ollama's "
                 "OLLAMA_CONTEXT_LENGTH. 8192 suits a 6 GB GPU.")
            menu = ctk.CTkOptionMenu(
                p, values=["4096", "8192", "16384", "32768"], height=30, corner_radius=8,
                font=Fonts.ui(12), fg_color=C["card"], button_color=C["raised"],
                button_hover_color=C["border2"], text_color=C["text"],
                command=lambda v: on_setting("context_tokens", int(v)))
            menu.set(str(st.get("context_tokens", 8192)))
            menu.grid(row=row_next(), column=0, sticky="w", padx=12, pady=(6, 0))

        label("Connections")
        for name, value in env_info:
            row = ctk.CTkFrame(p, fg_color="transparent")
            row.grid(row=row_next(), column=0, sticky="ew", padx=12, pady=3)
            ctk.CTkLabel(row, text=name, font=Fonts.ui(12), text_color=C["text2"]).pack(side="left")
            ctk.CTkLabel(row, text=value, font=Fonts.mono(11), text_color=C["muted"]).pack(side="right")
        note("API keys are read from .env in the private data folder. If a cloud model fails, "
             "the reply falls back to a local model.")

        if self.data_dir:
            label("Private data")
            note(self.data_dir)
            ctk.CTkButton(p, text="Open data folder", height=32, corner_radius=8, font=Fonts.ui(12),
                          fg_color="transparent", hover_color=C["raised"], border_width=1,
                          border_color=C["border2"], text_color=C["text2"],
                          command=self._open_data_dir).grid(row=row_next(), column=0, sticky="w",
                                                            padx=12, pady=(6, 0))

        label("Conversation", pady=(22, 6))
        ctk.CTkButton(p, text="Clear this conversation", height=36, corner_radius=8,
                      fg_color="transparent", hover_color=C["stop_bg"], border_width=1,
                      border_color=C["stop_border"], text_color=C["stop_fg"], font=Fonts.ui(13),
                      command=on_clear).grid(row=row_next(), column=0, sticky="ew", padx=12, pady=(0, 14))
        return p

    # ---------- access folders ----------
    def _render_folders(self):
        f = self._folder_frame
        for w in f.winfo_children():
            w.destroy()
        folders = self.settings.access.get("folders", [])
        if not folders:
            ctk.CTkLabel(f, text="No folders allowed yet.", font=Fonts.ui(12), text_color=C["faint"],
                         anchor="w").grid(row=0, column=0, sticky="w")
        home = os.path.expanduser("~")
        for i, folder in enumerate(folders):
            row = ctk.CTkFrame(f, fg_color=C["card"], corner_radius=8, border_width=1,
                               border_color=C["border"])
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(0, weight=1)
            shown = folder.replace(home, "~", 1) if folder.startswith(home) else folder
            if len(shown) > 34:
                shown = "…" + shown[-33:]
            ctk.CTkLabel(row, text=shown, font=Fonts.mono(11), text_color=C["text2"], anchor="w"
                         ).grid(row=0, column=0, sticky="w", padx=10, pady=5)
            ctk.CTkButton(row, text="✕", width=26, height=24, corner_radius=6, font=Fonts.ui(11),
                          fg_color="transparent", hover_color=C["stop_bg"], text_color=C["muted"],
                          command=lambda fo=folder: self._remove_folder(fo)
                          ).grid(row=0, column=1, padx=4)

    def _add_folder(self):
        folder = filedialog.askdirectory(parent=self, title="Allow the assistant to read this folder")
        if folder:
            ok_reason = self.gate.blocked_reason(os.path.normcase(os.path.realpath(folder)))
            if ok_reason:
                self.add_log(f"not added: {ok_reason}", "warn")
                return
            self.gate.add_folder(os.path.normpath(folder))
            self.add_log(f"access: added {os.path.normpath(folder)}")
            self._render_folders()

    def _remove_folder(self, folder):
        self.gate.remove_folder(folder)
        self.add_log(f"access: removed {folder}")
        self._render_folders()

    def set_recent_edits(self, records, on_undo):
        f = getattr(self, "_edits_frame", None)
        if f is None:
            return
        for w in f.winfo_children():
            w.destroy()
        if not records:
            ctk.CTkLabel(f, text="No edits yet.", font=Fonts.ui(12), text_color=C["faint"],
                         anchor="w").grid(row=0, column=0, sticky="w")
            return
        for i, r in enumerate(records):
            row = ctk.CTkFrame(f, fg_color=C["card"], corner_radius=8, border_width=1,
                               border_color=C["border"])
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(0, weight=1)
            name = os.path.basename(r["path"])
            when = r.get("time", "")[5:16].replace("T", " ")  # MM-DD HH:MM
            ctk.CTkLabel(row, text=name if len(name) <= 22 else name[:21] + "…", font=Fonts.ui(12),
                         text_color=C["text2"] if not r.get("undone") else C["faint"], anchor="w"
                         ).grid(row=0, column=0, sticky="w", padx=(10, 4), pady=(5, 0))
            ctk.CTkLabel(row, text=when, font=Fonts.mono(10), text_color=C["faint"], anchor="w"
                         ).grid(row=1, column=0, sticky="w", padx=(10, 4), pady=(0, 5))
            if r.get("undone"):
                ctk.CTkLabel(row, text="undone", font=Fonts.ui(11), text_color=C["faint"]
                             ).grid(row=0, column=1, rowspan=2, padx=10)
            elif on_undo:
                ctk.CTkButton(row, text="Undo", width=54, height=26, corner_radius=6, font=Fonts.ui(11),
                              fg_color="transparent", hover_color=C["raised"], border_width=1,
                              border_color=C["border2"], text_color=C["text2"],
                              command=lambda rec=r: on_undo(rec)).grid(row=0, column=1, rowspan=2, padx=8)

    def refresh_access(self):
        if self.gate:
            self._render_folders()

    def _open_data_dir(self):
        try:
            os.startfile(self.data_dir)  # Windows only
        except Exception as e:
            self.add_log(f"couldn't open the folder: {e}", "warn")
