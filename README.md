# Apis

Turn YouTube videos into connected Obsidian notes.

Apis is a local Windows desktop app with a graphite and violet interface,
coral accents, frosted glass panels, and an original SVG ribbon. It retrieves captions, prepares
a prompt for your own ChatGPT chat, and saves the Markdown you bring back
into your Obsidian vault. No AI API key is needed.

## Start

Install Python 3.11+ with Tkinter and pip, then double-click **Start Apis.cmd**.
On its first launch, Apis offers to install its desktop renderer (pywebview)
inside `.packages/`. Microsoft Edge WebView2 Runtime is also required; it is
already available on most Windows PCs. Keep `app.py`, `caption_core.py`,
`requirements.txt`, `web/`, and `assets/` together.

For automatic captions, install Node.js 22+ or Deno 2.3+.
The app offers to install yt-dlp locally on the first fetch.
Manual transcript import and note saving do not need a JavaScript runtime.

## Workflow

1. **Transcript:** paste a YouTube link and get captions, or import/paste a transcript.
2. **ChatGPT:** choose a reading style, then click **Save & copy prompt**.
   Attach the saved TXT to your ChatGPT chat and paste the prompt.
3. **Obsidian:** choose your vault, paste the returned Markdown, and click **Save & connect**.
   Topic wikilinks reuse existing notes where possible and create topic notes when needed.

Choose your vault before preparing a prompt to include existing YouTube topic names.
Press **Ctrl+L** to return to the video link. Switching screens preserves your work.

## Files and privacy

- Transcripts stay in `Transcripts/` beside the app.
- Notes go to `YouTube/Notes/` inside your vault; topics go to `YouTube/Topics/`.
- Repeated saves create numbered copies rather than overwriting notes.
- The app does not upload transcripts to ChatGPT or access browser cookies.
- Settings, transcripts, and installed dependencies are excluded from Git.

The interface and bundled DM Sans font load locally. There are no external
font requests, analytics, or decorative animation loops. The glass effect
uses CSS backdrop blur over original SVG artwork. See **START HERE.txt** for help.

## Development

- `app.py`: native window, Python bridge, caption tasks, dialogs, and file operations.
- `caption_core.py`: caption parsing, prompts, and Obsidian topic linking.
- `web/`: plain HTML, CSS, JavaScript, SVG, and the bundled font. No frontend build step.

Install the desktop dependencies with `python -m pip install --target .packages -r requirements.txt`.
Run with `python app.py`. Run offline checks with `python -m unittest discover -s tests`.

DM Sans is distributed under the SIL Open Font License, included in
`web/fonts/OFL.txt`; its source is [Google Fonts](https://github.com/google/fonts/tree/main/ofl/dmsans).
The design takes inspiration from [Jeton](https://www.jeton.com/)'s spacious typography and rounded controls;
Apis uses its own artwork, colours, and layout.
