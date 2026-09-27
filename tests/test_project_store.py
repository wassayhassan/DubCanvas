from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anime_dubber.application.project import ProjectStore, get_project, list_projects
from anime_dubber.application.service import ApplicationService
from anime_dubber.core import source_key


class ProjectStoreTests(unittest.TestCase):
    def test_project_names_are_required_unique_and_can_be_renamed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = ProjectStore(root, "https://youtu.be/first")
            second = ProjectStore(root, "https://youtu.be/second")
            with self.assertRaisesRegex(ValueError, "Project name is required"):
                first.create(source="https://youtu.be/first", name="  ")
            first.create(source="https://youtu.be/first", name="Episode One")
            with self.assertRaisesRegex(ValueError, "already exists"):
                second.create(source="https://youtu.be/second", name=" episode   one ")
            second.create(source="https://youtu.be/second", name="Episode Two")
            with self.assertRaisesRegex(ValueError, "already exists"):
                second.rename("EPISODE ONE", "")
            with self.assertRaisesRegex(ValueError, "Project name is required"):
                first.rename("", "")
            self.assertEqual(first.rename("Episode 1", "")["name"], "Episode 1")

    def test_local_files_with_same_name_get_separate_projects_and_work_keys(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = root / "output"
            first = root / "a" / "episode.mp4"
            second = root / "b" / "episode.mp4"
            for video in (first, second):
                video.parent.mkdir()
                video.write_bytes(b"test")

            service = ApplicationService()
            project_a = service.create_project(str(output), str(first), "First")
            project_b = service.create_project(str(output), str(second), "Second")
            self.assertEqual(project_a["project_id"], "episode")
            self.assertNotEqual(project_a["project_id"], project_b["project_id"])
            self.assertEqual(source_key(str(first), output), project_a["project_id"])
            self.assertEqual(source_key(str(second), output), project_b["project_id"])
            self.assertEqual(len(service.list_projects(str(output))), 2)
            self.assertEqual(service.create_project(str(output), str(second), "Second")["name"], "Second")

            first.unlink()
            self.assertEqual(ProjectStore(output, str(first)).project_id, "episode")

    def test_source_revisions_are_reused_and_existing_dubs_keep_their_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            store = ProjectStore(out, "source.mp4")
            store.create(source="source.mp4", name="Source project")
            source_srt = out / "source.srt"
            source_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
            store.begin(job_id="job_1", kind="run", config={"source": "source.mp4"}, dub_id="dub_1")
            store.publish_artifact("source_srt", str(source_srt), dub_id="dub_1", version_id="dub_1")
            first = store.load()
            self.assertEqual(first["analysis_revision"], 1)
            self.assertIn("source:en", first["subtitles"])
            self.assertEqual(first["dubs"][0]["analysis_revision"], 1)
            store.finish(status="completed")

            store.begin(job_id="job_2", kind="run", config={"source": "source.mp4"}, dub_id="dub_2")
            store.publish_artifact("source_srt", str(source_srt), dub_id="dub_2", version_id="dub_2")
            self.assertEqual(store.load()["analysis_revision"], 1)
            self.assertEqual(list(store.load()["subtitles"]), ["source:en"])
            source_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nCorrected\n")
            store.publish_artifact("source_srt", str(source_srt), dub_id="dub_2", version_id="dub_2")
            updated = store.load()
            self.assertEqual(updated["analysis_revision"], 2)
            self.assertEqual([d["analysis_revision"] for d in updated["dubs"]], [1, 2])

    def test_manifest_lifecycle_and_redaction(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            store = ProjectStore(out, "https://youtu.be/example")
            store.begin(
                job_id="job_1",
                kind="run",
                config={
                    "source": "https://youtu.be/example",
                    "series_id": "demo",
                    "elevenlabs_api_key": "secret",
                    "tts": {"provider": "elevenlabs", "api_key": "nested-secret"},
                },
            )
            store.update_stage(stage="transcribing", title="Transcribing Mandarin", progress=0.5)
            store.append_log("line one\nline two")
            store.finish(status="completed", artifacts={"dubbed_video": "/tmp/out.mp4"})

            rows = list_projects(out)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "completed")
            self.assertEqual(rows[0]["stage"], "completed")

            detail = get_project(out, store.project_id)
            self.assertEqual(detail["artifacts"]["dubbed_video"], "/tmp/out.mp4")
            self.assertEqual(detail["config"]["elevenlabs_api_key"], "<redacted>")
            self.assertEqual(detail["config"]["tts"]["api_key"], "<redacted>")
            self.assertTrue(Path(detail["log_path"]).exists())

    def test_legacy_run_metadata_is_backfilled(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            key = "legacy123"
            (out / f"{key}_run.json").write_text(
                json.dumps({
                    "source": "https://youtu.be/legacy123",
                    "series_id": "legacy-series",
                }),
                encoding="utf-8",
            )
            (out / f"{key}_EN_DUB.mp4").write_bytes(b"video")
            rows = list_projects(out)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["project_id"], key)
            self.assertEqual(rows[0]["status"], "completed")
            self.assertTrue(rows[0]["legacy"])
            self.assertIn("dubbed_video", rows[0]["artifacts"])

    def test_service_persists_fake_job(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            service = ApplicationService()

            def fake_pipeline(config, progress, runner):
                progress("Transcribing Mandarin…")
                progress("Generating English voice: 1/2")
                progress("Generating English voice: 2/2")
                return {"dubbed_video": out / "final.mp4"}

            with patch("anime_dubber.application.service.run_pipeline", side_effect=fake_pipeline):
                job = service.run_sync({
                    "source": "video.mp4",
                    "output_dir": str(out),
                    "series_id": "demo",
                })

            self.assertEqual(job["status"], "completed")
            projects = service.list_projects(str(out))
            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["status"], "completed")
            self.assertEqual(projects[0]["artifacts"]["dubbed_video"], str(out / "final.mp4"))


if __name__ == "__main__":
    unittest.main()
