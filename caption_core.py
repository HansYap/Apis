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


def link_contexts(lines):
    """(note name, line) for each wikilink outside code blocks and inline code, in order."""
    code_fence = ""
    for line in lines:
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if not code_fence:
                code_fence = fence[1]
            elif fence[1].startswith(code_fence) and not fence[2].strip():
                code_fence = ""
            continue
        if code_fence or line.startswith(("    ", "\t")):
            continue
        for index, part in enumerate(re.split(r"(`+[^`\n]*?`+)", line)):
            if index % 2 == 0:
                for link in WIKILINK.findall(part):
                    # [[Folder/Name#Heading|label]] points to the note called Name.
                    target = re.split(r"[#^]", link.partition("|")[0])[0].replace("\\", "/").rsplit("/", 1)[-1].strip()
                    if target:
                        yield target, line


def body_links(lines):
    """Note names the wikilinks point to, outside code blocks and inline code, in order."""
    return [target for target, _ in link_contexts(lines)]


def plain_line(line, limit=280):
    """A Markdown line as plain prose: link labels kept, markup and timestamp links removed."""
    line = WIKILINK.sub(lambda match: match[1].rpartition("|")[2], line)
    line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"^\s*(?:#{1,6}\s|>\s?|[-*+]\s(?:\[.\]\s)?|\d+[.)]\s)", "", line)
    line = " ".join(re.sub(r"[*_`~]|\[\]", "", line).split())
    return line if len(line) <= limit else line[:limit].rsplit(" ", 1)[0] + "…"


STOPWORDS = frozenset("""a an and are as at be by for from how in into is it its of on or the to vs what when
    where which who why with your you my our their this that these those not""".split())


