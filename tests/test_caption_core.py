"""Offline checks for caption parsing, prompts, and Obsidian linking."""

from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from caption_core import (choose_track, clean_transcript, frontmatter, make_prompt, parse_json3, parse_subtitle_file,
                          safe_stem, save_note, topic_names, unwrap_note, vault_markdown, video_url, with_transcript)

ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={ID}"


class VideoUrlTests(unittest.TestCase):
    def test_accepted_links_are_normalised(self):
        for link in (f"youtu.be/{ID}", f"https://youtu.be/{ID}?si=share",
                     f"https://www.youtube.com/watch?v={ID}&t=30", f"https://m.youtube.com/watch?v={ID}",
                     f"https://music.youtube.com/watch?v={ID}", f"https://www.youtube.com/shorts/{ID}",
                     f"https://www.youtube.com/live/{ID}", f"https://www.youtube.com/embed/{ID}",
                     f"https://www.youtube-nocookie.com/embed/{ID}", f"youtube.com/watch?v={ID}",
                     f"  https://YouTube.com/watch?v={ID}  "):
            with self.subTest(link=link):
                self.assertEqual(video_url(link), (URL, ID))

    def test_rejected_links(self):
        for link in ("https://www.youtube.com/playlist?list=PL1234567890", "https://www.youtube.com/@someone",
                     "https://www.youtube.com/channel/UC1234567890", f"https://example.com/watch?v={ID}",
                     "https://youtu.be/abcdefghij", "https://youtu.be/abcdefghijkl",
                     f"javascript:alert('{ID}')", f"javascript://youtube.com/watch?v={ID}",
                     f"file:///home/user/{ID}", "", "   "):
            with self.subTest(link=link), self.assertRaises(ValueError):
                video_url(link)


class TranscriptParsingTests(unittest.TestCase):
    def test_vtt_with_header_note_settings_and_tags(self):
        raw = ("\ufeffWEBVTT\nKind: captions\nLanguage: en\n\n"
               "NOTE This block is a comment\n00:00:09.000 --> 00:00:10.000\n\n"
               "00:00:01.000 --> 00:00:04.000 align:start position:0%\n<v Alice>Hello <c.colorE5E5E5>there</c></v>\n\n"
               "00:00:04.000 --> 00:00:06.500\nsecond<00:00:05.000><c> line</c>\n")
        self.assertEqual(parse_subtitle_file(raw), "[00:01] Alice: Hello there\n[00:04] second line")
        self.assertEqual(clean_transcript(raw), "[00:01] Alice: Hello there\n[00:04] second line")

    def test_srt_with_indexes_and_comma_milliseconds(self):
        raw = "1\r\n00:00:01,500 --> 00:00:03,000\r\nFirst cue\r\n\r\n2\r\n00:01:02,000 --> 00:01:04,250\r\nSecond\r\ncue\r\n"
        self.assertEqual(clean_transcript(raw), "[00:01] First cue\n[01:02] Second cue")

    def test_rolling_duplicate_captions_collapse(self):
        raw = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nsame words\n\n"
               "00:00:02.000 --> 00:00:04.000\nsame words\n\n00:00:03.500 --> 00:00:05.000\nsame words\n\n"
               "00:00:05.000 --> 00:00:06.000\nnew words\n")
        self.assertEqual(parse_subtitle_file(raw), "[00:01] same words\n[00:05] new words")

    def test_repeated_text_after_the_window_is_kept(self):
        raw = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nagain\n\n00:00:10.000 --> 00:00:11.000\nagain\n"
        self.assertEqual(parse_subtitle_file(raw), "[00:01] again\n[00:10] again")

    def test_pasted_show_transcript_style(self):
        raw = "0:05\nUse active recall.\n\n0:12\nReview it again tomorrow.\nAn untimed line"
        self.assertEqual(clean_transcript(raw),
                         "[00:05] Use active recall.\n[00:12] Review it again tomorrow.\nAn untimed line")

    def test_pasted_inline_and_bracketed_timestamps(self):
        self.assertEqual(clean_transcript("[0:07] Inline text\n12:30 Same line"), "[00:07] Inline text\n[12:30] Same line")

    def test_hours(self):
        self.assertEqual(clean_transcript("1:02:03\nLate point"), "[1:02:03] Late point")
        self.assertEqual(parse_subtitle_file("WEBVTT\n\n01:02:03.000 --> 01:02:05.000\nLate point\n"), "[1:02:03] Late point")

    def test_json3_skips_empty_events_and_unescapes_entities(self):
        raw = json.dumps({"events": [
            {"tStartMs": 0, "dDurationMs": 1000},
            {"tStartMs": 1500, "dDurationMs": 2000, "segs": [{"utf8": "Fish &amp; chips"}, {"utf8": " &#39;today&#39;"}]},
            {"tStartMs": 4000, "dDurationMs": 500, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 65000, "dDurationMs": 1000, "segs": [{"utf8": "Later"}]},
        ]})
        self.assertEqual(parse_json3(raw), "[00:01] Fish & chips 'today'\n[01:05] Later")


