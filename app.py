import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from caption_core import clean_transcript, make_prompt, safe_stem, save_note, topic_names, video_url, write_unique


ROOT = Path(__file__).resolve().parent
DEPS = ROOT / ".packages"
TRANSCRIPTS = ROOT / "Transcripts"
SETTINGS = ROOT / "settings.json"
MODES = ("Skim + detailed notes", "Quick skim", "Answer a question")
HIDE_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PYTHON = Path(sys.executable)
if PYTHON.name.casefold() == "pythonw.exe":
    PYTHON = PYTHON.with_name("python.exe")


class App:
    def __init__(self, window):
        self.window = window
        self.window.title("YouTube to Obsidian")
        self.window.geometry("960x770")
        self.window.minsize(780, 720)
        self.window.configure(bg="#f2f6fb")
        self.results = queue.Queue()
        self.fetched = None
        self.prepared = None
        self.saved_note = None
        self.busy = False

        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        self.vault = tk.StringVar(value=settings.get("vault", ""))
        self.url = tk.StringVar()
        self.title = tk.StringVar()
        self.language = tk.StringVar()
        self.mode = tk.StringVar(value=MODES[0])
        self.question = tk.StringVar()
        self.status = tk.StringVar(value="Ready. Paste a YouTube link to start.")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=(9, 6))
        style.configure("Action.TButton", background="#184d8a", foreground="white")
        style.map("Action.TButton", background=[("disabled", "#a9b9cc"), ("active", "#2360a4")])
        style.configure("TNotebook.Tab", padding=(18, 8))
        style.configure("Page.TFrame", background="#f7faff")
        style.configure("Page.TLabel", background="#f7faff", foreground="#213c54")
        style.configure("Small.TLabel", background="#f7faff", foreground="#465d70")

        mast = tk.Frame(window, background="#142c43", padx=22, pady=14)
        mast.pack(fill="x")
        tk.Label(mast, text="YouTube to Obsidian", bg="#142c43", fg="white",
                 font=("Segoe UI", 21, "bold")).pack(anchor="w")
        tk.Label(mast, text="Keep the transcript. Read the useful parts. Connect the ideas.",
                 bg="#142c43", fg="#d4e5f8", font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))
        self.tabs = ttk.Notebook(window)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(14, 5))
        self.read_tab = ttk.Frame(self.tabs, padding=15, style="Page.TFrame")
        self.save_tab = ttk.Frame(self.tabs, padding=15, style="Page.TFrame")
        self.tabs.add(self.read_tab, text="1  Transcript & ChatGPT")
        self.tabs.add(self.save_tab, text="2  Save to Obsidian")
        self.make_transcript_tab()
        self.make_save_tab()
        ttk.Label(window, textvariable=self.status, wraplength=720, padding=(20, 3, 20, 10)).pack(fill="x")
        self.window.after(125, self.poll)

    def row(self, tab):
        frame = ttk.Frame(tab, style="Page.TFrame")
        frame.pack(fill="x", pady=(0, 7))
        return frame

    def label(self, parent, text, small=False):
        return ttk.Label(parent, text=text, style="Small.TLabel" if small else "Page.TLabel")

    def editor(self, parent, height):
        box = ScrolledText(parent, wrap="word", height=height, undo=True,
                           relief="solid", borderwidth=1, padx=10, pady=8,
                           font=("Segoe UI", 10), foreground="#20364b")
        box.pack(fill="both", expand=True, pady=(2, 9))
        return box

    def make_transcript_tab(self):
        row = self.row(self.read_tab)
        self.label(row, "Video link").pack(side="left")
        entry = ttk.Entry(row, textvariable=self.url)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        entry.focus_set()
        self.fetch_button = ttk.Button(row, text="Get transcript", style="Action.TButton", command=self.fetch)
        self.fetch_button.pack(side="left")
        ttk.Button(row, text="New video", command=self.reset).pack(side="left", padx=(6, 0))

        row = self.row(self.read_tab)
        self.label(row, "Title").pack(side="left")
        ttk.Entry(row, textvariable=self.title).pack(side="left", fill="x", expand=True, padx=8)
        self.label(row, "Caption language (optional)").pack(side="left", padx=(6, 4))
        ttk.Entry(row, textvariable=self.language, width=9).pack(side="left")

        row = self.row(self.read_tab)
        self.label(row, "Transcript").pack(side="left")
        self.update_button = ttk.Button(row, text="Install / update caption reader", command=self.install)
        self.update_button.pack(side="right")
        ttk.Button(row, text="Open YouTube", command=self.open_video).pack(side="right", padx=6)
        ttk.Button(row, text="Load TXT / SRT / VTT", command=self.load_transcript).pack(side="right")
        self.label(self.read_tab, "If fetch fails, open the video, choose Show transcript, then copy and paste the captions below.", True).pack(anchor="w")
        self.transcript = self.editor(self.read_tab, 12)

        row = self.row(self.read_tab)
        self.label(row, "Output").pack(side="left")
        ttk.Combobox(row, textvariable=self.mode, values=MODES, state="readonly", width=25).pack(side="left", padx=8)
        self.label(row, "Question or focus").pack(side="left", padx=(6, 4))
        ttk.Entry(row, textvariable=self.question).pack(side="left", fill="x", expand=True)

        row = self.row(self.read_tab)
        ttk.Button(row, text="Save transcript + copy ChatGPT prompt", style="Action.TButton",
                   command=self.prepare).pack(side="left")
        ttk.Button(row, text="Open transcript folder", command=self.open_transcripts).pack(side="left", padx=6)
        ttk.Button(row, text="Open ChatGPT", command=lambda: webbrowser.open("https://chatgpt.com/")).pack(side="left")
        self.label(self.read_tab, "In ChatGPT, attach the saved TXT and paste the prompt. Bring the Markdown back in step 2.", True).pack(anchor="w")

    def make_save_tab(self):
        self.label(self.save_tab, "Choose the folder you open as an Obsidian vault. Set it before step 1 to reuse your existing YouTube topic names.", True).pack(anchor="w", pady=(0, 7))
        row = self.row(self.save_tab)
        self.label(row, "Vault folder").pack(side="left")
        ttk.Entry(row, textvariable=self.vault).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(row, text="Choose folder", command=self.choose_vault).pack(side="left")

        row = self.row(self.save_tab)
        self.label(row, "Paste ChatGPT's Markdown here, or load its Markdown file.").pack(side="left")
        ttk.Button(row, text="Load Markdown", command=self.load_note).pack(side="right")
        self.note_body = self.editor(self.save_tab, 15)
        row = self.row(self.save_tab)
        ttk.Button(row, text="Save note & link topics", style="Action.TButton", command=self.import_note).pack(side="left")
        ttk.Button(row, text="Open saved note folder", command=self.open_note_folder).pack(side="left", padx=6)
        self.label(self.save_tab, "Video notes go in YouTube/Notes. Topic notes go in YouTube/Topics. See the connections in Obsidian's Graph view.", True).pack(anchor="w")

    def show_error(self, action, error):
        self.status.set(action + " failed.")
        messagebox.showerror(action, str(error), parent=self.window)

    def choose_vault(self):
        path = filedialog.askdirectory(title="Choose your Obsidian vault", mustexist=True)
        if path:
            self.vault.set(path)
            try:
                self.remember_vault()
            except OSError as exc:
                self.show_error("Remember vault folder", exc)
                return
            self.status.set("Vault folder set. Your notes will be saved inside it.")

    def vault_path(self):
        selected = self.vault.get().strip()
        if not selected:
            raise ValueError("Choose your Obsidian vault folder first.")
        path = Path(selected).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("Choose an existing vault folder.")
        return path.resolve()

    def remember_vault(self):
        SETTINGS.write_text(json.dumps({"vault": str(self.vault_path())}), encoding="utf-8")

    def reset(self):
        if self.busy:
            self.status.set("Wait for the current caption task to finish before starting a new video.")
            return
        if self.note_body.get("1.0", "end-1c").strip() and not messagebox.askyesno(
            "Start a new video", "Clear the transcript and Markdown boxes?", parent=self.window
        ):
            return
        for field in (self.url, self.title, self.question):
            field.set("")
        self.transcript.delete("1.0", "end")
        self.note_body.delete("1.0", "end")
        self.fetched = self.prepared = None
        self.tabs.select(self.read_tab)
        self.status.set("Ready for another video.")

    def open_video(self):
        try:
            webbrowser.open(video_url(self.url.get())[0])
        except ValueError as exc:
            self.show_error("Open YouTube", exc)

    def load_transcript(self):
        path = filedialog.askopenfilename(filetypes=[("Transcripts", "*.txt *.srt *.vtt"), ("All files", "*.*")])
        if path:
            try:
                raw = Path(path).read_text(encoding="utf-8-sig")
                self.set_transcript(clean_transcript(raw))
                self.fetched = None
                self.status.set(f"Loaded {Path(path).name}.")
            except (OSError, ValueError) as exc:
                self.show_error("Load transcript", exc)

    def set_transcript(self, text):
        self.transcript.delete("1.0", "end")
        self.transcript.insert("1.0", text)
        self.transcript.edit_reset()

    def launch(self, job, success, label, status):
        self.busy = True
        self.fetch_button.configure(state="disabled")
        self.update_button.configure(state="disabled")
        self.status.set(status)

        def run():
            try:
                self.results.put((success, job(), None, label))
            except Exception as exc:
                self.results.put((success, None, exc, label))

        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        try:
            success, result, error, label = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.fetch_button.configure(state="normal")
            self.update_button.configure(state="normal")
            if error:
                self.show_error(label, error)
            else:
                success(result)
        self.window.after(125, self.poll)

    def install(self, fetch_after=False):
        if not messagebox.askyesno(
            "Caption reader setup",
            "Install or update the free yt-dlp caption reader from PyPI? "
            "It will be stored inside this app's folder.",
            parent=self.window,
        ):
            return

        def install_reader():
            result = subprocess.run([
                str(PYTHON), "-m", "pip", "--disable-pip-version-check", "--no-cache-dir", "install",
                "--index-url", "https://pypi.org/simple", "--retries", "1", "--timeout", "15",
                "--upgrade", "--target", str(DEPS), "yt-dlp[default]",
            ], capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=HIDE_CONSOLE, timeout=180)
            if result.returncode:
                tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-5:])
                raise RuntimeError("The caption reader could not be installed. You can still paste captions.\n\n" + tail)

        def installed(_):
            self.status.set("Caption reader is ready.")
            if fetch_after:
                self.fetch()

        self.launch(install_reader, installed, "Install caption reader", "Installing the free caption reader…")

    def fetch(self):
        try:
            url, _ = video_url(self.url.get())
        except ValueError as exc:
            self.show_error("Get transcript", exc)
            return
        if not (DEPS / "yt_dlp" / "__init__.py").is_file():
            self.install(fetch_after=True)
            return
        language = self.language.get().strip()
        prior_text = self.transcript.get("1.0", "end-1c")

        def retrieve():
            process = subprocess.run([
                str(PYTHON), str(ROOT / "caption_core.py"), url, language, str(DEPS)
            ], capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=HIDE_CONSOLE, timeout=90)
            try:
                data = json.loads(process.stdout.splitlines()[-1])
            except (ValueError, IndexError):
                data = {"error": "The caption reader stopped unexpectedly."}
            if "error" in data:
                raise RuntimeError(data["error"] + "\n\nTry Open YouTube → Show transcript, then paste the text here.")
            return data

        def display(data):
            try:
                same_video = video_url(self.url.get())[0] == url
            except ValueError:
                same_video = False
            if not same_video or self.transcript.get("1.0", "end-1c") != prior_text:
                self.status.set("The video or transcript changed during retrieval. Click Get transcript again if needed.")
                return
            self.title.set(data["title"])
            self.set_transcript(data["text"])
            self.fetched = (url, data["track"], data["text"])
            self.status.set(f"Transcript loaded. {data['track']} · {len(data['text'].splitlines()):,} lines.")

        self.launch(retrieve, display, "Get transcript", "Checking YouTube for captions…")

    def prepare(self):
        try:
            url, identifier = video_url(self.url.get())
            title = self.title.get().strip() or f"YouTube video {identifier}"
            text = clean_transcript(self.transcript.get("1.0", "end-1c"))
            if not text:
                raise ValueError("Fetch, load, or paste a transcript first.")
            if self.fetched and self.fetched[0] != url and self.fetched[2] == text:
                raise ValueError("The loaded transcript is from a different video. Fetch or paste the matching captions.")
            track = self.fetched[1] if self.fetched and self.fetched[0] == url and self.fetched[2] == text else "Pasted or imported"
            question = self.question.get().strip()
            if self.mode.get() == "Answer a question" and not question:
                raise ValueError("Type your question in the Question or focus box.")
            topics = topic_names(self.vault_path()) if self.vault.get().strip() else []
            prompt = make_prompt(url, self.mode.get(), question, topics)
            header = f"Title: {title}\nSource: {url}\nCaptions: {track}\n\n"
            path = write_unique(TRANSCRIPTS, f"{safe_stem(title, 'Video')} ({identifier})",
                                ".txt", header + text + "\n")
            self.window.clipboard_clear()
            self.window.clipboard_append(prompt)
            self.prepared = (url, title)
            self.status.set(f"Prompt copied. Attach “{path.name}” in ChatGPT and paste the prompt.")
            if os.name == "nt":
                subprocess.Popen(["explorer.exe", "/select," + str(path)], creationflags=HIDE_CONSOLE)
        except (OSError, ValueError, tk.TclError) as exc:
            self.show_error("Prepare for ChatGPT", exc)

    def load_note(self):
        path = filedialog.askopenfilename(filetypes=[("Markdown or text", "*.md *.txt"), ("All files", "*.*")])
        if path:
            try:
                body = Path(path).read_text(encoding="utf-8-sig")
                self.note_body.delete("1.0", "end")
                self.note_body.insert("1.0", body)
            except (OSError, ValueError) as exc:
                self.show_error("Load Markdown", exc)

    def import_note(self):
        try:
            vault = self.vault_path()
            url, _ = video_url(self.url.get())
            if self.prepared and self.prepared[0] != url:
                raise ValueError("The link changed since you prepared the transcript. Check the video link first.")
            title = self.title.get().strip() or (self.prepared[1] if self.prepared else "")
            self.remember_vault()
            path, linked, created = save_note(vault, url, title, self.note_body.get("1.0", "end-1c"))
            self.saved_note = path
            notice = f"Saved {path.name}. Linked {linked} topic notes ({created} new)."
            if not linked:
                notice += " To make graph connections, add topic names like [[Active recall]] to the Markdown."
            self.status.set(notice)
        except (OSError, ValueError) as exc:
            self.show_error("Save to Obsidian", exc)

    def open_transcripts(self):
        self.open_folder(TRANSCRIPTS)

    def open_note_folder(self):
        if self.saved_note:
            self.open_folder(self.saved_note.parent)
        else:
            self.status.set("Save a note first, then open its folder.")

    def open_folder(self, path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)
        except OSError as exc:
            self.show_error("Open folder", exc)


if __name__ == "__main__":
    window = tk.Tk()
    App(window)
    window.mainloop()
