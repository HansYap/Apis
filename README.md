# Apis

Turn YouTube videos into connected Obsidian notes.

Apis is a local desktop app for Windows and Linux (Fedora) with a graphite and violet
interface, coral accents, frosted glass panels, and an original SVG ribbon. It retrieves
captions, prepares a prompt for your AI model (ChatGPT, Claude, Gemini…), and saves the
Markdown you bring back into your Obsidian vault. No AI API key is needed.

## Start

**Windows:** install Python 3.11+ with Tkinter and pip, then double-click `Start Apis.cmd`.
Microsoft Edge WebView2 Runtime is also required; it is already available on most Windows PCs.

**Linux (Fedora):** Apis runs on the system Python (`/usr/bin/python3`), because GTK's
Python bindings come from dnf, not pip. Install the desktop packages once:

```bash
sudo dnf install python3-pip python3-tkinter python3-gobject gtk3 webkit2gtk4.1 wl-clipboard nodejs
```

On Arch or CachyOS: `sudo pacman -S --needed python-gobject gtk3 webkit2gtk-4.1 tk wl-clipboard nodejs`.
Python there ships without pip, so Apis makes a private one in `.pip-env/` when it first installs something.

Then run `./start-apis.sh`. Run `./install-desktop-entry.sh` once to add Apis to the app grid
(run it again if you move the folder).

**Open in Obsidian** uses `obsidian://` links. The Flatpak build of Obsidian (Flathub) handles them;
the AppImage does not until it has a desktop entry. See *Open in Obsidian with the AppImage* in `START HERE.txt`.

On its first launch, Apis offers to install its desktop renderer (pywebview) inside
`.packages/`. Keep `app.py`, `caption_core.py`, `requirements.txt`, `web/`, and `assets/` together.

For automatic captions, install Node.js 22+ or Deno 2.3+ (on Fedora, the `nodejs` package above).
The app offers to install yt-dlp locally on the first fetch.
Manual transcript import and note saving do not need a JavaScript runtime.

## Workflow

1. **Transcript:** paste a YouTube link and click **Get transcript**, or use **Import file** or paste a transcript.
   Open **Video details** to set the title or choose a caption language such as `en` or `ms`.
2. **AI model:** click **Continue to AI model**, choose a reading style, then click **Copy prompt**.
   The copied text includes the transcript, so paste it into your AI model's chat and send it.
3. **Obsidian:** choose your vault, paste the returned Markdown, and click **Save & connect**.
   Topic wikilinks are kept as written. Obsidian links them to notes with the same name, and a topic
   without a note still appears in the graph; click it in Obsidian to create that note.
   **Open in Obsidian** opens the new note; **Open saved folder** shows the file.

Choose your vault before copying a prompt so it reuses topic names from your earlier video notes.

**Topic matcher (optional).** Without it, Apis offers topics whose words appear in the transcript
(up to 40). Choose **Install / update topic matcher** in the ⋯ menu beside **Import file** to add a small
multilingual embedding model (`paraphrase-multilingual-MiniLM-L12-v2` via fastembed and onnxruntime,
about 450 MB in `.topic-model/`). It compares each topic, described by the sentences around its earlier
links, with passages of the transcript, and suggests the closest ones (up to 15) as *strong* or *possible*
matches with a timestamp and excerpt. The prompt tells your AI model that the matcher only saw short
passages, so it checks each suggestion against the full transcript, skips the ones that do not fit, and
creates new topic names for ideas that match none. It runs on your computer and goes online only to download
the model; if it fails, Apis falls back to word matching.
Apis remembers the vault across videos and restarts. Press **Ctrl + L** to return to the
video link. Switching screens preserves your work, and **New video** asks before clearing
a transcript or note.

## Files and privacy

- Transcripts are not saved; the copied prompt carries them to your AI model.
- Notes go to `YouTube/Notes/` inside your vault. Apis creates no other files there.
- Each note starts with Obsidian properties: `source`, `video_id`, `saved`, and `tags` (`youtube`).
  If the pasted Markdown already starts with its own properties, Apis keeps those instead.
- Repeated saves create numbered copies rather than overwriting notes.
- The app does not upload transcripts to any AI service or access browser cookies.
- Settings and installed dependencies are excluded from Git.

The interface and bundled DM Sans font load locally. There are no external
font requests, analytics, or decorative animation loops. The glass effect
uses CSS backdrop blur over original SVG artwork. See `START HERE.txt` for help.

## Development

- `app.py`: native window, Python bridge, caption tasks, dialogs, and file operations.
- `caption_core.py`: caption parsing, prompts, Obsidian properties, and topic linking.
- `topic_model.py`: the optional topic matcher, run as its own process with its own packages.
- `web/`: plain HTML, CSS, JavaScript, SVG, and the bundled font. No frontend build step.
- `tests/`: offline checks for the bridge (`test_app.py`) and the caption and linking logic (`test_caption_core.py`).

Install the desktop dependencies with `python -m pip install --target .packages -r requirements.txt`.
Run with `python app.py` (on Fedora, `./start-apis.sh`). Run offline checks with `python -m unittest discover -s tests`.

DM Sans is distributed under the SIL Open Font License, included in
`web/fonts/OFL.txt`; its source is [Google Fonts](https://github.com/google/fonts/tree/main/ofl/dmsans).
The design takes inspiration from [Jeton](https://www.jeton.com/)'s spacious typography and rounded controls;
Apis uses its own artwork, colours, and layout.
