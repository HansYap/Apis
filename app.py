"""Apis desktop host. The interface and font load locally from web/."""

from datetime import date
import functools
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.parse import quote
import webbrowser

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
LINUX_PACKAGES = "sudo dnf install python3-pip python3-tkinter python3-gobject gtk3 webkit2gtk4.1 wl-clipboard nodejs"


def endpoint(function):
    """Give every bridge call a predictable, user-readable error response."""
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return {"ok": True, "data": function(*args, **kwargs)}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or "Something went wrong. Please try again."}
    return wrapped


def open_with_system(target):
    """Open a folder or an app link (obsidian://) with the desktop's default handler."""
    if os.name == "nt":
        os.startfile(target)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])


def vault_path(raw):
    path = Path(raw.strip()).expanduser()
    if not raw.strip() or not path.is_absolute() or not path.is_dir():
        raise ValueError("Choose an existing Obsidian vault folder first.")
    return path.resolve()


class Api:
    def __init__(self):
        self._window = None
        self._fetched = None
        self._prepared = None
        self._saved_note = None
        self._caption_lock = threading.Lock()

    @endpoint
    def initialize(self):
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        selected = settings.get("vault", "") if isinstance(settings, dict) else ""
        return {"vault": selected if isinstance(selected, str) else "",
                "reader_installed": (DEPS / "yt_dlp" / "__init__.py").is_file()}

    @endpoint
    def choose_vault(self):
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not paths:
            return None
        path = vault_path(paths[0])
        self._remember_vault(path)
        return {"vault": str(path)}

    def _remember_vault(self, path):
        SETTINGS.write_text(json.dumps({"vault": str(path)}), encoding="utf-8")

    @endpoint
    def import_file(self, kind):
        import webview
        if kind not in {"transcript", "note"}:
            raise ValueError("Choose a transcript or Markdown file.")
        types = ("Transcripts (*.txt;*.srt;*.vtt)",) if kind == "transcript" else ("Markdown (*.md;*.txt)",)
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=types)
        if not paths:
            return None
        path = Path(paths[0])
        if path.stat().st_size > 16_000_000:
            raise ValueError("This file is too large. Import a smaller section.")
        text = path.read_text(encoding="utf-8-sig")
        if kind == "transcript":
            text = clean_transcript(text)
            self._fetched = None
        return {"text": text, "name": path.name}

    @endpoint
    def install_reader(self):
        if not self._caption_lock.acquire(blocking=False):
            raise ValueError("Wait for the current caption task to finish.")
        try:
            result = subprocess.run([
                str(PYTHON), "-m", "pip", "--disable-pip-version-check", "--no-cache-dir", "install",
                "--index-url", "https://pypi.org/simple", "--retries", "1", "--timeout", "15",
                "--upgrade", "--target", str(DEPS), "yt-dlp[default]",
            ], capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=HIDE_CONSOLE, timeout=180)
            if result.returncode:
                tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-5:])
                raise RuntimeError("The caption reader could not be installed. You can still paste captions.\n" + tail)
            return {"reader_installed": True}
        finally:
            self._caption_lock.release()

    @endpoint
    def fetch(self, link, language):
        url, _ = video_url(link)
        if not self._caption_lock.acquire(blocking=False):
            raise ValueError("Wait for the current caption task to finish.")
        try:
            if not (DEPS / "yt_dlp" / "__init__.py").is_file():
                raise ValueError("Install the caption reader first, or paste your transcript.")
            result = subprocess.run([
                str(PYTHON), str(ROOT / "caption_core.py"), url, language.strip(), str(DEPS)
            ], capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=HIDE_CONSOLE, timeout=90)
            try:
                data = json.loads(result.stdout.splitlines()[-1])
            except (ValueError, IndexError):
                raise RuntimeError("The caption reader stopped unexpectedly. Try importing or pasting captions.")
            if "error" in data:
                raise RuntimeError(data["error"] + "\nOpen the video, choose Show transcript, and paste the text here.")
            self._fetched = (url, data["track"], data["text"])
            return {**data, "url": url}
        finally:
            self._caption_lock.release()

    @endpoint
    def validate_transcript(self, link, raw):
        url, _ = video_url(link)
        text = clean_transcript(raw)
        if not text:
            raise ValueError("Fetch, import, or paste a transcript first.")
        if self._fetched and self._fetched[0] != url and self._fetched[2] == text:
            raise ValueError("This transcript is from a different video. Fetch or paste the matching captions.")
        return {"url": url, "text": text}

    @endpoint
    def prepare(self, payload):
        url, identifier = video_url(payload.get("url", ""))
        text = clean_transcript(payload.get("transcript", ""))
        if not text:
            raise ValueError("Fetch, import, or paste a transcript first.")
        if self._fetched and self._fetched[0] != url and self._fetched[2] == text:
            raise ValueError("This transcript is from a different video. Fetch or paste the matching captions.")
        mode = payload.get("mode", MODES[0])
        if mode not in MODES:
            raise ValueError("Choose a reading style.")
        question = payload.get("question", "").strip()
        if mode == "Answer a question" and not question:
            raise ValueError("Add your question so your AI model knows what to answer.")
        title = payload.get("title", "").strip() or f"YouTube video {identifier}"
        topics = topic_names(vault_path(payload["vault"])) if payload.get("vault", "").strip() else []
        track = self._fetched[1] if self._fetched and self._fetched[0] == url and self._fetched[2] == text else "Pasted or imported"
        prompt = make_prompt(url, mode, question, topics)
        content = f"Title: {title}\nSource: {url}\nCaptions: {track}\n\n{text}\n"
        path = write_unique(TRANSCRIPTS, f"{safe_stem(title, 'Video')} ({identifier})", ".txt", content)
        self._prepared = (url, title)
        return {"filename": path.name, "prompt": prompt, "copied": self._copy_prompt(prompt)}

    def _copy_prompt(self, prompt):
        if sys.platform.startswith("linux"):
            for command in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
                if shutil.which(command[0]) and (command[0] != "wl-copy" or os.environ.get("WAYLAND_DISPLAY")):
                    try:
                        # wl-copy and xclip fork a helper that serves the clipboard; it must not hold our pipes.
                        subprocess.run(command, input=prompt, text=True, encoding="utf-8", check=True, timeout=5,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return True
                    except (OSError, subprocess.SubprocessError):
                        continue
            return False
        if sys.platform != "win32" or not self._window:
            return False
        try:
            from System import Action
            from System.Windows.Forms import Clipboard
            self._window.native.Invoke(Action(lambda: Clipboard.SetText(prompt)))
            return True
        except Exception:
            return False

    @endpoint
    def save(self, payload):
        vault = vault_path(payload.get("vault", ""))
        url, _ = video_url(payload.get("url", ""))
        if self._prepared and self._prepared[0] != url:
            raise ValueError("The video link changed after you prepared the transcript. Check it before saving.")
        title = payload.get("title", "").strip() or (self._prepared[1] if self._prepared else "")
        self._remember_vault(vault)
        path, linked, created = save_note(vault, url, title, payload.get("note", ""),
                                          extra={"saved": date.today(), "tags": ["youtube"]})
        self._saved_note = path
        return {"filename": path.name, "linked": linked, "created": created}

    @endpoint
    def open_folder(self, kind):
        if kind == "transcripts":
            folder = TRANSCRIPTS
        elif kind == "note" and self._saved_note:
            folder = self._saved_note.parent
        else:
            raise ValueError("Save a note first, then open its folder.")
        folder.mkdir(parents=True, exist_ok=True)
        open_with_system(folder)
        return True

    @endpoint
    def open_external(self, destination, link=""):
        if destination == "obsidian":
            # Only the note Apis just saved; the link from the page is ignored.
            if not self._saved_note:
                raise ValueError("Save a note first, then open it in Obsidian.")
            open_with_system("obsidian://open?path=" + quote(str(self._saved_note), safe=""))
            return True
        if destination == "youtube":
            target, _ = video_url(link)
        else:
            raise ValueError("Unknown destination.")
        webbrowser.open(target)
        return True

    @endpoint
    def reset(self):
        self._fetched = self._prepared = self._saved_note = None
        return True


def desktop_dialog(kind, title, message):
    """Ask yes/no or show an error before the window exists: Tk, then zenity, then the terminal."""
    if kind == "error":
        print(f"{title}\n\n{message}", file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
    except Exception:  # Fedora ships Tk separately (python3-tkinter), or there is no display.
        root = None
    if root:
        root.withdraw()
        try:
            if kind == "question":
                return messagebox.askyesno(title, message, parent=root)
            messagebox.showerror(title, message, parent=root)
            return False
        finally:
            root.destroy()
    # zenity waits forever when there is no display, so only use it inside a desktop session.
    if shutil.which("zenity") and (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        result = subprocess.run(["zenity", "--question" if kind == "question" else "--error", "--no-markup",
                                 "--width=460", "--title", title, "--text", message], capture_output=True)
        return result.returncode == 0
    if kind == "question":
        print(f"{title}\n\n{message}\n\nNo dialog is available. To set up by hand, run:\n"
              f"{PYTHON} -m pip install --target {DEPS} -r {ROOT / 'requirements.txt'}", file=sys.stderr)
    return False


def ensure_desktop():
    sys.path.insert(0, str(DEPS))
    try:
        return importlib.import_module("webview")
    except ImportError:
        pass
    if not desktop_dialog("question", "Set up Apis", "Install Apis’ desktop interface from PyPI?\n\nIt stays inside this app’s folder. This is only needed once."):
        return None
    result = subprocess.run([str(PYTHON), "-m", "pip", "--disable-pip-version-check", "install",
                             "--target", str(DEPS), "-r", str(ROOT / "requirements.txt")],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            creationflags=HIDE_CONSOLE, timeout=240)
    if result.returncode:
        raise RuntimeError("Setup could not finish.\n\n" + (result.stderr or result.stdout)[-1500:])
    importlib.invalidate_caches()
    return importlib.import_module("webview")


def linux_gui():
    """Choose pywebview's Linux backend: GTK + WebKitGTK from dnf, or Qt if it is installed."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")
        return "gtk"
    except (ImportError, ValueError):
        pass
    if importlib.util.find_spec("qtpy") or (DEPS / "qtpy").is_dir():
        return "qt"
    raise RuntimeError("Apis needs GTK and WebKitGTK to open its window. Use the system Python (/usr/bin/python3).")


def dark_titlebar(window):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
        setter.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        for attribute, value in ((20, 1), (35, 0x1F1819), (36, 0xF6EDF0)):
            setting = ctypes.c_int(value)
            setter(window.native.Handle.ToInt64(), attribute, ctypes.byref(setting), ctypes.sizeof(setting))
    except (OSError, AttributeError):
        pass


def main():
    gui = "edgechromium" if sys.platform == "win32" else None
    if sys.platform.startswith("linux"):
        # webview.screens below already loads a backend, so pywebview must know the choice now.
        gui = os.environ["PYWEBVIEW_GUI"] = (os.environ.get("PYWEBVIEW_GUI") or linux_gui()).lower()
    webview = ensure_desktop()
    if webview is None:
        return
    api = Api()
    screen = webview.screens[0]
    window = webview.create_window("Apis", str(ROOT / "web" / "index.html"), js_api=api,
                                   width=min(1280, screen.width - 60), height=min(900, screen.height - 60),
                                   min_size=(860, 640), background_color="#19181F", text_select=True)
    api._window = window
    window.events.before_show += lambda: dark_titlebar(window)
    if gui == "gtk":
        from gi.repository import GLib
        GLib.set_prgname("apis")  # The Wayland app id, so GNOME matches apis.desktop and its icon.
    webview.start(gui=gui, icon=str(ROOT / "assets" / ("apis.ico" if sys.platform == "win32" else "apis.png")))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        hint = ("Apis needs Python and the Microsoft Edge WebView2 Runtime." if sys.platform == "win32"
                else "On Fedora, install the desktop packages, then start Apis again:\n" + LINUX_PACKAGES)
        desktop_dialog("error", "Apis could not start", f"{exc}\n\n{hint}")
        sys.exit(1)