def word_stem(word):
    """A rough singular, uninflected form, so "funnels" and "modeling" match "funnel" and "model"."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes", "zes", "ches", "shes")) and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    if word.endswith("ing") and len(word) > 5:
        return word[:-3]
    return word


def topic_words(text):
    return {word_stem(word) for word in re.findall(r"\w+", topic_key(text))
            if word not in STOPWORDS and (len(word) > 2 or any(char.isdigit() for char in word))}


def vault_topics(vault, contexts=3):
    """Topics linked from earlier video notes: {key: (name, notes using it, up to `contexts` sentences)}."""
    topics = {}
    for path in sorted(tool_folder(vault, "Notes").glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        seen = set()
        for target, line in link_contexts(text.splitlines()):
            key = topic_key(target)
            name, uses, sentences = topics.setdefault(key, (target, 0, []))
            if key not in seen:  # Count notes, not mentions.
                seen.add(key)
                uses += 1
            sentence = plain_line(line)
            if len(sentences) < contexts and sentence and sentence not in sentences:
                sentences.append(sentence)
            topics[key] = (name, uses, sentences)
    return topics


def topic_names(vault, transcript, limit=40):
    """Topics linked from earlier video notes that this transcript mentions, best matches first.

    Plain word matching, no AI model: a topic qualifies when one of its words appears in the
    transcript. Topics whose words all appear rank first, then the ones used in more notes.
    The list stays short however many topics the vault collects."""
    spoken = topic_words(transcript)
    ranked = []
    for key, (name, uses, _) in vault_topics(vault).items():
        words = topic_words(name)
        if words & spoken:
            ranked.append((-len(words & spoken) / len(words), -uses, name.casefold(), name))
    return [entry[-1] for entry in sorted(ranked)[:limit]]


def topic_profiles(vault):
    """What the topic model compares with the transcript: each topic's name plus how earlier notes used it.

    A bare name such as "Working memory" says little; the sentences around its links say what it means here."""
    return [{"name": name, "uses": uses, "text": f"{name}: " + " ".join(sentences) if sentences else name}
            for name, uses, sentences in vault_topics(vault).values()]


def transcript_chunks(transcript, size=60, step=40):
    """Overlapping passages of about `size` words, each with the timestamp it starts at."""
    words = []  # (word, timestamp of its line)
    for line in transcript.splitlines():
        match = re.match(rf"^\[({TIME})\]\s*(.*)$", line.strip())
        stamp, text = (match[1], match[2]) if match else ("", line)
        words.extend((word, stamp) for word in text.split())
    chunks = []
    for start in range(0, max(len(words) - size + step, 1), step):
        part = words[start:start + size]
        if part:
            chunks.append({"time": next((stamp for _, stamp in part if stamp), ""),
                           "text": " ".join(word for word, _ in part)})
    return chunks


# Tuned on held-out video notes: at 0.60, 9 in 10 suggestions were topics the note really used;
# from 0.50 the model finds about 7 in 10 reusable topics, and the chat model discards the rest.
STRONG_MATCH = 0.60
POSSIBLE_MATCH = 0.50


def pick_suggestions(scores, chunks, transcript, limit=15):
    """Rank the topic model's scores into strong and possible suggestions; drop the far-off ones.

    A topic whose words all appear in the transcript is at least a possible match, so the
    model never hides a topic the video names outright."""
    spoken = topic_words(transcript)
    picked = []
    for entry in scores:
        words = topic_words(entry["name"])
        score = entry["score"]
        if score >= STRONG_MATCH:
            strength = "strong"
        elif score >= POSSIBLE_MATCH or (words and words <= spoken):
            strength = "possible"
        else:
            continue
        chunk = chunks[entry["chunk"]] if 0 <= entry.get("chunk", -1) < len(chunks) else {"time": "", "text": ""}
        excerpt = chunk["text"].split()
        excerpt = " ".join(excerpt[:24]) + ("…" if len(excerpt) > 24 else "")
        picked.append({"name": entry["name"], "strength": strength, "score": round(score, 3),
                       "time": chunk["time"], "excerpt": excerpt})
    picked.sort(key=lambda item: (item["strength"] != "strong", -item["score"], item["name"].casefold()))
    return picked[:limit]


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


def make_prompt(url, mode, question, topics, suggestions=None):
    """`topics` are word-matched names; `suggestions`, when the topic model ran, replace them."""
    depth = {
        "Skim + detailed notes": "Start with a 60-second skim. Then give detailed notes in video order. "
                                  "Keep the important examples, steps, numbers, caveats, and disagreements.",
        "Quick skim": "Give a 60-second skim, with the most useful points and up to three moments to revisit.",
        "Answer a question": "Lead with a direct answer. Follow with the relevant evidence, context, and caveats.",
    }[mode]
    prompt = [
        f"Use the transcript above for this YouTube video: {url}",
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
    if suggestions:
        lines = []
        for item in suggestions:
            where = f" near [{item['time']}]" if item["time"] else ""
            excerpt = f': "{item["excerpt"]}"' if item["excerpt"] else ""
            lines.append(f"- [[{item['name']}]] ({item['strength']} match{where}{excerpt})")
        prompt.append(
            "Suggested existing topics from my vault. A small local model matched each one to the closest "
            "passage of the transcript. It saw only short passages, not the whole video, so judge each "
            "suggestion against the full transcript:\n" + "\n".join(lines) + "\n"
            "Link a suggested topic only where the passage really is about it, and skip any that do not fit. "
            "If an important idea matches none of them, create a new short topic name. Prefer an existing "
            "name when it fits, even if the video words the idea differently.")
    elif suggestions is not None:
        prompt.append("None of my existing topics closely match this video, so create new short topic names "
                      "for its reusable ideas.")
    elif topics:
        prompt.append("Existing YouTube topics (use only relevant ones): " + json.dumps(topics, ensure_ascii=False))
    return "\n\n".join(prompt)


def with_transcript(prompt, url, title, captions, transcript):
    """One message to paste: the transcript first (long material reads best before the instructions), then the prompt."""
    return (f"Transcript of \"{title}\" ({url})\nCaptions: {captions}\n\n"
            f"<transcript>\n{transcript}\n</transcript>\n\n{prompt}")


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
        raise ValueError("Paste the Markdown from your AI model first.")
    note_folder = tool_folder(vault, "Notes")
    lines = body.splitlines()
    # Links stay as written. Obsidian resolves [[Name]] to any note called Name, and a topic
    # without a note still joins the graph; clicking it in Obsidian creates the note.
    topics = {topic_key(target) for target in body_links(lines)}
    existing = {topic_key(path.stem) for path in vault_markdown(vault) if path.parent != note_folder} if topics else set()

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
    return path, len(topics), len(topics & existing)


if __name__ == "__main__":
    try:
        result = fetch_captions(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
        print(json.dumps(result, ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        sys.exit(1)
