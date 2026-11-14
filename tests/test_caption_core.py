"""Offline checks for caption parsing, prompts, and Obsidian linking."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from caption_core import (choose_track, clean_transcript, make_prompt, parse_json3, parse_subtitle_file,
                          safe_stem, save_note, unwrap_note, vault_markdown, video_url)

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
        path, linked, created = save_note(self.vault, URL, title, body)
        return path, path.read_text(encoding="utf-8"), linked, created

    def add(self, relative, content="# Existing\n"):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_new_topic_is_created_and_linked(self):
        path, text, linked, created = self.save("````markdown\n## Notes\nUse [[Active recall]] and [[Spaced repetition|spacing]].\n````")
        self.assertEqual(path, self.vault / "YouTube" / "Notes" / f"A useful idea ({ID}).md")
        self.assertEqual(text, f"# A useful idea\n\n[Watch on YouTube]({URL})\n\n## Notes\n"
                               "Use [[YouTube/Topics/Active recall|Active recall]] and "
                               "[[YouTube/Topics/Spaced repetition|spacing]].\n")
        self.assertEqual((linked, created), (2, 2))
        topic = self.vault / "YouTube" / "Topics" / "Active recall.md"
        self.assertEqual(topic.read_text(encoding="utf-8"),
                         "# Active recall\n\nSee **Backlinks** for videos and notes that mention this topic.\n")

    def test_existing_topic_note_is_reused_case_insensitively(self):
        self.add("YouTube/Topics/Active Recall.md")
        _, text, linked, created = self.save("Use [[active recall]].")
        self.assertIn("[[YouTube/Topics/Active Recall|active recall]]", text)
        self.assertEqual((linked, created), (1, 0))

    def test_unique_note_elsewhere_is_reused(self):
        self.add("Areas/Topic.md")
        _, text, linked, created = self.save("About [[Topic]].")
        self.assertIn("[[Areas/Topic|Topic]]", text)
        self.assertEqual((linked, created), (1, 0))
        self.assertFalse((self.vault / "YouTube" / "Topics").exists())

    def test_ambiguous_notes_elsewhere_create_a_topic_note(self):
        self.add("Areas/Topic.md")
        self.add("Projects/Topic.md")
        _, text, _, created = self.save("About [[Topic]].")
        self.assertIn("[[YouTube/Topics/Topic|Topic]]", text)
        self.assertEqual(created, 1)

    def test_topic_folder_wins_over_notes_elsewhere(self):
        self.add("Areas/Topic.md")
        self.add("Projects/Topic.md")
        self.add("YouTube/Topics/Topic.md")
        _, text, _, created = self.save("About [[Topic]].")
        self.assertIn("[[YouTube/Topics/Topic|Topic]]", text)
        self.assertEqual(created, 0)

    def test_saved_video_notes_are_not_link_targets(self):
        self.add(f"YouTube/Notes/Topic ({ID}).md")
        self.add("YouTube/Notes/Topic.md")
        _, text, _, created = self.save("About [[Topic]].")
        self.assertIn("[[YouTube/Topics/Topic|Topic]]", text)
        self.assertEqual(created, 1)

    def test_code_is_not_rewritten(self):
        body = ("Use [[Linked]].\n```\n[[Fenced]]\n```\n~~~~text\n[[Tilde]]\n~~~~\n"
                "Inline `[[Code]]` and ``[[Double]]`` stay.\n    [[Indented]]\n\t[[Tabbed]]")
        _, text, linked, created = self.save(body)
        for kept in ("```\n[[Fenced]]\n```", "[[Tilde]]", "`[[Code]]`", "``[[Double]]``",
                     "    [[Indented]]", "\t[[Tabbed]]"):
            self.assertIn(kept, text)
        self.assertIn("[[YouTube/Topics/Linked|Linked]]", text)
        self.assertEqual((linked, created), (1, 1))

    def test_unusual_links_are_left_alone(self):
        body = "![[embed]] [[Folder/Note]] [[Note#Heading]] [[Note^block]] [[C:\\x]] [[Back\\slash]] [[" + "x" * 121 + "]]"
        _, text, linked, created = self.save(body)
        self.assertTrue(text.endswith(body + "\n"))
        self.assertEqual((linked, created), (0, 0))

    def test_hidden_folders_are_not_link_targets(self):
        self.add(".obsidian/Active recall.md")
        self.add(".trash/Spacing.md")
        _, text, _, created = self.save("[[Active recall]] [[Spacing]]")
        self.assertIn("[[YouTube/Topics/Active recall|Active recall]]", text)
        self.assertIn("[[YouTube/Topics/Spacing|Spacing]]", text)
        self.assertEqual(created, 2)

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

    def test_uppercase_md_notes_are_link_targets(self):
        self.add("Areas/Upper.MD")
        self.add("Areas/.Hidden.md")
        _, text, _, created = self.save("[[Upper]] [[Hidden]]")
        self.assertIn("[[Areas/Upper|Upper]]", text)
        self.assertIn("[[YouTube/Topics/Hidden|Hidden]]", text)
        self.assertEqual(created, 1)

    def test_symlinks_out_of_the_vault_are_not_link_targets(self):
        outside = tempfile.TemporaryDirectory(prefix="apis-outside-")
        self.addCleanup(outside.cleanup)
        target = Path(outside.name) / "Outside.md"
        target.write_text("# Outside\n", encoding="utf-8")
        try:
            os.symlink(target, self.vault / "Outside.md")
            os.symlink(outside.name, self.vault / "Linked folder", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are not available here.")
        _, text, _, created = self.save("[[Outside]]")
        self.assertIn("[[YouTube/Topics/Outside|Outside]]", text)
        self.assertEqual(created, 1)

    def test_saving_twice_numbers_the_note_and_creates_topics_once(self):
        first = self.save("[[Topic]]")
        second = self.save("[[Topic]]")
        self.assertEqual(first[0].name, f"A useful idea ({ID}).md")
        self.assertEqual(second[0].name, f"A useful idea ({ID}) (2).md")
        self.assertEqual((first[3], second[3]), (1, 0))
        self.assertEqual(first[1], second[1])

    def test_title_fallback_and_unsafe_title(self):
        path, text, _, _ = self.save("Body", title="  ")
        self.assertEqual(path.name, f"YouTube video {ID} ({ID}).md")
        self.assertTrue(text.startswith(f"# YouTube video {ID}\n"))
        path, text, _, _ = self.save("Body", title="What? A: B/C")
        self.assertEqual(path.name, f"What A B C ({ID}).md")
        self.assertTrue(text.startswith("# What? A: B/C\n"))

    def test_empty_body_is_rejected(self):
        for body in ("", "   ", "````markdown\n\n````"):
            with self.subTest(body=body), self.assertRaises(ValueError):
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


if __name__ == "__main__":
    unittest.main()
