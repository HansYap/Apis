"""Offline behavior checks for the desktop bridge and real file outputs."""

import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app


class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="apis-test-", dir=app.ROOT)
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(app.ROOT.resolve()))
        self.addCleanup(self.temporary.cleanup)
        for name, value in (("SETTINGS", self.root / "settings.json"),
                            ("TRANSCRIPTS", self.root / "Transcripts"),
                            ("DEPS", self.root / "packages")):
            patcher = patch.object(app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.api = app.Api()
        self.payload = {"url": "https://youtu.be/abcdefghijk", "title": "A useful idea",
                        "transcript": "0:05\nUse active recall.\n0:12\nReview it again tomorrow.",
                        "mode": "Skim + detailed notes", "question": "How can I remember this?",
                        "vault": str(self.root), "note": "````markdown\n## Notes\nUse [[Active recall]].\n````"}

    def test_prepare_and_save_end_to_end(self):
        result = self.api.prepare(self.payload)
        self.assertTrue(result["ok"], result)
        content = (app.TRANSCRIPTS / result["data"]["filename"]).read_text(encoding="utf-8")
        self.assertIn("[00:05] Use active recall.", content)
        self.assertIn("How can I remember this?", result["data"]["prompt"])
        first = self.api.save(self.payload)
        second = self.api.save(self.payload)
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(first["data"]["filename"], second["data"]["filename"])
        self.assertEqual((first["data"]["linked"], first["data"]["created"]), (1, 1))
        self.assertEqual(second["data"]["created"], 0)
        note = self.root / "YouTube" / "Notes" / first["data"]["filename"]
        self.assertIn("[[YouTube/Topics/Active recall|Active recall]]", note.read_text(encoding="utf-8"))

    def test_question_mode_requires_question_without_writing(self):
        self.payload.update(mode="Answer a question", question="")
        self.assertFalse(self.api.prepare(self.payload)["ok"])
        self.assertFalse(app.TRANSCRIPTS.exists())

    def test_step_one_requires_a_valid_video_and_transcript(self):
        self.assertFalse(self.api.validate_transcript("https://example.com", "Some words")["ok"])
        self.assertFalse(self.api.validate_transcript(self.payload["url"], " ")["ok"])
        result = self.api.validate_transcript(self.payload["url"], self.payload["transcript"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["url"], "https://www.youtube.com/watch?v=abcdefghijk")
        self.assertIn("[00:05]", result["data"]["text"])
        self.assertFalse(app.TRANSCRIPTS.exists())

    def test_new_video_resets_workflow_without_removing_saved_files(self):
        self.assertTrue(self.api.prepare(self.payload)["ok"])
        result = self.api.save(self.payload)
        self.assertTrue(result["ok"], result)
        saved = self.api._saved_note
        self.api._fetched = (self.payload["url"], "Captions", self.payload["transcript"])
        self.assertTrue(self.api.reset()["ok"])
        self.assertEqual(self.api.initialize()["data"]["vault"], "")
        self.assertIsNone(self.api._prepared)
        self.assertIsNone(self.api._fetched)
        self.assertIsNone(self.api._saved_note)
        self.assertTrue(saved.is_file())
        self.assertEqual(len(list(app.TRANSCRIPTS.glob("*.txt"))), 1)

    def test_changed_video_is_rejected_after_preparing(self):
        self.assertTrue(self.api.prepare(self.payload)["ok"])
        self.payload["url"] = "https://youtu.be/lmnopqrstuv"
        self.assertFalse(self.api.save(self.payload)["ok"])
        self.assertFalse((self.root / "YouTube" / "Notes").exists())

    def test_cancelled_file_dialog_does_not_replace_content(self):
        self.api._window = SimpleNamespace(create_file_dialog=lambda *a, **k: None)
        fake_webview = SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1, FOLDER=2))
        with patch.dict("sys.modules", {"webview": fake_webview}):
            self.assertEqual(self.api.import_file("transcript"), {"ok": True, "data": None})
            self.assertEqual(self.api.choose_vault(), {"ok": True, "data": None})

    def test_import_and_vault_selection(self):
        source = self.root / "captions.vtt"
        source.write_text("WEBVTT\n\n00:00:03.000 --> 00:00:06.000\nA useful idea\n", encoding="utf-8")
        self.api._window = SimpleNamespace(create_file_dialog=lambda *a, **k: (str(source),))
        fake_webview = SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1, FOLDER=2))
        with patch.dict("sys.modules", {"webview": fake_webview}):
            self.assertEqual(self.api.import_file("transcript")["data"]["text"], "[00:03] A useful idea")
            self.api._window.create_file_dialog = lambda *a, **k: (str(self.root),)
            self.assertEqual(self.api.choose_vault()["data"]["vault"], str(self.root))
            self.assertEqual(self.api.initialize()["data"]["vault"], str(self.root))

    def test_fetch_success_and_failure_release_lock(self):
        (app.DEPS / "yt_dlp").mkdir(parents=True)
        (app.DEPS / "yt_dlp" / "__init__.py").touch()
        result = {"title": "Example", "text": "[00:03] Caption", "track": "Creator captions (en)"}
        with patch.object(app.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(result), returncode=0)):
            self.assertTrue(self.api.fetch(self.payload["url"], "")["ok"])
        with patch.object(app.subprocess, "run", side_effect=subprocess.TimeoutExpired("captions", 90)):
            self.assertFalse(self.api.fetch(self.payload["url"], "")["ok"])
        self.assertFalse(self.api._caption_lock.locked())

    def test_existing_caption_task_prevents_another(self):
        self.api._caption_lock.acquire()
        try:
            self.assertFalse(self.api.install_reader()["ok"])
            self.assertFalse(self.api.fetch(self.payload["url"], "")["ok"])
        finally:
            self.api._caption_lock.release()

    def test_external_links_are_limited_to_expected_destinations(self):
        with patch.object(app.webbrowser, "open") as opened:
            self.assertFalse(self.api.open_external("file:///C:/")["ok"])
            self.assertFalse(self.api.open_external("youtube", "https://example.com")["ok"])
            opened.assert_not_called()
            self.assertTrue(self.api.open_external("youtube", self.payload["url"])["ok"])
            opened.assert_called_once_with("https://www.youtube.com/watch?v=abcdefghijk")

    def test_invalid_settings_and_reset(self):
        for content in ("null", "[]", "{broken", '{"vault": 42}'):
            app.SETTINGS.write_text(content, encoding="utf-8")
            self.assertEqual(self.api.initialize()["data"]["vault"], "")
        self.api._prepared = ("old", "old")
        self.assertTrue(self.api.reset()["ok"])
        self.assertIsNone(self.api._prepared)


if __name__ == "__main__":
    unittest.main()
