#!/usr/bin/env python3
"""Project scoped metadata or opt-in prose from agent-reader normalized JSON."""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import sys


CREDENTIAL = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]+|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|AIza[A-Za-z0-9_-]{30,}|https?://[^\s/@]+:[^\s/@]+@[^\s]+"
)
SENSITIVE_LINE = re.compile(
    r"password|passwd|passphrase|secret|credential|authorization|cookie"
    r"|api[ _-]?key|access[ _-]?key|private[ _-]?key|\btoken\b|[A-Z_]+_TOKEN\b",
    re.IGNORECASE,
)
DUMP_LINE = re.compile(
    r"^\s*(?:[\[{<]|[\]}]|\$ |>>> |diff --git|@@ |[+-]{3} |"
    r"(?:tool[ _-]?(?:result|output|call)|stdout|stderr)\b|"
    r"(?:def|class|import|from|const|let|var|function|export|return)\s)"
    r"|^\s*[\"']?[\w.-]+[\"']?\s*[:=]\s*|[{};]|\w+\([^)]*\)",
    re.IGNORECASE,
)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments; use --help")


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def stamp(value):
    parsed = timestamp(value)
    return parsed.isoformat() if parsed else None


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("invalid session metadata")
    if CREDENTIAL.search(value):
        raise ValueError("invalid session metadata")
    return value


def workspace(value):
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("workspace must be an absolute path")
    return os.path.normpath(value)


def validate_session(value):
    if not isinstance(value, dict):
        raise ValueError("invalid normalized session")
    identifier(value.get("cli"))
    identifier(value.get("id"))
    if value.get("cwd") is not None:
        workspace(value["cwd"])


def in_workspace(value, args):
    return value.get("cwd") is not None and workspace(value["cwd"]) == args.workspace


def in_window(value, args):
    parsed = timestamp(value)
    return args.all_history or (parsed is not None and args.since <= parsed < args.until)


def prose(text):
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)",
                  "", text, flags=re.DOTALL)
    text = CREDENTIAL.sub("[credential omitted]", text)
    lines = []
    fence = None
    dump = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            dump = False
            continue
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            delimiter = marker.group(1)
            if fence is None:
                fence = delimiter
            elif (delimiter[0] == fence[0] and len(delimiter) >= len(fence)
                  and not stripped[marker.end():].strip()):
                fence = None
            continue
        if fence or dump:
            continue
        if SENSITIVE_LINE.search(line) or line.startswith(("    ", "\t")) or DUMP_LINE.search(line):
            dump = True
            continue
        line = re.sub(r"`[^`]*`", "[code omitted]", line)
        line = "".join(char for char in line if char.isprintable()).strip()
        if line:
            lines.append(line)
    return lines


def session_index(value, args):
    if not isinstance(value, list):
        raise ValueError("expected a normalized session array")
    candidates = []
    unknown = 0
    for item in value:
        validate_session(item)
        if not in_workspace(item, args) or (args.cli and item["cli"] != args.cli):
            continue
        if item["id"] in args.exclude_session:
            continue
        unknown += timestamp(item.get("last_activity")) is None
        if in_window(item.get("last_activity"), args):
            candidates.append({"cli": item["cli"], "id": item["id"],
                               "last_activity": stamp(item.get("last_activity"))})
    candidates.sort(key=lambda item: (item["last_activity"] or "", item["cli"], item["id"]), reverse=True)
    page = candidates[args.offset:args.offset + args.limit]
    next_offset = args.offset + len(page)
    more = next_offset < len(candidates)
    return dict(sessions=page, matched=len(candidates), unknown_dates=unknown,
                all_history=args.all_history, offset=args.offset, truncated=more,
                next_offset=next_offset if more else None)


