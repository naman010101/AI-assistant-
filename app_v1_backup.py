import customtkinter as ctk
import threading
from router import stream_chat

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class HybridAIApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Hybrid AI Assistant")
        self.geometry("700x800")
        self.iconbitmap("icon.ico")
        self.minsize(500, 600)

        self.mode = "auto"
        self.is_generating = False
        self.chat_history = []

        self.build_ui()

    def build_ui(self):
        # --- Top bar ---
        top = ctk.CTkFrame(self, height=50)
        top.pack(fill="x", padx=10, pady=(10, 0))

        ctk.CTkLabel(
            top, text="Hybrid AI",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(side="left", padx=10)

        self.mode_var = ctk.StringVar(value="auto")

        modes_frame = ctk.CTkFrame(top, fg_color="transparent")
        modes_frame.pack(side="right", padx=10)

        for m in ["auto", "local", "cloud"]:
            ctk.CTkRadioButton(
                modes_frame, text=m.capitalize(),
                variable=self.mode_var, value=m,
                command=self.change_mode,
                font=ctk.CTkFont(size=13)
            ).pack(side="left", padx=8)

        # --- Chat display ---
        self.chat_frame = ctk.CTkScrollableFrame(self)
        self.chat_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Input bar ---
        input_frame = ctk.CTkFrame(self)
        input_frame.pack(fill="x", padx=10, pady=(0, 10))

        self.input_box = ctk.CTkTextbox(
            input_frame, height=60,
            font=ctk.CTkFont(size=14),
            wrap="word"
        )
        self.input_box.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.input_box.bind("<Return>", self.on_enter)
        self.input_box.bind("<Shift-Return>", self.on_shift_enter)

        self.send_btn = ctk.CTkButton(
            input_frame, text="Send", width=70,
            command=self.send_message,
            font=ctk.CTkFont(size=14)
        )
        self.send_btn.pack(side="right")

        # --- Status bar ---
        self.status_label = ctk.CTkLabel(
            self, text="Mode: Auto | Ready",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.status_label.pack(pady=(0, 5))

    def change_mode(self):
        self.mode = self.mode_var.get()
        self.status_label.configure(
            text=f"Mode: {self.mode.capitalize()} | Ready"
        )

    def on_enter(self, event):
        if not event.state & 1:  # not shift
            self.send_message()
            return "break"

    def on_shift_enter(self, event):
        return  # allow newline

    def add_message(self, text, sender="user"):
        bubble_frame = ctk.CTkFrame(
            self.chat_frame,
            fg_color="#2563EB" if sender == "user" else "#374151",
            corner_radius=12
        )

        if sender == "user":
            bubble_frame.pack(
                anchor="e", padx=(60, 5), pady=4, fill="x"
            )
        else:
            bubble_frame.pack(
                anchor="w", padx=(5, 60), pady=4, fill="x"
            )

        label = ctk.CTkLabel(
            bubble_frame, text=text,
            wraplength=500,
            justify="left",
            font=ctk.CTkFont(size=13),
            anchor="w"
        )
        label.pack(padx=12, pady=8, anchor="w")

        self.scroll_to_bottom()
        return label

    def add_ai_bubble(self):
        bubble_frame = ctk.CTkFrame(
            self.chat_frame,
            fg_color="#374151",
            corner_radius=12
        )
        bubble_frame.pack(anchor="w", padx=(5, 60), pady=4, fill="x")

        textbox = ctk.CTkTextbox(
            bubble_frame,
            font=ctk.CTkFont(size=13),
            wrap="word",
            height=30,
            fg_color="#374151",
            activate_scrollbars=False,
            state="disabled"
        )
        textbox.pack(padx=12, pady=8, fill="x")

        meta_label = ctk.CTkLabel(
            bubble_frame, text="",
            font=ctk.CTkFont(size=10),
            text_color="gray",
            anchor="w"
        )
        meta_label.pack(padx=12, pady=(0, 6), anchor="w")

        self.scroll_to_bottom()
        return textbox, meta_label
        
    def scroll_to_bottom(self):
        self.chat_frame._parent_canvas.yview_moveto(1.0)

    def send_message(self):
        text = self.input_box.get("1.0", "end").strip()
        if not text or self.is_generating:
            return

        self.input_box.delete("1.0", "end")
        self.add_message(text, sender="user")

        self.is_generating = True
        self.send_btn.configure(state="disabled")
        self.status_label.configure(text=f"Mode: {self.mode.capitalize()} | Thinking...")

        ai_label, meta_label = self.add_ai_bubble()
        self.current_text = ""

        def on_token(token):
            self.current_text += token

            def update():
                ai_label.configure(state="normal")
                ai_label.insert("end", token)
                # Auto-resize height based on content
                lines = int(ai_label.index("end-1c").split(".")[0])
                ai_label.configure(height=max(30, lines * 20))
                ai_label.configure(state="disabled")
                self.scroll_to_bottom()

            self.after(0, update)

        def on_done(route_name, reason, elapsed_ms):
            info = f"{route_name} · {reason} · {elapsed_ms}ms"
            self.after(0, lambda: meta_label.configure(text=info))
            self.after(0, lambda: self.status_label.configure(
                text=f"Mode: {self.mode.capitalize()} | Ready"
            ))
            self.is_generating = False
            self.after(0, lambda: self.send_btn.configure(state="normal"))

        thread = threading.Thread(
            target=stream_chat,
            args=(self.chat_history, text, self.mode),
            kwargs={"on_token": on_token, "on_done": on_done},
            daemon=True
        )
        thread.start()


if __name__ == "__main__":
    app = HybridAIApp()
    app.mainloop()