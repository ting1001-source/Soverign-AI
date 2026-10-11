import queue
import threading
import tkinter as tk
from tkinter import font, scrolledtext

import ollama

# Must match the name you used with: ollama create soverign-ai-2.0v -f Modelfile
MODEL_NAME = "soverign-ai-2.0v"

# Colors
BG = "#0f1117"
PANEL = "#161922"
CHAT_BG = "#1a1d29"
INPUT_BG = "#222638"
BORDER = "#2e354a"
ACCENT = "#00f2fe"
USER_COLOR = "#38bdf8"
AI_COLOR = "#34d399"
SYS_COLOR = "#94a3b8"
THINK_COLOR = "#64748b"
ERR_COLOR = "#f87171"
OK_COLOR = "#00ff88"
WARN_COLOR = "#fbbf24"


class ThinkSplitter:
    """Splits a token stream into normal text and <think>...</think> text,
    even when a tag is split across two chunks."""

    def __init__(self):
        self.buf = ""
        self.in_think = False

    def feed(self, text):
        self.buf += text
        out = []
        while True:
            tag = "</think>" if self.in_think else "<think>"
            kind = "think" if self.in_think else "ai"
            i = self.buf.find(tag)
            if i != -1:
                if i:
                    out.append((kind, self.buf[:i]))
                self.buf = self.buf[i + len(tag):]
                self.in_think = not self.in_think
                continue
            keep = 0
            for n in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-n:]):
                    keep = n
                    break
            emit = self.buf[: len(self.buf) - keep]
            if emit:
                out.append((kind, emit))
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return out

    def flush(self):
        if not self.buf:
            return []
        kind = "think" if self.in_think else "ai"
        text, self.buf = self.buf, ""
        return [(kind, text)]


class SovereignAIApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sovereign-AI Studio")
        self.root.geometry("900x680")
        self.root.minsize(600, 420)
        self.root.configure(bg=BG)

        self.events = queue.Queue()
        self.history = []  # conversation memory sent to the model
        self.stop_event = threading.Event()
        self.generating = False

        self.title_font = font.Font(family="Segoe UI", size=13, weight="bold")
        self.chat_font = font.Font(family="Segoe UI", size=11)

        self._build_header()
        self._build_chat()
        self._build_input()

        self.append("System", "Sovereign-AI UI initialized. Enter = send, Shift+Enter = new line.\n", "system")
        self.root.after(30, self._poll_events)
        threading.Thread(target=self._check_model, daemon=True).start()
        self.input_field.focus_set()

    # ---------- UI construction ----------
    def _build_header(self):
        header = tk.Frame(self.root, bg=PANEL, height=50)
        header.pack(fill=tk.X)

        tk.Label(header, text="⚡ SOVEREIGN-AI STUDIO", fg=ACCENT, bg=PANEL,
                 font=self.title_font).pack(side=tk.LEFT, padx=20, pady=12)

        self.status_label = tk.Label(header, text="● Checking...", fg=WARN_COLOR, bg=PANEL,
                                     font=("Segoe UI", 9))
        self.status_label.pack(side=tk.RIGHT, padx=20, pady=12)

        self.clear_button = tk.Button(header, text="Clear chat", bg=INPUT_BG, fg="#e2e8f0",
                                      font=("Segoe UI", 9), bd=0, padx=10,
                                      activebackground=BORDER, activeforeground="#ffffff",
                                      command=self.clear_chat)
        self.clear_button.pack(side=tk.RIGHT, pady=12)

    def _build_chat(self):
        frame = tk.Frame(self.root, bg=BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)

        self.chat_display = scrolledtext.ScrolledText(
            frame, wrap=tk.WORD, bg=CHAT_BG, fg="#e2e8f0", insertbackground="white",
            font=self.chat_font, bd=0, highlightthickness=0, padx=12, pady=10,
        )
        self.chat_display.pack(fill=tk.BOTH, expand=True)
        self.chat_display.config(state=tk.DISABLED)

        self.chat_display.tag_config("user", foreground=USER_COLOR, font=("Segoe UI", 11, "bold"))
        self.chat_display.tag_config("ai", foreground=AI_COLOR, font=("Segoe UI", 11))
        self.chat_display.tag_config("think", foreground=THINK_COLOR, font=("Segoe UI", 10, "italic"))
        self.chat_display.tag_config("system", foreground=SYS_COLOR, font=("Segoe UI", 10, "italic"))
        self.chat_display.tag_config("error", foreground=ERR_COLOR, font=("Segoe UI", 10, "bold"))

    def _build_input(self):
        frame = tk.Frame(self.root, bg=PANEL)
        frame.pack(fill=tk.X, padx=15, pady=(0, 15))

        self.input_field = tk.Text(
            frame, height=3, wrap=tk.WORD, bg=INPUT_BG, fg="#ffffff", insertbackground="white",
            font=self.chat_font, bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT, padx=8, pady=6,
        )
        self.input_field.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(15, 10), pady=12)
        self.input_field.bind("<Return>", self._on_enter)
        self.input_field.bind("<Shift-Return>", lambda e: None)

        self.send_button = tk.Button(
            frame, text="Send", bg=ACCENT, fg=BG, font=("Segoe UI", 10, "bold"),
            activebackground="#00c0cb", activeforeground=BG, bd=0, padx=20, width=6,
            command=self.on_send_or_stop,
        )
        self.send_button.pack(side=tk.RIGHT, padx=(0, 15), pady=12, fill=tk.Y)

    # ---------- helpers ----------
    def _on_enter(self, event):
        if not (event.state & 0x1):  # Shift not held
            self.on_send_or_stop(send_only=True)
            return "break"

    def append(self, sender, message, tag):
        self.chat_display.config(state=tk.NORMAL)
        if sender == "You":
            self.chat_display.insert(tk.END, f"You: {message}\n\n", tag)
        elif sender == "System":
            self.chat_display.insert(tk.END, f"[{sender}]: {message}", tag)
        elif sender == "Error":
            self.chat_display.insert(tk.END, f"[Error]: {message}\n\n", tag)
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

    def append_raw(self, text, tag):
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.insert(tk.END, text, tag)
        self.chat_display.config(state=tk.DISABLED)
        self.chat_display.see(tk.END)

    def set_status(self, text, color):
        self.status_label.config(text=f"● {text}", fg=color)

    def set_generating(self, value):
        self.generating = value
        if value:
            self.send_button.config(text="Stop", bg=ERR_COLOR, activebackground="#dc2626")
            self.set_status("Thinking...", WARN_COLOR)
        else:
            self.send_button.config(text="Send", bg=ACCENT, activebackground="#00c0cb")

    # ---------- actions ----------
    def clear_chat(self):
        if self.generating:
            self.stop_event.set()
        self.history.clear()
        self.chat_display.config(state=tk.NORMAL)
        self.chat_display.delete("1.0", tk.END)
        self.chat_display.config(state=tk.DISABLED)
        self.append("System", "Chat cleared.\n", "system")

    def on_send_or_stop(self, send_only=False):
        if self.generating:
            if not send_only:
                self.stop_event.set()
            return

        prompt = self.input_field.get("1.0", tk.END).strip()
        if not prompt:
            return

        self.input_field.delete("1.0", tk.END)
        self.append("You", prompt, "user")
        self.history.append({"role": "user", "content": prompt})

        self.stop_event.clear()
        self.set_generating(True)
        self.append_raw("Sovereign-AI: ", "ai")
        threading.Thread(target=self._generate, args=(list(self.history),), daemon=True).start()

    # ---------- background work ----------
    def _check_model(self):
        try:
            data = ollama.list()
            models = data["models"] if isinstance(data, dict) else data.models
            names = []
            for m in models:
                name = m["name"] if isinstance(m, dict) and "name" in m else (
                    m["model"] if isinstance(m, dict) else getattr(m, "model", ""))
                names.append(name)
            found = any(n == MODEL_NAME or n.startswith(MODEL_NAME + ":") for n in names)
            if found:
                self.events.put(("status", "Model Ready", OK_COLOR))
            else:
                self.events.put(("status", "Model not found", WARN_COLOR))
                self.events.put(("system", f"Model '{MODEL_NAME}' not found. Run: ollama create {MODEL_NAME} -f Modelfile\n"))
        except Exception:
            self.events.put(("status", "Ollama offline", ERR_COLOR))
            self.events.put(("system", "Can't reach Ollama. Start it and try again.\n"))

    def _generate(self, messages):
        splitter = ThinkSplitter()
        answer = []
        started_answer = False
        try:
            stream = ollama.chat(model=MODEL_NAME, messages=messages, stream=True)
            for chunk in stream:
                if self.stop_event.is_set():
                    break
                content = chunk["message"]["content"]
                for kind, text in splitter.feed(content):
                    if kind == "ai":
                        if not started_answer:
                            text = text.lstrip()
                            if not text:
                                continue
                            started_answer = True
                        answer.append(text)
                    self.events.put(("token", kind, text))
            for kind, text in splitter.flush():
                if kind == "ai":
                    answer.append(text)
                self.events.put(("token", kind, text))
            self.events.put(("done", "".join(answer), self.stop_event.is_set()))
        except Exception as e:
            self.events.put(("error", f"Connection failed. Make sure Ollama is running! ({e})"))

    def _poll_events(self):
        """Runs on the UI thread; applies updates queued by the worker thread."""
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "token":
                    self.append_raw(ev[2], ev[1])
                elif kind == "done":
                    answer, stopped = ev[1], ev[2]
                    if answer:
                        self.history.append({"role": "assistant", "content": answer})
                    elif self.history and self.history[-1]["role"] == "user":
                        self.history.pop()
                    self.append_raw("\n\n" if not stopped else " [stopped]\n\n", "ai" if not stopped else "system")
                    self.set_generating(False)
                    self.set_status("Model Ready", OK_COLOR)
                elif kind == "error":
                    if self.history and self.history[-1]["role"] == "user":
                        self.history.pop()
                    self.append_raw("\n", "ai")
                    self.append("Error", ev[1], "error")
                    self.set_generating(False)
                    self.set_status("Error", ERR_COLOR)
                elif kind == "status":
                    self.set_status(ev[1], ev[2])
                elif kind == "system":
                    self.append("System", ev[1], "system")
        except queue.Empty:
            pass
        self.root.after(30, self._poll_events)


if __name__ == "__main__":
    root = tk.Tk()
    app = SovereignAIApp(root)
    root.mainloop()
