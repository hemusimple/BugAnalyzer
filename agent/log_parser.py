"""
log_parser.py — Parse Android logcat and generic log files.
Extracts: timestamps, log levels, tags, messages, exceptions, thread info.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# Android logcat line: "01-15 12:34:56.789  1234  5678 D TAG: message"
LOGCAT_RE = re.compile(
    r"^(?P<date>\d{2}-\d{2})\s+(?P<time>[\d:.]+)\s+"
    r"(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<level>[VDIWEF])\s+(?P<tag>[^\s:]+)\s*:\s*(?P<message>.*)$"
)

# Also handle: "D/TAG(pid): message"
LOGCAT_RE2 = re.compile(
    r"^(?P<level>[VDIWEF])/(?P<tag>[^\(]+)\(\s*(?P<pid>\d+)\)\s*:\s*(?P<message>.*)$"
)

# Exception / stack trace
EXCEPTION_RE = re.compile(r"^(?:\s+at\s+\S+|.*Exception.*:|.*Error.*:)")

# Timeout keywords
TIMEOUT_KEYWORDS = ["timeout", "timed out", "ANR", "watchdog", "deadline"]
ERROR_KEYWORDS = ["error", "exception", "crash", "fatal", "fail", "npe", "nullpointer"]
DELAY_KEYWORDS = ["slow", "delay", "latency", "blocked", "wait", "hung"]


@dataclass
class LogEntry:
    raw: str
    line_no: int
    date: str = ""
    time: str = ""
    pid: str = ""
    tid: str = ""
    level: str = ""       # V D I W E F
    tag: str = ""
    message: str = ""
    is_exception: bool = False
    is_stacktrace: bool = False


@dataclass
class LogSession:
    entries: list[LogEntry] = field(default_factory=list)
    tags_seen: set[str] = field(default_factory=set)
    errors: list[LogEntry] = field(default_factory=list)
    exceptions: list[LogEntry] = field(default_factory=list)
    timeouts: list[LogEntry] = field(default_factory=list)
    unique_tags: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "total_lines": len(self.entries),
            "error_count": len(self.errors),
            "exception_count": len(self.exceptions),
            "timeout_count": len(self.timeouts),
            "unique_tags": sorted(self.tags_seen),
            "error_lines": [e.raw for e in self.errors[:20]],
        }


def parse_log_file(path: Path) -> LogSession:
    session = LogSession()
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    for i, raw in enumerate(lines):
        entry = _parse_line(raw, i + 1)
        session.entries.append(entry)

        if entry.tag:
            session.tags_seen.add(entry.tag)

        if entry.level in ("E", "F"):
            session.errors.append(entry)

        if entry.is_exception:
            session.exceptions.append(entry)

        msg_lower = (entry.message or raw).lower()
        if any(k in msg_lower for k in TIMEOUT_KEYWORDS):
            session.timeouts.append(entry)

    session.unique_tags = sorted(session.tags_seen)
    return session


def _parse_line(raw: str, line_no: int) -> LogEntry:
    entry = LogEntry(raw=raw, line_no=line_no)

    m = LOGCAT_RE.match(raw)
    if m:
        entry.date = m.group("date")
        entry.time = m.group("time")
        entry.pid = m.group("pid")
        entry.tid = m.group("tid")
        entry.level = m.group("level")
        entry.tag = m.group("tag").strip()
        entry.message = m.group("message")
    else:
        m2 = LOGCAT_RE2.match(raw)
        if m2:
            entry.level = m2.group("level")
            entry.tag = m2.group("tag").strip()
            entry.pid = m2.group("pid")
            entry.message = m2.group("message")

    # Detect exceptions
    if EXCEPTION_RE.match(raw):
        entry.is_exception = True
        entry.is_stacktrace = raw.strip().startswith("at ")

    return entry


def extract_relevant_window(session: LogSession, query_tags: list[str], window: int = 50) -> str:
    """
    Extract lines around mentions of the given tags.
    Returns a condensed log string suitable for LLM context.
    """
    if not query_tags:
        # Fall back to errors + exceptions
        relevant = session.errors + session.exceptions + session.timeouts
        relevant.sort(key=lambda e: e.line_no)
        return "\n".join(e.raw for e in relevant[:200])

    tag_set = {t.lower() for t in query_tags}
    hit_lines: set[int] = set()

    for entry in session.entries:
        if entry.tag.lower() in tag_set or any(t in (entry.message or "").lower() for t in tag_set):
            for offset in range(-window // 2, window // 2):
                hit_lines.add(entry.line_no + offset)

    # Also always include errors
    for e in session.errors + session.exceptions + session.timeouts:
        for offset in range(-5, 5):
            hit_lines.add(e.line_no + offset)

    relevant = [
        e for e in session.entries
        if e.line_no in hit_lines
    ]
    relevant.sort(key=lambda e: e.line_no)
    return "\n".join(e.raw for e in relevant[:300])


def detect_layers_from_tags(tags: list[str]) -> dict[str, list[str]]:
    """
    Heuristically bucket log tags into architecture layers.
    Works for MVVM / Clean Architecture / AAOS patterns.
    """
    layers: dict[str, list[str]] = {
        "UI/View": [],
        "ViewModel": [],
        "Repository": [],
        "Service/DataSource": [],
        "HAL/System": [],
        "Unknown": [],
    }
    for tag in tags:
        tl = tag.lower()
        if any(k in tl for k in ["activity", "fragment", "view", "screen", "ui", "compose"]):
            layers["UI/View"].append(tag)
        elif any(k in tl for k in ["viewmodel", "vm", "presenter"]):
            layers["ViewModel"].append(tag)
        elif any(k in tl for k in ["repo", "repository", "store", "cache", "dao", "db", "database"]):
            layers["Repository"].append(tag)
        elif any(k in tl for k in ["service", "manager", "provider", "source", "api", "client", "network", "http", "remote"]):
            layers["Service/DataSource"].append(tag)
        elif any(k in tl for k in ["hal", "vhal", "binder", "system", "aidl", "hidl", "kernel"]):
            layers["HAL/System"].append(tag)
        else:
            layers["Unknown"].append(tag)

    return {k: v for k, v in layers.items() if v}
