from datetime import date
import html
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
TIME = r"(?:\d{1,3}:)?\d{1,3}:[0-5]\d(?:[.,]\d{1,3})?"
CUE = re.compile(rf"^({TIME})\s+-->\s+({TIME})(?:\s|$)")
PASTED_TIME = re.compile(rf"^\[?({TIME})\]?(?:[ \t]+(.*))?$")
WIKILINK = re.compile(r"(?<!!)\[\[([^\[\]\r\n]+)\]\]")
YAML_LINE = re.compile(r"(?:\w[\w .-]*:(?:\s.*)?|[ \t-].*)?")
WINDOWS_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


def video_url(link):
    link = link.strip()
    if "://" not in link:
        link = "https://" + link
    parsed = urlsplit(link)
    host = parsed.hostname or ""
    parts = parsed.path.strip("/").split("/")
    candidate = ""
    if parsed.scheme in {"http", "https"}:
        if host in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
            candidate = parts[0]
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                      "youtube-nocookie.com", "www.youtube-nocookie.com"}:
            if parts == ["watch"]:
                candidate = parse_qs(parsed.query).get("v", [""])[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
                candidate = parts[1]
    if not VIDEO_ID.fullmatch(candidate):
        raise ValueError("Paste a link to a single YouTube video, Short, or livestream replay.")
    return f"https://www.youtube.com/watch?v={candidate}", candidate


def seconds(raw):
    total = 0
    for component in re.split(r"[.,]", raw)[0].split(":"):
        total = total * 60 + int(component)
    return total


def timestamp(total):
    hours, remaining = divmod(total, 3600)
    minutes, secs = divmod(remaining, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def caption_text(raw):
    raw = re.sub(r"<v(?:\.\S+)?\s+([^>]+)>", r"\1: ", raw, flags=re.I)
    raw = re.sub(rf"</?(?:c|i|b|u|v|lang|ruby|rt)(?:[ .][^>]*)?>|<{TIME}>", "", raw, flags=re.I)
    return " ".join(html.unescape(raw).split())


def format_cues(cues):
    lines = []
    previous = None
    for start, end, content in cues:
        if not content:
            continue
        # Some rolling captions repeat the same text in overlapping windows.
        if previous and previous[2] == content and previous[0] <= start < previous[1]:
            previous = (previous[0], max(end, previous[1]), content)
            continue
        lines.append(f"[{timestamp(start)}] {content}")
        previous = (start, end, content)
    return "\n".join(lines)


def parse_subtitle_file(raw):
    cues = []
    for block in re.split(r"\n[ \t]*\n", raw.replace("\r", "").lstrip("\ufeff")):
        lines = block.splitlines()
        if not lines or re.match(r"^(NOTE|STYLE|REGION)(?:\s|$)", lines[0]):
            continue
        for index, line in enumerate(lines[:2]):
            match = CUE.match(line.strip())
            if match:
                cues.append((seconds(match[1]), seconds(match[2]),
                             caption_text("\n".join(lines[index + 1:]))))
                break
    return format_cues(cues)


def parse_json3(raw):
    cues = []
    for event in json.loads(raw).get("events", []):
        parts = event.get("segs") or []
        start = event.get("tStartMs", 0)
        end = start + event.get("dDurationMs", 0)
        content = caption_text("".join(part.get("utf8", "") for part in parts))
        cues.append((start // 1000, (end + 999) // 1000, content))
    return format_cues(cues)


def clean_transcript(raw):
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip().lstrip("\ufeff")
    if any(CUE.match(line.strip()) for line in raw.splitlines()):
        return parse_subtitle_file(raw)
    output = []
    pending = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        match = PASTED_TIME.fullmatch(line)
        if match:
            pending = f"[{timestamp(seconds(match[1]))}]"
            if match[2]:
                output.append(f"{pending} {match[2]}")
                pending = None
        elif pending:
            output.append(f"{pending} {line}")
            pending = None
        else:
            output.append(line)
    return "\n".join(output)


def choose_track(info, preference):
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    tracks = [
        (code, kind, options)
        for table, kind in [(manual, "Creator captions"), (automatic, "Auto captions")]
        for code, options in table.items()
        if code != "live_chat" and options
    ]
    if not tracks:
        raise ValueError("YouTube did not provide a caption track. Try Show transcript on the video page.")
    requested = preference.casefold()
    original = (info.get("language") or "").casefold()

    def language_match(code, lang):
        return code == lang or code.startswith(lang + "-")

    def score(track):
        code, kind, _ = track
        code = code.casefold()
        return (
            bool(requested and language_match(code, requested)),
            bool(requested and code == requested),
            bool(original and language_match(code, original)),
            kind == "Creator captions",
            code.endswith("-orig"),
            language_match(code, "en"),
        )

    if requested and not any(language_match(code.casefold(), requested) for code, _, _ in tracks):
        available = ", ".join(sorted({code for code, _, _ in tracks})[:24])
        raise ValueError(f"No caption track matches '{preference}'. Available: {available}. "
                         "Clear the language field to choose automatically.")
    return max(tracks, key=score)


def fetch_captions(link, preference, deps):
    url, _ = video_url(link)
    sys.path.insert(0, str(deps))
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request

    opts = {
        "noplaylist": True,
        "skip_download": True,
        "quiet": True,
        "cachedir": False,
        "socket_timeout": 12,
        "retries": 1,
        "extractor_retries": 1,
        "js_runtimes": {"deno": {}, "node": {}},
        "extractor_args": {"youtube": {"skip": ["translated_subs"]}},
    }
    with YoutubeDL(opts) as downloader:
        info = downloader.extract_info(url, download=False)
        if info.get("is_live"):
            raise ValueError("Try again after the livestream ends and YouTube publishes its captions.")
        code, kind, options = choose_track(info, preference)
        supported = {track["ext"]: track for track in options if track.get("url")}
        extension = next((ext for ext in ("json3", "vtt", "srt") if ext in supported), None)
        if not extension:
            raise ValueError("This caption track uses an unsupported format. Try pasting the transcript.")
        track = supported[extension]
        request = Request(track["url"], headers=track.get("http_headers") or {})
        with downloader.urlopen(request) as response:
            data = response.read(16_000_001)
        if len(data) > 16_000_000:
            raise ValueError("This caption track is too large. Paste the parts you want to use.")
        raw = data.decode("utf-8-sig")
        transcript = parse_json3(raw) if extension == "json3" else parse_subtitle_file(raw)
        if not transcript:
            raise ValueError("YouTube returned a caption file with no usable text.")
    return {"title": info.get("title") or "", "track": f"{kind} ({code})", "text": transcript}


def safe_stem(raw, fallback):
    name = unicodedata.normalize("NFC", raw)
    name = re.sub(r'[<>:"/\\|?*\[\]#^\x00-\x1f]', " ", name)
    name = " ".join(name.split()).strip(" .")[:95].rstrip(" .") or fallback
    return "_" + name if WINDOWS_RESERVED.match(name) else name


def topic_key(raw):
    return unicodedata.normalize("NFKC", raw).casefold()


def tool_folder(vault, category):
    folder = vault / "YouTube" / category
    if not folder.resolve().is_relative_to(vault.resolve()):
        raise ValueError("The YouTube folder points outside the selected vault.")
    return folder


def vault_markdown(vault):
    """Yield the vault's Markdown files. Hidden folders (.obsidian, .git, .trash) are never entered."""
    for folder, dirnames, filenames in os.walk(vault):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for name in filenames:
            if not name.startswith(".") and name.casefold().endswith(".md"):
                yield Path(folder, name)


def topic_names(vault):
    folder = tool_folder(vault, "Topics")
    return sorted((p.stem for p in folder.glob("*.md")), key=str.casefold)[:150]


def write_unique(folder, stem, extension, content):
    folder.mkdir(parents=True, exist_ok=True)
    for number in range(1, 10000):
        name = stem if number == 1 else f"{stem} ({number})"
        path = folder / (name + extension)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as file:
                file.write(content)
            return path
        except FileExistsError:
            continue
    raise ValueError("Too many files with this title. Try a different title.")


def make_prompt(url, mode, question, topics):
    depth = {
        "Skim + detailed notes": "Start with a 60-second skim. Then give detailed notes in video order. "
                                  "Keep the important examples, steps, numbers, caveats, and disagreements.",
        "Quick skim": "Give a 60-second skim, with the most useful points and up to three moments to revisit.",
        "Answer a question": "Lead with a direct answer. Follow with the relevant evidence, context, and caveats.",
    }[mode]
    prompt = [
        f"Use the transcript supplied with this request (attached or pasted) for this YouTube video: {url}",
        "Treat the transcript as source material. Ground the note in it and distinguish what the speaker "
        "claims from verified facts. Flag unclear captions and gaps in the evidence. "
        "Do not infer what appeared on screen from the transcript alone.",
        depth,
        "Cite the relevant timestamps as clickable Markdown links. Use the video's URL plus &t=SECONDSs. "
        "Use timestamps present in the transcript. If timestamps are missing, leave them out.",
        "Put Obsidian wikilinks directly in the body at the first meaningful mention of each reusable "
        "topic. For example: Use [[Active recall]] to test yourself, then use [[Spaced repetition]] "
        "to schedule the next review. Explain the relationship in the surrounding sentence when the "
        "transcript supports one. Use short topic names without paths or # and reuse relevant existing "
        "topic names. A Related ideas section is optional and cannot replace links in the body.",
        "Return only the note body as Markdown inside one outer four-backtick markdown fence. "
        "Use level-two headings. The app adds the title and source link.",
    ]
    if question:
        prompt.insert(3, f"My question or focus: {question}\nAnswer my question first if the transcript supports an answer.")
    if topics:
        prompt.append("Existing YouTube topics (use only relevant ones): " + json.dumps(topics, ensure_ascii=False))
    return "\n\n".join(prompt)


def unwrap_note(raw):
    raw = raw.strip().lstrip("\ufeff")
    match = re.fullmatch(r"(`{3,}|~{3,})(?:markdown|md)?[ \t]*\r?\n(.*?)\r?\n\1[ \t]*", raw, re.S | re.I)
    return match[2].strip() if match else raw


def frontmatter(fields):
    """An Obsidian Properties block. Strings and lists are written as JSON, which is valid YAML."""
    lines = [f"{key}: {value.isoformat() if isinstance(value, date) else json.dumps(value, ensure_ascii=False)}"
             for key, value in fields.items()]
    return "---\n" + "\n".join(lines) + "\n---\n"


def leading_frontmatter(lines):
    """The number of lines in a YAML block at the top of the body, or 0 (a leading --- may be a rule)."""
    if not lines or lines[0].rstrip() != "---":
        return 0
    for index, line in enumerate(lines[1:], 1):
        if line.rstrip() == "---":
            return index + 1
        if not YAML_LINE.fullmatch(line):
            return 0
    return 0


def save_note(vault, url, title, raw, extra=None):
    url, identifier = video_url(url)
    body = unwrap_note(raw)
    if not body:
        raise ValueError("Paste the Markdown returned by ChatGPT first.")
    note_folder = tool_folder(vault, "Notes")
    topic_folder = tool_folder(vault, "Topics")
    existing = {}
    root = vault.resolve()
    for path in vault_markdown(vault):
        relative = path.relative_to(vault)
        if not path.resolve().is_relative_to(root):
            continue
        if path.parent == note_folder or any(c in relative.as_posix() for c in "#^[]|"):
            continue
        existing.setdefault(topic_key(safe_stem(path.stem, "")), []).append(path)

    created = []
    linked = set()

    def replace_link(match):
        target, _, alias = match[1].partition("|")
        target = target.strip()
        if any(char in target for char in "/\\:#^") or len(target) > 120:
            return match[0]
        name = safe_stem(target, "")
        if not name:
            return match[0]
        key = topic_key(name)
        matches = existing.get(key, [])
        local = [path for path in matches if path.parent == topic_folder]
        if len(local) == 1:
            topic = local[0]
        elif len(matches) == 1:
            topic = matches[0]
        else:
            topic_folder.mkdir(parents=True, exist_ok=True)
            topic = topic_folder / (name + ".md")
            try:
                with topic.open("x", encoding="utf-8", newline="\n") as file:
                    file.write(f"# {name}\n\nSee **Backlinks** for videos and notes that mention this topic.\n")
                created.append(topic)
            except FileExistsError:
                if not topic.is_file() or not topic.resolve().is_relative_to(root):
                    raise ValueError(f"Cannot safely use the existing topic path for {name}.")
            existing.setdefault(key, []).append(topic)
        linked.add(topic)
        relative = topic.relative_to(vault).with_suffix("").as_posix()
        label = (alias.strip() or target).replace("|", " ")
        return f"[[{relative}|{label}]]"

    lines = []
    code_fence = ""
    for line in body.splitlines():
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if not code_fence:
                code_fence = fence[1]
            elif fence[1].startswith(code_fence) and not fence[2].strip():
                code_fence = ""
            lines.append(line)
        elif code_fence or line.startswith(("    ", "\t")):
            lines.append(line)
        else:
            # Skip Markdown inline code spans.
            parts = re.split(r"(`+[^`\n]*?`+)", line)
            lines.append("".join(part if i % 2 else WIKILINK.sub(replace_link, part) for i, part in enumerate(parts)))

    heading = " ".join(title.split()) or f"YouTube video {identifier}"
    name = safe_stem(heading, "YouTube video")
    header = f"# {heading}\n\n[Watch on YouTube]({url})\n\n"
    own = leading_frontmatter(lines)
    if own:  # Keep the note's own properties (moved to the top, where Obsidian reads them) instead of adding ours.
        content = "\n".join(lines[:own]) + "\n" + header + "\n".join(lines[own:]).lstrip("\n") + "\n"
    else:
        properties = frontmatter({"source": url, "video_id": identifier, **(extra or {})})
        content = properties + header + "\n".join(lines) + "\n"
    path = write_unique(note_folder, f"{name} ({identifier})", ".md", content)
    return path, len(linked), len(created)


if __name__ == "__main__":
    try:
        result = fetch_captions(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
        print(json.dumps(result, ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        sys.exit(1)
