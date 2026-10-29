"""Message input with attachments (📎, Ctrl+V), Send / Stop."""
import customtkinter as ctk

from .theme import C, Fonts

KIND_TAG = {"image": "IMG", "pdf": "PDF", "docx": "DOC", "xlsx": "XLS", "notebook": "NB",
            "code": "</>", "text": "TXT"}


class Composer(ctk.CTkFrame):
    def __init__(self, master, on_send, on_stop, on_attach=None, on_paste=None,
                 on_remove_attachment=None):
        super().__init__(master, fg_color=C["bg"], corner_radius=0)
        self.on_send = on_send
        self.on_stop = on_stop
        self.on_paste = on_paste
        self.on_remove_attachment = on_remove_attachment
        self._has_attachments = False
        self.grid_columnconfigure(0, weight=1)

        # a one-line notice above the box ("notes.pdf is outside the allowed folders…")
        self.notice = ctk.CTkLabel(self, text="", font=Fonts.ui(12), text_color=C["warn"],
                                   anchor="w", justify="left", wraplength=760)
        self._notice_job = None

        box = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=14,
                           border_width=1, border_color=C["border2"])
        box.grid(row=1, column=0, sticky="ew", padx=28, pady=(6, 20))
        box.grid_columnconfigure(0, weight=1)

        # attachment chips (hidden while empty)
        self.chips = ctk.CTkFrame(box, fg_color="transparent")

        self.input = ctk.CTkTextbox(box, height=58, wrap="word", fg_color="transparent",
                                    border_width=0, font=Fonts.ui(15), text_color=C["text"],
                                    activate_scrollbars=False)
        self.input.grid(row=1, column=0, sticky="ew", padx=(10, 10), pady=(8, 0))
        self.input.bind("<Return>", self._on_enter)
        # <<Paste>> fires for Ctrl+V, Shift+Insert and the context menu. If the
        # clipboard holds an image or copied files, the app takes it; otherwise
        # the normal text paste happens.
        self.input.bind("<<Paste>>", self._on_paste)

        bar = ctk.CTkFrame(box, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 10))
        bar.grid_columnconfigure(2, weight=1)
        self.attach_btn = ctk.CTkButton(
            bar, text="📎", width=36, height=34, corner_radius=8, font=Fonts.ui(16),
            fg_color="transparent", hover_color=C["raised"], border_width=1,
            border_color=C["border2"], text_color=C["text2"], command=on_attach)
        self.attach_btn.grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.hint = ctk.CTkLabel(bar, text="Enter to send · Shift+Enter new line · "
                                           "Ctrl+V pastes images · Esc to stop",
                                 font=Fonts.ui(12), text_color=C["muted"])
        self.hint.grid(row=0, column=1, sticky="w")

        self.stop_btn = ctk.CTkButton(
            bar, text="■  Stop", width=86, height=36, corner_radius=8, font=Fonts.ui(13),
            fg_color=C["stop_bg"], hover_color="#2E1D1D", border_width=1,
            border_color=C["stop_border"], text_color=C["stop_fg"], command=self.on_stop)
        self.send_btn = ctk.CTkButton(
            bar, text="Send", width=86, height=36, corner_radius=8, font=Fonts.ui(13, "bold"),
            fg_color=C["accent"], hover_color=C["accent_hi"], text_color=C["on_accent"],
            text_color_disabled=C["faint"], command=self._send)
        self.send_btn.grid(row=0, column=4, sticky="e")

    def _on_enter(self, event):
        if event.state & 0x0001:  # Shift held -> newline
            return None
        self._send()
        return "break"

    def _on_paste(self, event):
        if self.on_paste and self.on_paste():
            return "break"  # the app attached an image/files; skip the text paste
        return None

    def _send(self):
        text = self.input.get("1.0", "end").strip()
        if text or self._has_attachments:
            self.on_send(text)

    # ---- public ----
    def get_text(self):
        return self.input.get("1.0", "end").strip()

    def clear(self):
        self.input.delete("1.0", "end")

    def set_text(self, text):
        self.clear()
        self.input.insert("1.0", text)
        self.focus()

    def focus(self):
        self.input.focus_set()

    def set_generating(self, generating):
        if generating:
            self.stop_btn.grid(row=0, column=3, sticky="e", padx=(0, 8))
            self.send_btn.configure(state="disabled", fg_color=C["raised"])
            self.attach_btn.configure(state="disabled")
        else:
            self.stop_btn.grid_remove()
            self.send_btn.configure(state="normal", fg_color=C["accent"])
            self.attach_btn.configure(state="normal")

    def set_attachments(self, items):
        """items: list of (label, kind). Each chip has an ✕ that calls
        on_remove_attachment(index)."""
        for w in self.chips.winfo_children():
            w.destroy()
        self._has_attachments = bool(items)
        if not items:
            self.chips.grid_remove()
            return
        self.chips.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        for i, (label, kind) in enumerate(items):
            chip = ctk.CTkFrame(self.chips, fg_color=C["raised"], corner_radius=8,
                                border_width=1, border_color=C["border2"])
            chip.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 6), pady=(0, 6))
            ctk.CTkLabel(chip, text=KIND_TAG.get(kind, "FILE"), font=Fonts.mono(10, "bold"),
                         text_color=C["accent_hi"], width=30).pack(side="left", padx=(8, 2))
            short = label if len(label) <= 46 else label[:44] + "…"
            ctk.CTkLabel(chip, text=short, font=Fonts.ui(12), text_color=C["text2"]).pack(side="left")
            ctk.CTkButton(chip, text="✕", width=24, height=24, corner_radius=6, font=Fonts.ui(11),
                          fg_color="transparent", hover_color=C["stop_bg"], text_color=C["muted"],
                          command=lambda idx=i: self.on_remove_attachment and self.on_remove_attachment(idx)
                          ).pack(side="left", padx=(4, 4), pady=3)

    def show_notice(self, text, kind="warn", seconds=8):
        color = {"warn": C["warn"], "err": C["err"], "ok": C["ok"], "info": C["muted"]}.get(kind, C["warn"])
        self.notice.configure(text=text, text_color=color)
        self.notice.grid(row=0, column=0, sticky="ew", padx=34, pady=(6, 0))
        if self._notice_job:
            self.after_cancel(self._notice_job)
        self._notice_job = self.after(seconds * 1000, self.hide_notice) if seconds else None

    def hide_notice(self):
        self._notice_job = None
        self.notice.grid_remove()
