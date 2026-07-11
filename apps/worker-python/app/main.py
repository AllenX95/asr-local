from __future__ import annotations

import argparse
import locale
import sys

from app.logging_utils import configure_worker_logging, get_logger


LOGGER = get_logger()


def main(argv: list[str] | None = None) -> int:
    configure_worker_logging()
    LOGGER.info(
        "worker process startup | stdin=%s | stdout=%s | fs=%s | preferred=%s",
        sys.stdin.encoding,
        sys.stdout.encoding,
        sys.getfilesystemencoding(),
        locale.getpreferredencoding(False),
    )
    parser = argparse.ArgumentParser(description="ASR Local Workflow Runtime v2")
    parser.add_argument(
        "--contract",
        choices=("v2",),
        default="v2",
        help="Workflow protocol version. Only v2 is supported.",
    )
    parser.add_argument(
        "--pipeline-mode",
        choices=("auto", "fake", "production"),
        default="auto",
        help="Select auto/fake/production pipeline adapters.",
    )
    args = parser.parse_args(argv)

    from app.supervisor.server import run_v2_stdio

    return run_v2_stdio(pipeline_mode=args.pipeline_mode)


if __name__ == "__main__":
    raise SystemExit(main())