def transcript(value, args):
    if not isinstance(value, dict):
        raise ValueError("expected a normalized transcript object")
    session = value.get("session")
    validate_session(session)
    if not in_workspace(session, args) or session["id"] != args.session or session["cli"] != args.cli:
        raise ValueError("transcript does not match requested scope")
    turns = value.get("turns")
    if not isinstance(turns, list):
        raise ValueError("invalid normalized turns")
    candidates = []
    unknown = 0
    previous = -1
    for turn in turns:
        if not isinstance(turn, dict):
            raise ValueError("invalid normalized turn")
        index = turn.get("index")
        blocks = turn.get("blocks")
        prompt = turn.get("prompt")
        if (type(index) is not int or index <= previous or
                not isinstance(blocks, list) or any(not isinstance(block, str) for block in blocks) or
                (prompt is not None and not isinstance(prompt, str))):
            raise ValueError("invalid normalized turn")
        previous = index
        unknown += timestamp(turn.get("timestamp")) is None
        if not in_window(turn.get("timestamp"), args):
            continue
        lines = prose("\n\n".join([prompt or "", *blocks]))
        if args.topic:
            lines = [line for line in lines if args.topic.casefold() in line.casefold()]
            if not lines:
                continue
        item = dict(index=index, timestamp=stamp(turn.get("timestamp")))
        if args.include_content:
            text = "\n".join(lines)
            item.update(text=text[:args.max_chars], text_truncated=len(text) > args.max_chars)
        candidates.append(item)
    eligible = [item for item in candidates if item["index"] >= (args.start_turn or 0)]
    page = eligible[:args.max_turns]
    more = len(eligible) > len(page)
    return dict(cli=session["cli"], id=session["id"], turns=page, matched=len(candidates),
                unknown_dates=unknown, all_history=args.all_history,
                content_included=args.include_content, truncated=more,
                next_start_turn=eligible[len(page)]["index"] if more else None)


def arguments():
    parser = SafeParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in ("session-index", "transcript"):
        cmd = sub.add_parser(operation)
        cmd.add_argument("--workspace", required=True, help="Exact absolute cwd; no descendants or aliases")
        cmd.add_argument("--since", help="Inclusive ISO timestamp with timezone")
        cmd.add_argument("--until", help="Exclusive ISO timestamp with timezone")
        cmd.add_argument("--all-history", action="store_true", help="Include undated records too")
        cmd.add_argument("--cli", required=operation == "transcript")
        if operation == "session-index":
            cmd.add_argument("--offset", type=int, default=0)
            cmd.add_argument("--limit", type=int, default=20, help="Page size, 1..100")
            cmd.add_argument("--exclude-session", action="append", default=[])
        else:
            cmd.add_argument("--session", required=True, help="Full exact ID, not a prefix")
            cmd.add_argument("--topic", help="Literal case-insensitive match on sanitized prose lines")
            cmd.add_argument("--include-content", action="store_true")
            cmd.add_argument("--start-turn", type=int, help="Required for content; zero-based normalized index")
            cmd.add_argument("--max-turns", type=int, default=3, help="Page size, 1..5")
            cmd.add_argument("--max-chars", type=int, default=1000, help="Text per turn, 1..2000 characters")
    args = parser.parse_args()
    args.workspace = workspace(args.workspace)
    if args.all_history:
        if args.since or args.until:
            raise ValueError("all-history cannot be combined with a date window")
    else:
        args.since, args.until = timestamp(args.since), timestamp(args.until)
        if args.since is None or args.until is None or args.since >= args.until:
            raise ValueError("provide --since and --until with timezones, or --all-history")
    if args.cli:
        identifier(args.cli)
    if args.operation == "session-index":
        if args.offset < 0 or not 1 <= args.limit <= 100:
            raise ValueError("invalid pagination bounds")
    else:
        identifier(args.session)
        if args.topic is not None and (not args.topic.strip() or len(args.topic) > 200):
            raise ValueError("topic must contain 1..200 characters")
        if args.include_content and args.start_turn is None:
            raise ValueError("content requires an explicit --start-turn")
        if (args.start_turn is not None and args.start_turn < 0) or not 1 <= args.max_turns <= 5 or not 1 <= args.max_chars <= 2000:
            raise ValueError("invalid excerpt bounds")
    return args


def main():
    try:
        args = arguments()
        value = json.load(sys.stdin)
        result = session_index(value, args) if args.operation == "session-index" else transcript(value, args)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        print("recall-filter: invalid input or scope; no output released; check --help", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
