"""Shared logging setup for the DEVONthink pipeline.

Every Python script in the pipeline writes to the same central log at
~/Library/Logs/devonthink-pipeline.log using a consistent format so
records can be traced across scripts by grepping on UUID.

Import pattern (scripts live alongside this file in ~/.local/bin):

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path.home() / ".local" / "bin"))
    from pipeline_log import setup

    log = setup("singlefile-ingest")
    log.info("ingest start: %s", path)
    log.info("archived record %s", uuid, extra={"record_name": name, "record_uuid": uuid})

The `extra` dict with `record_name` / `record_uuid` is optional; when
present, it becomes a `(record="…"|uuid=…)` suffix on the log line.
Omitting it (most common) produces a plain message line.
"""

import logging
import sys
from pathlib import Path

LOG_PATH = Path.home() / "Library" / "Logs" / "devonthink-pipeline.log"


class _RecordSuffixFormatter(logging.Formatter):
    """Format a log record with an optional `(record="..."|uuid=...)` suffix."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        name = getattr(record, "record_name", None)
        uuid = getattr(record, "record_uuid", None)
        if name or uuid:
            base = f'{base} (record="{name or ""}"|uuid={uuid or ""})'
        return base


def setup(component: str, *, echo_stdout: bool | None = None) -> logging.Logger:
    """Return a logger configured to write to the central pipeline log.

    Args:
        component: Short identifier of the caller (e.g. "singlefile-ingest").
            Appears in brackets in every line.
        echo_stdout: If True, also echo INFO+ to stdout (useful for
            interactive CLI invocations). If None (default), auto-detect
            based on whether stdout is a TTY.

    Subsequent calls with the same component return the same logger
    without re-adding handlers.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"devonthink-pipeline.{component}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    file_handler = logging.FileHandler(str(LOG_PATH))
    file_handler.setFormatter(
        _RecordSuffixFormatter(
            f"%(asctime)s %(levelname)s [{component}] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(file_handler)

    if echo_stdout is None:
        echo_stdout = sys.stdout.isatty()
    if echo_stdout:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream_handler)

    return logger
