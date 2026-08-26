from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.supervisor.server import V2StdioServer


class _BinaryStdout:
    def __init__(self, buffer: io.BytesIO) -> None:
        self.buffer = buffer


class _BinaryStdin:
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

    def test_hello_starts_supervisor_before_runtime_is_acknowledged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.supervisor.server.project_root", return_value=root), patch(
                "app.supervisor.server.resolve_pipeline_mode", return_value="fake"
            ):
                server = V2StdioServer(pipeline_mode="fake")
            server.supervisor = SimpleNamespace(start=AsyncMock())
            try:
                async def exercise_protocol() -> None:
                    output = io.BytesIO()
                    with patch("sys.stdout", _BinaryStdout(output)):
                        await server._handle_line(
                            b'{"protocol":"asr-local-workflow","protocol_version":2,'
                            b'"kind":"request","request_id":"req_hello_start",'
                            b'"method":"runtime.hello","params":{"supported_versions":[2]}}\n'
                        )
                    server.supervisor.start.assert_awaited_once_with()
                    response = json.loads(output.getvalue())
                    self.assertTrue(response["ok"])

                asyncio.run(exercise_protocol())
            finally:
                server.registry.close()

    def test_eof_closes_production_transcriber_even_when_supervisor_never_started(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.supervisor.server.project_root", return_value=root), patch(
                "app.supervisor.server.resolve_pipeline_mode", return_value="fake"
            ):
                server = V2StdioServer(pipeline_mode="fake")
            close = Mock()
            server.supervisor = SimpleNamespace(
                _started=False,
                _transcriber_closed=False,
                transcriber=SimpleNamespace(close=close),
            )
            try:
                with patch("sys.stdin", _BinaryStdin(io.BytesIO())):
                    asyncio.run(server.run())
                close.assert_called_once_with()
            finally:
                # server.run closes this registry in the tested path.
                pass

    def test_production_construction_failure_closes_untransferred_dispatcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = object.__new__(V2StdioServer)
            server.registry = Mock()
            server.secret_provider = Mock()
            server._emit_event = Mock()
            server._preloaded_inference_dependencies = (None,)
            server._gpu_dispatcher = None
            server._gpu_dispatcher_owned = False
            server._shared_cuda_manager = None
            server._gpu_execution_gate = None

            manager = SimpleNamespace(
                qwen_path=root / "qwen",
                local_asr_batch_size=lambda: 4,
                local_asr_max_batch_size=lambda: 8,
            )
            dispatcher = Mock()
            gate = object()
            hardware = SimpleNamespace(cuda_available=True, bf16_supported=False)

            with patch(
                "app.workflow.runtime_plan.profile_hardware",
                return_value=hardware,
            ), patch(
                "app.models.manager.ModelManager",
                return_value=manager,
            ), patch(
                "app.runtime.gpu_batch_scheduler.GpuExecutionGate",
                return_value=gate,
            ), patch(
                "app.runtime.gpu_batch_scheduler.GpuBatchDispatcher",
                return_value=dispatcher,
            ), patch(
                "app.pipeline.chunked_local.ChunkedLocalTranscriber",
                side_effect=RuntimeError("transcriber construction failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "transcriber construction failed"):
                    V2StdioServer._production_supervisor(server)

            dispatcher.close.assert_called_once_with()
            self.assertIsNone(server._gpu_dispatcher)
            self.assertFalse(server._gpu_dispatcher_owned)


if __name__ == "__main__":
    unittest.main()
