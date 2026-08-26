from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.pipeline.chunked_local import ChunkedLocalTranscriber


class _DispatcherProbe:
    def __init__(self) -> None:
        self.cancel_calls: list[tuple[str, str]] = []
        self.close_called = threading.Event()

    def cancel_attempt(self, workflow_id: str, attempt_id: str) -> int:
        self.cancel_calls.append((workflow_id, attempt_id))
        return 1

    def close(self) -> None:
        self.close_called.set()


class Phase3LifecycleTests(unittest.TestCase):
    def test_native_sync_call_is_tracked_cancelled_and_drained_before_dispatcher_close(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as directory:
                transcript = Path(directory) / "transcript.md"
                transcript.write_text("done", encoding="utf-8")
                started = threading.Event()
                release = threading.Event()
                dispatcher = _DispatcherProbe()
                transcriber = ChunkedLocalTranscriber(batch_executor=dispatcher)

                def blocking_sync(_spec, _attempt_id, _progress=None):
                    started.set()
                    release.wait(timeout=3)
                    return {"md_path": str(transcript), "warnings": []}

                with patch.object(transcriber, "_transcribe_sync", side_effect=blocking_sync):
                    task = asyncio.create_task(
                        transcriber.transcribe({"workflow_id": "wf-native"}, "attempt-native")
                    )
                    await asyncio.to_thread(started.wait, 2)
                    self.assertEqual(
                        transcriber.cancel_attempt("wf-native", "attempt-native"),
                        1,
                    )
                    close_task = asyncio.create_task(asyncio.to_thread(transcriber.close))
                    await asyncio.sleep(0.05)
                    self.assertFalse(dispatcher.close_called.is_set())
                    self.assertFalse(task.done())
                    release.set()
                    result = await task
                    await close_task

                self.assertEqual(result["md_path"], str(transcript))
                self.assertTrue(dispatcher.close_called.is_set())
                self.assertEqual(dispatcher.cancel_calls, [("wf-native", "attempt-native")])

        asyncio.run(scenario())

    def test_async_cancel_keeps_native_call_active_and_defers_dispatcher_close(self) -> None:
        async def scenario() -> None:
            started = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            dispatcher = _DispatcherProbe()
            transcriber = ChunkedLocalTranscriber(
                batch_executor=dispatcher,
                close_wait_seconds=0.05,
            )

            def blocking_sync(_spec, _attempt_id, _progress=None):
                started.set()
                release.wait(timeout=3)
                finished.set()
                return {"md_path": "unused", "warnings": []}

            with patch.object(transcriber, "_transcribe_sync", side_effect=blocking_sync):
                task = asyncio.create_task(
                    transcriber.transcribe({"workflow_id": "wf-race"}, "attempt-race")
                )
                await asyncio.to_thread(started.wait, 2)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

                # Cancellation of the asyncio facade does not own the native
                # call's lifecycle reservation.
                self.assertEqual(transcriber._active_sync_calls, 1)
                self.assertIn(("wf-race", "attempt-race"), transcriber._active_attempts)
                await asyncio.to_thread(transcriber.close)
                self.assertFalse(dispatcher.close_called.is_set())
                self.assertEqual(transcriber._active_sync_calls, 1)

                release.set()
                await asyncio.to_thread(finished.wait, 2)
                await asyncio.to_thread(dispatcher.close_called.wait, 2)
                self.assertEqual(transcriber._active_sync_calls, 0)
                self.assertTrue(dispatcher.close_called.is_set())

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
