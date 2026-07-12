from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.supervisor.server import V2StdioServer


class _BinaryStdout:
    def __init__(self, buffer: io.BytesIO) -> None:
        self.buffer = buffer


class V2ServerStartupTests(unittest.TestCase):
    def test_production_dependencies_are_preloaded_on_the_main_thread_before_supervisor_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calls: list[tuple[str, str]] = []

            def preload() -> None:
                calls.append(("preload", threading.current_thread().name))

            def create_supervisor(_server):
                calls.append(("supervisor", threading.current_thread().name))
                from app.workflow.registry import WorkflowRegistry
                from app.workflow.supervisor import WorkflowSupervisor
                return WorkflowSupervisor(WorkflowRegistry(root / "secondary.sqlite3"))

            with patch("app.supervisor.server.project_root", return_value=root), patch(
                "app.supervisor.server.resolve_pipeline_mode", return_value="production"
            ), patch.object(V2StdioServer, "_preload_production_dependencies", side_effect=preload), patch.object(
                V2StdioServer, "_production_supervisor", autospec=True, side_effect=create_supervisor
            ):
                server = V2StdioServer(pipeline_mode="production")
            try:
                self.assertEqual(calls, [("preload", "MainThread"), ("supervisor", "MainThread")])
            finally:
                server.supervisor.registry.close()
                server.registry.close()

    def test_auto_mode_starts_without_native_inference_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.supervisor.server.project_root", return_value=root), patch(
                "app.supervisor.server.resolve_pipeline_mode", return_value="fake"
            ):
                server = V2StdioServer(pipeline_mode="auto")
            try:
                self.assertEqual(server.pipeline_mode, "fake")
                self.assertIsNone(server.startup_error)
            finally:
                server.registry.close()

    def test_missing_production_dependency_is_reported_without_startup_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = ModuleNotFoundError("No module named 'numpy'", name="numpy")
            with patch("app.supervisor.server.project_root", return_value=root), patch(
                "app.supervisor.server.resolve_pipeline_mode", return_value="production"
            ), patch.object(V2StdioServer, "_production_supervisor", side_effect=missing):
                server = V2StdioServer(pipeline_mode="production")
            try:
                self.assertEqual(server.startup_error["code"], "DEPENDENCY_MISSING")
                self.assertEqual(server.startup_error["details"]["dependency"], "numpy")

                async def exercise_protocol() -> None:
                    output = io.BytesIO()
                    with patch("sys.stdout", _BinaryStdout(output)):
                        await server._handle_line(
                            b'{"protocol":"asr-local-workflow","protocol_version":2,'
                            b'"kind":"request","request_id":"req_missing_dep",'
                            b'"method":"runtime.hello","params":{"supported_versions":[2]}}\n'
                        )
                    response = json.loads(output.getvalue())
                    self.assertFalse(response["ok"])
                    self.assertEqual(response["error"]["code"], "DEPENDENCY_MISSING")

                asyncio.run(exercise_protocol())
            finally:
                server.registry.close()

    def test_runtime_capabilities_report_the_instance_pipeline_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.supervisor.server.project_root", return_value=root), patch(
                "app.supervisor.server.resolve_pipeline_mode", return_value="fake"
            ):
                server = V2StdioServer(pipeline_mode="fake")
            try:
                result = asyncio.run(
                    server._dispatch({"method": "runtime.capabilities", "params": {}})
                )
                self.assertEqual(result["pipeline_mode"], {"requested": "fake", "resolved": "fake"})
            finally:
                server.registry.close()


if __name__ == "__main__":
    unittest.main()