class ChooseTrackTests(unittest.TestCase):
    option = [{"ext": "json3", "url": "https://example.invalid/captions"}]

    def test_creator_captions_beat_auto_captions(self):
        code, kind, _ = choose_track({"subtitles": {"en": self.option}, "automatic_captions": {"en": self.option}}, "")
        self.assertEqual((code, kind), ("en", "Creator captions"))

    def test_language_prefix_match(self):
        info = {"subtitles": {"en-GB": self.option, "fr": self.option}}
        self.assertEqual(choose_track(info, "en")[0], "en-GB")
        self.assertEqual(choose_track(info, "EN")[0], "en-GB")

    def test_original_language_wins_without_request(self):
        info = {"language": "ms", "subtitles": {"en": self.option}, "automatic_captions": {"ms": self.option}}
        self.assertEqual(choose_track(info, "")[:2], ("ms", "Auto captions"))

    def test_unmatched_request_lists_available_codes(self):
        info = {"subtitles": {"en": self.option}, "automatic_captions": {"de": self.option}}
        with self.assertRaisesRegex(ValueError, r"No caption track matches 'ja'\. Available: de, en\."):
            choose_track(info, "ja")

    def test_no_tracks_and_live_chat_ignored(self):
        for info in ({}, {"subtitles": {"live_chat": self.option}}, {"subtitles": {"en": []}}):
            with self.subTest(info=info), self.assertRaisesRegex(ValueError, "did not provide a caption track"):
                choose_track(info, "")
        self.assertEqual(choose_track({"subtitles": {"live_chat": self.option, "fr": self.option}}, "")[0], "fr")


class SafeStemTests(unittest.TestCase):
    def test_strips_unsafe_characters(self):
        self.assertEqual(safe_stem('a<b>c:d"e/f\\g|h?i*j[k]l#m^n\x00o\x1fp', "x"), "a b c d e f g h i j k l m n o p")

    def test_trims_dots_and_spaces_and_truncates(self):
        self.assertEqual(safe_stem("  .Title.  ", "x"), "Title")
        self.assertEqual(safe_stem("a" * 200, "x"), "a" * 95)
        self.assertEqual(len(safe_stem("b" * 94 + " c", "x")), 94)

    def test_windows_reserved_names_and_fallback(self):
        self.assertEqual(safe_stem("CON", "x"), "_CON")
        self.assertEqual(safe_stem("nul.txt", "x"), "_nul.txt")
        self.assertEqual(safe_stem("COM1", "x"), "_COM1")
        self.assertEqual(safe_stem("Console", "x"), "Console")
        self.assertEqual(safe_stem(" ... ", "Fallback"), "Fallback")
        self.assertEqual(safe_stem("", "Fallback"), "Fallback")


class UnwrapNoteTests(unittest.TestCase):
    def test_strips_outer_fences(self):
        self.assertEqual(unwrap_note("````markdown\n## Notes\nText\n````"), "## Notes\nText")
        self.assertEqual(unwrap_note("~~~md\n## Notes\n~~~"), "## Notes")
        self.assertEqual(unwrap_note("\n\ufeff````\r\n## Notes\r\n````  \n"), "## Notes")

    def test_leaves_unfenced_text_and_inner_code(self):
        self.assertEqual(unwrap_note("  ## Notes\nText  "), "## Notes\nText")
        inner = "## Code\n```python\nprint(1)\n```"
        self.assertEqual(unwrap_note(f"````markdown\n{inner}\n````"), inner)
        self.assertEqual(unwrap_note(inner), inner)


class SaveNoteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="apis-vault-")
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name).resolve()

    def save(self, body, title="A useful idea"):
        path, linked, existing = save_note(self.vault, URL, title, body)
        return path, path.read_text(encoding="utf-8"), linked, existing

    def add(self, relative, content="# Existing\n"):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_links_are_kept_as_written_and_no_topic_files_are_made(self):
        path, text, linked, existing = self.save("````markdown\n## Notes\nUse [[Active recall]] and [[Spaced repetition|spacing]].\n````")
        self.assertEqual(path, self.vault / "YouTube" / "Notes" / f"A useful idea ({ID}).md")
        self.assertEqual(text, f'---\nsource: "{URL}"\nvideo_id: "{ID}"\n---\n'
                               f"# A useful idea\n\n[Watch on YouTube]({URL})\n\n## Notes\n"
                               "Use [[Active recall]] and [[Spaced repetition|spacing]].\n")
        self.assertEqual((linked, existing), (2, 0))
        self.assertEqual(sorted(p.relative_to(self.vault).as_posix() for p in self.vault.rglob("*") if p.is_file()),
                         [f"YouTube/Notes/A useful idea ({ID}).md"])

    def test_topics_matching_vault_notes_are_counted_case_insensitively(self):
        self.add("Areas/Active Recall.md")
        self.add("Projects/Spacing.md")
        self.add("Projects/Spacing copy/Spacing.md")
        _, text, linked, existing = self.save("Use [[active recall]], [[Spacing#Why]], [[active recall|again]] and [[New idea]].")
        self.assertIn("Use [[active recall]], [[Spacing#Why]], [[active recall|again]] and [[New idea]].", text)
        self.assertEqual((linked, existing), (3, 2))

    def test_saved_video_notes_and_hidden_folders_do_not_count_as_existing(self):
        self.add(f"YouTube/Notes/Topic ({ID}).md")
        self.add("YouTube/Notes/Topic.md")
        self.add(".obsidian/Hidden.md")
        self.assertEqual(self.save("About [[Topic]] and [[Hidden]].")[2:], (2, 0))

    def test_code_is_not_counted(self):
        body = ("Use [[Linked]].\n```\n[[Fenced]]\n```\n~~~~text\n[[Tilde]]\n~~~~\n"
                "Inline `[[Code]]` and ``[[Double]]`` stay.\n    [[Indented]]\n\t[[Tabbed]]")
        _, text, linked, _ = self.save(body)
        self.assertTrue(text.endswith(body + "\n"))
        self.assertEqual(linked, 1)

    def test_unusual_links_are_left_alone(self):
        body = "![[embed]] [[Folder/Note]] [[Note#Heading]] [[Note^block]] [[C:\\x]] [[Back\\slash]] [[" + "x" * 121 + "]]"
        _, text, _, _ = self.save(body)
        self.assertTrue(text.endswith(body + "\n"))

    def test_topic_names_come_from_saved_video_notes(self):
        everything = "spaced repetition sales funnel café code image youtube topic"
        self.assertEqual(topic_names(self.vault, everything), [])
        self.add("YouTube/Notes/One.md", "[[Spaced repetition]] [[YouTube/Topics/Sales funnel|Sales funnel]] ![[image.png]]")
        self.add("YouTube/Notes/Two.md", "[[spaced repetition|spacing]] [[Café#Menu]] `[[Code]]`")
        self.add("Areas/Elsewhere.md", "[[Not a YouTube topic]]")
        self.assertEqual(sorted(topic_names(self.vault, everything)), ["Café", "Sales funnel", "Spaced repetition"])

    def test_only_topics_the_transcript_mentions_are_offered(self):
        self.add("YouTube/Notes/One.md", "[[Sales funnel]] [[Cold email outreach]] [[B2B lead generation]] [[Sleep]]")
        transcript = "[00:05] Most B2B founders ignore their funnels.\n[00:09] Cold calling still works."
        # Half of "Sales funnel" matches, a third of the others; "Sleep" is not mentioned at all.
        self.assertEqual(topic_names(self.vault, transcript),
                         ["Sales funnel", "B2B lead generation", "Cold email outreach"])
        self.assertEqual(topic_names(self.vault, "Nothing related here."), [])
        self.assertEqual(topic_names(self.vault, ""), [])

    def test_full_matches_then_most_used_topics_come_first_within_the_limit(self):
        self.add("YouTube/Notes/One.md", "[[Sales funnel]] [[Sales calls]] [[Pricing]]")
        self.add("YouTube/Notes/Two.md", "[[Sales calls]] [[Pricing]] [[Pricing]]")
        self.add("YouTube/Notes/Three.md", "[[Pricing]]")
        transcript = "Sales calls, funnels, and pricing."
        # Full matches first (Pricing is in 3 notes, Sales calls in 2, Sales funnel in 1).
        self.assertEqual(topic_names(self.vault, transcript), ["Pricing", "Sales calls", "Sales funnel"])
        self.add("YouTube/Notes/Four.md", "[[Sales tax]]")
        self.assertEqual(topic_names(self.vault, transcript, limit=4)[-1], "Sales tax")  # Half its words match.
        self.assertEqual(topic_names(self.vault, transcript, limit=2), ["Pricing", "Sales calls"])

    def test_word_matching_handles_plurals_and_endings(self):
        self.add("YouTube/Notes/One.md", "[[Modeling successful businesses]] [[Category theory]] [[Box]]")
        self.assertEqual(topic_names(self.vault, "Two business models. Categories. Boxes."),
                         ["Box", "Modeling successful businesses", "Category theory"])  # 1/1, 2/3, 1/2 of the words.

    def test_vault_scan_never_enters_hidden_folders(self):
        for relative in (".obsidian/plugins/p/Active recall.md", ".git/objects/x.md", "Areas/.Hidden.md",
                         "Areas/Upper.MD", "Areas/Note.md", "Areas/Notes.txt"):
            self.add(relative)
        scanned = []
        real_scandir = os.scandir

        def recording_scandir(path="."):
            scanned.append(Path(path))
            return real_scandir(path)
        with patch.object(os, "scandir", recording_scandir):
            found = sorted(path.relative_to(self.vault).as_posix() for path in vault_markdown(self.vault))
        self.assertEqual(found, ["Areas/Note.md", "Areas/Upper.MD"])
        self.assertTrue(scanned)
        self.assertFalse([path for path in scanned if any(part.startswith(".") for part in path.relative_to(self.vault).parts)])

    def test_saving_twice_numbers_the_note(self):
        first = self.save("[[Topic]]")
        second = self.save("[[Topic]]")
        self.assertEqual(first[0].name, f"A useful idea ({ID}).md")
        self.assertEqual(second[0].name, f"A useful idea ({ID}) (2).md")
        self.assertEqual(first[1], second[1])

    def test_title_fallback_and_unsafe_title(self):
        path, text, _, _ = self.save("Body", title="  ")
        self.assertEqual(path.name, f"YouTube video {ID} ({ID}).md")
        self.assertIn(f"\n---\n# YouTube video {ID}\n", text)
        path, text, _, _ = self.save("Body", title="What? A: B/C")
        self.assertEqual(path.name, f"What A B C ({ID}).md")
        self.assertIn("\n---\n# What? A: B/C\n", text)

    def test_properties_come_first_exactly_once(self):
        properties = '---\nsource: "%s"\nvideo_id: "%s"\nsaved: 2026-11-14\ntags: ["youtube"]\n---\n' % (URL, ID)
        path, linked, existing = save_note(self.vault, URL, 'Say "hi": now', "## Notes\n---\nText",
                                          extra={"saved": date(2026, 11, 14), "tags": ["youtube"]})
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(properties + '# Say "hi": now\n\n[Watch on YouTube]'))
        self.assertEqual(text.count("---\nsource:"), 1)
        self.assertTrue(text.endswith("## Notes\n---\nText\n"))

    def test_note_with_its_own_properties_gets_no_second_block(self):
        body = "---\naliases: [Recall]\ntags:\n  - study\nrelated: \"[[Topic]]\"\n---\n\n## Notes\nText"
        _, text, _, _ = self.save(f"````markdown\n{body}\n````")
        self.assertTrue(text.startswith('---\naliases: [Recall]\ntags:\n  - study\nrelated: "[[Topic]]"\n---\n'
                                        f"# A useful idea\n\n[Watch on YouTube]({URL})\n\n## Notes\nText\n"))
        self.assertNotIn("video_id", text)

    def test_leading_rule_is_not_mistaken_for_properties(self):
        body = "---\n## Part one\nText\n---\n## Part two"
        _, text, _, _ = self.save(body)
        self.assertTrue(text.startswith(f'---\nsource: "{URL}"\n'))
        self.assertTrue(text.endswith(f"[Watch on YouTube]({URL})\n\n{body}\n"))
        _, text, _, _ = self.save("---\nkey: value\nno closing line")
        self.assertTrue(text.startswith(f'---\nsource: "{URL}"\n'))

    def test_frontmatter_values_are_quoted_safely(self):
        tricky = 'He said: "yes" # not a comment\n- [x] {y} \\ ünïcödé'
        block = frontmatter({"title": tricky, "saved": date(2026, 1, 2), "tags": ["youtube", "a: b"]})
        lines = block.splitlines()
        self.assertEqual((lines[0], lines[-1], len(lines)), ("---", "---", 5))
        self.assertEqual(json.loads(lines[1].split(": ", 1)[1]), tricky)
        self.assertEqual(lines[2], "saved: 2026-01-02")
        self.assertEqual(json.loads(lines[3].split(": ", 1)[1]), ["youtube", "a: b"])

    def test_empty_body_is_rejected(self):
        for body in ("", "   ", "````markdown\n\n````"):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "^Paste the Markdown from your AI model first\\.$"):
                save_note(self.vault, URL, "Title", body)
        self.assertFalse((self.vault / "YouTube").exists())


