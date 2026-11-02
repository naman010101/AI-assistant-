"""Left navigation: brand, new chat, sections, recent chats, provider status."""
import os
import tkinter as tk

import customtkinter as ctk

from .theme import C, Fonts, section_label


class Sidebar(ctk.CTkFrame):
    def __init__(self, master, on_new_chat, on_select_chat, on_delete_chat=None, icon_path=None,
                 on_open_memory=None, on_select_summary=None, on_open_code=None):
        super().__init__(master, width=232, corner_radius=0, fg_color=C["panel"],
                         border_width=0)
        self.grid_propagate(False)
        self.on_select_chat = on_select_chat
        self.on_delete_chat = on_delete_chat
        self.on_open_memory = on_open_memory
        self.on_select_summary = on_select_summary
        self._enabled = True
        self._chat_buttons = []
        self._status_rows = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)  # the recent-chats list stretches

        # --- brand ---
        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 14))
        logo = self._logo(brand, icon_path)
        logo.pack(side="left")
        text = ctk.CTkFrame(brand, fg_color="transparent")
        text.pack(side="left", padx=(10, 0))
        ctk.CTkLabel(text, text="Hybrid Workspace", font=Fonts.ui(14, "bold"),
                     text_color=C["text"], anchor="w", height=18).pack(anchor="w")
        ctk.CTkLabel(text, text="Local + cloud assistant", font=Fonts.ui(11),
                     text_color=C["muted"], anchor="w", height=14).pack(anchor="w")

        # --- new chat ---
        ctk.CTkButton(
            self, text="+  New conversation", height=40, corner_radius=10,
            font=Fonts.ui(14, "bold"), fg_color=C["accent_bg"], hover_color=C["accent_sel"],
            border_width=1, border_color=C["accent_border"], text_color=C["accent_hi"],
            command=on_new_chat,
        ).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 14))

        # --- sections (only Chats works today; the others are placeholders) ---
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=2, column=0, sticky="ew", padx=12)
        self._nav_item(nav, "Chats", active=True)
        self._nav_item(nav, "Memory", command=on_open_memory)
        self._nav_item(nav, "Code", command=on_open_code)
        self._nav_item(nav, "Reminders", soon=True)

        section_label(self, "Recent").grid(row=3, column=0, sticky="ew", padx=22, pady=(18, 4))

        self.chat_list = ctk.CTkScrollableFrame(
            self, fg_color="transparent", scrollbar_button_color=C["border2"],
            scrollbar_button_hover_color=C["faint"])
        self.chat_list.grid(row=4, column=0, sticky="nsew", padx=6)

        # --- provider status ---
        status = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=10,
                              border_width=1, border_color=C["border"])
        status.grid(row=5, column=0, sticky="ew", padx=12, pady=12)
        names = ("Ollama", "Gemini", "Groq", "Router")
        for name in names:
            row = ctk.CTkFrame(status, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(8 if name == names[0] else 2, 8 if name == names[-1] else 2))
            dot = ctk.CTkLabel(row, text="●", font=Fonts.ui(10), text_color=C["faint"], width=12)
            dot.pack(side="left")
            ctk.CTkLabel(row, text=name, font=Fonts.ui(12), text_color=C["text2"]).pack(side="left", padx=(6, 0))
            value = ctk.CTkLabel(row, text="checking…", font=Fonts.ui(11), text_color=C["muted"])
            value.pack(side="right")
            self._status_rows[name] = (dot, value)

    # ---------- public ----------
    def set_status(self, name, state, text):
        """state: 'ok' | 'warn' | 'err' | 'unknown'."""
        dot, value = self._status_rows[name]
        dot.configure(text_color={"ok": C["ok"], "warn": C["warn"], "err": C["err"]}.get(state, C["faint"]))
        value.configure(text=text)

    def set_chats(self, chats, active_id, summaries=None):
        """chats: list of (id, title) or (id, title, state), newest first.
        state "pending" = being summarised (shown greyed, not clickable).
        summaries: list of (summary id, title, date text) for past sessions."""
        for w in self.chat_list.winfo_children():
            w.destroy()
        self._chat_buttons = []
        self._pending_buttons = []
        for item in chats:
            chat_id, title = item[0], item[1]
            state = item[2] if len(item) > 2 else "open"
            active = chat_id == active_id
            if state == "pending":
                b = ctk.CTkButton(self.chat_list, text=f"{title}  · summarising…", anchor="w", height=34,
                                  corner_radius=8, font=Fonts.ui(12), text_color=C["faint"],
                                  fg_color="transparent", hover_color=C["panel"], state="disabled",
                                  text_color_disabled=C["faint"])
                b.pack(fill="x", padx=6, pady=1)
                self._pending_buttons.append(b)
                continue
            b = ctk.CTkButton(
                self.chat_list, text=title, anchor="w", height=36, corner_radius=8,
                font=Fonts.ui(13), text_color=C["text"] if active else C["text2"],
                fg_color=C["raised"] if active else "transparent", hover_color=C["raised"],
                border_width=1 if active else 0, border_color=C["border2"],
                command=lambda cid=chat_id: self.on_select_chat(cid),
            )
            b.pack(fill="x", padx=6, pady=1)
            if self.on_delete_chat:
                b.bind("<Button-3>", lambda e, cid=chat_id: self._chat_menu(e, cid))
            self._chat_buttons.append(b)
        if summaries:
            ctk.CTkLabel(self.chat_list, text="PAST SESSIONS", font=Fonts.ui(11, "bold"),
                         text_color=C["muted"], anchor="w").pack(fill="x", padx=16, pady=(14, 2))
            for sid, title, when in summaries:
                b = ctk.CTkButton(
                    self.chat_list, text=f"◷  {title}", anchor="w", height=32, corner_radius=8,
                    font=Fonts.ui(12), text_color=C["muted"], fg_color="transparent",
                    hover_color=C["raised"],
                    command=lambda s=sid: self.on_select_summary and self.on_select_summary(s))
                b.pack(fill="x", padx=6, pady=0)
                self._chat_buttons.append(b)
        self.set_enabled(self._enabled)

    def set_enabled(self, enabled):
        """Chats can't be switched while a reply is streaming."""
        self._enabled = enabled
        for b in self._chat_buttons:
            b.configure(state="normal" if enabled else "disabled")

    # ---------- helpers ----------
    def _chat_menu(self, event, chat_id):
        """Right-click on a chat: a small menu with Delete."""
        if not self._enabled:
            return
        menu = tk.Menu(self, tearoff=0, bg=C["card"], fg=C["text"], activebackground=C["raised"],
                       activeforeground=C["text"], bd=0)
        menu.add_command(label="Delete conversation", command=lambda: self.on_delete_chat(chat_id))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _logo(self, master, icon_path):
        if icon_path and os.path.exists(icon_path):
            try:
                from PIL import Image
                img = ctk.CTkImage(Image.open(icon_path), size=(30, 30))
                return ctk.CTkLabel(master, text="", image=img, width=30)
            except Exception:
                pass
        return ctk.CTkLabel(master, text="H", width=30, height=30, corner_radius=9,
                            fg_color=C["accent"], text_color=C["on_accent"], font=Fonts.ui(14, "bold"))

    def _nav_item(self, master, text, active=False, soon=False, command=None):
        if command:
            ctk.CTkButton(master, text=text, anchor="w", height=36, corner_radius=8, font=Fonts.ui(13),
                          fg_color="transparent", hover_color=C["raised"], text_color=C["text2"],
                          command=command).pack(fill="x", pady=1)
            return
        row = ctk.CTkFrame(master, height=36, corner_radius=8,
                           fg_color=C["raised"] if active else "transparent")
        row.pack(fill="x", pady=1)
        row.pack_propagate(False)
        ctk.CTkLabel(row, text=text, font=Fonts.ui(13),
                     text_color=C["text"] if active else C["muted"]).pack(side="left", padx=12)
        if soon:
            ctk.CTkLabel(row, text="soon", font=Fonts.ui(11), text_color=C["faint"]).pack(side="right", padx=10)
