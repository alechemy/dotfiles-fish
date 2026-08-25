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

A run attached to a terminal, or one with PIPELINE_MANUAL=1 in its environment,
logs its component as `<component>/manual`. dt-watchdog.sh scans this same file
for failures and cannot otherwise tell a hand-run script from a launchd one, so
a manual test would raise a desktop notification about a failure nobody is
subject to. Set PIPELINE_MANUAL=1 when driving these scripts from a harness
whose stdout is a pipe (an agent, a CI job); smart-rule invocations leave it
unset, so their failures still notify.
"""

# Imported by scripts pinned to /usr/bin/python3 (3.9); future annotations
# keep PEP 604 union syntax parseable. See AGENTS.md.
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

LOG_PATH = Path.home() / "Library" / "Logs" / "devonthink-pipeline.log"


MAX_RECORD_CHARS = 3000


class _RecordSuffixFormatter(logging.Formatter):
    """Format a log record as a single log entry.

    Appends an optional `(record="..."|uuid=...)` suffix to the first line,
    then TAB-indents every continuation line. A multi-line message (an
    exception traceback, a subprocess's captured stderr) would otherwise be
    read by dt-watchdog.sh as one failure per line and raise a notification
    for each; the leading tab marks them as belonging to the entry above.
    The entry is capped so the append stays within PIPE_BUF and concurrent
    writers can't interleave fragments.
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        name = getattr(record, "record_name", None)
        uuid = getattr(record, "record_uuid", None)
        head, _, rest = base.partition("\n")
        if name or uuid:
            head = f'{head} (record="{name or ""}"|uuid={uuid or ""})'
        if rest:
            head = "\n".join(
                [head] + [f"\t{line}" for line in rest.split("\n") if line.strip()]
            )
        if len(head) > MAX_RECORD_CHARS:
            head = head[:MAX_RECORD_CHARS] + "\n\t[truncated]"
        return head


def _is_manual() -> bool:
    if os.environ.get("PIPELINE_MANUAL") == "1":
        return True
    return sys.stdout.isatty() or sys.stderr.isatty()


def setup(
    component: str,
    *,
    echo_stdout: bool | None = None,
    manual: bool | None = None,
) -> logging.Logger:
    """Return a logger configured to write to the central pipeline log.

    Args:
        component: Short identifier of the caller (e.g. "singlefile-ingest").
            Appears in brackets in every line.
        echo_stdout: If True, also echo INFO+ to stdout (useful for
            interactive CLI invocations). If None (default), auto-detect
            based on whether stdout is a TTY.
        manual: If True, tag the component `<component>/manual` so
            dt-watchdog.sh does not notify on this run's failures. If None
            (default), auto-detect from a TTY on either stream or
            PIPELINE_MANUAL=1.

    Subsequent calls with the same component return the same logger
    without re-adding handlers.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"devonthink-pipeline.{component}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if manual is None:
        manual = _is_manual()
    label = f"{component}/manual" if manual else component

    file_handler = logging.FileHandler(str(LOG_PATH))
    file_handler.setFormatter(
        _RecordSuffixFormatter(
            f"%(asctime)s %(levelname)s [{label}] %(message)s",
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
