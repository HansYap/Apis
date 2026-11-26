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

from caption_core import (clean_transcript, make_prompt, pick_suggestions, save_note, topic_names, topic_profiles,
                          transcript_chunks, video_url, with_transcript)
import topic_model

ROOT = Path(__file__).resolve().parent
DEPS = ROOT / ".packages"
SETTINGS = ROOT / "settings.json"
MODES = ("Skim + detailed notes", "Quick skim", "Answer a question")
HIDE_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PYTHON = Path(sys.executable)
if PYTHON.name.casefold() == "pythonw.exe":
    PYTHON = PYTHON.with_name("python.exe")
LINUX_PACKAGES = "sudo dnf install python3-pip python3-tkinter python3-gobject gtk3 webkit2gtk4.1 wl-clipboard nodejs"


def pip_command():
    """pip for this Python. Some distributions (Arch, CachyOS) ship Python without pip; then Apis makes
    a private environment with Python's bundled ensurepip and uses its pip. Packages still go to --target."""
    if importlib.util.find_spec("pip"):
        return [str(PYTHON), "-m", "pip"]
    env = ROOT / ".pip-env"
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        result = subprocess.run([str(PYTHON), "-m", "venv", "--clear", str(env)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", creationflags=HIDE_CONSOLE, timeout=180)
        if result.returncode or not python.is_file():
            raise RuntimeError("Python's package installer (pip) is missing. Install it (Fedora: python3-pip, "
                               "Arch: python-pip) and try again.\n" + (result.stderr or result.stdout)[-500:])
    return [str(python), "-m", "pip"]


def pip_install(target, *packages, timeout=180):
    result = subprocess.run([
        *pip_command(), "--disable-pip-version-check", "--no-cache-dir", "install",
        "--index-url", "https://pypi.org/simple", "--retries", "1", "--timeout", "15",
        "--upgrade", "--target", str(target), *packages,
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=HIDE_CONSOLE, timeout=timeout)
    if result.returncode:
        return "\n".join((result.stderr or result.stdout).strip().splitlines()[-5:])
    return ""


def run_topic_model(arguments=(), job=None, online=False, timeout=120):
    """Run topic_model.py in its own process and return its JSON reply."""
    result = subprocess.run([str(PYTHON), str(ROOT / "topic_model.py"), *arguments],
                            input=json.dumps(job) if job is not None else "", capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=topic_model.worker_env(online),
                            creationflags=HIDE_CONSOLE, timeout=timeout)
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        lines = (result.stderr or "").strip().splitlines()
        data = {"error": lines[-1] if lines else "it gave no reply"}
    if "error" in data:
        raise RuntimeError(f"The topic matcher stopped: {data['error']}")
    return data


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


def obsidian_links_work():
    """On Linux, obsidian:// links only open if an app claims them (an AppImage does not by itself)."""
    if not sys.platform.startswith("linux") or not shutil.which("xdg-mime"):
        return True
    try:
        result = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/obsidian"],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(result.stdout.strip())


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
        self._topic_lock = threading.Lock()

    @endpoint
    def initialize(self):
        try:
            settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        selected = settings.get("vault", "") if isinstance(settings, dict) else ""
        return {"vault": selected if isinstance(selected, str) else "",
                "reader_installed": (DEPS / "yt_dlp" / "__init__.py").is_file(),
                "topic_model_installed": topic_model.installed()}

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
            error = pip_install(DEPS, "yt-dlp[default]")
            if error:
                raise RuntimeError("The caption reader could not be installed. You can still paste captions.\n" + error)
            return {"reader_installed": True}
        finally:
            self._caption_lock.release()

    @endpoint
    def install_topic_model(self):
        """fastembed + onnxruntime in .topic-model/packages, then the model itself (about 250 MB in all)."""
        if not self._topic_lock.acquire(blocking=False):
            raise ValueError("The topic matcher is already being set up.")
        try:
            error = pip_install(topic_model.PACKAGES, topic_model.FASTEMBED, timeout=600)
            if error:
                raise RuntimeError("The topic matcher could not be installed. Apis keeps using word matching.\n" + error)
            topic_model.READY.unlink(missing_ok=True)
            run_topic_model(["--download"], online=True, timeout=900)
            return {"topic_model_installed": topic_model.installed()}
        finally:
            self._topic_lock.release()

    def _suggest_topics(self, vault, text):
        """Model suggestions when the matcher is installed, otherwise word matching (and why)."""
        profiles = topic_profiles(vault)
        if not profiles:
            return [], None, {"method": "none", "count": 0}
        if topic_model.installed():
            if not self._topic_lock.acquire(blocking=False):
                raise ValueError("Wait for the topic matcher setup to finish.")
            try:
                chunks = transcript_chunks(text)
                scores = run_topic_model(job={"topics": [{"name": p["name"], "text": p["text"]} for p in profiles],
                                              "chunks": chunks})["scores"]
                suggestions = pick_suggestions(scores, chunks, text)
                return [], suggestions, {"method": "model", "count": len(suggestions),
                                         "strong": sum(item["strength"] == "strong" for item in suggestions)}
            except (RuntimeError, OSError, subprocess.SubprocessError, KeyError) as exc:
                fallback = str(exc)
            finally:
                self._topic_lock.release()
        else:
            fallback = ""
        topics = topic_names(vault, text)
        return topics, None, {"method": "words", "count": len(topics), "problem": fallback}

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
        if payload.get("vault", "").strip():
            topics, suggestions, linking = self._suggest_topics(vault_path(payload["vault"]), text)
        else:
            topics, suggestions, linking = [], None, {"method": "none", "count": 0}
        track = self._fetched[1] if self._fetched and self._fetched[0] == url and self._fetched[2] == text else "Pasted or imported"
        prompt = with_transcript(make_prompt(url, mode, question, topics, suggestions), url, title, track, text)
        self._prepared = (url, title)
        return {"prompt": prompt, "words": len(text.split()), "copied": self._copy_prompt(prompt), "linking": linking}

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
        path, linked, existing = save_note(vault, url, title, payload.get("note", ""),
                                           extra={"saved": date.today(), "tags": ["youtube"]})
        self._saved_note = path
        return {"filename": path.name, "linked": linked, "existing": existing}

    @endpoint
    def open_folder(self, kind):
        if kind != "note" or not self._saved_note:
            raise ValueError("Save a note first, then open its folder.")
        open_with_system(self._saved_note.parent)
        return True

    @endpoint
    def open_external(self, destination, link=""):
        if destination == "obsidian":
            # Only the note Apis just saved; the link from the page is ignored.
            if not self._saved_note:
                raise ValueError("Save a note first, then open it in Obsidian.")
            if not obsidian_links_work():
                raise ValueError("Obsidian is not set up to open links on this computer. An AppImage needs a "
                                 "desktop entry for obsidian:// links; see START HERE.txt. Open saved folder still works.")
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
    result = subprocess.run([*pip_command(), "--disable-pip-version-check", "install",
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
                                   min_size=(480, 420), background_color="#19181F", text_select=True)
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
