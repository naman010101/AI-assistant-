"""Message input with Send / Stop."""
import customtkinter as ctk

from .theme import C, Fonts


class Composer(ctk.CTkFrame):
    def __init__(self, master, on_send, on_stop):
        super().__init__(master, fg_color=C["bg"], corner_radius=0)
        self.on_send = on_send
        self.on_stop = on_stop
        self.grid_columnconfigure(0, weight=1)

        box = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=14,
                           border_width=1, border_color=C["border2"])
        box.grid(row=0, column=0, sticky="ew", padx=28, pady=(6, 20))
        box.grid_columnconfigure(0, weight=1)

        self.input = ctk.CTkTextbox(box, height=58, wrap="word", fg_color="transparent",
                                    border_width=0, font=Fonts.ui(15), text_color=C["text"],
                                    activate_scrollbars=False)
        self.input.grid(row=0, column=0, sticky="ew", padx=(10, 10), pady=(8, 0))
        self.input.bind("<Return>", self._on_enter)

        bar = ctk.CTkFrame(box, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 10))
        bar.grid_columnconfigure(1, weight=1)
        self.hint = ctk.CTkLabel(bar, text="Enter to send · Shift+Enter for a new line · Esc to stop",
                                 font=Fonts.ui(12), text_color=C["muted"])
        self.hint.grid(row=0, column=0, sticky="w")

        self.stop_btn = ctk.CTkButton(
            bar, text="■  Stop", width=86, height=36, corner_radius=8, font=Fonts.ui(13),
            fg_color=C["stop_bg"], hover_color="#2E1D1D", border_width=1,
            border_color=C["stop_border"], text_color=C["stop_fg"], command=self.on_stop)
        self.send_btn = ctk.CTkButton(
            bar, text="Send", width=86, height=36, corner_radius=8, font=Fonts.ui(13, "bold"),
            fg_color=C["accent"], hover_color=C["accent_hi"], text_color=C["on_accent"],
            text_color_disabled=C["faint"], command=self._send)
        self.send_btn.grid(row=0, column=3, sticky="e")

    def _on_enter(self, event):
        if event.state & 0x0001:  # Shift held -> newline
            return None
        self._send()
        return "break"

    def _send(self):
        text = self.input.get("1.0", "end").strip()
        if text:
            self.on_send(text)

    # ---- public ----
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
            self.stop_btn.grid(row=0, column=2, sticky="e", padx=(0, 8))
            self.send_btn.configure(state="disabled", fg_color=C["raised"])
        else:
            self.stop_btn.grid_remove()
            self.send_btn.configure(state="normal", fg_color=C["accent"])
