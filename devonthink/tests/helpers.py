"""Load the pipeline's hyphenated scripts as importable modules.

The scripts under stow/devonthink/.local/bin/ are named for the command line,
not for `import`, and they call pipeline_log.setup() at module scope. Stubbing
pipeline_log before the import keeps the suite off the real pipeline log —
otherwise a test that exercises a warning path writes to the file dt-watchdog
scans, and the run raises a desktop notification.
"""

import importlib.util
import logging
import sys
import types
from pathlib import Path

BIN = Path(__file__).resolve().parents[2] / "stow" / "devonthink" / ".local" / "bin"


def _stub_pipeline_log():
    if "pipeline_log" in sys.modules:
        return
    mod = types.ModuleType("pipeline_log")

    def setup(component, **_kwargs):
        logger = logging.getLogger("dtpipeline-test." + component)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        return logger

    mod.setup = setup
    sys.modules["pipeline_log"] = mod


def load(filename, module_name):
    _stub_pipeline_log()
    if str(BIN) not in sys.path:
        sys.path.insert(0, str(BIN))
    spec = importlib.util.spec_from_file_location(module_name, BIN / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level=logging.WARNING):
        return [r.getMessage() for r in self.records if r.levelno >= level]


class capture_logs:
    """Collect log records emitted by a loaded module during the block."""

    def __init__(self, module):
        self.module = module
        self.handler = CapturingHandler()

    def __enter__(self):
        self.module.log.addHandler(self.handler)
        return self.handler

    def __exit__(self, *exc):
        self.module.log.removeHandler(self.handler)
        return False


def person(name, uuid=None, aliases="", **md):
    """A dump_people entry. md kwargs are bare field names (relationship=...)."""
    return {
        "uuid": uuid or f"uuid-{name.replace(' ', '-').lower()}",
        "name": name,
        "aliases": aliases,
        "md": {f"md{k}": v for k, v in md.items()},
    }


def contact(name, nickname="", emails=(), phones=(), birthday=None):
    """A contacts-json.js entry. birthday is {"month", "day", "year"?} or None."""
    return {
        "id": f"cn-{name.replace(' ', '-').lower()}",
        "name": name,
        "nickname": nickname,
        "emails": list(emails),
        "phones": list(phones),
        "birthday": birthday,
    }


def attendee(name, email="", is_self=False, is_person=True):
    return {"name": name, "email": email, "is_self": is_self, "is_person": is_person}


def event(title, attendees=(), date="2026-07-07", calendar="Calendar",
          all_day=False, rsvp="accepted", organizer_is_self=False,
          canceled=False, calendar_id="", source_id="", event_id=""):
    """`rsvp` mirrors calendar-events-json.js: your own participant status, or
    None when the event carries no invitation for you (no attendees at all, or
    a distribution-list invite that never lists you individually)."""
    return {
        "title": title,
        "calendar": calendar,
        "calendar_id": calendar_id,
        "source_id": source_id,
        "event_id": event_id,
        "date": date,
        "start": f"{date}T09:00:00",
        "end": f"{date}T10:00:00",
        "all_day": all_day,
        "rsvp": rsvp,
        "organizer_is_self": organizer_is_self,
        "canceled": canceled,
        "location": "",
        "attendees": list(attendees),
    }
