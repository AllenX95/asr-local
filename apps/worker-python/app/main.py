from __future__ import annotations

import argparse
import locale
import sys

from app.ipc.protocol import decode_message, encode_message
from app.logging_utils import configure_worker_logging, get_logger
from app.runtime.env import environment_snapshot


LOGGER = get_logger()


def health_payload() -> dict:
    payload = environment_snapshot()
    payload.update(
        {
            "contract_version": "worker-contract-v1",
            "supported_commands": ["health_check", "run_job", "shutdown"],
        }
    )
    return payload


def handle_command(message: dict, emit=None) -> dict:
    message_type = message.get("type")
    LOGGER.info("received command | type=%s", message_type)

    if message_type == "health_check":
        LOGGER.info("running health check")
        return {
            "type": "health_check_ok",
            "payload": health_payload(),
        }

    if message_type == "run_job":
        from app.pipeline.job_runner import run_job

        payload = message.get("payload", {})
        job_id = payload.get("job_id")
        try:
            LOGGER.info("running job | job_id=%s | source=%s", job_id, payload.get("source_path"))
            result = run_job(payload, emit=emit)
            LOGGER.info(
                "job completed | job_id=%s | md_path=%s | segments=%s | speakers=%s",
                job_id,
                result.get("md_path"),
                result.get("segments"),
                result.get("speakers"),
            )
            return {
                "type": "job_completed",
                "job_id": job_id,
                "payload": result,
            }
        except Exception as exc:
            LOGGER.exception("job failed | job_id=%s", job_id)
            if emit is not None and job_id:
                emit(
                    job_id,
                    {
                        "stage": "failed",
                        "progress": 1.0,
                        "processed_ms": 0,
                        "total_ms": 0,
                        "error_message": str(exc),
                    },
                )
            return {
                "type": "job_failed",
                "job_id": job_id,
                "payload": {
                    "reason": str(exc),
                    "user_message": "转写任务失败。",
                    "diagnostic_detail": str(exc),
                    "stage": "failed",
                },
            }

    if message_type == "shutdown":
        LOGGER.info("shutdown requested")
        return {"type": "shutdown_ack", "payload": {}}

    LOGGER.warning("unsupported message type | type=%s", message_type)
    return {
        "type": "error",
        "payload": {"reason": f"unsupported message type: {message_type}"},
}


def emit_message(message_type: str, payload: dict | None = None, **extra) -> None:
    sys.stdout.write(encode_message(message_type, payload, **extra) + "\n")
    sys.stdout.flush()


def run_stdio_server() -> int:
    LOGGER.info("stdio worker server started")
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            message = decode_message(line)
            response = handle_command(
                message,
                emit=lambda job_id, payload: emit_message(
                    "job_event",
                    payload,
                    job_id=job_id,
                ),
            )
        except Exception as exc:  # pragma: no cover - skeleton error surface
            LOGGER.exception("failed to handle incoming message")
            response = {"type": "error", "payload": {"reason": str(exc)}}

        emit_message(
            response["type"],
            response.get("payload"),
            **{
                key: value
                for key, value in response.items()
                if key not in {"type", "payload"}
            },
        )

        if response["type"] == "shutdown_ack":
            break

    return 0


def main(argv: list[str] | None = None) -> int:
    configure_worker_logging()
    LOGGER.info(
        "worker process startup | stdin=%s | stdout=%s | fs=%s | preferred=%s",
        sys.stdin.encoding,
        sys.stdout.encoding,
        sys.getfilesystemencoding(),
        locale.getpreferredencoding(False),
    )
    parser = argparse.ArgumentParser(description="Local ASR worker skeleton")
    parser.add_argument("--health-check", action="store_true", help="Print a JSON health snapshot and exit.")
    args = parser.parse_args(argv)

    if args.health_check:
        LOGGER.info("health check CLI mode")
        sys.stdout.write(encode_message("health_check_ok", health_payload()) + "\n")
        return 0

    return run_stdio_server()


if __name__ == "__main__":
    raise SystemExit(main())