class MakePromptTests(unittest.TestCase):
    def test_modes_include_their_own_instruction(self):
        expected = {"Skim + detailed notes": "detailed notes in video order",
                    "Quick skim": "up to three moments to revisit",
                    "Answer a question": "Lead with a direct answer"}
        for mode, phrase in expected.items():
            with self.subTest(mode=mode):
                prompt = make_prompt(URL, mode, "", [])
                self.assertIn(phrase, prompt)
                self.assertIn(URL, prompt)
                for other in set(expected.values()) - {phrase}:
                    self.assertNotIn(other, prompt)

    def test_question_is_inserted_when_given(self):
        self.assertNotIn("My question or focus", make_prompt(URL, "Quick skim", "", []))
        prompt = make_prompt(URL, "Answer a question", "Why sleep?", [])
        self.assertIn("My question or focus: Why sleep?", prompt)
        self.assertLess(prompt.index("Why sleep?"), prompt.index("Cite the relevant timestamps"))

    def test_topics_are_appended_only_when_present(self):
        self.assertNotIn("Existing YouTube topics", make_prompt(URL, "Quick skim", "", []))
        prompt = make_prompt(URL, "Quick skim", "", ["Active recall", "Café"])
        self.assertTrue(prompt.endswith('Existing YouTube topics (use only relevant ones): ["Active recall", "Café"]'))

    def test_transcript_comes_first_in_one_message(self):
        prompt = make_prompt(URL, "Quick skim", "", [])
        message = with_transcript(prompt, URL, "A useful idea", "Auto captions (en)", "[00:05] Hello")
        self.assertEqual(message, f'Transcript of "A useful idea" ({URL})\nCaptions: Auto captions (en)\n\n'
                                  f"<transcript>\n[00:05] Hello\n</transcript>\n\n{prompt}")
        self.assertTrue(prompt.startswith(f"Use the transcript above for this YouTube video: {URL}"))


if __name__ == "__main__":
    unittest.main()
