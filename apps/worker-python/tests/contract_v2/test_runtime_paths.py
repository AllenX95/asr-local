from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.config import config_dir, project_root, state_dir


class RuntimePathTests(unittest.TestCase):
    def test_explicit_project_and_state_directories_override_source_layout(self) -> None:
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as state:
            with patch.dict(
                os.environ,
                {"ASR_LOCAL_PROJECT_ROOT": project, "ASR_LOCAL_STATE_DIR": state},
                clear=False,
            ):
                self.assertEqual(project_root(), Path(project).resolve())
                self.assertEqual(state_dir(), Path(state).resolve())

    def test_explicit_config_directory_is_independent_from_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as config:
            with patch.dict(os.environ, {"ASR_LOCAL_CONFIG_DIR": config}, clear=False):
                self.assertEqual(config_dir(), Path(config).resolve())

    def test_state_directory_defaults_to_project_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            with patch.dict(os.environ, {"ASR_LOCAL_PROJECT_ROOT": project}, clear=False):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("ASR_LOCAL_STATE_DIR", None)
                    self.assertEqual(state_dir(), Path(project).resolve() / "outputs" / ".workflow")


if __name__ == "__main__":
    unittest.main()
