import json
import unittest
from unittest.mock import patch
from macos.publish_update import main


class UpdatePublisherTests(unittest.TestCase):
    def test_failed_ci_cannot_publish_update(self):
        runs = {"workflow_runs": [{"id": 1, "name": "Backend boundary tests", "status": "completed", "conclusion": "failure"}]}
        with patch.dict("os.environ", GITHUB_SHA="a" * 40, GITHUB_REPOSITORY="owner/repo"), \
             patch("macos.publish_update.run", return_value=json.dumps(runs)) as run:
            with self.assertRaisesRegex(RuntimeError, "required check failed"):
                main()
        self.assertEqual(run.call_count, 1)

    def test_outdated_main_build_cannot_replace_update_manifest(self):
        runs = {"workflow_runs": [{"id": i, "name": name, "status": "completed", "conclusion": "success"}
                for i, name in enumerate(("Backend boundary tests", "SwiftUI shell build"))]}
        with patch.dict("os.environ", GITHUB_SHA="a" * 40, GITHUB_REPOSITORY="owner/repo"), \
             patch("macos.publish_update.run", side_effect=[json.dumps(runs), "b" * 40]) as run:
            main()
        self.assertEqual(run.call_count, 2)
