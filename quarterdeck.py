#!/usr/bin/env python3
"""Build a local, read-only review page from a Firstmate home."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import ipaddress
import html
import json
import os
import re
import shlex
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import webbrowser
from collections.abc import Callable, Iterator, Set
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


DEFAULT_TITLE = "Quarterdeck"
ALLOWED_CONFIG_KEYS = {"page_title", "output_dir", "homes"}
PR_URL_RE = re.compile(r"https://github\.com/[^\s|)]+/pull/\d+", re.IGNORECASE)
SECONDMATE_ENTRY_RE = re.compile(
    r"^\s*[-*]\s+(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)\s+-\s+(?P<summary>.*?)\s+\((?P<attributes>.*)\)\s*$"
)
ROUTE_HOME_RE = re.compile(r"(?:^|;\s*)home:\s*(.*?)(?=\s*;\s*(?:scope|projects|added):|$)", re.I)
ROUTE_HOST_RE = re.compile(r"(?:^|;\s*)host:\s*([^;]+)", re.I)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
FIELD_RE = re.compile(
    r"(?i)(captain_actionable|held|hold_bucket|hold_kind|hold_reason|hold_until|"
    r"hold_set|state|status|closed|merged|review_ready|pr_url|pr|report|report_path|"
    r"blocked|blocked_by|block_reason|blocked_reason|waiting_on|waiting_for|source_path|"
    r"recommendation)\s*[:=]\s*(.+?)(?=\s+(?:[a-z_]+)\s*[:=]|$)"
)


@dataclass
class Record:
    title: str
    section: str
    fields: dict[str, str]
    text: str
    report: Path | None = None
    recommendation: str | None = None
    pr_url: str | None = None
    report_source: str | None = None


@dataclass
class Report:
    title: str
    path: Path
    recommendation: str | None
    markdown: str
    fingerprint: str
    report_id: str = ""
    reviewed: bool = False
    discarded: bool = False
    relevance: str = "current"
    relevance_reason: str = ""
    related_to: list[str] = field(default_factory=list)
    modified: datetime | None = None

    @property
    def state(self) -> str:
        return "discarded" if self.discarded else "reviewed" if self.reviewed else "needs review"

    @property
    def needs_review(self) -> bool:
        return not self.reviewed and not self.discarded

    @property
    def waiting(self) -> bool:
        """True when the report belongs on the review list: unreviewed, not discarded, and still relevant."""
        return self.needs_review and self.relevance != "stale"


@dataclass
class HomeSpec:
    label: str
    home: Path | None
    data_dir: Path | None = None
    parent_label: str | None = None
    route_id: str | None = None
    remote_host: str | None = None
    unavailable_reason: str | None = None


@dataclass
class HomeSnapshot:
    spec: HomeSpec
    data_dir: Path | None = None
    backlog_path: Path | None = None
    backlog_markdown: str | None = None
    records: list[Record] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    registry_error: str | None = None
    children: list[HomeSnapshot] = field(default_factory=list)
    board: CaptainBoard | None = None
    backlog_changed: datetime | None = None
    review_state: ReviewState | None = None


def markdown_text(value: str) -> str:
    """Turn a compact Markdown fragment into readable plain text."""
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = MARKDOWN_LINK_RE.sub(lambda match: match.group(1), value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"(\*\*|__)(.+?)\1", r"\2", value)
    value = re.sub(r"(?<!\w)([*_~])([^*_~]+)\1(?!\w)", r"\2", value)
    return html.unescape(value).strip()


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def heading_section(heading: str) -> str:
    return re.sub(r"\s+", " ", markdown_text(heading)).strip().lower()


def fields_from(text: str, columns: dict[str, str] | None = None) -> dict[str, str]:
    fields = dict(columns or {})
    for match in FIELD_RE.finditer(text):
        fields[match.group(1).lower()] = markdown_text(match.group(2).strip(" `;,"))
    return fields


def value_for(fields: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = fields.get(key.lower(), "").strip()
        if value:
            return value
    return ""


def report_recommendation(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^\s{0,3}#{1,6}\s*recommendation\s*:?[ \t]*(.*)$", line, re.I)
        if match:
            collected = [match.group(1).strip()] if match.group(1).strip() else []
            for following in lines[index + 1 :]:
                if re.match(r"^\s{0,3}#{1,6}\s+", following):
                    break
                if not following.strip():
                    if collected:
                        break
                    continue
                collected.append(following.strip())
            summary = markdown_text(" ".join(collected))
            return summary[:500] or None

        match = re.match(r"^\s*(?:[-*]\s*)?recommendation\s*:\s*(.+)$", line, re.I)
        if match:
            return markdown_text(match.group(1))[:500] or None
    return None


def read_reports(data_dir: Path, warnings: list[str] | None = None) -> list[Report]:
    reports: list[Report] = []
    if not data_dir.is_dir():
        return reports
    for report_path in sorted(data_dir.glob("*/report.md")):
        try:
            raw_source = report_path.read_bytes()
            source = raw_source.decode("utf-8", errors="replace")
        except OSError as exc:
            if warnings is not None:
                warnings.append(f"Could not read report {report_path}: {exc}")
            continue
        title = next(
            (markdown_text(match.group(1)) for line in source.splitlines()
             if (match := re.match(r"^\s{0,3}#{1,2}\s+(.+)$", line))),
            report_path.parent.name.replace("-", " ").replace("_", " "),
        )
        try:
            modified = datetime.fromtimestamp(report_path.stat().st_mtime, timezone.utc)
        except OSError:
            modified = None
        reports.append(Report(
            title, report_path.resolve(), report_recommendation(source), source,
            hashlib.sha256(raw_source).hexdigest(), modified=modified,
        ))
    return reports


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _field_name(header: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")
    aliases = {
        "task_id": "id", "ticket": "id", "key": "id", "task": "title",
        "name": "title", "summary": "title", "what": "title",
        "current_state": "state", "task_state": "state", "pr_link": "pr_url",
        "pr": "pr_url", "pull_request": "pr_url", "pull_request_url": "pr_url",
        "review": "review_ready", "ready_for_review": "review_ready", "report_link": "report_path",
        "report_file": "report_path", "report": "report_path",
        "is_held": "held", "hold_type": "hold_kind", "closed_at": "closed",
        "blocker": "blocked_by", "waiting": "waiting_on", "waiting_for": "waiting_on",
        "blocker_reason": "blocked_reason", "source": "source_path", "recommendation": "recommendation",
    }
    return aliases.get(normalized, normalized)


def _record_from_line(line: str, section: str, headers: list[str] | None) -> Record | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("<!--"):
        return None

    cells: list[str] | None = None
    if stripped.startswith("|"):
        candidate = table_cells(stripped)
        if _is_separator(candidate):
            return None
        cells = candidate

    columns: dict[str, str] = {}
    if cells and headers:
        columns = {
            _field_name(header): markdown_text(value)
            for header, value in zip(headers, cells)
            if _field_name(header) and value.strip()
        }
        raw = " · ".join(markdown_text(cell) for cell in cells if cell.strip())
    else:
        raw = stripped
        raw = re.sub(r"^[-*+]\s+", "", raw)
        raw = re.sub(r"^\d+[.)]\s+", "", raw)
        raw = re.sub(r"^[-*]\s+\[[ xX]\]\s+", "", raw)
        raw = raw.strip()
        if raw.startswith("|"):
            return None
        if re.match(r"^#{1,6}\s+", raw):
            return None

    fields = dict(columns) if cells and headers else fields_from(raw)
    title = value_for(fields, "title", "name", "summary")
    if not title:
        title = markdown_text(cells[0] if cells else raw)
    title = re.sub(r"^\[[ xX]\]\s*", "", title).strip()
    if not title:
        return None

    return Record(
        title=title,
        section=section,
        fields=fields,
        text=value_for(fields, "description", "notes", "body") or (title if cells else markdown_text(raw)),
        recommendation=value_for(fields, "recommendation") or None,
        pr_url=value_for(fields, "pr_url", "pr") or None,
    )


def parse_backlog(source: str, data_dir: Path, reports: list[Report]) -> list[Record]:
    records: list[Record] = []
    section = ""
    headers: list[str] | None = None
    report_by_id = {report.path.parent.name.lower(): report for report in reports}

    for line in source.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            section = heading_section(heading.group(1))
            headers = None
            continue

        if line.strip().startswith("|"):
            cells = table_cells(line)
            if _is_separator(cells):
                continue
            lowered = [cell.lower() for cell in cells]
            if any(_field_name(cell) in {"title", "state", "status", "hold_kind", "id"} for cell in lowered):
                headers = cells
                continue

        record = _record_from_line(line, section, headers)
        if not record:
            continue
        record.section = section
        identifier = value_for(record.fields, "id").lower()
        report = report_by_id.get(identifier)
        if report is None:
            explicit = value_for(record.fields, "report_path", "report")
            if explicit:
                target = (data_dir.parent / explicit).resolve()
                if target.is_file() and target.name == "report.md":
                    report = next((item for item in reports if item.path == target), None)
        record.report = report.path if report else None
        if report:
            try:
                conventional_source = report.path.relative_to(data_dir.parent).as_posix()
            except ValueError:
                conventional_source = str(report.path)
            record.report_source = value_for(record.fields, "report_path", "report") or conventional_source
        if report and not record.recommendation:
            record.recommendation = report.recommendation

        match = PR_URL_RE.search(line)
        if match:
            record.pr_url = match.group(0)
        records.append(record)
    return records


FALSE_VALUES = {"", "-", "none", "(none)", "null", "false", "no", "0", "off"}
TRUE_VALUES = {"true", "yes", "1", "on"}


def normalized_state(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def state_for(record: Record) -> str:
    return normalized_state(value_for(record.fields, "state", "status") or record.section)


def field_is_set(value: str) -> bool:
    return value.strip().lower() not in FALSE_VALUES


def is_cancelled_state(state: str) -> bool:
    return normalized_state(state) in {"cancelled", "canceled"}


def is_closed_state(state: str) -> bool:
    return normalized_state(state) in {
        "done", "closed", "finished", "resolved", "complete", "completed", "archived", "merged", "released",
    }


def is_review_ready_state(review_flag: str, state: str) -> bool:
    return normalized_state(review_flag) in {"true", "yes", "1", "on", "ready", "review_ready", "reviewready"} or normalized_state(state) in {
        "review_ready", "reviewready", "ready_for_review", "open_for_review"
    }


def is_in_flight_state(state: str) -> bool:
    return normalized_state(state) in {"in_flight", "inflight", "working", "active", "running", "in_progress"}


def valid_pr_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.netloc.lower() == "github.com" and bool(PR_URL_RE.search(value))


def is_closed(record: Record) -> bool:
    if is_cancelled_state(state_for(record)):
        return False
    if any(field_is_set(value_for(record.fields, name)) for name in ("closed", "merged")):
        return True
    return is_closed_state(state_for(record))


def is_captain_held(record: Record) -> bool:
    return (
        not is_closed(record)
        and value_for(record.fields, "held").strip().lower() in TRUE_VALUES
        and normalized_state(value_for(record.fields, "hold_kind")) == "captain"
    )


def is_review_ready(record: Record) -> bool:
    if is_closed(record) or not record.pr_url:
        return False
    if not valid_pr_url(record.pr_url):
        return False
    return is_review_ready_state(value_for(record.fields, "review_ready"), state_for(record))


def is_in_flight(record: Record) -> bool:
    if is_closed(record):
        return False
    return is_in_flight_state(state_for(record))


def is_waiting_on_captain_or_external(record: Record) -> bool:
    if is_closed(record):
        return False
    blocked = value_for(record.fields, "blocked").strip().lower() in TRUE_VALUES
    blocked = (
        blocked or state_for(record) == "blocked"
        or field_is_set(value_for(record.fields, "blocked_by"))
        or field_is_set(value_for(record.fields, "waiting_on", "waiting_for"))
    )
    if not blocked:
        return False
    wait_target = normalized_state(value_for(record.fields, "waiting_on", "waiting_for", "blocked_by"))
    return wait_target in {
        "captain", "external", "external_party", "third_party", "vendor", "customer", "provider"
    }


def attention_groups(snapshot: HomeSnapshot, show_all: bool = False) -> dict[str, list[Record]]:
    groups: dict[str, list[Record]] = {name: [] for name in ("held", "reviews", "in_flight", "blocked", "other")}
    if snapshot.error:
        return groups

    assigned: set[int] = set()

    def take(name: str, predicate: Callable[[Record], bool]) -> None:
        for record in snapshot.records:
            if id(record) not in assigned and not is_cancelled_state(state_for(record)) and predicate(record):
                groups[name].append(record)
                assigned.add(id(record))

    take("held", is_captain_held)
    take("reviews", is_review_ready)
    take("in_flight", is_in_flight)
    take("blocked", is_waiting_on_captain_or_external)
    if show_all:
        groups["other"] = [
            record for record in snapshot.records
            if id(record) not in assigned and not is_cancelled_state(state_for(record))
        ]
    return groups


def record_decision_relevance(
    snapshot: HomeSnapshot, record: Record, decisions: dict[str, dict[str, object]] | None,
) -> object:
    bullet = BULLET_ITEM_RE.match("- [ ] " + record.title)
    identifier = value_for(record.fields, "id") or (bullet.group("id") if bullet else "")
    key = task_key(snapshot, identifier, all_snapshots([snapshot]))
    return (decisions or {}).get(key, {}).get("relevance")


def snapshot_counts(
    snapshot: HomeSnapshot, show_all: bool = False, decisions: dict[str, dict[str, object]] | None = None,
) -> tuple[int, ...]:
    groups = {
        name: [record for record in records if record_decision_relevance(snapshot, record, decisions) in (None, *RELEVANT_REASONS)]
        for name, records in attention_groups(snapshot, show_all).items()
    }
    counts = tuple(len(groups[name]) for name in ("held", "reviews", "in_flight", "blocked"))
    if show_all:
        return counts + (len(groups["other"]), len(snapshot.reports) if not snapshot.error else 0)
    return counts


def hidden_summary(
    snapshot: HomeSnapshot, groups: dict[str, list[Record]], decisions: dict[str, dict[str, object]] | None = None,
) -> str | None:
    visible = {id(record) for name in ("held", "reviews", "in_flight", "blocked") for record in groups[name]}
    hidden = [
        record for record in snapshot.records
        if id(record) not in visible and not is_cancelled_state(state_for(record))
        and record_decision_relevance(snapshot, record, decisions) in (None, *RELEVANT_REASONS)
    ]
    closed = sum(is_closed(record) for record in hidden)
    queued = sum(not is_closed(record) and state_for(record) == "queued" for record in hidden)
    other_blocked = sum(
        not is_closed(record)
        and (state_for(record) == "blocked" or field_is_set(value_for(record.fields, "blocked_by")))
        and not is_waiting_on_captain_or_external(record)
        for record in hidden
    )
    notes = []
    if closed:
        notes.append(f"{closed} finished or closed")
    if queued:
        notes.append(f"{queued} queued")
    if other_blocked:
        notes.append(f"{other_blocked} blocked on an internal or unspecified dependency")
    return "Not shown: " + "; ".join(notes) + "." if notes else None


def markdown_inline(value: str) -> str:
    protected: list[str] = []

    def keep(rendered: str) -> str:
        token = f"\x00QD{len(protected)}\x00"
        protected.append(rendered)
        return token

    value = re.sub(r"`([^`]+)`", lambda match: keep(f"<code>{html.escape(match.group(1))}</code>"), value)

    def link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2).strip()
        parsed = urlparse(target)
        safe = parsed.scheme in {"http", "https", "mailto"} and (parsed.scheme == "mailto" or bool(parsed.netloc))
        rendered_label = markdown_inline(label)
        if safe:
            return keep(f'<a href="{html.escape(target, quote=True)}">{rendered_label}</a>')
        return keep(rendered_label)

    value = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", link, value)
    value = html.escape(value, quote=False)
    value = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", value)
    value = re.sub(
        r"(?<!\*)\*([^*]+)\*(?!\*)|(?<![\w_])_([^_]+)_(?![\w_])",
        lambda m: f"<em>{m.group(1) or m.group(2)}</em>",
        value,
    )
    value = re.sub(r"~~(.+?)~~", r"<del>\1</del>", value)
    for index, rendered in enumerate(protected):
        value = value.replace(f"\x00QD{index}\x00", rendered)
    return value


def markdown_table_cells(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [cell.strip().replace("\\|", "|") for cell in cells]


def is_markdown_table(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines)
        and lines[index].lstrip().startswith("|")
        and lines[index + 1].lstrip().startswith("|")
        and _is_separator(markdown_table_cells(lines[index + 1]))
    )


def render_markdown(source: str) -> str:
    lines = source.splitlines()
    blocks: list[str] = []
    index = 0
    list_pattern = re.compile(r"^\s*(?:([-*+])|(\d+)[.)])\s+(.+)$")
    heading_pattern = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        fence = re.match(r"^\s*(```+|~~~+)", line)
        if fence:
            marker = fence.group(1)
            code: list[str] = []
            index += 1
            while index < len(lines) and not re.match(r"^\s*" + re.escape(marker[:3]) + r"\s*$", lines[index]):
                code.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
            continue
        heading = heading_pattern.match(line)
        if heading:
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{markdown_inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if is_markdown_table(lines, index):
            headers = markdown_table_cells(lines[index])
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(markdown_table_cells(lines[index]))
                index += 1
            head_html = "".join(f"<th>{markdown_inline(cell)}</th>" for cell in headers)
            row_html = "".join(
                "<tr>" + "".join(f"<td>{markdown_inline(cell)}</td>" for cell in row) + "</tr>"
                for row in rows
            )
            blocks.append(f"<div class=\"table-wrap\"><table><thead><tr>{head_html}</tr></thead><tbody>{row_html}</tbody></table></div>")
            continue
        list_match = list_pattern.match(line)
        if list_match:
            ordered = list_match.group(2) is not None
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while index < len(lines):
                current = list_pattern.match(lines[index])
                if not current or (current.group(2) is not None) != ordered:
                    break
                items.append(f"<li>{markdown_inline(current.group(3))}</li>")
                index += 1
            blocks.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        if re.match(r"^\s{0,3}(?:---+|\*\*\*+|___+)\s*$", line):
            blocks.append("<hr>")
            index += 1
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip():
            if heading_pattern.match(lines[index]) or list_pattern.match(lines[index]) or is_markdown_table(lines, index):
                break
            paragraph.append(lines[index].strip())
            index += 1
        blocks.append(f"<p>{markdown_inline(' '.join(paragraph))}</p>")
    return "\n".join(blocks)


def source_page_filename(kind: str, home: Path, source: Path) -> str:
    digest = hashlib.sha256(f"{home.resolve()}\0{source.resolve()}".encode("utf-8")).hexdigest()[:12]
    return f"{kind}-{digest}.html"


def display_path(path: Path, home: Path) -> str:
    try:
        return path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def source_page_html(title: str, source_path: str, markdown: str) -> str:
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="quarterdeck-{'gen' + 'erated'}" content="read-only source page"><title>{html.escape(title)} · Quarterdeck source</title>
<style>
body {{ margin: 0; background: #f3eddf; color: #20303d; font: 16px/1.65 system-ui, sans-serif; }}
header {{ background: #17324d; color: #fffdf8; padding: 1.6rem max(1.2rem, calc((100vw - 850px) / 2)); border-bottom: 4px solid #c98635; }}
header p {{ margin: .35rem 0 0; color: #d8c49b; overflow-wrap: anywhere; }}
main {{ max-width: 850px; margin: 2rem auto 4rem; padding: 0 1.2rem; }}
article {{ background: #fffdf8; border: 1px solid #d8d3c8; border-radius: 12px; padding: clamp(1.1rem, 4vw, 2.2rem); overflow-wrap: anywhere; }}
h1,h2,h3,h4,h5,h6 {{ color: #17324d; line-height: 1.25; }}
pre {{ overflow-x: auto; padding: 1rem; background: #172b3b; color: #f7f3e9; border-radius: 8px; }}
code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; background: #eee8da; padding: .1em .25em; border-radius: 4px; }}
pre code {{ background: transparent; padding: 0; }}
.table-wrap {{ overflow-x: auto; }} table {{ border-collapse: collapse; min-width: 100%; }} th,td {{ border: 1px solid #d8d3c8; padding: .5rem .7rem; text-align: left; vertical-align: top; }}
th {{ background: #e8e2d5; color: #17324d; }} a {{ color: #176b69; }} blockquote {{ border-left: 3px solid #c98635; margin-left: 0; padding-left: 1rem; color: #52616d; }}
</style></head><body><header><h1>{html.escape(title)}</h1><p>{html.escape(source_path)}</p></header><main><article>{render_markdown(markdown)}</article></main></body></html>'''


def write_generated_page(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing symbolic-link output page: {path}")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def source_pages(homes: list[HomeSnapshot], show_all: bool = False, bearings: Bearings | None = None) -> dict[str, str]:
    """Build every readable report, backlog and backlog-item page, keyed by file name."""
    pages: dict[str, str] = {}
    for snapshot in all_snapshots(homes):
        if snapshot.spec.home is None:
            continue
        if snapshot.backlog_path and snapshot.backlog_markdown is not None:
            name = source_page_filename("backlog", snapshot.spec.home, snapshot.backlog_path)
            snapshot_path = display_path(snapshot.backlog_path, snapshot.spec.home)
            pages[name] = source_page_html(f"{snapshot.spec.label} backlog", snapshot_path, snapshot.backlog_markdown)
        if show_all:
            linked_reports = {report.path for report in snapshot.reports}
        else:
            linked_reports = {
                record.report for record in snapshot.records
                if record.report and (is_captain_held(record) or is_review_ready(record))
            }
            linked_reports.update(report.path for report in snapshot.reports)
        if bearings is not None:
            linked_reports.update(bearings.linked_reports)
        for report in snapshot.reports:
            if report.path not in linked_reports:
                continue
            name = source_page_filename("report", snapshot.spec.home, report.path)
            pages[name] = source_page_html(report.title, display_path(report.path, snapshot.spec.home), report.markdown)
    if bearings is not None:
        pages.update(bearings.item_pages)
    return pages


def write_source_pages(
    homes: list[HomeSnapshot], output_dir: Path, show_all: bool = False, bearings: Bearings | None = None
) -> None:
    for name, content in source_pages(homes, show_all, bearings).items():
        write_generated_page(output_dir / name, content)


def review_element_id(kind: str, home: Path, identity: str) -> str:
    digest = hashlib.sha256(f"{home.resolve()}\0{identity}".encode("utf-8")).hexdigest()[:12]
    return f"review-{kind}-{digest}"


def request_button(payload: dict[str, str]) -> str:
    encoded = html.escape(json.dumps(payload, ensure_ascii=False), quote=True)
    return (
        f'<div class="request-row"><button class="request-lavish" type="button" '
        f'data-lavish-request="{encoded}">Request a Lavish page</button>'
        '<span class="request-status" aria-live="polite"></span></div>'
    )


def request_payload(record: Record, snapshot: HomeSnapshot, category: str) -> dict[str, str]:
    home = snapshot.spec.home or Path(".")
    report_request = category in {"held", "reviews"} and record.report is not None
    if report_request:
        report = next((item for item in snapshot.reports if item.path == record.report), None)
        source = record.report_source or display_path(record.report, home)
        item_id = report.report_id if report and report.report_id else report_reference(snapshot.spec.label, record.report.parent.name)
        title = report.title if report else record.title
        kind = "report"
    else:
        source = value_for(record.fields, "source_path") or (
            display_path(snapshot.backlog_path, home) if snapshot.backlog_path else "data/backlog.md"
        )
        item_id = value_for(record.fields, "id") or hashlib.sha256(
            f"{record.section}\0{record.title}\0{record.text}".encode("utf-8")
        ).hexdigest()[:12]
        title = record.title
        kind = "backlog item"
    payload = {"item_id": item_id, "title": title, "kind": kind, "home_label": snapshot.spec.label, "source_path": source}
    if report_request:
        payload["report_id"] = item_id
    return payload


def card(
    record: Record,
    snapshot: HomeSnapshot,
    badge: str,
    category: str,
    lavish: bool,
    show_report: bool,
) -> str:
    home = snapshot.spec.home or Path(".")
    safe_title = html.escape(record.title)
    identity = value_for(record.fields, "id") or f"{record.section}:{record.title}:{record.text}"
    element_id = f' id="{review_element_id("item", home, identity)}"' if lavish else ""
    chunks = [f'<article class="card"{element_id}><p class="eyebrow">{html.escape(badge)}</p>', f"<h3>{safe_title}</h3>"]
    hold_reason = value_for(record.fields, "hold_reason") if category == "held" else ""
    wait_reason = value_for(record.fields, "blocked_reason", "block_reason", "waiting_on", "blocked_by") if category == "blocked" else ""
    if hold_reason:
        chunks.append(f'<p class="reason"><strong>Hold reason:</strong> {html.escape(hold_reason)}</p>')
    elif wait_reason:
        chunks.append(f'<p class="reason"><strong>Waiting on:</strong> {html.escape(wait_reason)}</p>')
    if record.recommendation:
        chunks.append(f'<p><strong>Recommendation:</strong> {html.escape(record.recommendation)}</p>')
    elif record.text and record.text != record.title:
        summary = record.text
        if len(summary) > 360:
            summary = summary[:357].rstrip() + "…"
        chunks.append(f"<p>{html.escape(summary)}</p>")

    links: list[str] = []
    if record.pr_url:
        parsed = urlparse(record.pr_url)
        if parsed.scheme == "https" and parsed.netloc == "github.com" and PR_URL_RE.search(record.pr_url):
            links.append(f'<a href="{html.escape(record.pr_url, quote=True)}">Open pull request</a>')
    if show_report and record.report:
        report_name = source_page_filename("report", home, record.report)
        report_relative = display_path(record.report, home)
        report_id = report_reference(snapshot.spec.label, record.report.parent.name)
        links.append(
            f'<a href="{html.escape(report_name, quote=True)}">Read report '
            f'<code>{html.escape(report_id)}</code> <span class="path">{html.escape(report_relative)}</span></a>'
        )
    if snapshot.backlog_path:
        backlog_name = source_page_filename("backlog", home, snapshot.backlog_path)
        links.append(f'<a href="{html.escape(backlog_name, quote=True)}">Read backlog item</a>')
    if links:
        chunks.append('<p class="links">' + " · ".join(links) + "</p>")
    if lavish and category in {"held", "reviews", "in_flight"}:
        chunks.append(request_button(request_payload(record, snapshot, category)))
    chunks.append("</article>")
    return "".join(chunks)


def report_card(report: Report, snapshot: HomeSnapshot, lavish: bool = False) -> str:
    home = snapshot.spec.home or Path(".")
    element_id = f' id="{review_element_id("report", home, str(report.path))}"' if lavish else ""
    recommendation = f'<p><strong>Recommendation:</strong> {html.escape(report.recommendation)}</p>' if report.recommendation else ""
    name = source_page_filename("report", home, report.path)
    relative = display_path(report.path, home)
    review_label = {"reviewed": "Reviewed", "discarded": "Discarded"}.get(report.state, "Needs review")
    if report.needs_review and report.relevance == "stale":
        review_label += " · stale"
    parts = [
        f'<article class="card"{element_id}><p class="eyebrow">Scout report · {review_label} · '
        f'<code>{html.escape(report.report_id)}</code></p>',
        f'<h3>{html.escape(report.title)}</h3>{recommendation}',
        f'<p class="links"><a href="{html.escape(name, quote=True)}">Read report '
        f'<code>{html.escape(report.report_id)}</code> <span class="path">{html.escape(relative)}</span></a></p>',
    ]
    if lavish and report.waiting:
        payload = {
            "item_id": report.report_id,
            "report_id": report.report_id,
            "title": report.title,
            "kind": "report",
            "home_label": snapshot.spec.label,
            "source_path": relative,
        }
        parts.append(request_button(payload))
    parts.append("</article>")
    return "".join(parts)


def section_html(title: str, description: str, cards: list[str], empty: str) -> str:
    content = "".join(cards) if cards else f'<p class="empty">{html.escape(empty)}</p>'
    return f'<div class="dashboard-section"><div class="section-head"><div><h3>{html.escape(title)}</h3><p>{html.escape(description)}</p></div><span class="count">{len(cards)}</span></div><div class="cards">{content}</div></div>'


def home_panel(
    snapshot: HomeSnapshot, lavish: bool = False, show_all: bool = False,
    decisions: dict[str, dict[str, object]] | None = None,
) -> str:
    spec = snapshot.spec
    if spec.parent_label:
        role = f"Secondmate · registered by {spec.parent_label}"
    else:
        role = "Firstmate home"
    if spec.route_id:
        role += f" · {spec.route_id}"
    heading = [
        f'<section class="home"><div class="home-head"><div><p class="home-role">{html.escape(role)}</p>',
        f'<h2>{html.escape(spec.label)}</h2>',
    ]
    if snapshot.data_dir and not spec.remote_host:
        heading.append(f'<p class="home-source">Source: {html.escape(str(snapshot.data_dir))}</p>')
    if spec.remote_host:
        heading.append(f'<p class="home-source">Registered on remote host {html.escape(spec.remote_host)}.</p>')
    heading.append('</div></div>')

    body: list[str] = []
    if snapshot.error:
        body.append(
            '<div class="failure" role="status"><strong>Home could not be read</strong>'
            f'<p>{html.escape(snapshot.error)}</p></div>'
        )
    else:
        groups = attention_groups(snapshot, show_all)
        background = []
        for category, records in groups.items():
            kept = []
            for record in records:
                why = record_decision_relevance(snapshot, record, decisions)
                if why in {"quiet", "stale"}:
                    background.append(card(record, snapshot, "Background decision", category, lavish, True))
                elif why != "deferred":
                    kept.append(record)
            groups[category] = kept
        counts = tuple(len(groups[name]) for name in ("held", "reviews", "in_flight", "blocked", "other")) + (len(snapshot.reports),)
        if background:
            body.append('<details class="dashboard-section"><summary>'
                        f'{len(background)} background decisions</summary>' + "".join(background) + '</details>')
        home_count_labels = [
            f"{counts[0]} held for captain",
            f"{counts[1]} review-ready PRs",
            f"{counts[2]} in-flight",
            f"{counts[3]} blocked for captain/external",
        ]
        if show_all:
            home_count_labels.extend([f"{counts[4]} other backlog items", f"{counts[5]} scout reports"])
        home_counts = "".join(f"<span>{html.escape(label)}</span>" for label in home_count_labels)
        body.append(f'<div class="home-counts">{home_counts}</div>')
        held_cards = [card(record, snapshot, "Held for captain", "held", lavish, True) for record in groups["held"]]
        review_cards = [card(record, snapshot, "Review-ready pull request", "reviews", lavish, True) for record in groups["reviews"]]
        in_flight_cards = [card(record, snapshot, "In-flight work", "in_flight", lavish, False) for record in groups["in_flight"]]
        blocked_cards = [card(record, snapshot, "Blocked for captain or external party", "blocked", lavish, False) for record in groups["blocked"]]
        if not show_all:
            needs_review = [report for report in snapshot.reports if report.waiting]
            needs_review_cards = [report_card(report, snapshot, lavish) for report in needs_review]
            body.append(section_html(
                "Reports needing review",
                "Reports without a reviewed mark for their current content.",
                needs_review_cards,
                "Every discovered report is marked reviewed.",
            ))
        body.extend([
            section_html("Held for the captain", "Unresolved captain holds, with the recorded reason and linked report.", held_cards, "Nothing is waiting for a captain answer."),
            section_html("Review-ready pull requests", "Open pull requests explicitly marked ready for review.", review_cards, "No review-ready pull requests are recorded."),
            section_html("In-flight work", "Tasks recorded as actively in progress.", in_flight_cards, "No work is currently in flight."),
            section_html("Blocked for the captain or an external party", "Blocked items with a structured captain or external-party blocker.", blocked_cards, "Nothing is blocked on the captain or an external party."),
        ])
        if show_all:
            other_cards = [card(record, snapshot, "Other backlog item", "other", lavish, True) for record in groups["other"]]
            report_cards = [report_card(report, snapshot, lavish) for report in snapshot.reports]
            body.extend([
                section_html("Other backlog items", "Queued, finished, closed, and other records for an exhaustive view.", other_cards, "No other backlog items were found."),
                section_html("All scout reports", "Every report found in this home's data directory, with its review state.", report_cards, "No scout reports were found."),
            ])
        else:
            summary = hidden_summary(snapshot, groups, decisions)
            if summary:
                body.append(f'<p class="quiet-summary">{html.escape(summary)}</p>')
    if snapshot.registry_error:
        body.append(
            '<div class="warning"><strong>Secondmate registry could not be read</strong>'
            f'<p>{html.escape(snapshot.registry_error)}</p></div>'
        )
    if snapshot.warnings:
        warning_text = (
            "; ".join(snapshot.warnings)
            if show_all else
            f"{len(snapshot.warnings)} report file(s) could not be read."
        )
        body.append(
            '<div class="warning"><strong>Some report files could not be read</strong>'
            f'<p>{html.escape(warning_text)}</p></div>'
        )
    if snapshot.children:
        body.append('<div class="secondmates"><h3>Registered secondmates</h3>')
        body.extend(home_panel(child, lavish, show_all, decisions) for child in snapshot.children)
        body.append('</div>')
    return ''.join(heading + body + ['</section>'])


def all_snapshots(snapshots: list[HomeSnapshot]) -> list[HomeSnapshot]:
    result: list[HomeSnapshot] = []
    for snapshot in snapshots:
        result.append(snapshot)
        result.extend(all_snapshots(snapshot.children))
    return result


def report_reference(home_label: str, task_id: str) -> str:
    """Build a readable report ID from its configured home label and task ID."""
    label_slug = re.sub(r"[^a-z0-9]+", "-", home_label.casefold()).strip("-")
    label_slug = label_slug or "home"
    return f"{label_slug}/{task_id}"


def review_state_path() -> Path:
    """The legacy review-marks file; its directory also holds state.db, which is where state now lives."""
    return default_output_dir() / "review-state" / "marks.json"


def validate_review_state_location(snapshots: list[HomeSnapshot]) -> Path:
    state_dir = review_state_path().parent.resolve()
    roots = [Path(__file__).resolve().parent]
    for snapshot in all_snapshots(snapshots):
        if snapshot.spec.remote_host is not None or snapshot.spec.home is None:
            continue
        roots.append(snapshot.spec.home.resolve())
        data_dir = snapshot.spec.data_dir or snapshot.data_dir or (snapshot.spec.home / "data")
        roots.append(data_dir.resolve())
    for root in roots:
        try:
            state_dir.relative_to(root)
        except ValueError:
            continue
        raise ValueError(
            "review state must be outside the Quarterdeck checkout, selected Firstmate homes, "
            "and their data directories"
        )
    return state_dir / "marks.json"


# --- Review state (SQLite) ---------------------------------------------------
# Review marks, discards, relevance and relations live in <state>/review-state/state.db.
# The legacy marks.json beside it is imported once and then left untouched and readable.

STATE_DB_NAME = "state.db"
REPORT_STATES = ("reviewed", "discarded")
RELEVANCE_VALUES = ("current", "stale")
RELATION_KINDS = ("report", "call")
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS report_state (
    report_id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK (state IN ('reviewed', 'discarded')),
    fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS relevance (
    kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL CHECK (value IN ('current', 'stale')),
    reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS relations (
    kind TEXT NOT NULL, key TEXT NOT NULL, task TEXT NOT NULL, PRIMARY KEY (kind, key, task));
CREATE TABLE IF NOT EXISTS reactions (
    id TEXT PRIMARY KEY, item_id TEXT NOT NULL, kind TEXT NOT NULL, task TEXT NOT NULL,
    reaction TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL, source TEXT NOT NULL);
"""
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")


@dataclass
class ReviewState:
    reports: dict[str, tuple[str, str]] = field(default_factory=dict)  # report id -> (state, fingerprint)
    relevance: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)  # (kind, key) -> (value, reason)
    relations: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (kind, key) -> task ids
    reactions: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)  # (kind, item id) -> latest delivered
    delivered: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (kind, item id) -> delivered ids

    def relevance_of(self, kind: str, key: str) -> tuple[str, str]:
        return self.relevance.get((kind, key), ("current", ""))


def state_db_path(path: Path) -> Path:
    return path.with_name(STATE_DB_NAME)


def read_legacy_marks(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise ValueError(f"refusing symbolic-link review state: {path}")
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read review state {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("reviewed"), dict):
        raise ValueError(f"invalid review state format: {path}")
    marks = value["reviewed"]
    if any(not isinstance(key, str) or not isinstance(fingerprint, str)
           or not FINGERPRINT_RE.fullmatch(fingerprint)
           for key, fingerprint in marks.items()):
        raise ValueError(f"invalid review state entry: {path}")
    return dict(marks)


def state_timestamp() -> str:
    return board_time_text(datetime.now(timezone.utc))


@contextmanager
def state_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Open the state database beside the legacy marks file and import marks.json once."""
    db_path = state_db_path(path)
    for candidate in (path, db_path):
        if candidate.is_symlink():
            raise ValueError(f"refusing symbolic-link review state: {candidate}")
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(db_path, timeout=15, isolation_level=None)
    try:
        os.chmod(db_path, 0o600)
        connection.executescript(SCHEMA)
        migrated = connection.execute("SELECT value FROM meta WHERE key = 'marks_migrated'").fetchone()
        if migrated is None:
            marks = read_legacy_marks(path)
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute("SELECT 1 FROM meta WHERE key = 'marks_migrated'").fetchone() is None:
                    now = state_timestamp()
                    connection.executemany(
                        "INSERT OR IGNORE INTO report_state VALUES (?, 'reviewed', ?, ?)",
                        [(report_id, fingerprint, now) for report_id, fingerprint in marks.items()],
                    )
                    connection.execute("INSERT INTO meta VALUES ('marks_migrated', ?)", (now,))
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        yield connection
    except sqlite3.Error as exc:
        raise ValueError(f"could not use review state {db_path}: {exc}") from exc
    finally:
        connection.close()


def load_review_state(path: Path) -> ReviewState:
    """Read the whole state; an absent database and marks file mean an empty state without creating either."""
    if not state_db_path(path).exists() and not path.exists():
        return ReviewState()
    state = ReviewState()
    with state_connection(path) as connection:
        for report_id, kind, fingerprint in connection.execute("SELECT report_id, state, fingerprint FROM report_state"):
            state.reports[report_id] = (kind, fingerprint)
        for kind, key, value, reason in connection.execute("SELECT kind, key, value, reason FROM relevance"):
            state.relevance[(kind, key)] = (value, reason)
        for kind, key, task in connection.execute("SELECT kind, key, task FROM relations ORDER BY task"):
            state.relations.setdefault((kind, key), []).append(task)
        for row in connection.execute(
            "SELECT id, item_id, kind, task, reaction, text, at, source FROM reactions ORDER BY at, rowid"
        ):
            keys = ("id", "item_id", "kind", "task", "reaction", "text", "at", "source")
            state.reactions[(row[2], row[1])] = dict(zip(keys, row))
            state.delivered.setdefault((row[2], row[1]), []).append(row[0])
    return state


def load_review_marks(path: Path) -> dict[str, str]:
    """The reviewed marks as {report id: content fingerprint}."""
    return {
        report_id: fingerprint
        for report_id, (state, fingerprint) in load_review_state(path).reports.items() if state == "reviewed"
    }


def write_state(path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> None:
    with state_connection(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement, parameters in statements:
                connection.execute(statement, parameters)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise


def update_report_state(path: Path, report_id: str, state: str | None, fingerprint: str = "") -> None:
    """Set a report's state to reviewed or discarded for a fingerprint, or clear it with None."""
    if state is None:
        write_state(path, [("DELETE FROM report_state WHERE report_id = ?", (report_id,))])
        return
    if state not in REPORT_STATES:
        raise ValueError(f"report state must be one of {', '.join(REPORT_STATES)}")
    write_state(path, [(
        "INSERT INTO report_state VALUES (?, ?, ?, ?) ON CONFLICT(report_id) DO UPDATE SET "
        "state = excluded.state, fingerprint = excluded.fingerprint, updated_at = excluded.updated_at",
        (report_id, state, fingerprint, state_timestamp()),
    )])


def update_review_mark(path: Path, report_id: str, fingerprint: str | None) -> None:
    update_report_state(path, report_id, None if fingerprint is None else "reviewed", fingerprint or "")


def update_relevance(path: Path, kind: str, key: str, value: str, reason: str = "") -> None:
    if value not in RELEVANCE_VALUES:
        raise ValueError(f"relevance must be one of {', '.join(RELEVANCE_VALUES)}")
    write_state(path, [(
        "INSERT INTO relevance VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, key) DO UPDATE SET "
        "value = excluded.value, reason = excluded.reason, updated_at = excluded.updated_at",
        (kind, key, value, reason.strip(), state_timestamp()),
    )])


def update_relation(path: Path, kind: str, key: str, task: str, add: bool) -> None:
    if add:
        write_state(path, [("INSERT OR IGNORE INTO relations VALUES (?, ?, ?)", (kind, key, task))])
    else:
        write_state(path, [("DELETE FROM relations WHERE kind = ? AND key = ? AND task = ?", (kind, key, task))])


REPORT_ACTIONS = {
    "mark": ("reviewed", "Marked {id} reviewed for its current content."),
    "unmark": (None, "Removed the reviewed mark for {id}."),
    "discard": ("discarded", "Discarded {id}: off the list, not marked reviewed."),
    "undiscard": (None, "Returned {id} to the list."),
}


def set_report_action(
    args: argparse.Namespace, report_id: str, action: str, expected_fingerprint: str | None = None,
) -> Report:
    """Mark, unmark, discard or undiscard one report by ID; shared by the CLI and the served page's buttons.

    Reviewed and discarded are exclusive: each replaces the other. Unmark and undiscard clear only their own state.
    """
    if action not in REPORT_ACTIONS:
        raise ValueError(f"unknown report action: {action}")
    snapshots, state_path = load_report_snapshots(args)
    apply_review_marks(snapshots, {})
    return apply_report_action(snapshots, state_path, report_id, action, expected_fingerprint)


def apply_report_action(
    snapshots: list[HomeSnapshot], state_path: Path, report_id: str, action: str,
    expected_fingerprint: str | None = None,
) -> Report:
    _snapshot, report = unique_report(snapshots, report_id)
    if expected_fingerprint is not None and report.fingerprint != expected_fingerprint:
        raise ValueError("report changed since the page was loaded; reload before marking")
    new_state = REPORT_ACTIONS[action][0]
    if new_state is not None:
        update_report_state(state_path, report.report_id, new_state, report.fingerprint)
    else:
        wanted = "reviewed" if action == "unmark" else "discarded"
        write_state(state_path, [(
            "DELETE FROM report_state WHERE report_id = ? AND state = ?",
            (report.report_id, wanted),
        )])
    apply_review_marks(snapshots, load_review_state(state_path))
    return report


def set_report_mark(
    args: argparse.Namespace, report_id: str, reviewed: bool, expected_fingerprint: str | None = None,
) -> Report:
    return set_report_action(args, report_id, "mark" if reviewed else "unmark", expected_fingerprint)


def assign_report_ids(snapshots: list[HomeSnapshot]) -> set[str]:
    counts: dict[str, int] = {}
    for snapshot in all_snapshots(snapshots):
        for report in snapshot.reports:
            report.report_id = report_reference(snapshot.spec.label, report.path.parent.name)
            counts[report.report_id] = counts.get(report.report_id, 0) + 1
    return {report_id for report_id, count in counts.items() if count > 1}


def apply_review_marks(snapshots: list[HomeSnapshot], marks: dict[str, str] | ReviewState) -> None:
    """Apply review state to every report; a plain dict is read as reviewed marks only."""
    state = marks if isinstance(marks, ReviewState) else ReviewState(
        reports={report_id: ("reviewed", fingerprint) for report_id, fingerprint in marks.items()}
    )
    ambiguous_ids = assign_report_ids(snapshots)
    for snapshot in all_snapshots(snapshots):
        snapshot.review_state = state
        for report in snapshot.reports:
            report.reviewed = report.discarded = False
            report.relevance, report.relevance_reason = "current", ""
            report.related_to = []
            if report.report_id in ambiguous_ids:
                continue
            kind, fingerprint = state.reports.get(report.report_id, ("", ""))
            if fingerprint == report.fingerprint:
                report.reviewed, report.discarded = kind == "reviewed", kind == "discarded"
            report.relevance, report.relevance_reason = state.relevance_of("report", report.report_id)
            report.related_to = list(state.relations.get(("report", report.report_id), []))


def report_discovery_errors(snapshots: list[HomeSnapshot]) -> list[tuple[HomeSnapshot, str]]:
    errors: list[tuple[HomeSnapshot, str]] = []
    for snapshot in all_snapshots(snapshots):
        if snapshot.error:
            errors.append((snapshot, f"home could not be read: {snapshot.error}"))
        if snapshot.registry_error:
            errors.append((snapshot, f"secondmate registry could not be read: {snapshot.registry_error}"))
        errors.extend((snapshot, warning) for warning in snapshot.warnings)
    return errors


def local_report_resolution_errors(snapshots: list[HomeSnapshot]) -> list[tuple[HomeSnapshot, str]]:
    return [
        (snapshot, error)
        for snapshot, error in report_discovery_errors(snapshots)
        if snapshot.spec.remote_host is None
    ]


def report_matches(snapshots: list[HomeSnapshot], requested_id: str) -> list[tuple[HomeSnapshot, Report]]:
    return [
        (snapshot, report)
        for snapshot in all_snapshots(snapshots)
        for report in snapshot.reports
        if report.report_id == requested_id
    ]


def unique_report(snapshots: list[HomeSnapshot], requested_id: str) -> tuple[HomeSnapshot, Report]:
    incomplete_local_homes = local_report_resolution_errors(snapshots)
    if incomplete_local_homes:
        details = "; ".join(f"{snapshot.spec.label}: {error}" for snapshot, error in incomplete_local_homes)
        raise ValueError(f"cannot resolve report ID while selected local homes are incomplete: {details}")
    matches = report_matches(snapshots, requested_id)
    if not matches:
        raise ValueError(f"no report matches ID: {requested_id}")
    if len(matches) > 1:
        locations = ", ".join(
            f'{snapshot.spec.label} ({display_path(report.path, snapshot.spec.home or report.path.parent)})'
            for snapshot, report in matches
        )
        raise ValueError(f"report ID is ambiguous: {requested_id}; matching reports: {locations}")
    return matches[0]


def legacy_home_load_error(snapshots: list[HomeSnapshot], legacy_single_home: bool) -> str | None:
    if not legacy_single_home or not snapshots or not snapshots[0].error:
        return None
    if any(snapshot.board is not None for snapshot in all_snapshots(snapshots)):
        return None
    return snapshots[0].error


def load_report_snapshots(args: argparse.Namespace) -> tuple[list[HomeSnapshot], Path]:
    if args.config is None:
        configured_path = default_config_path()
        args.config = configured_path if configured_path.is_file() else None
    config = load_config(args.config)
    specs, legacy_single_home = resolve_home_specs(args, config)
    snapshots = [load_home_snapshot(spec, discover_secondmates=True) for spec in specs]
    load_error = legacy_home_load_error(snapshots, legacy_single_home)
    if load_error:
        raise ValueError(load_error)
    state_path = validate_review_state_location(snapshots)
    return snapshots, state_path


def load_report_context(args: argparse.Namespace) -> tuple[list[HomeSnapshot], Path, ReviewState]:
    snapshots, state_path = load_report_snapshots(args)
    state = load_review_state(state_path)
    apply_review_marks(snapshots, state)
    return snapshots, state_path, state


def build_html(
    homes: list[HomeSnapshot], title: str, lavish: bool = False, show_all: bool = False,
    bearings: Bearings | None = None, refresh_seconds: int | None = None, allow_marks: bool = False,
) -> str:
    snapshots = all_snapshots(homes)
    ambiguous_ids = assign_report_ids(homes)
    report_resolution_incomplete = bool(local_report_resolution_errors(homes))
    decisions = decision_rows(homes, bearings.attention if bearings else None, bearings.call if bearings else [])
    counts = [sum(snapshot_counts(snapshot, show_all, decisions)[index] for snapshot in snapshots) for index in range(6 if show_all else 4)]
    metric_labels = ["Held for captain", "Review-ready PRs", "In-flight work", "Blocked for captain/external"]
    if show_all:
        metric_labels.extend(["Other backlog items", "Scout reports"])
    counts.append(sum(report.waiting for snapshot in snapshots for report in snapshot.reports))
    metric_labels.append("Reports needing review")
    generated = bearings.generated if bearings else datetime.now(timezone.utc)
    timestamp = generated.strftime("%Y-%m-%d %H:%M:%S UTC")
    board = board_view(homes, generated, decisions=decisions)
    review_state = next((s.review_state for s in snapshots if s.review_state is not None), None) or ReviewState()
    reactions = ReactionContext(enabled=allow_marks, latest=review_state.reactions, now=generated)
    related_block = related_html(
        related_items(homes, board, decisions=decisions), generated, can_mark=allow_marks, ambiguous_ids=ambiguous_ids,
        report_resolution_incomplete=report_resolution_incomplete, reactions=reactions,
    )
    board_block = board_html(board, generated, show_all, related_block, reactions, bearings.attention if bearings else None) if board else related_block
    board_status = board_status_html(board, generated) if board else ""
    bearings_block = bearings_html(
        bearings, collapse_reviews=board is not None, can_mark=allow_marks, ambiguous_ids=ambiguous_ids,
        report_resolution_incomplete=report_resolution_incomplete, reactions=reactions, decisions=decisions,
    ) if bearings else ""
    attention_block = attention_html(bearings.attention, allow_marks) if bearings else ""
    reviewed_block = (
        reviewed_reports_html(homes, ambiguous_ids, report_resolution_incomplete, reactions) if allow_marks else ""
    ) + set_aside_reports_html(homes, ambiguous_ids, report_resolution_incomplete, allow_marks, reactions)
    reload_script = """
  <script>
    function quarterdeckReload() {
      const editing = Array.from(document.querySelectorAll(".reaction-text")).some(
        field => field.value || field === document.activeElement
      );
      if (editing || document.querySelector('[data-reaction-choice][aria-pressed=true], .reaction-send:disabled, #view-editor[data-dirty=true], #view-save:disabled[data-saving=true]')) {
        setTimeout(quarterdeckReload, 1000);
        return;
      }
      location.reload();
    }
  </script>"""
    mark_script = f"""
  <script>
    document.addEventListener("click", async (event) => {{
      const button = event.target.closest("[data-mark-report]");
      if (!button || button.disabled) return;
      const status = button.parentElement.querySelector(".request-status");
      button.disabled = true;
      if (status) status.textContent = "Saving…";
      try {{
        const response = await fetch("{MARK_ENDPOINT}", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{
            report_id: button.dataset.markReport,
            fingerprint: button.dataset.markFingerprint,
            action: button.dataset.markAction
          }}),
        }});
        if (!response.ok) throw new Error((await response.text()).trim() || response.statusText);
        if (status) status.textContent = "Saved";
        quarterdeckReload();
      }} catch (error) {{
        button.disabled = false;
        if (status) status.textContent = "Could not save: " + error.message;
      }}
    }});
  </script>""" if allow_marks else ""
    reaction_script = f"""
  <script>
    function reactionId(box) {{
      if (!box.dataset.reactionId) {{
        box.dataset.reactionId = Array.from(crypto.getRandomValues(new Uint8Array(16)),
          byte => byte.toString(16).padStart(2, "0")).join("");
      }}
      return box.dataset.reactionId;
    }}
    document.addEventListener("input", event => {{
      if (event.target.matches(".reaction-text")) delete event.target.closest(".reaction").dataset.reactionId;
    }});
    document.addEventListener("click", async (event) => {{
      const choice = event.target.closest("[data-reaction-choice]");
      if (choice) {{
        if (choice.disabled) return;
        delete choice.closest(".reaction").dataset.reactionId;
        const pressed = choice.getAttribute("aria-pressed") !== "true";
        for (const other of choice.parentElement.querySelectorAll("[data-reaction-choice]")) other.setAttribute("aria-pressed", "false");
        choice.setAttribute("aria-pressed", String(pressed));
        return;
      }}
      const send = event.target.closest(".reaction-send");
      if (!send || send.disabled) return;
      const box = send.closest(".reaction");
      const status = box.querySelector(".reaction-status");
      const field = box.querySelector(".reaction-text");
      const picked = box.querySelector("[data-reaction-choice][aria-pressed=true]");
      const reaction = picked ? picked.dataset.reactionChoice : "";
      const text = field.value.trim();
      if (!reaction && !text) {{ status.textContent = "Pick a reaction or write a reply first."; return; }}
      const controls = [send, field, ...box.querySelectorAll("[data-reaction-choice]")];
      for (const control of controls) control.disabled = true;
      status.textContent = "Sending…";
      try {{
        const response = await fetch("{REACTION_ENDPOINT}", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{id: reactionId(box), kind: box.dataset.reactionKind, item_id: box.dataset.reactionItem, reaction: reaction, text: text}}),
        }});
        if (!response.ok) throw new Error((await response.text()).trim() || response.statusText);
        const sent = await response.json();
        const label = {json.dumps(REACTION_LABELS)}[sent.reaction] || "";
        const summary = label && sent.text ? label + ": " + sent.text : label || sent.text;
        for (const node of document.querySelectorAll("[data-reaction-latest]")) {{
          if (node.dataset.reactionLatest !== box.dataset.reactionKind + ":" + box.dataset.reactionItem) continue;
          node.textContent = (node.tagName === "P" ? "Latest reaction: " : "") + summary + " (from this page · just now)";
          node.hidden = false;
        }}
        field.value = "";
        if (picked) picked.setAttribute("aria-pressed", "false");
        delete box.dataset.reactionId;
        status.textContent = "Sent to Firstmate";
      }} catch (error) {{
        status.textContent = "Not sent: " + error.message;
      }}
      for (const control of controls) control.disabled = false;
    }});
  </script>""" if allow_marks else ""
    refresh_script = (
        f"""
  <script>
    (function() {{
      const storageKey = "quarterdeck-open-details";
      try {{
        const openKeys = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
        if (Array.isArray(openKeys)) {{
          const open = new Set(openKeys);
          for (const detail of document.querySelectorAll("details")) {{
            const key = detail.dataset.refreshKey || detail.id;
            if (key && open.has(key)) detail.open = true;
          }}
        }}
        sessionStorage.removeItem(storageKey);
      }} catch (_) {{}}
      setTimeout(function() {{
        try {{
          const openKeys = Array.from(
            document.querySelectorAll("details[open]"),
            detail => detail.dataset.refreshKey || detail.id
          ).filter(Boolean);
          sessionStorage.setItem(storageKey, JSON.stringify(openKeys));
        }} catch (_) {{}}
        quarterdeckReload();
      }}, {int(refresh_seconds) * 1000});
    }})();
  </script>""" if refresh_seconds else ""
    )
    footer_label = (
        "Read-only snapshot except report review marks, reactions sent to Firstmate and saved views" if allow_marks
        else "Read-only snapshot"
    )
    refresh_note = f" · reloads every {int(refresh_seconds)} seconds" if refresh_seconds else ""
    metrics = "".join(
        f'<div class="metric"><b>{count}</b><span>{html.escape(label)}</span></div>'
        for count, label in zip(counts, metric_labels)
    )
    introductory_text = (
        "Exhaustive snapshot of configured homes, reports needing review, and their registered secondmates."
        if show_all else
        "A read-only view of reports needing review, unresolved captain holds, review-ready pull requests, in-flight work, and external blockers."
    )
    lavish_script = '''
  <script>
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-lavish-request]");
      if (!button || button.disabled) return;
      const status = button.parentElement.querySelector(".request-status");
      if (!window.lavish || typeof window.lavish.queuePrompt !== "function") {
        if (status) status.textContent = "Open this page inside an active Lavish session to queue the request.";
        return;
      }
      const payload = JSON.parse(button.dataset.lavishRequest);
      const prompt = "Create a Lavish page from this source and reply with its link in this session's conversation panel. " +
        "Use the structured request below as the source of truth.\\n\\n" + JSON.stringify(payload);
      window.lavish.queuePrompt(prompt, {
        tag: "quarterdeck-page-request",
        text: "Request a Lavish page: " + payload.title,
        element: button,
        data: payload
      });
      button.disabled = true;
      button.textContent = "Request queued";
      if (status) status.textContent = "Send the queued prompt to the agent. An armed listener must be available to create the page and return its link.";
    });
  </script>''' if lavish else ""
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="quarterdeck-{'gen' + 'erated'}" content="read-only review page">
  <title>{html.escape(title)} · Firstmate review</title>
  <style>
    :root {{ color-scheme: light dark; --navy: #17324d; --sea: #176b69; --sand: #f3eddf; --paper: #fffdf8; --ink: #20303d; --muted: #657482; --line: #d8d3c8; --gold: #c98635; --head: #17324d; --soft: #ffffff70; --warn-bg: #fff8e9; --warn-ink: #624313; --fail-bg: #fff2ee; --fail-ink: #6f251b; --chip: #e6f1ee; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--sand); color: var(--ink); font: 16px/1.55 system-ui, -apple-system, Segoe UI, sans-serif; }}
    header {{ background: var(--head); color: #fffdf8; padding: .55rem max(1.2rem, calc((100vw - 1080px) / 2)); border-bottom: 3px solid var(--gold); display: flex; flex-wrap: wrap; align-items: baseline; gap: .1rem 1rem; }}
    h1 {{ margin: 0; font: 700 1.1rem/1.3 Georgia, serif; letter-spacing: -.01em; }}
    header p {{ margin: 0; color: #e4e8e8; font-size: .82rem; }}
    main {{ width: min(1080px, calc(100% - 2.4rem)); margin: 2rem auto 4rem; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .8rem; margin-bottom: 2rem; }}
    .metric {{ background: var(--paper); border: 1px solid var(--line); border-radius: 12px; padding: 1rem 1.1rem; }}
    .metric b {{ display: block; color: var(--sea); font: 700 1.8rem Georgia, serif; }}
    .metric span {{ color: var(--muted); font-size: .9rem; }}
    .home {{ margin: 2rem 0 2.5rem; padding: 1.2rem; background: var(--soft); border: 1px solid var(--line); border-radius: 16px; }}
    .home-head {{ display: flex; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }}
    .home h2 {{ margin: 0; color: var(--navy); font: 700 1.8rem Georgia, serif; overflow-wrap: anywhere; }}
    .home-role {{ margin: 0 0 .25rem; color: var(--sea); font-size: .73rem; text-transform: uppercase; letter-spacing: .09em; font-weight: 700; }}
    .home-source {{ margin: .35rem 0 0; color: var(--muted); font-size: .83rem; overflow-wrap: anywhere; }}
    .home-counts {{ display: flex; flex-wrap: wrap; gap: .5rem; margin: .3rem 0 1rem; }}
    .home-counts span {{ padding: .2rem .6rem; border: 1px solid var(--line); border-radius: 999px; background: var(--paper); color: var(--muted); font-size: .82rem; }}
    .dashboard-section {{ margin: 1.5rem 0 1.8rem; }}
    .section-head {{ display: flex; align-items: start; justify-content: space-between; gap: 1rem; margin-bottom: .8rem; }}
    .section-head h3 {{ margin: 0; color: var(--navy); font: 700 1.35rem Georgia, serif; }}
    .section-head p {{ margin: .3rem 0; color: var(--muted); }}
    .count {{ border: 1px solid var(--line); background: var(--paper); color: var(--sea); border-radius: 999px; min-width: 2.2rem; padding: .25rem .65rem; text-align: center; font-weight: 700; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 310px), 1fr)); gap: .8rem; }}
    .card {{ background: var(--paper); border: 1px solid var(--line); border-top: 3px solid var(--sea); border-radius: 12px; padding: 1rem 1.1rem; min-width: 0; }}
    .card h3 {{ margin: .1rem 0 .55rem; font-size: 1.08rem; overflow-wrap: anywhere; }}
    .card p {{ margin: .4rem 0; }}
    .eyebrow {{ color: var(--muted); font-size: .73rem; text-transform: uppercase; letter-spacing: .09em; font-weight: 700; }}
    .links {{ margin-top: .8rem !important; font-size: .9rem; }}
    .request-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: .65rem; margin-top: .8rem; }}
    .request-lavish {{ border: 1px solid var(--sea); border-radius: 7px; background: var(--chip); color: var(--ink); padding: .45rem .7rem; font: inherit; font-size: .86rem; font-weight: 700; cursor: pointer; }}
    .request-lavish:focus-visible {{ outline: 3px solid var(--gold); outline-offset: 2px; }}
    .request-lavish:disabled {{ opacity: .7; cursor: default; }}
    .request-status {{ color: var(--muted); font-size: .82rem; }}
    .reaction {{ margin-top: .6rem; }}
    .reaction-latest {{ margin: .3rem 0; color: var(--muted); font-size: .85rem; }}
    .reaction-text {{ flex: 1 1 14rem; min-width: 0; min-height: 2.2rem; padding: 0 .6rem; border: 1px solid var(--line); border-radius: 7px; background: var(--paper); color: var(--ink); font: inherit; font-size: .9rem; }}
    .reaction-choice[aria-pressed=true] {{ background: var(--sea); color: var(--paper); }}
    .chip-reaction {{ color: var(--ink); border-color: var(--gold); }}
    .chip-reaction[hidden], .reaction-latest[hidden] {{ display: none; }}
    .quiet-summary {{ color: var(--muted); font-size: .82rem; margin: -.5rem 0 1rem; }}
    a {{ color: var(--sea); font-weight: 650; }}
    a:focus-visible {{ outline: 3px solid var(--gold); outline-offset: 3px; }}
    .path {{ color: var(--muted); font-weight: 400; overflow-wrap: anywhere; }}
    .empty {{ background: var(--soft); border: 1px dashed var(--line); border-radius: 10px; color: var(--muted); padding: 1rem; }}
    .failure, .warning {{ margin: 1rem 0; border-radius: 10px; padding: 1rem; }}
    .failure {{ border: 1px solid #b8503c; background: var(--fail-bg); color: var(--fail-ink); }}
    .warning {{ border: 1px solid #c98635; background: var(--warn-bg); color: var(--warn-ink); }}
    .failure p, .warning p {{ margin: .35rem 0 0; overflow-wrap: anywhere; }}
    .secondmates {{ margin: 1.7rem 0 0; padding-left: 1rem; border-left: 3px solid var(--line); }}
    .secondmates > h3 {{ margin: 0 0 .8rem; color: var(--navy); font: 700 1.2rem Georgia, serif; }}
    .secondmates .home {{ margin: 1rem 0; background: var(--paper); }}
    .jump {{ display: flex; flex-wrap: wrap; gap: .5rem; margin: 0 0 1.5rem; }}
    .jump a {{ border: 1px solid var(--line); background: var(--paper); border-radius: 999px; padding: .3rem .8rem; text-decoration: none; }}
    .jump b {{ color: var(--gold); }}
    .dashboard-section h2 {{ margin: 0; color: var(--navy); font: 700 1.5rem Georgia, serif; }}
    .reviews {{ border: 2px solid var(--gold); border-radius: 14px; padding: 1rem; background: var(--warn-bg); }}
    .detail-heading {{ margin: 3rem 0 1rem; color: var(--navy); font: 700 1.5rem Georgia, serif; }}
    .when {{ color: var(--muted); font-size: .88rem; }}
    .bearing details {{ margin: .6rem 0; }}
    .bearing summary {{ cursor: pointer; color: var(--sea); font-weight: 650; }}
    .bearing .body {{ border-left: 3px solid var(--line); padding-left: .8rem; overflow-wrap: anywhere; }}
    .generated, .source-note {{ color: #d8c49b; font-size: .85rem; }}
    .source-note {{ color: var(--muted); }}
    footer {{ border-top: 1px solid var(--line); margin-top: 3rem; padding-top: 1rem; color: var(--muted); font-size: .85rem; }}
    .board {{ margin-bottom: 1rem; }}
    .board-item {{ background: var(--paper); border: 1px solid var(--line); border-left: 4px solid var(--sea); border-radius: 12px; margin: .6rem 0; }}
    .board-item summary {{ display: flex; gap: .8rem; align-items: flex-start; min-height: 3rem; padding: .8rem 1rem; cursor: pointer; list-style: none; }}
    .board-item summary::-webkit-details-marker {{ display: none; }}
    .board-item summary:focus-visible {{ outline: 3px solid var(--gold); outline-offset: 2px; }}
    .board-num {{ flex: none; min-width: 2.2rem; height: 2.2rem; border-radius: 999px; background: var(--sea); color: #fff; font: 700 1.1rem/2.2rem Georgia, serif; text-align: center; }}
    .board-text {{ display: block; min-width: 0; }}
    .board-title {{ display: block; color: var(--navy); font: 700 1.2rem/1.3 Georgia, serif; overflow-wrap: anywhere; }}
    .board-home {{ display: inline-block; margin-top: .15rem; color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .08em; }}
    .board-line {{ display: block; margin-top: .2rem; overflow-wrap: anywhere; }}
    .board-body {{ border-top: 1px solid var(--line); padding: .3rem 1rem 1rem; overflow-wrap: anywhere; }}
    .board-links {{ margin: .5rem 0 0; padding-left: 1.1rem; }}
    .board-links li {{ margin: .6rem 0; }}
    .board-links a {{ display: inline-block; min-height: 2.75rem; line-height: 2.75rem; }}
    .board-links .path {{ display: block; margin-top: -.5rem; font-size: .8rem; }}
    .board-chips, .board-brief {{ display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .45rem; }}
    .board-brief {{ align-items: center; gap: .5rem; }}
    .chip {{ display: inline-block; padding: .1rem .55rem; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); font-size: .78rem; overflow-wrap: anywhere; }}
    .chip-group {{ color: var(--sea); border-color: var(--sea); font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }}
    .chip-deferred, .chip-sweep {{ color: var(--warn-ink); background: var(--warn-bg); border-color: var(--gold); }}
    .chip-rec {{ color: var(--ink); background: var(--chip); border-color: var(--sea); font-size: .88rem; padding: .35rem .7rem; }}
    .rec-label {{ color: var(--sea); font-weight: 700; text-transform: uppercase; letter-spacing: .06em; font-size: .72rem; }}
    .brief-link {{ display: inline-flex; align-items: center; min-height: 44px; padding: 0 1rem; border: 1px solid var(--sea); border-radius: 10px; background: var(--paper); text-decoration: none; }}
    details.board-band > summary {{ display: block; min-height: 44px; cursor: pointer; list-style: none; }}
    details.board-band > summary::-webkit-details-marker, .board-more > summary::-webkit-details-marker {{ display: none; }}
    details.board-band > summary h2::before {{ content: "▸ "; color: var(--gold); }}
    details.board-band[open] > summary h2::before {{ content: "▾ "; }}
    details.board-band > summary:focus-visible, .board-more > summary:focus-visible {{ outline: 3px solid var(--gold); outline-offset: 2px; }}
    .board-more > summary {{ display: flex; align-items: center; min-height: 44px; padding: 0 1rem; cursor: pointer; list-style: none; color: var(--sea); font-weight: 700; border: 1px dashed var(--line); border-radius: 12px; }}
    .views {{ display: grid; grid-template-columns: 11rem minmax(0, 1fr); gap: 1.2rem; align-items: start; }}
    .views-rail {{ display: flex; flex-direction: column; gap: .4rem; }}
    .views-rail button, .view-editor button {{ min-height: 44px; padding: 0 .9rem; border: 1px solid var(--line); border-radius: 10px; background: var(--paper); color: var(--ink); font: inherit; font-weight: 700; text-align: left; cursor: pointer; }}
    .views-rail button[aria-pressed=true] {{ border-color: var(--sea); box-shadow: inset 4px 0 0 var(--sea); color: var(--sea); }}
    .views-rail button:focus-visible, .view-editor button:focus-visible {{ outline: 3px solid var(--gold); outline-offset: 2px; }}
    .view-section {{ margin: 0 0 1.4rem; }}
    .view-section h4 {{ margin: .8rem 0 .2rem; color: var(--muted); font-size: .85rem; text-transform: uppercase; letter-spacing: .04em; }}
    .view-editor {{ margin-top: 1rem; }}
    .view-editor ul {{ list-style: none; padding: 0; }}
    .view-editor li {{ display: flex; gap: .5rem; align-items: center; min-height: 44px; }}
    .view-editor li label {{ flex: 1 1 auto; }}
    .view-editor input[type=text] {{ min-height: 44px; padding: 0 .6rem; border: 1px solid var(--line); border-radius: 8px; background: var(--paper); color: var(--ink); font: inherit; }}
    .desk-group {{ margin: .9rem 0 .4rem; font-size: .95rem; color: var(--sea); }}
    .sweep {{ margin: .2rem 0 .6rem; padding: .6rem .8rem; border: 1px solid var(--gold); border-radius: 10px; background: var(--warn-bg); color: var(--warn-ink); }}
    .stale {{ color: #ffd48a; }}
    details.dashboard-section:not(.board-band) > summary {{ display: flex; justify-content: space-between; gap: 1rem; align-items: center; min-height: 3rem; cursor: pointer; list-style: none; }}
    details.dashboard-section:not(.board-band) > summary::-webkit-details-marker {{ display: none; }}
    details.dashboard-section:not(.board-band) > summary h2 {{ display: inline; }}
    details.dashboard-section:not(.board-band) > summary h2::before {{ content: "▸ "; color: var(--gold); }}
    details.dashboard-section:not(.board-band)[open] > summary h2::before {{ content: "▾ "; }}
    @media (prefers-color-scheme: dark) {{
      :root {{ --navy: #a9cdee; --sea: #5fc2b9; --sand: #11181f; --paper: #1a242d; --ink: #e3e8ec; --muted: #9aa8b3; --line: #33434f; --gold: #e0a24e; --head: #0d2438; --soft: #ffffff10; --warn-bg: #2d2412; --warn-ink: #f0d9a8; --fail-bg: #331b17; --fail-ink: #f3c4bb; --chip: #1f3a37; }}
      .board-num {{ color: #0b1a18; }}
    }}
    @media (max-width: 640px) {{
      .summary {{ grid-template-columns: repeat(2, 1fr); }}
      .views {{ grid-template-columns: minmax(0, 1fr); }}
      .views-rail {{ flex-direction: row; flex-wrap: wrap; }}
      .views-rail button {{ text-align: center; flex: 1 1 auto; }}
    }}
    @media print {{ body {{ background: #fff; }} header {{ padding: 1.2rem; }} main {{ width: 100%; margin: 1rem 0; }} .card {{ break-inside: avoid; }} }}
  </style>
</head>
<body>
  <header>
    <h1>⚓ {html.escape(title)}</h1>
    <p class="generated">Generated at {timestamp}{refresh_note}</p>
    {board_status}
  </header>
  <main>
    {board_block}
    {attention_block}
    {bearings_block}
    {reviewed_block}
    <h2 class="detail-heading">Details by home</h2>
    <div class="summary" aria-label="Review counts">
      {metrics}
    </div>
    {''.join(home_panel(home, lavish, show_all, decisions) for home in homes)}
    <footer>{html.escape(introductory_text)} {footer_label} · generated {timestamp} · source files open as readable pages beside this one.</footer>
  </main>
  {lavish_script}{refresh_script}{reload_script}{mark_script}{reaction_script}
</body>
</html>
'''


# --- Bearings view -----------------------------------------------------------

SNAPSHOT_SCRIPT = Path("bin") / "fm-bearings-snapshot.sh"
SNAPSHOT_TIMEOUT_SECONDS = 15
ITEM_ATTRIBUTE_RE = re.compile(
    r"\((?P<key>repo|kind|since|hold-kind|hold|held|merged|done|closed|until|hold[-_]until|hold[-_]set|priority|pr|"
    r"state|status|review_ready|blocked[-_]by|waiting[-_](?:on|for))(?::\s*|\s+)", re.I
)
BLOCKER_FIELD_RE = re.compile(
    r"\b(?P<key>blocked[-_]by|waiting[-_](?:on|for)):\s*(?P<value>\S+)", re.I
)
BULLET_ITEM_RE = re.compile(r"^-\s+\[(?P<mark>[ xX])\]\s+(?P<id>[A-Za-z0-9][\w.-]*)\s+-\s+(?P<rest>.*)$")
REPORT_PATH_RE = re.compile(r"\bdata/([\w.-]+)/report\.md\b")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?")
OPTIONS_RE = re.compile(r"(?is)\boptions?\s*(?:\([^)]*\))?\s*:\s*(.+?)(?=\n\s*\n|\brecommend(?:ation|ed)?\s*:|\Z)")
RECOMMENDATION_RE = re.compile(r"(?is)\brecommend(?:ation|ed)?\s*:\s*(.+?)(?=\n\s*\n|\boptions?\s*:|\Z)")
@dataclass
class BacklogItem:
    id: str
    title: str
    state: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    body: str = ""
    raw: str = ""
    done: bool = False
    hold_reason: str = ""
    hold_kind: str = ""
    held: bool = False
    blocked_by: str = ""
    report_dir: str = ""
    pr_url: str = ""
    review_ready: bool = False

    @property
    def project(self) -> str:
        return self.attrs.get("repo", "")

    @property
    def filed(self) -> str:
        for key in ("since",):
            if self.attrs.get(key):
                return self.attrs[key]
        match = DATE_RE.search(self.body)
        return match.group(0) if match else ""

    @property
    def closed_on(self) -> str:
        for key in ("merged", "done"):
            match = DATE_RE.search(self.attrs.get(key, ""))
            if match:
                return match.group(0)
        return ""


@dataclass
class BItem:
    """One row of a bearings section, whichever source produced it."""
    title: str
    owner: str = ""
    item_id: str = ""
    project: str = ""
    filed: str = ""
    hold_reason: str = ""
    body: str = ""
    options: str = ""
    recommendation: str = ""
    report_page: str = ""
    report_label: str = ""
    item_page: str = ""
    pr_url: str = ""
    artifact: str = ""
    waits_on: str = ""
    status: str = ""
    detail: str = ""


@dataclass
class HomeSource:
    label: str
    mode: str  # "snapshot" or "fallback"
    reason: str = ""


@dataclass
class Bearings:
    call: list[BItem] = field(default_factory=list)
    landed: list[BItem] = field(default_factory=list)
    underway: list[BItem] = field(default_factory=list)
    charted: list[BItem] = field(default_factory=list)
    sources: list[HomeSource] = field(default_factory=list)
    omissions: list[tuple[str, str, str]] = field(default_factory=list)
    reports: list[tuple[HomeSnapshot, Report]] = field(default_factory=list)
    generated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    item_pages: dict[str, str] = field(default_factory=dict)
    linked_reports: set[Path] = field(default_factory=set)
    attention: dict[str, object] | None = None


HOLD_REASON_PREFIX = "fm-hold-v1:"


def decode_hold_reason(value: str) -> str:
    """Decode Firstmate's reversible hold-reason encoding (fm-hold-v1: + base64 utf-8); other text is unchanged."""
    if not value.startswith(HOLD_REASON_PREFIX):
        return value
    payload = value[len(HOLD_REASON_PREFIX):]
    try:
        raw = base64.b64decode(payload, validate=True)
        if base64.b64encode(raw).decode("ascii") != payload:
            return value
        return raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return value


def balanced_close(text: str, start: int) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def parse_bullet_item(header: re.Match[str], continuation: list[str], section: str) -> BacklogItem:
    rest = header.group("rest")
    attrs: dict[str, str] = {}
    attribute_spans: list[tuple[int, int]] = []
    first_attr = len(rest)
    position = 0
    while True:
        match = ITEM_ATTRIBUTE_RE.search(rest, position)
        if not match:
            break
        close = balanced_close(rest, match.start())
        key = match.group("key").lower()
        attrs.setdefault(key, rest[match.end():close].strip())
        attribute_spans.append((match.start(), close + 1))
        first_attr = min(first_attr, match.start())
        position = close + 1
    title_part = rest[:first_attr]
    unstructured_parts: list[str] = []
    segment_start = 0
    for start, end in attribute_spans:
        unstructured_parts.append(rest[segment_start:start])
        segment_start = end
    unstructured_parts.append(rest[segment_start:])
    unstructured_text = " ".join(unstructured_parts)
    blocker_values: dict[str, str] = {}
    for blocker in BLOCKER_FIELD_RE.finditer(unstructured_text):
        key = blocker.group("key").lower().replace("-", "_")
        blocker_values.setdefault(key, blocker.group("value"))
    for key, value in attrs.items():
        normalized_key = key.replace("-", "_")
        if normalized_key in {"blocked_by", "waiting_on", "waiting_for"} and value:
            blocker_values.setdefault(normalized_key, value)
    blocked_by = next(
        (blocker_values[key] for key in ("waiting_on", "waiting_for", "blocked_by") if blocker_values.get(key)),
        "",
    )
    attrs.update(blocker_values)
    title_part = BLOCKER_FIELD_RE.sub(" ", title_part)
    report_dir = ""
    report = REPORT_PATH_RE.search(title_part)
    if report:
        report_dir = report.group(1)
        title_part = title_part.replace(report.group(0), " ")
    title = markdown_text(re.sub(r"\s+", " ", title_part).strip(" -·")) or header.group("id")
    body = "\n".join(line[2:] if line.startswith("  ") else line.lstrip() for line in continuation).strip()
    raw = header.group(0) + ("\n" + "\n".join(continuation) if continuation else "")
    hold_kind = normalized_state(attrs.get("hold-kind", ""))
    if not report_dir:
        report = REPORT_PATH_RE.search(body)
        report_dir = report.group(1) if report else ""
    pr = PR_URL_RE.search(raw)
    state = normalized_state(attrs.get("state") or attrs.get("status") or section)
    done = not is_cancelled_state(state) and (
        header.group("mark") in "xX"
        or field_is_set(attrs.get("closed", ""))
        or field_is_set(attrs.get("done", ""))
        or field_is_set(attrs.get("merged", ""))
        or is_closed_state(state)
    )
    if done:
        state = "done"
    pr_url = pr.group(0) if pr else ""
    return BacklogItem(
        id=header.group("id"), title=title,
        state=state,
        attrs=attrs, body=body, raw=raw, done=done,
        hold_reason=markdown_text(decode_hold_reason(attrs.get("hold", ""))), hold_kind=hold_kind,
        held=(
            attrs["held"].strip().lower() in TRUE_VALUES if "held" in attrs
            else field_is_set(attrs.get("hold", ""))
        ),
        blocked_by=blocked_by,
        report_dir=report_dir, pr_url=pr_url,
        review_ready=(not done and valid_pr_url(pr_url) and is_review_ready_state(attrs.get("review_ready", ""), state)),
    )


def backlog_items(source: str, snapshot: HomeSnapshot) -> list[BacklogItem]:
    """Read Firstmate's bullet backlog format, plus table rows the attention view already understands."""
    items: list[BacklogItem] = []
    lines = source.splitlines()
    section = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            section = heading_section(heading.group(1))
            index += 1
            continue
        bullet = BULLET_ITEM_RE.match(line)
        if bullet:
            index += 1
            continuation: list[str] = []
            while index < len(lines) and (not lines[index].strip() or lines[index][:1].isspace()):
                continuation.append(lines[index])
                index += 1
            while continuation and not continuation[-1].strip():
                continuation.pop()
            items.append(parse_bullet_item(bullet, continuation, section))
            continue
        index += 1
    seen = {item.id for item in items}
    for record in snapshot.records:
        identifier = value_for(record.fields, "id")
        if not identifier or identifier in seen:
            continue
        state = state_for(record)
        review_flag = is_review_ready(record)
        merged_date = value_for(record.fields, "merged")
        item = BacklogItem(
            id=identifier, title=record.title, state=state,
            body=record.text if record.text != record.title else "", raw=record.text,
            done=is_closed(record),
            attrs={key: value for key, value in {
                "merged": merged_date,
                "repo": value_for(record.fields, "repo"),
                "blocked_by": value_for(record.fields, "blocked_by", "blocked-by"),
                "waiting_on": value_for(record.fields, "waiting_on", "waiting-on"),
                "waiting_for": value_for(record.fields, "waiting_for", "waiting-for"),
                "hold-until": value_for(record.fields, "hold_until", "hold-until", "until"),
                "hold-set": value_for(record.fields, "hold_set", "hold-set"),
            }.items() if value},
            hold_reason=markdown_text(decode_hold_reason(_blank(value_for(record.fields, "hold_reason")))),
            hold_kind=normalized_state(_blank(value_for(record.fields, "hold_kind"))),
            held=value_for(record.fields, "held").lower() in TRUE_VALUES,
            blocked_by=_blank(value_for(record.fields, "waiting_on", "waiting_for", "blocked_by")),
            report_dir=record.report.parent.name if record.report else "",
            pr_url=record.pr_url or "", review_ready=review_flag,
        )
        closed = value_for(record.fields, "closed")
        if field_is_set(closed):
            item.attrs["done"] = closed
        reason = _blank(value_for(record.fields, "blocked_reason", "block_reason"))
        if reason and not item.hold_reason:
            item.hold_reason = markdown_text(decode_hold_reason(reason))
        items.append(item)
    return items


def parse_when(value: str) -> datetime | None:
    match = DATE_RE.search(value or "")
    if not match:
        return None
    text = match.group(0).replace("Z", "+00:00").replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def waited_text(filed: str, now: datetime) -> str:
    then = parse_when(filed)
    if then is None:
        return ""
    return elapsed_text(then, now)


def elapsed_text(then: datetime, now: datetime) -> str:
    seconds = max(0, int((now - then).total_seconds()))
    days, hours = seconds // 86400, seconds // 3600
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''}"
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return "under an hour"


def options_and_recommendation(*texts: str) -> tuple[str, str]:
    options = recommendation = ""
    for text in texts:
        if not text:
            continue
        if not options and (match := OPTIONS_RE.search(text)):
            options = markdown_text(re.sub(r"\s+", " ", match.group(1))).strip()
        if not recommendation and (match := RECOMMENDATION_RE.search(text)):
            recommendation = markdown_text(re.sub(r"\s+", " ", match.group(1))).strip()
    return options, recommendation


def item_page_filename(home: Path | None, owner: str, item_id: str) -> str:
    digest = hashlib.sha256(f"{home}\0{owner}\0{item_id}".encode("utf-8")).hexdigest()[:12]
    return f"item-{digest}.html"


def report_backlog_item(items: list[BacklogItem], report: Report) -> BacklogItem | None:
    """The backlog task a report belongs to: one naming its directory, else one with no report named and the
    directory's id."""
    directory = report.path.parent.name
    return next((i for i in items if i.report_dir == directory), None) or next(
        (i for i in items if not i.report_dir and i.id == directory), None
    )


def find_report(item: BacklogItem, snapshot: HomeSnapshot) -> Report | None:
    candidates = [name for name in (item.report_dir, item.id) if name]
    for name in candidates:
        for report in snapshot.reports:
            if report.path.parent.name == name:
                return report
    return None


def run_bearings_snapshot(
    home: Path, data_dir: Path | None = None, timeout: float = SNAPSHOT_TIMEOUT_SECONDS
) -> tuple[dict[str, object] | None, str]:
    """Run a home's own bearings snapshot; return (data, "") or (None, reason it cannot be used)."""
    script = home / SNAPSHOT_SCRIPT
    if not script.is_file() or not os.access(script, os.X_OK):
        return None, "no executable bin/fm-bearings-snapshot.sh in this home"
    selected_data_dir = (data_dir or home / "data").expanduser().resolve()
    env = dict(os.environ, FM_HOME=str(home), FM_DATA_OVERRIDE=str(selected_data_dir))
    try:
        process = subprocess.Popen(
            [str(script), "--json"], cwd=home, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError as exc:
        return None, f"could not start the snapshot script: {exc}"
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, 9)
        except OSError:
            process.kill()
        process.communicate()
        return None, f"the snapshot script timed out after {timeout:g} seconds"
    if process.returncode != 0:
        return None, f"the snapshot script exited with status {process.returncode}"
    try:
        data = json.loads(stdout.decode("utf-8"))
    except UnicodeDecodeError:
        return None, "the snapshot script printed invalid utf-8"
    except json.JSONDecodeError:
        return None, "the snapshot script did not print JSON"
    if not isinstance(data, dict) or data.get("schema") != "fm-bearings.v1":
        return None, "the snapshot script printed an unrecognized schema"
    row_fields = {
        "decisions_open": ("owner", "summary", "verb"),
        "landed": ("owner", "id", "what", "artifact"),
        "in_flight": ("id", "name", "repo", "state", "doing"),
        "secondmates": ("id", "state", "doing", "reason"),
        "gates": ("owner", "id", "title", "filed", "blocked_by", "reason"),
    }
    nullable_fields = {"in_flight": {"repo"}, "gates": {"filed"}}
    for name, fields in row_fields.items():
        rows = data.get(name)
        if not isinstance(rows, list):
            return None, f"the snapshot field {name} is not an array"
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                return None, f"the snapshot {name} row {index + 1} is not an object"
            for field_name in fields:
                if field_name not in row:
                    return None, f"the snapshot {name} row {index + 1} has no {field_name}"
                value = row[field_name]
                if not isinstance(value, str) and not (value is None and field_name in nullable_fields.get(name, set())):
                    return None, f"the snapshot {name} row {index + 1} has an invalid {field_name}"
            if name == "decisions_open":
                for field_name in ("key", "id"):
                    if field_name in row and not isinstance(row[field_name], str):
                        return None, f"the snapshot {name} row {index + 1} has an invalid {field_name}"
                if not any(isinstance(row.get(field_name), str) and row[field_name].strip() for field_name in ("key", "id")):
                    return None, f"the snapshot {name} row {index + 1} has no key or id"
            elif not row.get("id", "").strip():
                return None, f"the snapshot {name} row {index + 1} has no id"
    omissions = data.get("omitted", [])
    if not isinstance(omissions, list):
        return None, "the snapshot field omitted is not an array"
    for index, omission in enumerate(omissions):
        if not isinstance(omission, dict):
            return None, f"the snapshot omitted row {index + 1} is not an object"
        if not all(isinstance(omission.get(field_name), str) and omission[field_name].strip()
                   for field_name in ("surface", "reveal")):
            return None, f"the snapshot omitted row {index + 1} has invalid details"
    return data, ""


def _text(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _rows(data: dict[str, object], key: str) -> list[dict[str, object]]:
    rows = data.get(key)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _blank(value: str) -> str:
    return "" if value.strip() in {"", "-", "(none)"} else value.strip()


class BearingsBuilder:
    def __init__(self, homes: list[HomeSnapshot], now: datetime, use_snapshot: bool = True,
                 runner: Callable[[Path, Path | None], tuple[dict[str, object] | None, str]] = run_bearings_snapshot) -> None:
        self.homes = homes
        self.now = now
        self.use_snapshot = use_snapshot
        self.runner = runner
        self.result = Bearings(generated=now)

    # -- shared helpers --
    def lookup(self, snapshot: HomeSnapshot, owner: str, item_id: str) -> tuple[HomeSnapshot, BacklogItem | None, str]:
        """Find the backlog entry for an id, using the secondmate's home when the owner is one."""
        target = snapshot
        if owner and owner != "(main)":
            target = next((child for child in snapshot.children if child.spec.route_id == owner), snapshot)
            if target is not snapshot:
                item_id = item_id.removeprefix(owner + "/")
        target, item_id = task_owner(target, item_id)
        if target.error or target.backlog_markdown is None:
            return target, None, item_id
        items = getattr(target, "_items", None)
        if items is None:
            items = {item.id: item for item in backlog_items(target.backlog_markdown, target)}
            setattr(target, "_items", items)
        return target, items.get(item_id), item_id

    def home_label(self, snapshot: HomeSnapshot, owner: str) -> str:
        if not owner or owner == "(main)":
            return snapshot.spec.label
        child = next((child for child in snapshot.children if child.spec.route_id == owner), None)
        return child.spec.label if child else owner

    def decorate(self, bitem: BItem, snapshot: HomeSnapshot, item: BacklogItem | None) -> None:
        if item is None:
            return
        home = snapshot.spec.home or Path(".")
        bitem.title = item.title or bitem.title
        bitem.project = item.project or bitem.project
        bitem.filed = bitem.filed or item.filed
        bitem.hold_reason = item.hold_reason
        bitem.body = item.body
        bitem.pr_url = bitem.pr_url or (item.pr_url if PR_URL_RE.fullmatch(item.pr_url or "") else "")
        report = find_report(item, snapshot)
        options, recommendation = options_and_recommendation(item.hold_reason, item.body)
        bitem.options = options
        bitem.recommendation = recommendation or (report.recommendation if report and report.recommendation else "")
        if report:
            bitem.report_page = source_page_filename("report", home, report.path)
            bitem.report_label = report_reference(snapshot.spec.label, report.path.parent.name)
            self.result.linked_reports.add(report.path)
        name = item_page_filename(snapshot.spec.home, snapshot.spec.label, item.id)
        bitem.item_page = name
        self.result.item_pages[name] = source_page_html(
            item.title, f"{snapshot.spec.label} · backlog item {item.id}", item.raw or item.title
        )

    # -- snapshot path --
    def from_snapshot(self, snapshot: HomeSnapshot, data: dict[str, object]) -> None:
        for row in _rows(data, "decisions_open"):
            owner = _text(row.get("owner"))
            key = _text(row.get("key")) or _text(row.get("id"))
            target, item, task = self.lookup(snapshot, owner, key)
            if item and is_cancelled_state(item.state):
                continue
            summary = _text(row.get("summary")) or key
            resolved = target is not snapshot or not owner or owner == "(main)"
            bitem = BItem(title=summary, owner=target.spec.label if resolved else self.home_label(snapshot, owner),
                          item_id=task if resolved else key, status=_text(row.get("verb")))
            if item is None:
                bitem.detail = (
                    f"Full background lives in the secondmate home: {self.home_label(snapshot, owner)}."
                    if owner and owner != "(main)" else summary
                )
            self.decorate(bitem, target, item)
            self.result.call.append(bitem)
        for row in _rows(data, "landed"):
            owner = _text(row.get("owner"))
            artifact = _blank(_text(row.get("artifact")))
            item_id = _text(row.get("id"))
            target, item, _task = self.lookup(snapshot, owner, item_id)
            if item and is_cancelled_state(item.state):
                continue
            bitem = BItem(title=_text(row.get("what")) or item_id, owner=self.home_label(snapshot, owner),
                          item_id=item_id, artifact=artifact)
            match = PR_URL_RE.search(artifact)
            if match:
                bitem.pr_url = match.group(0)
            elif item and item.pr_url:
                bitem.pr_url = item.pr_url
            if item:
                bitem.title = item.title
                bitem.project = item.project
                bitem.filed = item.closed_on
                bitem.item_page = item_page_filename(target.spec.home, target.spec.label, item.id)
                self.result.item_pages[bitem.item_page] = source_page_html(
                    item.title, f"{target.spec.label} · backlog item {item.id}", item.raw or item.title
                )
            self.result.landed.append(bitem)
        for row in _rows(data, "in_flight"):
            item_id = _text(row.get("id"))
            owner = item_id.partition("/")[0] if "/" in item_id else "(main)"
            target, item, task = self.lookup(snapshot, owner, item_id)
            if is_cancelled_state(_text(row.get("state"))) or (item and is_cancelled_state(item.state)):
                continue
            resolved = target is not snapshot or owner == "(main)"
            self.result.underway.append(BItem(
                title=_text(row.get("name")) or item_id,
                owner=target.spec.label if resolved else self.home_label(snapshot, owner),
                item_id=task if resolved else item_id,
                project=_blank(_text(row.get("repo"))), status=_text(row.get("state")),
                detail=_blank(_text(row.get("doing"))),
            ))
        for row in _rows(data, "secondmates"):
            state = normalized_state(_text(row.get("state")))
            if state in {"active_child_work", "captain_decision", "no_active_work"}:
                continue
            doing = _blank(_text(row.get("doing")))
            reason = _blank(_text(row.get("reason")))
            self.result.charted.append(BItem(
                title=f"Secondmate {_text(row.get('id'))}", owner=self.home_label(snapshot, _text(row.get("id"))),
                item_id=_text(row.get("id")), status=state or "unknown",
                waits_on=" — ".join(part for part in (doing, reason) if part) or "status unavailable",
            ))
        for row in _rows(data, "gates"):
            owner = _text(row.get("owner"))
            item_id = _text(row.get("id"))
            target, item, task = self.lookup(snapshot, owner, item_id)
            if item and is_cancelled_state(item.state):
                continue
            waits = " — ".join(part for part in (_blank(_text(row.get("blocked_by"))), _blank(_text(row.get("reason")))) if part)
            resolved = target is not snapshot or not owner or owner == "(main)"
            bitem = BItem(title=_text(row.get("title")) or item_id,
                          owner=target.spec.label if resolved else self.home_label(snapshot, owner),
                          item_id=task if resolved else item_id, filed=_text(row.get("filed")), waits_on=waits or "queue order")
            self.decorate(bitem, target, item)
            if item and not bitem.waits_on:
                bitem.waits_on = item.blocked_by or "queue order"
            self.result.charted.append(bitem)

    # -- fallback path --
    def from_backlog(self, snapshot: HomeSnapshot) -> None:
        if snapshot.error or snapshot.backlog_markdown is None:
            return
        label = snapshot.spec.label
        items = backlog_items(snapshot.backlog_markdown, snapshot)
        landed: list[tuple[str, BItem]] = []
        for item in items:
            if is_cancelled_state(item.state):
                continue
            bitem = BItem(title=item.title, owner=label, item_id=item.id, project=item.project, filed=item.filed)
            self.decorate(bitem, snapshot, item)
            if item.done:
                bitem.filed = item.closed_on
                bitem.pr_url = item.pr_url if PR_URL_RE.fullmatch(item.pr_url or "") else bitem.pr_url
                landed.append((item.closed_on, bitem))
            elif (item.held and item.hold_kind == "captain") or item.review_ready:
                bitem.status = "review" if item.review_ready and not item.held else "captain hold"
                self.result.call.append(bitem)
            elif is_in_flight_state(item.state):
                bitem.status = "in flight"
                bitem.detail = item.hold_reason
                self.result.underway.append(bitem)
            else:
                bitem.waits_on = item.blocked_by or item.hold_reason or "queue order"
                self.result.charted.append(bitem)
        landed.sort(key=lambda pair: pair[0], reverse=True)
        self.result.landed.extend(bitem for _when, bitem in landed[:8])

    def build(self) -> Bearings:
        for snapshot in self.homes:
            data, reason = (None, "snapshot disabled with --no-snapshot")
            if self.use_snapshot and snapshot.spec.home is not None and not snapshot.spec.remote_host and not snapshot.error:
                data, reason = self.runner(snapshot.spec.home, snapshot.data_dir)
            elif snapshot.error:
                reason = "home could not be read"
            if data is not None:
                self.result.sources.append(HomeSource(snapshot.spec.label, "snapshot"))
                self.result.omissions.extend(
                    (snapshot.spec.label, _text(row.get("surface")), _text(row.get("reveal")))
                    for row in _rows(data, "omitted")
                )
                self.from_snapshot(snapshot, data)
            else:
                self.result.sources.append(HomeSource(snapshot.spec.label, "fallback", reason))
                for member in [snapshot] + all_snapshots(snapshot.children):
                    self.from_backlog(member)
        state = next((s.review_state for s in all_snapshots(self.homes) if s.review_state is not None), None)
        if state is not None:
            self.result.call = [
                item for item in self.result.call
                if state.relevance_of("call", report_reference(item.owner, item.item_id))[0] != "stale"
            ]
        self.result.call.sort(key=lambda item: parse_when(item.filed) or datetime.max.replace(tzinfo=timezone.utc))
        self.result.reports = [
            (snapshot, report)
            for snapshot in all_snapshots(self.homes) for report in snapshot.reports if report.waiting
        ]
        return self.result


def build_bearings(homes: list[HomeSnapshot], now: datetime | None = None, use_snapshot: bool = True,
                   runner: Callable[[Path, Path | None], tuple[dict[str, object] | None, str]] = run_bearings_snapshot) -> Bearings:
    return BearingsBuilder(homes, now or datetime.now(timezone.utc), use_snapshot, runner).build()


def bearings_item_html(item: BItem, now: datetime, section: str) -> str:
    chips: list[str] = []
    if item.owner:
        chips.append(html.escape(item.owner))
    if item.project:
        chips.append("project " + html.escape(item.project))
    if item.status:
        chips.append(html.escape(item.status))
    parts = [f'<article class="card bearing"><p class="eyebrow">{" · ".join(chips)}</p>',
             f"<h3>{html.escape(item.title)}</h3>"]
    when = []
    if item.filed:
        waited = waited_text(item.filed, now)
        when.append(f"Filed {html.escape(item.filed[:16].replace('T', ' '))}")
        if waited:
            when.append(f"waiting {html.escape(waited)}")
    elif section == "call":
        when.append("Filed date not recorded")
    if when:
        parts.append(f'<p class="when">{" · ".join(when)}</p>')
    if item.hold_reason:
        parts.append(f'<p class="reason"><strong>Why it is held:</strong> {markdown_inline(item.hold_reason)}</p>')
    if item.waits_on:
        parts.append(f'<p class="reason"><strong>Waits on:</strong> {markdown_inline(item.waits_on)}</p>')
    if item.detail:
        parts.append(f"<p>{html.escape(item.detail)}</p>")
    if item.options:
        parts.append(f'<p><strong>Options:</strong> {markdown_inline(item.options)}</p>')
    if item.recommendation:
        parts.append(f'<p><strong>Recommendation:</strong> {markdown_inline(item.recommendation)}</p>')
    if item.body:
        detail_key = item.item_page or f"{item.owner}:{item.item_id or item.title}"
        parts.append(
            f'<details data-refresh-key="{html.escape("bearing:" + detail_key, quote=True)}">'
            f'<summary>Full task description</summary><div class="body">{render_markdown(item.body)}</div></details>'
        )
    links: list[str] = []
    if item.pr_url:
        links.append(f'<a href="{html.escape(item.pr_url, quote=True)}">{html.escape(item.pr_url)}</a>')
    elif item.artifact:
        parsed = urlparse(item.artifact)
        if parsed.scheme == "https" and parsed.netloc:
            links.append(f'<a href="{html.escape(item.artifact, quote=True)}">{html.escape(item.artifact)}</a>')
        else:
            links.append(html.escape(item.artifact))
    if item.report_page:
        links.append(f'<a href="{html.escape(item.report_page, quote=True)}">Read report <code>{html.escape(item.report_label)}</code></a>')
    if item.item_page:
        links.append(f'<a href="{html.escape(item.item_page, quote=True)}">Read backlog item</a>')
    if links:
        parts.append('<p class="links">' + " · ".join(links) + "</p>")
    parts.append("</article>")
    return "".join(parts)


MARK_ENDPOINT = "/api/reports/review"


def report_mark_unavailable_reason(
    report_id: str, ambiguous_ids: set[str], report_resolution_incomplete: bool,
) -> str | None:
    if report_resolution_incomplete:
        return "Review changes are disabled while a selected local home is incomplete."
    if report_id in ambiguous_ids:
        return "ID is shared by multiple homes; marking is disabled."
    return None


MARK_LABELS = {"mark": "Mark reviewed", "unmark": "Unmark", "discard": "Discard", "undiscard": "Undiscard"}
MARK_TITLES = {"discard": "Take it off the list without marking it reviewed"}


def mark_buttons(report_id: str, actions: list[str], fingerprint: str, unavailable_reason: str | None) -> str:
    if unavailable_reason:
        buttons = "".join(
            f'<button class="request-lavish" type="button" disabled>{MARK_LABELS[action]}</button>' for action in actions
        )
        return (
            f'<div class="request-row">{buttons}'
            f'<span class="request-status">{html.escape(unavailable_reason)}</span></div>'
        )
    buttons = "".join(
        f'<button class="request-lavish" type="button" data-mark-report="{html.escape(report_id, quote=True)}" '
        f'data-mark-fingerprint="{html.escape(fingerprint, quote=True)}" data-mark-action="{action}"'
        + (f' title="{html.escape(MARK_TITLES[action], quote=True)}"' if action in MARK_TITLES else "")
        + f'>{MARK_LABELS[action]}</button>'
        for action in actions
    )
    return f'<div class="request-row">{buttons}<span class="request-status" aria-live="polite"></span></div>'


def mark_button(report_id: str, action: str, fingerprint: str, unavailable_reason: str | None) -> str:
    return mark_buttons(report_id, [action], fingerprint, unavailable_reason)


def report_list_html(
    homes: list[HomeSnapshot], ambiguous_ids: set[str], report_resolution_incomplete: bool, *,
    section_id: str, title: str, intro: str, empty: str, reports: Callable[[Report], bool],
    actions: list[str], can_mark: bool = True, reactions: ReactionContext | None = None,
) -> str:
    cards = []
    for snapshot in all_snapshots(homes):
        for report in snapshot.reports:
            if not reports(report):
                continue
            home = snapshot.spec.home or Path(".")
            page = source_page_filename("report", home, report.path)
            reason = (
                f'<p class="reason"><strong>Marked stale:</strong> {html.escape(report.relevance_reason)}</p>'
                if report.relevance == "stale" and report.relevance_reason else ""
            )
            buttons = mark_buttons(
                report.report_id, actions, report.fingerprint,
                report_mark_unavailable_reason(report.report_id, ambiguous_ids, report_resolution_incomplete),
            ) if can_mark else ""
            reaction = reaction_html(
                reactions, "report", report.report_id,
                not report_mark_unavailable_reason(report.report_id, ambiguous_ids, report_resolution_incomplete),
            )
            cards.append(
                f'<article class="card"><p class="eyebrow">{html.escape(snapshot.spec.label)} · '
                f'<code>{html.escape(report.report_id)}</code></p><h3>{html.escape(report.title)}</h3>{reason}'
                f'<p class="links"><a href="{html.escape(page, quote=True)}">Read report</a></p>{buttons}{reaction}</article>'
            )
    content = "".join(cards) or f'<p class="empty">{html.escape(empty)}</p>'
    return (
        f'<details class="dashboard-section" id="{section_id}" data-refresh-key="{section_id}">'
        f'<summary><h2>{html.escape(title)}</h2><span class="count">{len(cards)}</span></summary>'
        f'<p>{html.escape(intro)}</p><div class="cards">{content}</div></details>'
    )


def reviewed_reports_html(
    homes: list[HomeSnapshot], ambiguous_ids: set[str], report_resolution_incomplete: bool,
    reactions: ReactionContext | None = None,
) -> str:
    return report_list_html(
        homes, ambiguous_ids, report_resolution_incomplete, section_id="reviewed", title="Reviewed reports",
        intro="Unmark a report to return it to “needs review”. A report that changes after you review it returns on its own.",
        empty="No reports are marked reviewed.", reports=lambda report: report.reviewed, actions=["unmark"],
        reactions=reactions,
    )


def set_aside_reports_html(
    homes: list[HomeSnapshot], ambiguous_ids: set[str], report_resolution_incomplete: bool, can_mark: bool,
    reactions: ReactionContext | None = None,
) -> str:
    """The stale and discarded report lists: hidden from the review list, one click away."""
    stale = report_list_html(
        homes, ambiguous_ids, report_resolution_incomplete, section_id="stale-reports", title="Stale reports",
        intro="One-offs or already decided; Firstmate marked these no longer relevant, so they are off the review list. "
              "Read them if you want, or discard them.",
        empty="No unreviewed report is marked stale.",
        reports=lambda report: report.needs_review and report.relevance == "stale",
        actions=["mark", "discard"], can_mark=can_mark, reactions=reactions,
    )
    discarded = report_list_html(
        homes, ambiguous_ids, report_resolution_incomplete, section_id="discarded", title="Discarded reports",
        intro="Taken off your list without being marked reviewed. Undiscard returns one to “needs review”; "
              "a report that changes after you discard it returns on its own.",
        empty="No reports are discarded.", reports=lambda report: report.discarded,
        actions=["undiscard"], can_mark=can_mark, reactions=reactions,
    )
    return stale + discarded


def bearings_html(
    bearings: Bearings, collapse_reviews: bool = False, can_mark: bool = False,
    ambiguous_ids: set[str] | None = None,
    report_resolution_incomplete: bool = False, reactions: ReactionContext | None = None,
    decisions: dict[str, dict[str, object]] | None = None,
) -> str:
    ambiguous_ids = ambiguous_ids or set()
    decisions = decisions or {}
    now = bearings.generated
    sections = [
        ("call", "Captain's Call", "Decisions and reviews waiting on you, with everything needed to answer.",
         bearings.call, "Nothing is waiting on a captain decision."),
        ("underway", "Underway", "Work that is live right now.", bearings.underway, "Nothing is underway."),
        ("charted", "Charted Next", "Queued or gated work, and what each item waits on.",
         bearings.charted, "Nothing is queued or gated."),
    ]
    background: dict[str, tuple[BItem, str]] = {}
    visible_sections = []
    for key, title, description, items, empty in sections:
        visible = []
        for item in items:
            identity = report_reference(item.owner, item.item_id)
            why = decisions.get(identity, {}).get("relevance")
            if why in {"quiet", "stale"}:
                background.setdefault(identity, (item, key))
            elif why != "deferred":
                visible.append(item)
        visible_sections.append((key, title, description, visible, empty))
    sections = visible_sections
    review_cards = []
    for snapshot, report in bearings.reports:
        home = snapshot.spec.home or Path(".")
        page = source_page_filename("report", home, report.path)
        recommendation = (
            f'<p><strong>Recommendation:</strong> {html.escape(report.recommendation)}</p>' if report.recommendation else ""
        )
        if can_mark:
            unavailable_reason = report_mark_unavailable_reason(
                report.report_id, ambiguous_ids, report_resolution_incomplete
            )
            tail = f'</p>{mark_buttons(report.report_id, ["mark", "discard"], report.fingerprint, unavailable_reason)}'
        elif report_resolution_incomplete:
            tail = ' · <span class="path">Review changes are disabled while a selected local home is incomplete.</span></p>'
        elif report.report_id in ambiguous_ids:
            tail = ' · <span class="path">ID is shared by multiple homes; narrow the selection before marking.</span></p>'
        else:
            tail = (
                f' · <span class="path">after reading: quarterdeck reports mark-reviewed '
                f'{html.escape(report.report_id)}</span></p>'
            )
        review_cards.append(
            f'<article class="card"><p class="eyebrow">{html.escape(snapshot.spec.label)} · <code>{html.escape(report.report_id)}</code></p>'
            f'<h3>{html.escape(report.title)}</h3>{recommendation}'
            f'<p class="links"><a href="{html.escape(page, quote=True)}">Read report</a>{tail}'
            + reaction_html(
                reactions, "report", report.report_id,
                not report_mark_unavailable_reason(report.report_id, ambiguous_ids, report_resolution_incomplete),
            ) + '</article>'
        )
    nav = "".join(
        f'<a href="#{key}">{html.escape(title)} <b>{len(items)}</b></a>' for key, title, _d, items, _e in sections
    )
    nav = f'<a href="#reviews">Reports to review <b>{len(review_cards)}</b></a>' + nav
    out = [f'<nav class="jump" aria-label="Sections">{nav}</nav>']
    reviews_cards = "".join(review_cards) or '<p class="empty">No reports are waiting on your review.</p>'
    if collapse_reviews:
        out.append(
            '<details class="dashboard-section reviews" id="reviews"><summary><h2>Reports waiting on your review</h2>'
            f'<span class="count">{len(review_cards)}</span></summary>'
            f'<p>Reports whose review state is “needs review”.</p><div class="cards">{reviews_cards}</div></details>'
        )
    else:
        out.append(
            '<div class="dashboard-section reviews" id="reviews"><div class="section-head"><div><h2>Reports waiting on your review</h2>'
            '<p>Reports whose review state is “needs review”.</p></div>'
            f'<span class="count">{len(review_cards)}</span></div><div class="cards">{reviews_cards}</div></div>'
        )
    for key, title, description, items, empty in sections:
        cards = "".join(bearings_item_html(item, now, key) for item in items)
        if not cards:
            cards = f'<p class="empty">{html.escape(empty)}</p>'
        out.append(
            f'<div class="dashboard-section" id="{key}"><div class="section-head"><div><h2>{html.escape(title)}</h2>'
            f'<p>{html.escape(description)}</p></div><span class="count">{len(items)}</span></div>'
            f'<div class="cards">{cards}</div></div>'
        )
    if background:
        out.append('<details class="dashboard-section" id="call-background"><summary>'
                   f'{len(background)} background decisions</summary><div class="cards">'
                   + "".join(bearings_item_html(item, now, key) for item, key in background.values()) + '</div></details>')
    notes = []
    for source in bearings.sources:
        if source.mode == "snapshot":
            notes.append(f"{html.escape(source.label)}: Firstmate bearings snapshot")
        else:
            notes.append(f"{html.escape(source.label)}: <strong>fallback</strong> — parsed from the backlog ({html.escape(source.reason)})")
    out.append('<p class="source-note">Sources — ' + "; ".join(notes) + ".</p>")
    if bearings.omissions:
        omitted_notes = "; ".join(
            f"{html.escape(home)}: {html.escape(surface)} ({html.escape(reveal)})"
            for home, surface, reveal in bearings.omissions
        )
        out.append('<p class="source-note"><strong>Snapshot omissions</strong> — ' + omitted_notes + ".</p>")
    return "".join(out)


# --- Recent work and decision relevance ----------------------------------------
# `quarterdeck attention` and the page's "Waiting on you" section share one view, computed from two documented
# Firstmate sources: state/fleet-ledger.jsonl (docs/fleet-ledger.md, record version 1), read directly, and the open
# decisions from the home's own bin/fm-attention.sh --json (schema fm-attention.v1). A source in a shape this code
# does not understand is an error the reader shows, never a guess and never an empty view.

ATTENTION_SCHEMA = "quarterdeck-attention.v1"
ATTENTION_SCRIPT = Path("bin") / "fm-attention.sh"
ATTENTION_FEED_SCHEMA = "fm-attention.v1"
ATTENTION_TIMEOUT_SECONDS = 15
LEDGER_PATH = Path("state") / "fleet-ledger.jsonl"
LEDGER_VERSION = 1
RECENT_WINDOWS = (("last_12h", 0, 12), ("12h_to_24h", 12, 24), ("24h_to_48h", 24, 48))
WINDOW_TITLES = {"last_12h": "Last 12 hours", "12h_to_24h": "12–24 hours ago", "24h_to_48h": "24–48 hours ago"}
DEFAULT_RECENT_DAYS = 3
RECENT_LIST_LIMIT = 6
RECENT_HOURS = 6
PRIORITY_LIMIT = 3
PREFERENCES_VERSION = 1
VIEW_LIMIT = 8
VIEW_NAME_LIMIT = 40
VIEW_SECTION_TITLES = {
    "waiting": "Waiting on you", "recent": f"Recent work, last {RECENT_HOURS} hours",
    "priorities": "Priority projects", "project": "Project", "information": "For your information",
    "done": "Done recently",
}
SEEDED_VIEWS: tuple[dict[str, object], ...] = (
    {"name": "Morning", "sections": [{"type": "waiting"}, {"type": "recent"}, {"type": "information"}]},
    {"name": "Priorities", "sections": [{"type": "priorities"}, {"type": "waiting"}]},
)
STATUS_LABELS = {
    "decision_waiting": "Decision waiting", "blocked": "Blocked", "pr_ready": "PR ready to merge",
    "review_waiting": "Review waiting", "result_ready": "Result ready", "in_flight": "In flight", "queued": "Queued",
}
GROUP_STATUS = {
    "decision": "decision_waiting", "decide": "decision_waiting", "unsorted": "decision_waiting",
    "forward": "blocked", "merge": "pr_ready", "approve": "review_waiting", "read": "result_ready",
    "in_flight": "in_flight", "queued": "queued",
}
RELEVANT_REASONS = ("raised", "captain_activity", "recent_work")
RELEVANCE_TEXT = {
    "raised": "raised recently", "captain_activity": "you acted on it recently", "recent_work": "worked on recently",
    "deferred": "deferred to a later date", "quiet": "quiet", "stale": "marked stale",
}


class AttentionSourceError(Exception):
    def __init__(self, source: str, code: str, message: str) -> None:
        super().__init__(message)
        self.source = source
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"source": self.source, "code": self.code, "message": self.message}


def iso_seconds(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_fleet_ledger(path: Path) -> list[dict[str, object]] | None:
    """Return the ledger's distinct records, None when the home records no ledger, or raise AttentionSourceError."""
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise AttentionSourceError("ledger", "ledger_unreadable", f"{path} is not a regular file")
    try:
        text = path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise AttentionSourceError("ledger", "ledger_unreadable", f"cannot read {path}: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise AttentionSourceError("ledger", "ledger_malformed", f"{path} is not utf-8 text") from exc
    lines = text.split("\n")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            if number == len(lines):
                continue  # an unterminated final line is an append still being written
            raise AttentionSourceError("ledger", "ledger_malformed", f"{path} line {number} is not JSON") from exc
        if not isinstance(record, dict):
            raise AttentionSourceError("ledger", "ledger_malformed", f"{path} line {number} is not a JSON object")
        version = record.get("v")
        if type(version) is not int or version != LEDGER_VERSION:
            raise AttentionSourceError(
                "ledger", "ledger_version",
                f"{path} line {number} has record version {json.dumps(version)}; "
                f"Quarterdeck understands only version {LEDGER_VERSION}",
            )
        ts = record.get("ts")
        if type(ts) not in (int, float) or not isinstance(record.get("event"), str) or not isinstance(record.get("task"), str):
            raise AttentionSourceError(
                "ledger", "ledger_malformed", f"{path} line {number} lacks a numeric ts, an event or a task"
            )
        identity = json.dumps(record, sort_keys=True)
        if identity not in seen:  # the ledger may repeat a record after an interrupted capture
            seen.add(identity)
            records.append(record)
    return records


def run_attention_feed(
    home: Path, data_dir: Path | None = None, timeout: float = ATTENTION_TIMEOUT_SECONDS
) -> dict[str, object]:
    """Run the home's own fm-attention.sh --json and check its fm-attention.v1 open decisions, or raise."""
    script = home / ATTENTION_SCRIPT
    if not script.is_file() or not os.access(script, os.X_OK):
        raise AttentionSourceError("decisions", "feed_missing", f"no executable {ATTENTION_SCRIPT} in this home")
    selected_data_dir = (data_dir or home / "data").expanduser().resolve()
    env = dict(os.environ, FM_HOME=str(home), FM_DATA_OVERRIDE=str(selected_data_dir))
    try:
        process = subprocess.Popen(
            [str(script), "--json"], cwd=home, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError as exc:
        raise AttentionSourceError("decisions", "feed_failed", f"could not start {ATTENTION_SCRIPT}: {exc}") from exc
    try:
        stdout, _stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(process.pid, 9)
        except OSError:
            process.kill()
        process.communicate()
        raise AttentionSourceError(
            "decisions", "feed_failed", f"{ATTENTION_SCRIPT} timed out after {timeout:g} seconds"
        ) from exc
    if process.returncode != 0:
        raise AttentionSourceError("decisions", "feed_failed", f"{ATTENTION_SCRIPT} exited with status {process.returncode}")
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttentionSourceError("decisions", "feed_malformed", f"{ATTENTION_SCRIPT} did not print JSON") from exc
    return check_attention_feed(data)


def check_attention_feed(data: object) -> dict[str, object]:
    if not isinstance(data, dict) or data.get("schema") != ATTENTION_FEED_SCHEMA:
        schema = data.get("schema") if isinstance(data, dict) else None
        raise AttentionSourceError(
            "decisions", "feed_schema",
            f"{ATTENTION_SCRIPT} printed schema {json.dumps(schema)}; Quarterdeck understands only {ATTENTION_FEED_SCHEMA}",
        )
    rows = data.get("open_decisions")
    if not isinstance(rows, list):
        raise AttentionSourceError("decisions", "feed_malformed", "the feed's open_decisions is not an array")
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise AttentionSourceError("decisions", "feed_malformed", f"open decision {index} is not an object")
        for name in ("task", "kind", "title"):
            if not isinstance(row.get(name), str) or not row[name].strip():
                raise AttentionSourceError("decisions", "feed_malformed", f"open decision {index} has no {name}")
        for name in ("bucket", "opened_at", "last_touched_at"):
            if row.get(name) is not None and not isinstance(row[name], str):
                raise AttentionSourceError("decisions", "feed_malformed", f"open decision {index} has an invalid {name}")
    return data


def feed_epoch(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def plain_title(value: str) -> str:
    """A backlog title without the links Firstmate appends to it."""
    return re.sub(r"\s+", " ", re.sub(r"https?://\S+", " ", value)).strip(" -·")


def recent_work(records: list[dict[str, object]] | None, snapshot: HomeSnapshot, feed_titles: dict[str, str],
                now: datetime) -> dict[str, object]:
    records = records or []
    items: dict[str, BacklogItem] = {}
    if snapshot.backlog_markdown is not None:
        items = {item.id: item for item in backlog_items(snapshot.backlog_markdown, snapshot)}
    ordered = sorted(records, key=lambda record: record["ts"])  # type: ignore[arg-type, return-value]
    projects: dict[str, str] = {}
    prs: dict[str, str] = {}
    for record in ordered:
        task = str(record["task"])
        if record["event"] == "task.dispatched" and isinstance(record.get("project"), str):
            projects[task] = str(record["project"])
        if record["event"] in {"task.pr_ready", "task.merged"} and isinstance(record.get("pr"), str):
            prs[task] = str(record["pr"])

    def describe(record: dict[str, object]) -> dict[str, object]:
        task = str(record["task"])
        item = items.get(task)
        pr = record.get("pr") if isinstance(record.get("pr"), str) else prs.get(task) or (item.pr_url if item else "")
        return {
            "at": iso_seconds(record["ts"]),  # type: ignore[arg-type]
            "task": task,
            "title": plain_title((item.title if item else "") or feed_titles.get(task) or "") or task,
            "project": projects.get(task) or (item.project if item else "") or None,
            "pr": pr or None,
        }

    starts = min((float(record["ts"]) for record in records), default=None)  # type: ignore[arg-type]
    end = now.timestamp()
    windows = []
    for name, near, far in RECENT_WINDOWS:
        start, stop = end - far * 3600, end - near * 3600
        inside = [r for r in reversed(ordered) if float(r["ts"]) >= start and (near == 0 or float(r["ts"]) < stop)]  # type: ignore[arg-type]
        merged = [describe(r) | {"via": r.get("via") if isinstance(r.get("via"), str) else None}
                  for r in inside if r["event"] == "task.merged"]
        started = [describe(r) | {"kind": r.get("kind") if isinstance(r.get("kind"), str) else None}
                   for r in inside if r["event"] == "task.dispatched"]
        answered = sum(1 for r in inside if r["event"] == "captain.answered" and r.get("mode") in {"done", "release"})
        coverage = "none" if starts is None or starts >= stop else "partial" if starts > start else "full"
        windows.append({
            "window": name, "from": iso_seconds(start), "to": iso_seconds(stop), "coverage": coverage,
            "merged_count": len(merged), "started_count": len(started), "answered_count": answered,
            "merged": merged, "started": started,
        })
    latest = [r for r in reversed(ordered) if end - RECENT_HOURS * 3600 <= float(r["ts"]) <= end]  # type: ignore[arg-type]
    last_6h = [
        describe(r) | ({"event": "merged", "via": r.get("via") if isinstance(r.get("via"), str) else None}
                       if r["event"] == "task.merged" else
                       {"event": "started", "kind": r.get("kind") if isinstance(r.get("kind"), str) else None})
        for r in latest if r["event"] in {"task.merged", "task.dispatched"}
    ]
    return {"ledger_starts_at": iso_seconds(starts) if starts is not None else None, "windows": windows, "last_6h": last_6h}


def decision_key(snapshot: HomeSnapshot, row: dict[str, object]) -> str:
    if row.get("kind") == "keyed" and row.get("key"):
        owner = next((child.spec.label for child in snapshot.children if child.spec.route_id == row["task"]), str(row["task"]))
        return report_reference(owner, str(row["key"]))
    return task_key(snapshot, str(row["task"]), all_snapshots([snapshot]))


def decision_relevance(
    row: dict[str, object], records: list[dict[str, object]], since: float, board_touched: float | None = None,
) -> tuple[str, float | None]:
    if row.get("bucket") == "dated":
        return "deferred", None
    opened = feed_epoch(row.get("opened_at"))
    touched = feed_epoch(row.get("last_touched_at"))
    task_records = [r for r in records if r["task"] == row["task"] and float(r["ts"]) >= since]
    stamps = [opened, touched, board_touched, *(float(r["ts"]) for r in task_records)]
    activity = max((stamp for stamp in stamps if stamp is not None and stamp >= since), default=None)
    if opened is not None and opened >= since:
        return "raised", activity
    if any(stamp is not None and stamp >= since for stamp in (touched, board_touched)):
        return "captain_activity", activity
    if any(str(r["event"]).startswith("captain.") for r in task_records):
        return "captain_activity", activity
    if task_records:
        return "recent_work", activity
    return "quiet", None


def home_attention(
    snapshot: HomeSnapshot, now: datetime, all_decisions: bool = False,
    use_feed: bool = True, feed_runner: Callable[[Path, Path | None], dict[str, object]] = run_attention_feed,
    board_activity: dict[str, float] | None = None, projects_out: dict[str, str] | None = None,
) -> dict[str, object]:
    spec = snapshot.spec
    since = now.timestamp() - DEFAULT_RECENT_DAYS * 86400
    errors: list[dict[str, str]] = []
    view: dict[str, object] = {
        "label": spec.label, "home": str(spec.home) if spec.home else None, "ok": True, "errors": errors,
        "ledger": None, "recent_work": None, "recent_work_6h": [], "show_recent": False,
        "decision_relevance": {"recent_days": DEFAULT_RECENT_DAYS, "since": iso_seconds(since)}, "decisions": None,
    }
    if spec.home is None or spec.remote_host:
        errors.append({"source": "home", "code": "home_not_local",
                       "message": snapshot.error or "this home has no local path Quarterdeck can read"})
        view["ok"] = False
        return view
    if snapshot.error:
        errors.append({"source": "backlog", "code": "backlog_unreadable", "message": snapshot.error})
    records: list[dict[str, object]] | None = None
    ledger_ok = True
    try:
        records = read_fleet_ledger(spec.home / LEDGER_PATH)
    except AttentionSourceError as exc:
        errors.append(exc.as_dict())
        ledger_ok = False
    feed: dict[str, object] | None = None
    if use_feed:
        try:
            feed = feed_runner(spec.home, snapshot.data_dir)
        except AttentionSourceError as exc:
            errors.append(exc.as_dict())
    feed_rows = [row for row in (feed or {}).get("open_decisions", []) if isinstance(row, dict)]  # type: ignore[union-attr]
    feed_titles = {str(row["task"]): str(row["title"]) for row in feed_rows}
    for row in (feed or {}).get("in_flight", []) or []:  # type: ignore[union-attr]
        if isinstance(row, dict) and isinstance(row.get("task"), str) and isinstance(row.get("title"), str):
            feed_titles.setdefault(row["task"], row["title"])
    if ledger_ok:
        view["ledger"] = {
            "path": str(LEDGER_PATH), "present": records is not None, "version": LEDGER_VERSION,
            "records": len(records or []),
        }
        work = recent_work(records, snapshot, feed_titles, now)
        view["recent_work_6h"] = work.pop("last_6h")
        view["show_recent"] = bool(view["recent_work_6h"])
        view["recent_work"] = work
    snapshots = all_snapshots([snapshot])
    normalized: list[dict[str, object]] = []
    for owner in snapshots:
        if owner.error or owner.spec.home is None or owner.spec.remote_host:
            continue
        local_records = records or []
        if owner is not snapshot:
            try:
                local_records = read_fleet_ledger(owner.spec.home / LEDGER_PATH) or []
            except AttentionSourceError as exc:
                errors.append(exc.as_dict())
                local_records = []
        normalized.extend(dict(record, task=task_key(owner, str(record["task"]), snapshots)) for record in local_records)
    if projects_out is not None:
        for owner in snapshots:
            if not owner.error and owner.backlog_markdown is not None:
                for item in backlog_items(owner.backlog_markdown, owner):
                    if item.project:
                        projects_out[task_key(owner, item.id, snapshots)] = item.project
        for record in sorted(normalized, key=lambda r: r["ts"]):  # type: ignore[arg-type, return-value]
            if record["event"] == "task.dispatched" and isinstance(record.get("project"), str):
                projects_out[str(record["task"])] = str(record["project"])
    candidates = list(feed_rows)
    represented = {decision_key(snapshot, row) for row in feed_rows}
    for owner in snapshots:
        if owner.error or owner.spec.home is None or owner.spec.remote_host:
            continue
        items = backlog_items(owner.backlog_markdown or "", owner)
        missing = [item for item in items if needs_captain(item) and task_key(owner, item.id, snapshots) not in represented]
        for item in missing:
            touched, deferred = backlog_recency(item, now, {other.id for other in items})
            row = {
                "task": task_key(owner, item.id, snapshots), "home": owner.spec.label, "title": item.title,
                "kind": "captain_hold", "source": "backlog", "reason": item.hold_reason,
                "bucket": "dated" if deferred else "live", "opened_at": item.filed or None,
                "last_touched_at": iso_or_none(touched),
            }
            candidates.append(row)
    relevant: list[dict[str, object]] = []
    background: list[dict[str, object]] = []
    state = snapshot.review_state or ReviewState()
    for row in candidates:
        key = decision_key(snapshot, row)
        why, activity = decision_relevance(dict(row, task=key), normalized, since, (board_activity or {}).get(key))
        if why != "deferred" and state.relevance_of("call", key)[0] == "stale":
            why = "stale"
        (relevant if why in RELEVANT_REASONS else background).append(dict(row, relevance=why, activity_at=iso_seconds(activity) if activity is not None else None))
    if feed is not None or candidates or not use_feed:
        view["decisions"] = {
            "source": ATTENTION_FEED_SCHEMA if feed is not None else "backlog", "relevant": relevant,
            "background_count": len(background), "background": background if all_decisions else None,
        }
        if not use_feed:
            view["decisions"]["skipped"] = "the Firstmate scripts were skipped with --no-snapshot"
    view["ok"] = not errors
    return view


def board_engagement(homes: list[HomeSnapshot]) -> dict[str, float]:
    snapshots = all_snapshots(homes)
    activity: dict[str, float] = {}
    for snapshot in snapshots:
        if snapshot.board is not None and snapshot.board.supported:
            for item in snapshot.board.items:
                if item.touched_at is not None:
                    key = task_key(snapshot, item.task, snapshots)
                    activity[key] = max(activity.get(key, float("-inf")), item.touched_at.timestamp())
    return activity


def project_matches(value: object, name: str) -> bool:
    """True when a recorded project is the canonical named project, ignoring case."""
    if not isinstance(value, str) or not value.strip():
        return False
    wanted = name.strip().lower()
    recorded = value.strip().lower()
    return recorded == wanted


def waiting_item(kind: str, key: str, title: str, ask: str, project: str | None, group: str, at: str | None) -> dict[str, object]:
    return {"kind": kind, "key": key, "title": title, "ask": ask, "group": group, "project": project or None, "at": at}


def home_waiting(
    snapshot: HomeSnapshot, view: dict[str, object], board: BoardView | None, projects: dict[str, str],
) -> dict[str, list[dict[str, object]]]:
    """What waits on the captain in one home: its decisions, the rest of what needs them, then informational items."""
    labels = {owner.spec.label for owner in all_snapshots([snapshot])}
    decisions = view.get("decisions")
    rows = [*decisions["relevant"], *(decisions.get("background") or [])] if isinstance(decisions, dict) else []
    entries = {entry.key: entry for entry in (
        [*(entry for items in board.bands.values() for entry in items), *board.stale] if board else []
    ) if entry.home in labels and not entry.deferred}
    waiting_decisions: list[dict[str, object]] = []
    needs_you: list[dict[str, object]] = []
    informational: list[dict[str, object]] = []
    queues = {"decision": waiting_decisions, "needs_you": needs_you, "informational": informational}
    seen: set[str] = set()
    for row in rows:
        if row.get("relevance") == "deferred":
            continue
        key = decision_key(snapshot, row)
        if key in seen:
            continue
        seen.add(key)
        entry = entries.get(key)
        group = entry.item.group if entry else "decision"
        kind = "informational" if group == "read" else "decision" if group in {"decision", "decide", "unsorted"} else "needs_you"
        queues[kind].append(dict(waiting_item(
            kind, key, entry.item.title if entry else str(row["title"]),
            entry.item.ask if entry else str(row.get("reason") or ""),
            (entry.item.project if entry else None) or projects.get(key), group,
            row.get("activity_at") or row.get("last_touched_at") or row.get("opened_at"),
        ), relevance=row.get("relevance")))
    for entry in entries.values():
        if entry.key in seen:
            continue
        kind = "informational" if entry.item.group == "read" else "needs_you"
        queues[kind].append(waiting_item(
            kind, entry.key, entry.item.title, entry.item.ask,
            entry.item.project or projects.get(entry.key), entry.item.group, iso_or_none(entry.touched),
        ))
    return {"decisions": waiting_decisions, "needs_you": needs_you, "informational": informational}


def priority_entries(waiting: dict[str, list[dict[str, object]]], names: list[str], complete: bool) -> list[dict[str, object]]:
    """For each named project, its waiting items, or an explicit nothing_waiting flag and a sentence to say."""
    everything = [*waiting["decisions"], *waiting["needs_you"], *waiting["informational"]]
    entries = []
    for name in names:
        items = [item for item in everything if project_matches(item["project"], name)]
        nothing_waiting = not items if complete or items else None
        say = (f"{name}: waiting status unavailable." if nothing_waiting is None else
               f"{name}: nothing is waiting." if nothing_waiting else
               f"{name}: {len(items)} waiting. " + "; ".join(str(item["title"]) for item in items) + ".")
        entries.append({"project": name, "nothing_waiting": nothing_waiting, "waiting": items, "say": say})
    return entries


def scope_waiting(
    waiting: dict[str, list[dict[str, object]]], names: list[str], show_more: bool, has_priority: bool,
) -> tuple[dict[str, list[dict[str, object]]], dict[str, object]]:
    """Put priority-project items first, in priority order; hold back the rest unless asked or nothing is pending for them."""
    def rank(item: dict[str, object]) -> int | None:
        return next((index for index, name in enumerate(names) if project_matches(item["project"], name)), None)

    ranked = {kind: [dict(item, priority=rank(item)) for item in items] for kind, items in waiting.items()}
    for items in ranked.values():
        items.sort(key=lambda item: len(names) if item["priority"] is None else item["priority"])  # type: ignore[operator]
    total = sum(len(items) for items in ranked.values())
    mine = sum(1 for items in ranked.values() for item in items if item["priority"] is not None)
    if not names or not has_priority or show_more:
        return ranked, {"scope": "all", "more_count": 0}
    return (
        {kind: [item for item in items if item["priority"] is not None] for kind, items in ranked.items()},
        {"scope": "priority", "more_count": total - mine},
    )


def plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def clean_view(raw: object) -> dict[str, object]:
    """A saved view as {name, sections}; ValueError when it is not one Quarterdeck can build."""
    if not isinstance(raw, dict) or set(raw) != {"name", "sections"}:
        raise ValueError("a view is an object with a name and sections")
    name, sections = raw["name"], raw["sections"]
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > VIEW_NAME_LIMIT:
        raise ValueError(f"a view name is 1 to {VIEW_NAME_LIMIT} characters")
    if not isinstance(sections, list) or not 1 <= len(sections) <= len(VIEW_SECTION_TITLES) + PRIORITY_LIMIT:
        raise ValueError(f"view {name.strip()!r} needs one or more sections")
    cleaned: list[dict[str, str]] = []
    for section in sections:
        kind = section.get("type") if isinstance(section, dict) else None
        project = section.get("project") if isinstance(section, dict) else None
        if not isinstance(kind, str) or kind not in VIEW_SECTION_TITLES:
            raise ValueError(f"unknown section type {kind!r}; use {', '.join(VIEW_SECTION_TITLES)}")
        if set(section) != ({"type", "project"} if kind == "project" else {"type"}):
            raise ValueError(f"section {kind!r} takes {'a project name' if kind == 'project' else 'no other fields'}")
        if kind == "project" and (not isinstance(project, str) or not project.strip()):
            raise ValueError("a project section needs a project name")
        entry = {"type": kind, **({"project": project.strip()} if kind == "project" else {})}  # type: ignore[union-attr]
        if entry in cleaned:
            raise ValueError(f"view {name.strip()!r} lists the {kind} section twice")
        cleaned.append(entry)
    return {"name": name.strip(), "sections": cleaned}


def effective_views(document: dict[str, object]) -> tuple[list[dict[str, object]], str]:
    """The saved views and the startup view's name; the seeded Morning and Priorities views when none are saved."""
    views = [dict(view) for view in document.get("views") or SEEDED_VIEWS]  # type: ignore[attr-defined]
    return views, str(document.get("default_view") or views[0]["name"])


def find_view(views: list[dict[str, object]], name: str) -> dict[str, object]:
    match = next((view for view in views if str(view["name"]).lower() == name.strip().lower()), None)
    if match is None:
        raise ValueError(f"unknown view {name!r}; saved views: {', '.join(str(view['name']) for view in views)}")
    return match


def view_item(item: dict[str, object], home: str, rank: Callable[[object], int]) -> dict[str, object]:
    status = GROUP_STATUS.get(str(item.get("group"))) or (
        "result_ready" if item["kind"] == "informational" else "decision_waiting")
    when = parse_when(str(item["at"])) if item.get("at") else None
    return {
        "key": item["key"], "kind": item["kind"], "title": item["title"], "ask": item.get("ask") or "",
        "project": item["project"], "home": home, "status": status, "status_label": STATUS_LABELS[status],
        "at": item.get("at"), "background": item.get("relevance") in {"quiet", "stale"},
        "_order": (list(STATUS_LABELS).index(status), rank(item["project"]), -(when.timestamp() if when else 0)),
    }


def build_view(
    definition: dict[str, object], default: bool, homes: list[dict[str, object]], names: list[str],
) -> dict[str, object]:
    """Stack a saved view's sections; the first section to claim an item shows it and later ones count it as shown above."""
    def rank(project: object) -> int:
        return next((index for index, name in enumerate(names) if project_matches(project, name)), len(names))

    needs: list[dict[str, object]] = []
    info: list[dict[str, object]] = []
    recent: list[dict[str, object]] = []
    for home in homes:
        for kind in ("decisions", "needs_you", "informational"):
            (info if kind == "informational" else needs).extend(
                view_item(item, str(home["label"]), rank) for item in home["waiting"][kind])  # type: ignore[index]
        info.extend(view_item(item, str(home["label"]), rank) for item in home["open_work"])  # type: ignore[union-attr]
        recent.extend(dict(item, home=home["label"]) for item in home["recent_work_6h"])  # type: ignore[union-attr]
    complete = all(home["complete"] for home in homes)
    for pool in (needs, info):
        pool.sort(key=lambda item: item["_order"])  # type: ignore[arg-type, return-value]

    wanted: list[dict[str, str]] = []
    for section in definition["sections"]:  # type: ignore[union-attr]
        for entry in ([{"type": "project", "project": name} for name in names]
                      if section["type"] == "priorities" else [section]):
            if not any(entry == other or (entry.get("project") and project_matches(entry["project"], other.get("project", "")))
                       for other in wanted):
                wanted.append(entry)
    status_order = {item["key"]: item["_order"][0] for item in [*needs, *info]}
    claimed: set[object] = set()
    sections: list[dict[str, object]] = []
    for entry in wanted:
        kind, project = entry["type"], entry.get("project")
        if kind in {"recent", "done"}:
            candidates = sorted((item for item in recent if kind == "recent" or item["event"] == "merged"),
                                key=lambda item: (
                                    status_order.get(item["key"], list(STATUS_LABELS).index(
                                        "result_ready" if item["event"] == "merged" else "in_flight")),
                                    rank(item["project"]), -parse_when(str(item["at"])).timestamp(),
                                ))
            if candidates or kind == "done":
                shown = []
                above_keys = set()
                for item in candidates:
                    if item["key"] in claimed:
                        above_keys.add(item["key"])
                    elif not any(other["key"] == item["key"] for other in shown):
                        shown.append(item)
                claimed.update(item["key"] for item in shown)
                merged = sum(item["event"] == "merged" for item in shown)
                started = len(shown) - merged
                say = (f"In the last six hours, {merged} merged and {started} started." if kind == "recent" else
                       f"{len(shown)} done in the last six hours.")
                if above_keys:
                    say += f" {len(above_keys)} shown above."
                sections.append({
                    "type": kind, "title": VIEW_SECTION_TITLES[kind], "needs_you": [], "information": shown,
                    "background": None, "shown_above": len(above_keys), "nothing_waiting": not shown and not above_keys, "say": say,
                })
            continue
        title = str(project) if project else VIEW_SECTION_TITLES[str(kind)]  # type: ignore[arg-type]
        mine = [item for item in needs if kind in {"waiting", "project"}
                and (not project or project_matches(item["project"], str(project)))]
        notes = [item for item in info if kind in {"information", "project"}
                 and (not project or project_matches(item["project"], str(project)))]
        above = sum(item["key"] in claimed for item in [*mine, *notes])
        mine = [item for item in mine if item["key"] not in claimed]
        notes = [item for item in notes if item["key"] not in claimed]
        claimed.update(item["key"] for item in [*mine, *notes])
        older = [item for item in mine if item["background"]] if kind == "waiting" else []
        mine = [item for item in mine if item not in older]
        shown = len(mine) + len(notes) + len(older)
        nothing = True if complete and not shown and not above else None if not shown and not above else False
        if kind == "project":
            if nothing:
                say = f"Nothing waiting on {project}."
            elif nothing is None:
                say = f"{project}: waiting status unavailable."
            else:
                say = f"{project}: " + (f"{plural(len(mine), 'thing needs', 'things need')} you. First, {mine[0]['title']}: {mine[0]['status_label']}."
                                        if mine else "nothing needs you.")
                say += f" {len(notes)} for your information." if notes else ""
        elif kind == "waiting":
            say = ("Nothing is waiting on you." if nothing else "Waiting status unavailable." if nothing is None else
                   (f"{plural(len(mine), 'thing needs', 'things need')} you. First, {mine[0]['title']}: {mine[0]['status_label']}."
                    if mine else "Nothing new needs you."))
            say += f" {plural(len(older), 'older decision', 'older decisions')} waiting." if older else ""
        else:
            say = f"{len(notes)} for your information." if notes else "Nothing else for your information."
        say += f" {above} shown above." if above else ""
        section = {
            "type": kind, "title": title, "needs_you": mine, "information": notes,
            "background": {"count": len(older), "say": f"{plural(len(older), 'older decision', 'older decisions')} waiting",
                           "items": older} if older else None,
            "shown_above": above, "nothing_waiting": nothing, "say": say.strip(),
        }
        if project:
            section["project"] = project
        sections.append(section)
    for section in sections:
        for item in (*section["needs_you"], *section["information"], *((section["background"] or {}).get("items") or [])):  # type: ignore[union-attr, index]
            item.pop("_order", None)  # type: ignore[union-attr]
    return {"name": definition["name"], "default": default, "layout": definition["sections"], "sections": sections}


def informational_work(snapshot: HomeSnapshot, taken: set[object]) -> list[dict[str, object]]:
    """Open backlog tasks not already waiting on the captain."""
    snapshots = all_snapshots([snapshot])
    items: list[dict[str, object]] = []
    for owner in snapshots:
        if owner.error or owner.backlog_markdown is None:
            continue
        for item in backlog_items(owner.backlog_markdown, owner):
            key = task_key(owner, item.id, snapshots)
            if item.done or is_cancelled_state(item.state) or key in taken:
                continue
            when = parse_when(item.filed)
            group = "in_flight" if is_in_flight_state(item.state) else "queued"
            items.append(dict(
                waiting_item("informational", key, item.title, "", item.project, group, iso_or_none(when)),
                group=group,
            ))
    return items


def attention_view(
    homes: list[HomeSnapshot], now: datetime | None = None,
    all_decisions: bool = False, use_feed: bool = True,
    feed_runner: Callable[[Path, Path | None], dict[str, object]] = run_attention_feed,
    priority_projects: list[str] | None = None, project: str | None = None, show_more: bool = False,
    saved_views: tuple[list[dict[str, object]], str] | None = None, view_name: str | None = None, all_views: bool = False,
) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    board_activity = board_engagement(homes)
    projects: dict[str, str] = {}
    views = [home_attention(snapshot, now, True, use_feed, feed_runner, board_activity, projects) for snapshot in homes]
    shared = {"schema": ATTENTION_SCHEMA, "generated": iso_seconds(now.timestamp()), "homes": views}
    decisions = decision_rows(homes, shared)
    board = board_view(homes, now, fallback=True, decisions=decisions)
    waiting_by_home = [home_waiting(snapshot, view, board, projects) for snapshot, view in zip(homes, views)]
    known = registered_projects(homes, waiting_by_home) if project or priority_projects else []
    if project:
        project = resolve_project(project, known)
    names = [project] if project else []
    if not project:
        for name in priority_projects or []:
            try:
                names.append(resolve_project(name, known))
            except ValueError:
                names.append(name)
    has_priority = any(project_matches(item["project"], name)
                       for waiting in waiting_by_home for items in waiting.values() for item in items for name in names)
    view_homes: list[dict[str, object]] = []
    for snapshot, view, waiting in zip(homes, views, waiting_by_home):
        complete = use_feed and bool(view["ok"]) and not any(
            owner.registry_error or (not owner.spec.remote_host and owner.error)
            or (owner.board is not None and (not owner.board.supported or owner.board.warnings))
            for owner in all_snapshots([snapshot])
        )
        view["priorities"] = priority_entries(waiting, names, complete)
        view["waiting"], scope = scope_waiting(waiting, names, show_more, has_priority)
        visible = {item["key"] for items in view["waiting"].values() for item in items}
        view["waiting_more"] = {kind: [item for item in items if item["key"] not in visible]
                                for kind, items in waiting.items()}
        view["waiting_scope"] = scope["scope"]
        view["more_count"] = scope["more_count"]
        if view_name is not None or all_views:
            taken = set(decisions).union(item["key"] for items in waiting.values() for item in items)
            view_homes.append({
                "label": view["label"], "waiting": waiting, "complete": complete,
                "open_work": informational_work(snapshot, taken),
                "recent_work_6h": [dict(item, key=task_key(snapshot, str(item["task"]), all_snapshots([snapshot])))
                                   for item in view["recent_work_6h"]],
            })
        if not all_decisions and isinstance(view["decisions"], dict):
            view["decisions"]["background"] = None
    result = {
        "schema": ATTENTION_SCHEMA, "generated": shared["generated"],
        "ok": all(view["ok"] for view in views), "priority_projects": list(priority_projects or []),
        "requested_project": project or None, "homes": views,
    }
    if view_name is not None or all_views:
        definitions, startup = saved_views or effective_views({})
        build = lambda definition: build_view(  # noqa: E731
            definition, str(definition["name"]) == startup, view_homes, names)
        if all_views:
            result["views"] = [build(definition) for definition in definitions]
            result["default_view"] = startup
        else:
            result["view"] = build(find_view(definitions, view_name or startup))
            result["view"]["available"] = [str(definition["name"]) for definition in definitions]  # type: ignore[index]
    return result


def decision_rows(
    homes: list[HomeSnapshot], view: dict[str, object] | None, calls: list[BItem] | None = None,
) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    if view is None:
        return rows
    snapshots = all_snapshots(homes)
    activity = board_engagement(homes)
    now = parse_when(str(view["generated"])) or datetime.now(timezone.utc)
    since = now.timestamp() - DEFAULT_RECENT_DAYS * 86400
    for call in calls or []:
        key = report_reference(call.owner, call.item_id)
        row = {"task": call.item_id, "opened_at": call.filed}
        why, stamp = decision_relevance(row, [], since, activity.get(key))
        owner = next((owner for owner in snapshots if owner.spec.label == call.owner), None)
        if owner and owner.review_state and owner.review_state.relevance_of("call", key)[0] == "stale":
            why = "stale"
        rows[key] = dict(row, relevance=why, activity_at=iso_seconds(stamp) if stamp is not None else None)
    for home in view["homes"]:
        snapshot = next((s for s in snapshots if s.spec.label == home["label"]), None)
        decisions = home.get("decisions")
        if snapshot is None or not isinstance(decisions, dict):
            continue
        for row in [*(decisions.get("background") or []), *decisions["relevant"]]:
            key = decision_key(snapshot, row)
            rows[key] = row
    if any(home.get("waiting_scope") == "priority" for home in view["homes"]):
        visible = {item["key"] for home in view["homes"] for items in home.get("waiting", {}).values() for item in items}
        rows = {key: dict(row, relevance="quiet") if key not in visible and row.get("relevance") in RELEVANT_REASONS else row
                for key, row in rows.items()}
    return rows


def attention_check_line(view: dict[str, object]) -> str:
    parts = []
    for home in view["homes"]:  # type: ignore[union-attr]
        assert isinstance(home, dict)
        if home["errors"]:
            parts.append(f"{home['label']}: " + "; ".join(f"{e['source']}: {e['message']}" for e in home["errors"]))
            continue
        ledger, work, decisions = home["ledger"], home["recent_work"], home["decisions"]
        if ledger["present"]:
            ledger_text = f"ledger v{LEDGER_VERSION}, {ledger['records']} records from {work['ledger_starts_at'] or 'none'}"
        else:
            ledger_text = f"no {LEDGER_PATH} (recording off; recent work not covered)"
        if decisions.get("skipped"):
            parts.append(f"{home['label']}: {ledger_text}; decisions skipped: {decisions['skipped']}")
            continue
        open_count = len(decisions["relevant"]) + decisions["background_count"]
        parts.append(f"{home['label']}: {ledger_text}; {ATTENTION_FEED_SCHEMA}, {open_count} open decisions")
    return f"quarterdeck attention check: {'ok' if view['ok'] else 'FAILED'} — " + "; ".join(parts)


def default_preferences_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
    return (config_home / "quarterdeck" / "preferences.json").resolve()


def preferences_path(args: argparse.Namespace) -> Path:
    chosen = getattr(args, "preferences", None)
    return chosen.expanduser().resolve() if chosen else default_preferences_path()


def read_preferences(path: Path) -> dict[str, object]:
    """The shared preference document, empty when none was saved; ValueError when it cannot be understood."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read preferences {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != PREFERENCES_VERSION:
        raise ValueError(f"preferences {path} must be a JSON object with version {PREFERENCES_VERSION}")
    names = data.get("priority_projects", [])
    if (not isinstance(names, list) or len(names) > PRIORITY_LIMIT
            or not all(isinstance(name, str) and name.strip() for name in names) or len(set(names)) != len(names)):
        raise ValueError(f"preferences {path}: priority_projects must be up to {PRIORITY_LIMIT} distinct project names")
    if "views" in data or "default_view" in data:
        try:
            views = data.get("views")
            if not isinstance(views, list) or not 1 <= len(views) <= VIEW_LIMIT:
                raise ValueError(f"views must list 1 to {VIEW_LIMIT} views")
            views = [clean_view(view) for view in views]
            labels = [view["name"].lower() for view in views]  # type: ignore[union-attr]
            if len(set(labels)) != len(labels):
                raise ValueError("view names must be distinct")
            if data.get("default_view") is not None:
                default = str(data["default_view"]).strip().lower()
                if default not in labels:
                    raise ValueError("default_view must name a saved view")
                data["default_view"] = views[labels.index(default)]["name"]
            data["views"] = views
        except ValueError as exc:
            raise ValueError(f"preferences {path}: {exc}") from exc
    return data


def read_saved_views(path: Path) -> tuple[list[dict[str, object]], str]:
    return effective_views(read_preferences(path))


def store_view(
    path: Path, homes: list[HomeSnapshot], name: str | None, sections: list[dict[str, str]], make_default: bool,
) -> tuple[list[dict[str, object]], str]:
    """Save one view (or only change the startup view when sections is empty); project sections must be registered."""
    document = read_preferences(path)
    views, startup = effective_views(document)
    if not sections:
        startup = str(find_view(views, name or "")["name"])
    else:
        known = registered_projects(homes) if any(section.get("type") == "project" for section in sections) else []
        resolved = [dict(section, project=resolve_project(str(section.get("project") or ""), known))
                    if section.get("type") == "project" else section for section in sections]
        view = clean_view({"name": name or "", "sections": resolved})
        index = next((i for i, other in enumerate(views) if str(other["name"]).lower() == str(view["name"]).lower()), None)
        if index is None and len(views) >= VIEW_LIMIT:
            raise ValueError(f"at most {VIEW_LIMIT} views can be saved")
        if index is None:
            views.append(view)
        else:
            views[index] = view
        if make_default or startup.lower() == str(view["name"]).lower():
            startup = str(view["name"])
    document.update({"version": PREFERENCES_VERSION, "views": views, "default_view": startup})
    write_preferences(path, document, homes)
    return views, startup


def read_priority_projects(path: Path) -> list[str]:
    return list(read_preferences(path).get("priority_projects", []))  # type: ignore[call-overload]


def registered_projects(
    homes: list[HomeSnapshot], waiting: list[dict[str, list[dict[str, object]]]] | None = None,
) -> list[str]:
    """Project names recorded in route registries or on pending work."""
    names: set[str] = set()
    for owner in all_snapshots(homes):
        if owner.spec.home is None or owner.spec.remote_host or owner.registry_error:
            continue
        registry_path = owner.spec.home / "data" / "secondmates.md"
        try:
            registry = registry_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            registry = ""
        except (OSError, UnicodeDecodeError) as exc:
            owner.registry_error = f"{registry_path}: {exc}"
            continue
        for line in registry.splitlines():
            route = SECONDMATE_ENTRY_RE.match(line)
            projects = re.search(r"(?:^|;\s*)projects:\s*([^;]+)", route.group("attributes"), re.I) if route else None
            if projects:
                names.update(name.strip("`") for name in re.split(r"[,\s]+", projects[1].strip()) if name.strip("`"))
    if waiting is None:
        waiting = [home["waiting"] for home in attention_view(homes)["homes"]]
    for queues in waiting:
        for items in queues.values():
            names.update(str(item["project"]) for item in items if item["project"])
    return sorted(names, key=str.lower)


def resolve_project(raw: str, known: list[str]) -> str:
    match = next((name for name in known if name.lower() == raw.strip().lower()), None)
    match = match or next((name for name in known if project_matches(name.rsplit("/", 1)[-1], raw)), None)
    if match is None:
        raise ValueError(f"unknown project {raw!r}; recorded projects: {', '.join(known) or 'none'}")
    return match


def write_preferences(path: Path, document: dict[str, object], homes: list[HomeSnapshot]) -> None:
    destination = path.resolve()
    for owner in all_snapshots(homes):
        if owner.spec.home is None or owner.spec.remote_host:
            continue
        for root in (owner.spec.home, owner.spec.data_dir or owner.data_dir or owner.spec.home / "data"):
            if destination.is_relative_to(root.resolve()):
                raise ValueError("preferences must be outside selected Firstmate homes and their data directories")
    write_config(path, document)


def selected_homes(args: argparse.Namespace) -> list[HomeSnapshot]:
    if args.config is None:
        default_path = default_config_path()
        args.config = default_path if default_path.is_file() else None
    specs, _legacy = resolve_home_specs(args, load_config(args.config))
    return [load_home_snapshot(spec, discover_secondmates=True) for spec in specs]


def attention(args: argparse.Namespace) -> int:
    if not args.json and not args.check:
        print("quarterdeck: attention needs --json or --check", file=sys.stderr)
        return 2
    try:
        homes = selected_homes(args)
        document = read_preferences(preferences_path(args))
        priorities = list(document.get("priority_projects", []))  # type: ignore[call-overload]
        apply_review_marks(homes, load_review_state(validate_review_state_location(homes)))
        view = attention_view(
            homes, all_decisions=args.all_decisions, priority_projects=priorities, project=args.project, show_more=args.more,
            saved_views=effective_views(document), view_name=args.view,
        )
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    if args.check:
        print(attention_check_line(view))
    else:
        print(json.dumps(view, indent=2))
    return 0 if view["ok"] else 1


def view_spec(text: str) -> dict[str, str]:
    kind, _, project = text.partition(":")
    return {"type": kind.strip().lower(), "project": project.strip()} if kind.strip().lower() == "project" else {"type": kind.strip().lower()}


def views(args: argparse.Namespace) -> int:
    path = preferences_path(args)
    try:
        saved, startup = read_saved_views(path)
        if args.views_command in {"list", "show"}:
            chosen = [find_view(saved, args.name)] if args.views_command == "show" else saved
            if args.as_json:
                print(json.dumps({"views": chosen, "default_view": startup, "path": str(path)}, indent=2))
            else:
                for view in chosen:
                    parts = [f"project {s['project']}" if s["type"] == "project" else VIEW_SECTION_TITLES[s["type"]] for s in view["sections"]]  # type: ignore[index, union-attr]
                    print(f"{view['name']}{' (startup)' if view['name'] == startup else ''}: " + ", ".join(parts) + ".")
            return 0
        homes = selected_homes(args)
        if args.views_command == "save":
            store_view(path, homes, args.name, [view_spec(text) for text in args.section], args.default)
        else:
            store_view(path, homes, args.name, [], True)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    if args.views_command == "save":
        print(f"Saved view {args.name.strip()}." + (" It opens at startup." if args.default else ""))
    else:
        print(f"{find_view(read_saved_views(path)[0], args.name)['name']} is now your startup view.")
    return 0


def priorities(args: argparse.Namespace) -> int:
    path = preferences_path(args)
    try:
        current = read_priority_projects(path)
        if args.priorities_command == "show":
            if args.as_json:
                print(json.dumps({"priority_projects": current, "limit": PRIORITY_LIMIT, "path": str(path)}, indent=2))
            else:
                print("Priority projects: " + ", ".join(current) + "." if current else "No priority projects are set.")
            return 0
        homes = selected_homes(args)
        if args.priorities_command == "clear":
            names: list[str] = []
        else:
            if len(args.projects) > PRIORITY_LIMIT:
                raise ValueError(f"at most {PRIORITY_LIMIT} priority projects are allowed; got {len(args.projects)}")
            known = registered_projects(homes)
            names = []
            for raw in args.projects:
                match = resolve_project(raw, known)
                if match not in names:
                    names.append(match)
        document = read_preferences(path)
        document.update({"version": PREFERENCES_VERSION, "priority_projects": names})
        write_preferences(path, document, homes)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print("Priority projects: " + ", ".join(names) + "." if names else "Priority projects cleared.")
    return 0


def attention_item_html(item: dict[str, object], now: datetime) -> str:
    pr = item.get("pr")
    link = (f' · <a href="{html.escape(str(pr), quote=True)}">PR</a>'
            if isinstance(pr, str) and pr.startswith("https://") else "")
    at = parse_when(str(item["at"]))
    project = f'{html.escape(str(item["project"]))} · ' if item.get("project") else ""
    return (f'<li>{html.escape(str(item["title"]))} <span class="path">{project}'
            f'{elapsed_text(at, now) + " ago" if at else ""}</span>{link}</li>')


def waiting_item_html(item: dict[str, object]) -> str:
    ask = f' — {html.escape(str(item["ask"]))}' if item.get("ask") else ""
    return f'<li>{html.escape(str(item["title"]))}{ask}</li>'


def priorities_panel_html(homes: list[dict[str, object]]) -> str:
    rows = []
    for home in homes:
        label = f"{home['label']}: " if len(homes) > 1 else ""
        for entry in home.get("priorities") or []:  # type: ignore[union-attr]
            if entry["nothing_waiting"] is None:
                rows.append(f'<li><strong>{html.escape(label)}{html.escape(entry["project"])}</strong> '
                            '<span class="path">waiting status unavailable</span></li>')
                continue
            if entry["nothing_waiting"]:
                rows.append(f'<li><strong>{html.escape(label)}{html.escape(entry["project"])}</strong> '
                            f'<span class="path">nothing is waiting</span></li>')
                continue
            rows.append(f'<li><strong>{html.escape(label)}{html.escape(entry["project"])}</strong> '
                        f'<span class="path">{len(entry["waiting"])} waiting</span></li>')
    if not rows:
        return ""
    return f'<div class="priority-projects" id="priority-projects"><h3>Priority projects</h3><ul>{"".join(rows)}</ul></div>'


def attention_errors_html(homes: list[dict[str, object]]) -> list[str]:
    out = []
    for home in homes:
        label = f"{home['label']}: " if len(homes) > 1 else ""
        for error in home["errors"]:  # type: ignore[union-attr]
            out.append(
                f'<div class="warning"><p><strong>{html.escape(label)}{html.escape(str(error["source"]))} '
                f'unavailable</strong> — {html.escape(str(error["message"]))}. Run <code>quarterdeck attention '
                f'--check</code> and report this line.</p></div>'
            )
        decisions = home.get("decisions")
        if isinstance(decisions, dict) and decisions.get("skipped"):
            out.append(f'<p class="source-note">{html.escape(label)}Attention feed not read: '
                       f'{html.escape(str(decisions["skipped"]))}.</p>')
    return out


def view_item_html(item: dict[str, object], multi_home: bool) -> str:
    ask = f' — {html.escape(str(item["ask"]))}' if item.get("ask") else ""
    where = " · ".join(html.escape(str(part)) for part in (
        item["home"] if multi_home else None, item.get("project")) if part)
    return (f'<li>{html.escape(str(item["title"]))}{ask} <span class="chip">{html.escape(str(item["status_label"]))}</span>'
            f'{f" <span class=path>{where}</span>" if where else ""}</li>')


def view_section_html(section: dict[str, object], now: datetime, multi_home: bool) -> str:
    out = [f'<section class="view-section" data-section="{html.escape(str(section["type"]))}">'
           f'<h3>{html.escape(str(section["title"]))}</h3>']
    if section["type"] in {"recent", "done"}:
        items = section["information"]
        lines = [f'<li>{html.escape(str(item["event"]).capitalize())} · ' + attention_item_html(item, now)[4:]
                 for item in items]  # type: ignore[union-attr]
        out.append(f'<p class="source-note">{html.escape(str(section["say"]))}</p><ul>{"".join(lines[:RECENT_LIST_LIMIT])}</ul>')
        if lines[RECENT_LIST_LIMIT:]:
            out.append(f'<details class="board-more"><summary>+{len(lines) - RECENT_LIST_LIMIT} more</summary><ul>'
                       f'{"".join(lines[RECENT_LIST_LIMIT:])}</ul></details>')
    else:
        for key, heading in (("needs_you", "Needs you"), ("information", "For your information")):
            if section[key]:
                out.append(f'<h4>{heading}</h4><ul>' + "".join(
                    view_item_html(item, multi_home) for item in section[key]) + '</ul>')  # type: ignore[union-attr]
        background = section["background"]
        if isinstance(background, dict):
            out.append(f'<details class="board-more"><summary>{html.escape(str(background["say"]))}</summary><ul>'
                       + "".join(view_item_html(item, multi_home) for item in background["items"]) + '</ul></details>')
        if not (section["needs_you"] or section["information"] or background):
            out.append(f'<p class="empty">{html.escape(str(section["say"]))}</p>')
        elif section["shown_above"]:
            out.append(f'<p class="source-note">{section["shown_above"]} shown above</p>')
    out.append("</section>")
    return "".join(out)


def view_editor_html(allow_marks: bool) -> str:
    note = ("" if allow_marks else '<p class="source-note">Saving from this page needs <code>quarterdeck serve '
            '--allow-marks</code>. Otherwise use <code>quarterdeck views save</code>.</p>')
    return (
        '<details class="view-editor board-more" id="view-editor" data-refresh-key="view-editor"><summary>Edit view</summary>'
        '<p><label>Name <input type="text" id="view-name" maxlength="40"></label></p>'
        '<ul id="view-sections"></ul>'
        '<p><label><input type="checkbox" id="view-startup"> Use at startup</label></p>'
        f'<p><button type="button" id="view-save"{"" if allow_marks else " disabled"}>Save view</button> '
        f'<span id="view-status" role="status"></span></p>{note}</details>'
    )


def views_html(view: dict[str, object], homes: list[dict[str, object]], now: datetime, allow_marks: bool) -> str:
    """The Views rail, one panel per saved view with the startup view open, and the editor."""
    built = [entry for entry in view["views"] if isinstance(entry, dict)]  # type: ignore[union-attr]
    startup = str(view["default_view"])
    multi_home = len(homes) > 1
    data = {
        "default": startup, "endpoint": VIEWS_ENDPOINT if allow_marks else "", "titles": VIEW_SECTION_TITLES,
        "views": [{"name": entry["name"], "sections": entry["layout"]} for entry in built],
        "priorities": view["priority_projects"],
    }
    rail = "".join(
        f'<button type="button" data-view-button="{html.escape(str(entry["name"]), quote=True)}" '
        f'aria-pressed="{str(entry["name"] == startup).lower()}">{html.escape(str(entry["name"]))}</button>' for entry in built)
    panels = "".join(
        f'<div class="view-panel" data-view-panel="{html.escape(str(entry["name"]), quote=True)}"'
        f'{"" if entry["name"] == startup else " hidden"}>'
        + "".join(view_section_html(section, now, multi_home) for section in entry["sections"])  # type: ignore[union-attr]
        + "</div>" for entry in built)
    payload = json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    script = f"""<script type="application/json" id="views-data">{payload}</script>
<script>
(function() {{
  const data = JSON.parse(document.getElementById("views-data").textContent);
  const buttons = Array.from(document.querySelectorAll("[data-view-button]"));
  const panels = Array.from(document.querySelectorAll("[data-view-panel]"));
  const list = document.getElementById("view-sections");
  const nameField = document.getElementById("view-name");
  const startup = document.getElementById("view-startup");
  const status = document.getElementById("view-status");
  const save = document.getElementById("view-save");
  const editor = document.getElementById("view-editor");
  const dirty = () => {{ editor.dataset.dirty = "true"; }};
  editor.addEventListener("input", dirty);
  editor.addEventListener("change", dirty);
  let current = data.default;
  try {{
    const kept = sessionStorage.getItem("quarterdeck-view");
    if (kept && data.views.some(entry => entry.name === kept)) current = kept;
  }} catch (_) {{}}
  const key = section => section.type === "project" ? "project:" + section.project : section.type;
  const label = section => section.type === "project" ? section.project : data.titles[section.type];
  function row(section, checked) {{
    const item = document.createElement("li");
    item.dataset.section = JSON.stringify(section);
    const box = document.createElement("input");
    box.type = "checkbox"; box.checked = checked; box.id = "view-section-" + key(section);
    const text = document.createElement("label");
    text.htmlFor = box.id; text.textContent = label(section);
    item.append(box, text);
    for (const [name, move] of [["Up", -1], ["Down", 1]]) {{
      const button = document.createElement("button");
      button.type = "button"; button.textContent = name;
      button.setAttribute("aria-label", "Move " + label(section) + " " + name.toLowerCase());
      button.addEventListener("click", () => {{
        const other = move < 0 ? item.previousElementSibling : item.nextElementSibling;
        if (other) {{
          move < 0 ? list.insertBefore(item, other) : list.insertBefore(other, item);
          dirty();
        }}
      }});
      item.append(button);
    }}
    return item;
  }}
  function edit(entry) {{
    editor.dataset.dirty = "false";
    nameField.value = entry.name;
    startup.checked = entry.name === data.default;
    list.replaceChildren();
    const selected = entry.sections.flatMap(section => section.type === "priorities"
      ? data.priorities.map(project => ({{type: "project", project}})) : [section]);
    const all = [{{type: "waiting"}}, {{type: "recent"}}, {{type: "information"}}, {{type: "done"}},
      ...data.priorities.map(project => ({{type: "project", project}}))];
    for (const other of data.views) for (const section of other.sections) if (section.type === "project") all.push(section);
    const seen = new Set();
    for (const section of selected) if (!seen.has(key(section))) {{ seen.add(key(section)); list.append(row(section, true)); }}
    for (const section of all) if (!seen.has(key(section))) {{ seen.add(key(section)); list.append(row(section, false)); }}
  }}
  function show(name) {{
    current = name;
    for (const button of buttons) button.setAttribute("aria-pressed", String(button.dataset.viewButton === name));
    for (const panel of panels) panel.hidden = panel.dataset.viewPanel !== name;
    edit(data.views.find(entry => entry.name === name));
    try {{ sessionStorage.setItem("quarterdeck-view", name); }} catch (_) {{}}
  }}
  for (const button of buttons) button.addEventListener("click", () => show(button.dataset.viewButton));
  show(current);
  save.addEventListener("click", async () => {{
    const sections = Array.from(list.children).filter(item => item.querySelector("input").checked)
      .map(item => JSON.parse(item.dataset.section));
    if (!sections.length) {{ status.textContent = "Pick at least one section."; return; }}
    const submitted = {{name: nameField.value, sections: sections, default: startup.checked}};
    const controls = [...editor.querySelectorAll("input, button"), ...buttons];
    for (const control of controls) control.disabled = true;
    save.dataset.saving = "true";
    status.textContent = "Saving…";
    try {{
      const response = await fetch(data.endpoint, {{
        method: "POST", headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify(submitted),
      }});
      if (!response.ok) throw new Error((await response.text()).trim() || response.statusText);
      try {{ sessionStorage.setItem("quarterdeck-view", submitted.name.trim()); }} catch (_) {{}}
      status.textContent = "Saved";
      editor.dataset.dirty = "false";
      location.reload();
    }} catch (error) {{
      status.textContent = "Not saved: " + error.message;
    }} finally {{
      for (const control of controls) control.disabled = false;
      save.dataset.saving = "false";
    }}
  }});
}})();
</script>"""
    return (f'<div class="views" id="views"><nav class="views-rail" aria-label="Briefing views">{rail}</nav>'
            f'<div class="views-body">{panels}{view_editor_html(allow_marks)}</div></div>{script}')


def attention_html(view: dict[str, object] | None, allow_marks: bool = False) -> str:
    if view is None:
        return ""
    now = parse_when(str(view["generated"])) or datetime.now(timezone.utc)
    homes = [home for home in view["homes"] if isinstance(home, dict)]  # type: ignore[union-attr]
    out = ['<section class="dashboard-section recent-work" id="attention" aria-label="Waiting and recent work">'
           '<div class="section-head"><div><h2>Waiting on you</h2>'
           "<p>Waiting decisions, then needs you and information, with your priority projects first. "
           f"Work from the last {RECENT_HOURS} hours appears only when there is some. "
           "Dated decisions stay hidden until due.</p></div></div>"]
    out.extend(attention_errors_html(homes))
    if view.get("views"):
        out.append(views_html(view, homes, now, allow_marks))
        out.append("</section>")
        return "".join(out)
    out.append(priorities_panel_html(homes))
    for kind, title in (("decisions", "Waiting decisions"), ("needs_you", "Needs you"), ("informational", "For your information")):
        for index, home in enumerate(homes):
            label = f"{home['label']}: " if len(homes) > 1 else ""
            items = home.get("waiting", {}).get(kind, [])
            scoped = bool(view.get("priority_projects") or view.get("requested_project"))
            background = [item for item in items if kind == "decisions" and not scoped and item.get("relevance") in {"quiet", "stale"}]
            shown = [item for item in items if item not in background]
            if shown:
                out.append(f'<h3>{html.escape(label)}{title}</h3><ul>'
                           + "".join(waiting_item_html(item) for item in shown) + '</ul>')
            if background:
                out.append(f'<details class="board-more" id="recent-background-{index}" data-refresh-key="recent-background:{index}">'
                           f'<summary>{len(background)} background decisions</summary><ul>'
                           + "".join(waiting_item_html(item) for item in background) + '</ul></details>')
    count = sum(int(home.get("more_count", 0)) for home in homes)
    if count:
        out.append(f'<details class="board-more" data-refresh-key="waiting-more"><summary>+{count} more waiting items</summary>')
        for kind, title in (("decisions", "Waiting decisions"), ("needs_you", "Needs you"), ("informational", "For your information")):
            items = [item for home in homes for item in home["waiting_more"][kind]]
            if items:
                out.append(f'<h3>{title}</h3><ul>' + "".join(waiting_item_html(item) for item in items) + '</ul>')
        out.append('</details>')
    recent = [(home, item) for home in homes for item in home.get("recent_work_6h") or []]  # type: ignore[union-attr]
    recent.sort(key=lambda pair: parse_when(str(pair[1]["at"])), reverse=True)
    if recent:
        shown = "".join(
            f'<li>{html.escape(str(home["label"]) + ": " if len(homes) > 1 else "")}{html.escape(str(item["event"]).capitalize())} · '
            + attention_item_html(item, now)[4:] for home, item in recent[:RECENT_LIST_LIMIT]
        )
        more = recent[RECENT_LIST_LIMIT:]
        extra = (f'<details class="board-more" data-refresh-key="recent-6h"><summary>+{len(more)} more</summary><ul>'
                 + "".join(f'<li>{html.escape(str(item["event"]).capitalize())} · ' + attention_item_html(item, now)[4:]
                           for _home, item in more) + "</ul></details>") if more else ""
        out.append(f'<div id="recent-work"><h3>Last {RECENT_HOURS} hours</h3><ul>{shown}</ul>{extra}</div>')
    out.append("</section>")
    return "".join(out)


# --- Captain board -----------------------------------------------------------

BOARD_FILENAME = "captain-board.json"
BOARD_VERSION = 1
BOARD_GROUP_KEYS = ("merge", "approve", "decide", "forward", "read")
UNSORTED_TITLE = "Not yet sorted"


@dataclass
class BoardItem:
    id: str
    group: str
    title: str
    ask: str
    detail: str = ""
    topic: str = ""
    task: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)
    touched_at: datetime | None = None
    project: str = ""
    brief_url: str = ""
    recommendation: str = ""


@dataclass
class CaptainBoard:
    path: Path
    updated_at: datetime | None = None
    items: list[BoardItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    supported: bool = True
    last_active_at: datetime | None = None
    session_started_at: datetime | None = None


@dataclass
class BoardEntry:
    item: BoardItem
    home: str
    number: int = 0
    refresh_key: str = ""
    touched: datetime | None = None
    activity_reason: str = ""
    deferred: str = ""
    band: str = ""
    sweep: bool = False
    key: str = ""  # <home-label-slug>/<task>, the id for relevance and relations
    refs: Set[str] = field(default_factory=frozenset)  # task ids this item waits on
    relevance: str = "current"
    reason: str = ""


@dataclass
class DeskSession:
    """The operator's current (or, when idle, last) session with Firstmate, from `quarterdeck board active`."""
    last_active: datetime
    started: datetime
    idle: bool


@dataclass
class BoardView:
    groups: dict[str, list[BoardEntry]]
    unsorted: list[BoardEntry]
    hidden: int
    warnings: list[str]
    status: list[tuple[str, datetime | None, str]]  # (home label, updated_at, stale reason)
    multiple_homes: bool = False
    bands: dict[str, list[BoardEntry]] = field(default_factory=dict)
    session: DeskSession | None = None
    shared_relevance: bool = False
    desk_groups: list[tuple[str, list[BoardEntry]]] = field(default_factory=list)  # (heading, entries)
    stale: list[BoardEntry] = field(default_factory=list)  # marked no longer relevant; hidden from the bands


def _board_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _board_url(value: object) -> str | None:
    """Return an absolute http(s) URL, or None when the value is not one."""
    url = _board_text(value)
    try:
        parsed = urlparse(url) if url else None
    except ValueError:
        parsed = None
    if not url or parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def parse_board_time(value: object) -> datetime | None:
    """Parse an ISO 8601 time; a value without a zone is read as UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_captain_board(source: str, path: Path) -> CaptainBoard:
    """Parse the board file, keeping every valid item and warning about the rest."""
    board = CaptainBoard(path)
    name = BOARD_FILENAME
    try:
        data = json.loads(source)
    except ValueError as exc:
        board.warnings.append(f"{name} is not valid JSON ({exc}); no board items could be read.")
        return board
    if not isinstance(data, dict):
        board.warnings.append(f"{name} must contain a JSON object; no board items could be read.")
        return board
    version = data.get("version")
    if type(version) is not int or version != BOARD_VERSION:
        board.supported = False
        board.warnings.append(f"{name} has unsupported version {version!r}; expected {BOARD_VERSION}; board items were skipped.")
        return board
    board.updated_at = parse_board_time(data.get("updated_at"))
    if board.updated_at is None:
        board.warnings.append(f"{name} has a missing or invalid updated_at (expected ISO 8601), so its age is unknown.")
    operator = data.get("operator")
    if operator is not None:
        last = parse_board_time(operator.get("last_active_at")) if isinstance(operator, dict) else None
        if last is None:
            board.warnings.append(
                f"{name} operator needs an ISO 8601 last_active_at and was ignored; the desk uses the 48-hour rule."
            )
        else:
            assert isinstance(operator, dict)
            started = parse_board_time(operator.get("session_started_at"))
            if started is None and operator.get("session_started_at") is not None:
                board.warnings.append(f"{name} operator session_started_at must be an ISO 8601 time and was ignored.")
            board.last_active_at = last
            board.session_started_at = min(started, last) if started else last
    items = data.get("items")
    if not isinstance(items, list):
        board.warnings.append(f"{name} needs an items list; no board items could be read.")
        return board
    seen: set[str] = set()
    for index, raw in enumerate(items, start=1):
        where = f"{name} item {index}"
        if not isinstance(raw, dict):
            board.warnings.append(f"{where} is not an object and was skipped.")
            continue
        identifier = _board_text(raw.get("id"))
        if identifier:
            where = f"{name} item {index} ({identifier})"
        problems = []
        if not identifier:
            problems.append("id is missing")
        elif identifier in seen:
            problems.append("id is a duplicate")
        group = raw.get("group")
        if group not in BOARD_GROUP_KEYS:
            problems.append(f"group {group!r} is not one of {', '.join(BOARD_GROUP_KEYS)}")
        title, ask = _board_text(raw.get("title")), _board_text(raw.get("ask"))
        task = _board_text(raw.get("task"))
        if not title:
            problems.append("title is missing")
        if not ask:
            problems.append("ask is missing")
        if not task:
            problems.append("task is missing")
        if problems:
            board.warnings.append(f"{where} was skipped: {'; '.join(problems)}.")
            continue
        assert identifier and title and ask and task
        seen.add(identifier)
        optional: dict[str, object] = {}
        for key in ("detail", "topic", "project"):
            value = raw.get(key)
            if value is None:
                continue
            text = _board_text(value)
            if text is None:
                if not isinstance(value, str):
                    board.warnings.append(f"{where}: {key} must be a string and was ignored.")
                continue
            optional[key] = text
        links: list[tuple[str, str]] = []
        raw_links = raw.get("links")
        if raw_links is not None and not isinstance(raw_links, list):
            board.warnings.append(f"{where}: links must be a list and was ignored.")
            raw_links = []
        for link in raw_links or []:
            label = _board_text(link.get("label")) if isinstance(link, dict) else None
            url = _board_url(link.get("url")) if isinstance(link, dict) else None
            if not label or not url:
                board.warnings.append(f"{where}: a link needs a label and an http(s) url and was ignored.")
                continue
            links.append((label, url))
        touched = raw.get("touched_at")
        if touched is not None:
            optional["touched_at"] = parse_board_time(touched)
            if optional["touched_at"] is None:
                board.warnings.append(f"{where}: touched_at must be an ISO 8601 time and was ignored.")
        brief = raw.get("brief")
        if brief is not None:
            if not isinstance(brief, dict):
                board.warnings.append(f"{where}: brief must be an object with url and recommendation and was ignored.")
            else:
                if brief.get("url") is not None:
                    brief_url = _board_url(brief.get("url"))
                    if brief_url:
                        optional["brief_url"] = brief_url
                    else:
                        board.warnings.append(f"{where}: brief url must be an http(s) url and was ignored.")
                recommendation = brief.get("recommendation")
                if recommendation is not None:
                    if _board_text(recommendation):
                        optional["recommendation"] = _board_text(recommendation)
                    elif not isinstance(recommendation, str):
                        board.warnings.append(f"{where}: brief recommendation must be a string and was ignored.")
        board.items.append(BoardItem(
            id=identifier, group=str(group), title=title, ask=ask, task=task, links=links,
            **optional,  # type: ignore[arg-type]
        ))
    return board


def read_captain_board(data_dir: Path) -> CaptainBoard | None:
    """Read <data>/captain-board.json; an absent file means there is no board."""
    path = data_dir / BOARD_FILENAME
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        board = CaptainBoard(path)
        board.warnings.append(f"{BOARD_FILENAME} could not be read ({exc.__class__.__name__}); no board items could be shown.")
        return board
    return parse_captain_board(source, path)


def needs_captain(item: BacklogItem) -> bool:
    """True while the backlog still shows the item open and waiting on the captain."""
    if item.done or is_cancelled_state(item.state):
        return False
    return (
        (item.held and item.hold_kind == "captain")
        or item.review_ready
        or normalized_state(item.blocked_by) == "captain"
    )


def unsorted_note(item: BacklogItem) -> str:
    if item.hold_reason:
        return item.hold_reason
    if item.review_ready:
        return "Review-ready pull request"
    return f"Blocked, waiting on {item.blocked_by}" if item.blocked_by else "Waiting on the captain"


BOARD_BANDS = (
    ("desk", "On your desk", "What you touched in the last 2 days."),
    ("week", "This week", "Touched in the last 7 days."),
    ("parked", "Parked", "Older, deferred, or never touched."),
)
BOARD_BAND_KEYS = tuple(key for key, _title, _description in BOARD_BANDS)
DESK_HOURS = 48
WEEK_DAYS = 7
SWEEP_DAYS = 14
DESK_LIMIT = 5
SESSION_IDLE = timedelta(hours=4)  # a longer gap between operator messages ends the session
GROUP_RANK = {key: index for index, key in enumerate((*BOARD_GROUP_KEYS, "unsorted"))}
GROUP_SHORT = {"merge": "Merge", "approve": "Approve", "decide": "Decide", "forward": "Forward", "read": "Read", "unsorted": UNSORTED_TITLE}
CAPTAIN_NOTE_RE = re.compile(r"\bCaptain (\d{4}-\d{2}-\d{2})\b")
HOLD_SET_RE = re.compile(r"\bCaptain hold set:\s*(\d{4}-\d{2}-\d{2}[T ][^\s]+)")
DEFERRED_HOLD_RE = re.compile(
    r"(?i)\b(?:deferred\s+until|hold\s+off\b[^\n!?;]*?\buntil|(?:waits?|waiting)\s+until)\s+(?P<until>\S+(?:\s+\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?)"
)
HOLD_DEPENDENCY_RE = re.compile(r"(?i)\b(?:(?:waits|waiting)\s+(?:on|for)|after)\s+(\S+)")


def backlog_recency(item: BacklogItem, now: datetime, task_ids: Set[str]) -> tuple[datetime | None, str]:
    """Latest captain-dated note or hold-set time from the backlog, and why the item is deferred, if it is."""
    times: list[datetime] = []
    deferred = ""
    for match in CAPTAIN_NOTE_RE.finditer(item.raw):
        when = parse_when(match.group(1))
        if when is None:
            continue
        if when.date() <= now.date():
            times.append(when)
    for match in HOLD_SET_RE.finditer(item.raw):
        when = parse_board_time(match.group(1))
        if when is not None and when <= now:
            times.append(when)
    for key in ("hold-set", "hold_set"):
        when = parse_board_time(item.attrs.get(key, ""))
        if when is not None and when <= now:
            times.append(when)
    for key in ("hold-until", "hold_until", "until"):
        until = parse_board_time(item.attrs.get(key, ""))
        if until is not None and until > now:
            deferred = f"deferred until {until.date().isoformat()}"
            break
    if not deferred and (
        any(
            (until := parse_board_time(match.group("until").rstrip(".,;:!?)]}\"'`"))) is None or until > now
            for match in DEFERRED_HOLD_RE.finditer(item.hold_reason)
        )
        or any(
            token != item.id and token in task_ids
            for value in [item.blocked_by, *(item.attrs.get(key, "") for key in (
                "blocked_by", "blocked-by", "waiting_on", "waiting-on", "waiting_for", "waiting-for",
            ))]
            for token in [value.rstrip(".,;:!?)]}\"'`")]
        )
        or any(
            token != item.id and token in task_ids
            for match in HOLD_DEPENDENCY_RE.finditer(item.hold_reason)
            for token in [match.group(1).rstrip(".,;:!?)]}\"'`")]
        )
    ):
        deferred = "deferred by its hold note"
    return (max(times) if times else None), deferred


def rank_entry(
    entry: BoardEntry, backlog_item: BacklogItem | None, now: datetime, task_ids: Set[str],
    session: DeskSession | None = None, decision: dict[str, object] | None = None,
) -> None:
    """Set the entry's touched time, deferral, band, and sweep flag from the board and backlog.

    With a recorded operator session the desk holds what was raised or engaged in that session;
    without one it falls back to the 48-hour rule.
    """
    times = [entry.item.touched_at] if entry.item.touched_at else []
    if backlog_item is not None:
        fallback, entry.deferred = backlog_recency(backlog_item, now, task_ids)
        if fallback is not None:
            times.append(fallback)
        if not entry.item.project and backlog_item.project:
            entry.item = replace(entry.item, project=backlog_item.project)
        entry.refs = frozenset(dependency_tokens(backlog_item))
    entry.touched = min(max(times), now) if times else None
    age = (now - entry.touched).total_seconds() if entry.touched else None
    if session is not None:
        conversation_touch = min(entry.item.touched_at, now) if entry.item.touched_at else None
        on_desk = conversation_touch is not None and conversation_touch >= session.started
    else:
        on_desk = age is not None and age <= DESK_HOURS * 3600
    if on_desk and not entry.deferred:
        entry.band = "desk"
        if session is not None:
            entry.touched = conversation_touch
    elif age is not None and not entry.deferred and age <= WEEK_DAYS * 86400:
        entry.band = "week"
    else:
        entry.band = "parked"
        entry.sweep = age is not None and age > SWEEP_DAYS * 86400

    if decision is not None:
        why = decision["relevance"]
        entry.band = "desk" if why in RELEVANT_REASONS else "parked"
        entry.deferred = "deferred" if why == "deferred" else ""
        entry.sweep = False
        if entry.band == "desk":
            entry.activity_reason = str(why)
            activity = parse_board_time(decision.get("activity_at"))
            entry.touched = max((stamp for stamp in (entry.touched, activity) if stamp is not None), default=None)


def desk_session(boards: list[CaptainBoard], now: datetime) -> DeskSession | None:
    """The newest operator activity any board records; the operator is one person across homes."""
    recorded = [board for board in boards if board.supported and board.last_active_at is not None]
    if not recorded:
        return None
    board = max(recorded, key=lambda b: b.last_active_at)  # type: ignore[arg-type,return-value]
    assert board.last_active_at is not None and board.session_started_at is not None
    last = min(board.last_active_at, now)
    return DeskSession(last, min(board.session_started_at, last), now - last > SESSION_IDLE)


def desk_bucket(touched: datetime, session: DeskSession, now: datetime) -> tuple[int, str]:
    """Rank and heading for a desk item, by when it was raised relative to the operator's messages."""
    if touched >= session.last_active:
        return 0, "Open items from your last message"
    age = now - touched
    if age < timedelta(hours=1):
        return 1, ""  # "Still waiting on you from N minutes ago", worded from the group's newest item
    if age < timedelta(hours=2):
        return 2, "Open items from an hour ago"
    local_touched, local_now = touched.astimezone(), now.astimezone()
    days = (local_now.date() - local_touched.date()).days
    if days <= 0:
        hour = local_touched.hour
        part, rank = ("morning", 5) if hour < 12 else ("afternoon", 4) if hour < 17 else ("evening", 3)
        return rank, f"Open items from this {part}"
    if days == 1:
        return 6, "Open items from yesterday"
    return 7, "Open items from earlier this session"


def desk_groups(
    entries: list[BoardEntry], session: DeskSession | None, now: datetime, shared_relevance: bool = False,
) -> list[tuple[str, list[BoardEntry]]]:
    """Split the desk band into headed groups: one for the 48-hour rule or an idle session, else by message recency."""
    if shared_relevance or session is None or session.idle:
        return [("", entries)] if entries else []
    buckets: dict[tuple[int, str], list[BoardEntry]] = {}
    for entry in entries:
        assert entry.touched is not None
        buckets.setdefault(desk_bucket(entry.touched, session, now), []).append(entry)
    groups = []
    for (_rank, heading), members in sorted(buckets.items()):
        if not heading:
            newest = max(e.touched for e in members if e.touched is not None)
            minutes = max(1, int((now - newest).total_seconds() // 60))
            heading = f"Still waiting on you from {minutes} minute{'s' if minutes != 1 else ''} ago"
        groups.append((heading, members))
    return groups


def band_entries(entries: list[BoardEntry]) -> dict[str, list[BoardEntry]]:
    """Group entries into bands, ordered by group, then newest touched first."""
    bands: dict[str, list[BoardEntry]] = {key: [] for key in BOARD_BAND_KEYS}
    for entry in entries:
        bands[entry.band].append(entry)
    for band in bands.values():
        band.sort(key=lambda e: (GROUP_RANK[e.item.group], -(e.touched.timestamp() if e.touched else float("-inf"))))
    return bands


def settle_relevance(entry: BoardEntry, state: ReviewState, view: BoardView) -> bool:
    """Apply the call's recorded relevance; True when it is stale and was set aside from the desk."""
    entry.relevance, entry.reason = state.relevance_of("call", entry.key)
    if entry.relevance == "stale":
        view.stale.append(entry)
        return True
    return False


def task_owner(snapshot: HomeSnapshot, task: str) -> tuple[HomeSnapshot, str]:
    route, separator, rest = task.partition("/")
    child = next((c for c in snapshot.children if separator and c.spec.route_id == route), None)
    return task_owner(child, rest) if child is not None else (snapshot, task)


def task_key(snapshot: HomeSnapshot, task: str, snapshots: list[HomeSnapshot]) -> str:
    _prefix, separator, rest = task.strip().partition("/")
    owner = next((s for s in snapshots if separator and report_reference(s.spec.label, rest) == task.strip()), None)
    if owner is not None:
        return report_reference(owner.spec.label, rest)
    owner, task = task_owner(snapshot, task.strip())
    return report_reference(owner.spec.label, task)


def board_view(
    homes: list[HomeSnapshot], now: datetime | None = None, *, fallback: bool = False,
    decisions: dict[str, dict[str, object]] | None = None,
) -> BoardView | None:
    """Merge the homes' boards, drop settled items, add backlog items the boards miss, and rank them by recency."""
    now = now or datetime.now(timezone.utc)
    snapshots = all_snapshots(homes)
    boarded = [home for home in snapshots if home.board is not None]
    if not boarded and not fallback:
        return None
    view = BoardView({key: [] for key in BOARD_GROUP_KEYS}, [], 0, [], [], shared_relevance=bool(decisions))
    view.session = session = desk_session([home.board for home in boarded if home.board is not None], now)
    state = next((home.review_state for home in snapshots if home.review_state is not None), None) or ReviewState()
    cache: dict[int, dict[str, BacklogItem]] = {}

    def items_of(snapshot: HomeSnapshot) -> dict[str, BacklogItem]:
        if id(snapshot) not in cache:
            cache[id(snapshot)] = {
                item.id: item for item in backlog_items(snapshot.backlog_markdown or "", snapshot)
            } if snapshot.backlog_markdown is not None and not snapshot.error else {}
        return cache[id(snapshot)]

    referenced: set[tuple[int, str]] = set()
    for home in boarded:
        board = home.board
        assert board is not None
        label = home.spec.label
        view.warnings.extend(f"{label}: {warning}" for warning in board.warnings)
        if not board.supported:
            continue
        stale = ""
        if board.updated_at is None:
            stale = "its update time is unknown"
        elif home.backlog_changed and board.updated_at < home.backlog_changed:
            stale = "the backlog changed after the board was last updated"
        view.status.append((label, board.updated_at, stale))
        if home.error:
            view.warnings.append(f"Could not check board items for {label}: the backlog could not be read.")
        for item in board.items:
            backlog_item: BacklogItem | None = None
            target, task_id = task_owner(home, item.task)
            if not home.error:
                if target.error:
                    view.warnings.append(
                        f"Could not check {item.id}: the backlog could not be read for {target.spec.label}; the item remains visible."
                    )
                else:
                    referenced.add((id(target), task_id))
                    backlog_item = items_of(target).get(task_id)
                    if backlog_item is None or not needs_captain(backlog_item):
                        view.hidden += 1
                        continue
                    pr_url = backlog_item.pr_url
                    if (
                        item.group == "merge"
                        and PR_URL_RE.fullmatch(pr_url or "")
                        and not any(url == pr_url for _link_label, url in item.links)
                    ):
                        item = replace(item, links=[*item.links, ("Pull request", pr_url)])
            entry = BoardEntry(item, label, refresh_key=f"{board.path}:{item.id}", key=report_reference(target.spec.label, task_id))
            rank_entry(entry, backlog_item, now, items_of(target).keys(), session, (decisions or {}).get(entry.key))
            if entry.deferred and (decisions or {}).get(entry.key):
                continue
            entry.refs = frozenset(task_key(target, ref, snapshots) for ref in entry.refs)
            if settle_relevance(entry, state, view):
                continue
            view.groups[item.group].append(entry)
    for snapshot in snapshots:
        for backlog_item in items_of(snapshot).values():
            if (id(snapshot), backlog_item.id) in referenced or not needs_captain(backlog_item):
                continue
            links = [("Pull request", backlog_item.pr_url)] if PR_URL_RE.fullmatch(backlog_item.pr_url or "") else []
            entry = BoardEntry(
                BoardItem(
                    id=backlog_item.id, group="unsorted", title=backlog_item.title, ask=unsorted_note(backlog_item),
                    detail=f"Backlog item `{backlog_item.id}`", task=backlog_item.id, links=links,
                ), snapshot.spec.label,
                refresh_key=f"{snapshot.backlog_path}:{backlog_item.id}",
                key=report_reference(snapshot.spec.label, backlog_item.id),
            )
            rank_entry(entry, backlog_item, now, items_of(snapshot).keys(), session, (decisions or {}).get(entry.key))
            if entry.deferred and (decisions or {}).get(entry.key):
                continue
            entry.refs = frozenset(task_key(snapshot, ref, snapshots) for ref in entry.refs)
            if settle_relevance(entry, state, view):
                continue
            view.unsorted.append(entry)
    view.multiple_homes = len({entry.home for entries in view.groups.values() for entry in entries} | {e.home for e in view.unsorted}) > 1
    view.bands = band_entries([*(e for key in BOARD_GROUP_KEYS for e in view.groups[key]), *view.unsorted])
    view.desk_groups = desk_groups(view.bands["desk"], session, now, view.shared_relevance)
    view.bands["desk"] = [entry for _heading, entries in view.desk_groups for entry in entries]
    number = 0
    for band in BOARD_BAND_KEYS:
        for entry in view.bands[band]:
            number += 1
            entry.number = number
    view.stale.sort(key=lambda e: -(e.touched.timestamp() if e.touched else float("-inf")))
    for entry in view.stale:
        number += 1
        entry.number = number
    return view


def board_age_text(updated: datetime | None, now: datetime) -> str:
    if updated is None:
        return "at an unknown time"
    if updated > now:
        return f"{elapsed_text(now, updated)} ahead (clock skew)"
    if (now - updated).total_seconds() < 120:
        return "just now"
    return f"{elapsed_text(updated, now)} ago"


def board_status_html(view: BoardView, now: datetime) -> str:
    lines = []
    for label, updated, stale in view.status:
        prefix = f"{html.escape(label)}: " if len(view.status) > 1 else ""
        stamp = f" ({updated.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})" if updated else ""
        flag = f' <strong class="stale">⚠ Board may be out of date: {html.escape(stale)}.</strong>' if stale else ""
        lines.append(f'<p class="generated">{prefix}Board updated {html.escape(board_age_text(updated, now))}{stamp}{flag}</p>')
    return "".join(lines)


def touched_text(entry: BoardEntry, now: datetime) -> str:
    if entry.touched is None:
        return "touched time unknown"
    label = "worked on" if entry.activity_reason == "recent_work" else "activity" if entry.activity_reason else "touched"
    return f"{label} {elapsed_text(entry.touched, now)} ago"


def board_entry_html(
    entry: BoardEntry, show_home: bool, now: datetime, reactions: ReactionContext | None = None,
) -> str:
    item = entry.item
    chips = [f'<span class="chip chip-group">{html.escape(GROUP_SHORT[item.group])}</span>']
    if item.project:
        chips.append(f'<span class="chip">{html.escape(item.project)}</span>')
    if item.topic:
        chips.append(f'<span class="chip">{html.escape(item.topic)}</span>')
    if show_home:
        chips.append(f'<span class="chip board-home">{html.escape(entry.home)}</span>')
    chips.append(f'<span class="chip chip-age">{html.escape(touched_text(entry, now))}</span>')
    if entry.deferred:
        chips.append(f'<span class="chip chip-deferred">{html.escape(entry.deferred)}</span>')
    if entry.sweep:
        chips.append('<span class="chip chip-sweep">keep, defer or drop?</span>')
    brief = []
    if item.recommendation:
        brief.append(
            f'<span class="chip chip-rec"><span class="rec-label">Recommend</span> {html.escape(item.recommendation)}</span>'
        )
    if item.brief_url:
        brief.append(f'<a class="brief-link" href="{html.escape(item.brief_url, quote=True)}">Open brief</a>')
    parts = [
        f'<p class="board-ask">{html.escape(item.ask)}</p>',
    ]
    if item.detail:
        detail_warning = ""
        try:
            detail = render_markdown(item.detail)
        except ValueError:
            detail_warning = (
                f'<div class="warning"><p>{html.escape(BOARD_FILENAME)} item {html.escape(item.id)}: '
                "detail contains a malformed Markdown URL and was shown as text.</p></div>"
            )
            detail = html.escape(item.detail)
        parts.append(f'{detail_warning}<div class="body">{detail}</div>')
    links = ([("Decision brief", item.brief_url)] if item.brief_url else []) + [
        (label, url) for label, url in item.links if url != item.brief_url
    ]
    if links:
        rendered = "".join(
            f'<li><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a> '
            f'<span class="path">{html.escape(url)}</span></li>'
            for label, url in links
        )
        parts.append(f'<ul class="board-links">{rendered}</ul>')
    brief_row = f'<span class="board-brief">{"".join(brief)}</span>' if brief else ""
    reaction = reaction_html(reactions, "call", entry.key)
    if reactions is not None and (reactions.latest.get(("call", entry.key))):
        chips.append(
            f'<span class="chip chip-reaction" data-reaction-latest="{html.escape("call:" + entry.key, quote=True)}">'
            f'{html.escape(reaction_latest_text(reactions.latest[("call", entry.key)], now).removeprefix("Latest reaction: "))}</span>'
        )
    parts.append(reaction)
    return (
        f'<details class="board-item" id="item-{entry.number}" '
        f'data-refresh-key="{html.escape(entry.refresh_key, quote=True)}"><summary>'
        f'<span class="board-num" aria-label="Number {entry.number}">{entry.number}</span>'
        f'<span class="board-text"><span class="board-title">{html.escape(item.title)}</span>'
        f'<span class="board-line">{html.escape(item.ask)}</span>'
        f'<span class="board-chips">{"".join(chips)}</span>{brief_row}</span></summary>'
        f'<div class="board-body">{"".join(parts)}</div></details>'
    )


def band_counts(entries: list[BoardEntry]) -> str:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.item.group] = counts.get(entry.item.group, 0) + 1
    return " · ".join(
        f"{counts[key]} {GROUP_SHORT[key].lower()}" for key in (*BOARD_GROUP_KEYS, "unsorted") if counts.get(key)
    )


def session_desk_html(
    view: BoardView, now: datetime, show_home: bool, reactions: ReactionContext | None = None,
) -> str:
    """The desk for a recorded operator session: grouped by message recency, or the last session when idle."""
    session = view.session
    assert session is not None
    entries = view.bands["desk"]
    if view.shared_relevance:
        title = "On your desk"
        description = f"Open decisions raised, acted on or worked on in the last {DEFAULT_RECENT_DAYS} days."
        empty = "No decisions are in play. Background decisions are below."
    elif session.idle:
        title = "Open items from our last session"
        description = (
            f"Your last message to Firstmate was {elapsed_text(session.last_active, now)} ago. "
            "Everything else still open follows."
        )
        empty = "Nothing from our last session is still waiting on you. Earlier work is below."
    else:
        title = "On your desk"
        description = f"Raised or discussed in this session with Firstmate, which began {elapsed_text(session.started, now)} ago."
        empty = "Nothing from this session is waiting on you. Earlier work is below."
    head = (
        f'<div class="section-head"><div><h2>{html.escape(title)}</h2><p>{html.escape(description)}</p></div>'
        f'<span class="count">{len(entries)}</span></div>'
    )
    body = []
    for heading, members in view.desk_groups:
        if heading:
            body.append(f'<h3 class="desk-group">{html.escape(heading)}</h3>')
        body.extend(board_entry_html(entry, show_home, now, reactions) for entry in members)
    if not entries:
        body.append(f'<p class="empty">{html.escape(empty)}</p>')
    return f'<div class="dashboard-section board-band" id="board-desk">{head}{"".join(body)}</div>'


def stale_calls_html(view: BoardView, now: datetime, reactions: ReactionContext | None = None) -> str:
    if not view.stale:
        return ""
    show_home = view.multiple_homes
    return (
        '<details class="dashboard-section board-band" id="board-stale" data-refresh-key="board-band:stale">'
        '<summary><div class="section-head"><div><h2>Marked stale</h2>'
        '<p>One-off or already decided; Firstmate marked these no longer relevant, so they are off your desk. '
        'Bring one back with <code>quarterdeck calls set-relevance TASK current</code>.</p></div>'
        f'<span class="count">{len(view.stale)}</span></div></summary>'
        + "".join(
            board_entry_html(entry, show_home, now, reactions)
            + (f'<p class="source-note">Marked stale: {html.escape(entry.reason)}</p>' if entry.reason else "")
            for entry in view.stale
        )
        + "</details>"
    )


def board_html(
    view: BoardView, now: datetime, show_all: bool = False, related: str = "",
    reactions: ReactionContext | None = None, attention: dict[str, object] | None = None,
) -> str:
    if attention and (attention.get("priority_projects") or attention.get("requested_project")):
        entries = {entry.key: entry for items in view.bands.values() for entry in items}
        entries.update((entry.key, entry) for entry in view.stale)
        out = ['<section class="board" id="board" aria-label="Waiting on you">']
        out.extend(f'<div class="warning"><p>{html.escape(warning)}</p></div>' for warning in view.warnings)
        for field in ("waiting", "waiting_more"):
            count = sum(1 for home in attention["homes"] for items in home[field].values()
                        for item in items if item["key"] in entries)
            if field == "waiting_more":
                if not count:
                    continue
                out.append(f'<details class="board-more" data-refresh-key="board-priority-more"><summary>+{count} more waiting items</summary>')
            for kind, title in (("decisions", "Waiting decisions"), ("needs_you", "Needs you"), ("informational", "For your information")):
                items = [entries[item["key"]] for home in attention["homes"] for item in home[field][kind] if item["key"] in entries]
                if items:
                    out.append(f'<div class="dashboard-section"><h2>{title}</h2>'
                               + "".join(board_entry_html(entry, view.multiple_homes, now, reactions) for entry in items) + '</div>')
            if field == "waiting_more":
                out.append('</details>')
        out.append(related)
        out.append('</section>')
        return "".join(out)
    out = ['<section class="board" id="board" aria-label="Waiting on you">']
    out.extend(f'<div class="warning"><p>{html.escape(warning)}</p></div>' for warning in view.warnings)
    if view.hidden:
        out.append(f'<p class="source-note">{view.hidden} board item{"s" if view.hidden != 1 else ""} already settled in the backlog and hidden.</p>')
    if not any(view.bands.values()):
        out.append('<p class="empty">Nothing is waiting on you right now.</p>')
        out.append(related)
        out.append(stale_calls_html(view, now, reactions))
        out.append("</section>")
        return "".join(out)
    show_home = view.multiple_homes
    for key, title, description in BOARD_BANDS:
        if key == "desk" and view.shared_relevance:
            description = f"Open decisions raised, acted on or worked on in the last {DEFAULT_RECENT_DAYS} days."
        entries = view.bands[key]
        counts = band_counts(entries)
        head = (
            f'<div class="section-head"><div><h2>{html.escape(title)}</h2>'
            f'<p>{html.escape(description)}{" " + html.escape(counts) + "." if counts and key != "desk" else ""}</p></div>'
            f'<span class="count">{len(entries)}</span></div>'
        )
        if key == "desk" and view.session is not None:
            out.append(session_desk_html(view, now, show_home, reactions))
            out.append(related)
            continue
        if key == "desk":
            shown = entries if show_all else entries[:DESK_LIMIT]
            more = [] if show_all else entries[DESK_LIMIT:]
            body = "".join(board_entry_html(entry, show_home, now, reactions) for entry in shown)
            if not entries:
                body = ('<p class="empty">No decisions are in play. Background decisions are below.</p>' if view.shared_relevance
                        else '<p class="empty">Nothing touched in the last 2 days. Older items are below.</p>')
            if more:
                body += (
                    '<details class="board-more" id="board-desk-more" data-refresh-key="board-band:desk-more">'
                    f'<summary>+{len(more)} more {"decisions in play" if view.shared_relevance else "you touched recently"}</summary>'
                    f'{"".join(board_entry_html(entry, show_home, now, reactions) for entry in more)}</details>'
                )
            out.append(f'<div class="dashboard-section board-band" id="board-desk">{head}{body}</div>')
            out.append(related)
            continue
        if not entries:
            continue
        sweep = sum(entry.sweep for entry in entries)
        sweep_line = (
            f'<p class="sweep">Weekly sweep: {sweep} parked item{"s" if sweep != 1 else ""} untouched for over '
            f"{SWEEP_DAYS} days. Keep, defer or drop?</p>"
        ) if sweep else ""
        # After a long idle gap the page reads like a catch-up digest, so This week starts open.
        start_open = show_all or (key == "week" and view.session is not None and view.session.idle)
        out.append(
            f'<details class="dashboard-section board-band" id="board-{key}" data-refresh-key="board-band:{key}"'
            f'{" open" if start_open else ""}><summary>{head}</summary>{sweep_line}'
            f'{"".join(board_entry_html(entry, show_home, now, reactions) for entry in entries)}</details>'
        )
    out.append(stale_calls_html(view, now, reactions))
    out.append("</section>")
    return "".join(out)


# --- Related to this session ---------------------------------------------------

RELATED_WEIGHTS = {"relation": 10, "link": 5, "project": 3, "topic": 2}


@dataclass
class RelatedItem:
    kind: str  # "report" or "call"
    key: str
    title: str
    home: str
    why: list[str]
    score: int
    when: datetime | None = None
    report: Report | None = None
    snapshot: HomeSnapshot | None = None
    entry: BoardEntry | None = None


def dependency_tokens(item: BacklogItem) -> set[str]:
    """Task ids a backlog item names as what it waits on."""
    values = [item.blocked_by, *(item.attrs.get(key, "") for key in (
        "blocked_by", "blocked-by", "waiting_on", "waiting-on", "waiting_for", "waiting-for",
    ))]
    values.extend(match.group(1) for match in HOLD_DEPENDENCY_RE.finditer(item.hold_reason))
    return {value.rstrip(".,;:!?)]}\"'`").strip() for value in values if value.strip()} - {item.id}


def related_items(
    homes: list[HomeSnapshot], view: BoardView | None, *, decisions: dict[str, dict[str, object]] | None = None,
) -> list[RelatedItem]:
    """Reports and deferred or held captain calls that relate to the active session, most related first.

    The session is the desk's items plus in-flight backlog work. A candidate relates through an explicit relation
    set with `quarterdeck reports relate` / `calls relate`, a task link (the report of, a dependency on or from),
    a shared project, or a shared topic.
    """
    snapshots = all_snapshots(homes)
    if view is None:
        view = board_view(homes, fallback=True, decisions=decisions)
    context_tasks: dict[str, str] = {}
    context_projects: dict[str, str] = {}
    context_topics: dict[str, str] = {}
    context_refs: dict[str, str] = {}
    topics: dict[str, str] = {}
    backlog: dict[int, list[BacklogItem]] = {}
    for snapshot in snapshots:
        backlog[id(snapshot)] = backlog_items(snapshot.backlog_markdown or "", snapshot) if (
            snapshot.backlog_markdown is not None and not snapshot.error
        ) else []
        for item in backlog[id(snapshot)]:
            if is_in_flight_state(item.state) and not item.done and not is_cancelled_state(item.state):
                context_tasks.setdefault(task_key(snapshot, item.id, snapshots), item.title)
                if item.project:
                    context_projects.setdefault(item.project, item.title)
                for token in dependency_tokens(item):
                    context_refs.setdefault(task_key(snapshot, token, snapshots), item.title)
    assert view is not None
    every = [*(e for key in BOARD_GROUP_KEYS for e in view.groups[key]), *view.unsorted]
    for entry in every:
        if entry.item.topic:
            topics.setdefault(entry.key, entry.item.topic)
    for entry in view.bands["desk"]:
        task = entry.key
        context_tasks.setdefault(task, entry.item.title)
        if entry.item.project:
            context_projects.setdefault(entry.item.project, entry.item.title)
        if entry.item.topic:
            context_topics.setdefault(entry.item.topic, entry.item.title)
        for token in entry.refs:
            context_refs.setdefault(token, entry.item.title)
    candidates = [*view.bands["week"], *view.bands["parked"]]
    state = next((s.review_state for s in snapshots if s.review_state is not None), None) or ReviewState()

    def evaluate(task: str, project: str, topic: str, refs: set[str], explicit: list[str], own: str) -> tuple[int, list[str]]:
        score, why = 0, []
        for target in explicit:
            if target in context_tasks:
                score += RELATED_WEIGHTS["relation"]
                why.append(f"linked to {context_tasks[target]} by Firstmate")
        if task and task in context_refs and task not in context_tasks:
            score += RELATED_WEIGHTS["link"]
            why.append(f"{context_refs[task]} waits on it")
        for token in sorted(refs & set(context_tasks)):
            score += RELATED_WEIGHTS["link"]
            why.append(f"waits on {context_tasks[token]}")
        if own and own in context_tasks:
            score += RELATED_WEIGHTS["link"]
            why.append(f"report for {context_tasks[own]}")
        if project and project in context_projects:
            score += RELATED_WEIGHTS["project"]
            why.append(f"same project: {project}")
        if topic and topic in context_topics:
            score += RELATED_WEIGHTS["topic"]
            why.append(f"same topic: {topic}")
        return score, why

    found: list[RelatedItem] = []
    for entry in candidates:
        task = entry.key
        if task in context_tasks:
            continue  # already on the desk or in flight
        owner = next(s for s in snapshots if report_reference(s.spec.label, task.partition("/")[2]) == task)
        score, why = evaluate(
            task, entry.item.project, entry.item.topic, entry.refs,
            [task_key(owner, target, snapshots) for target in state.relations.get(("call", entry.key), [])], "",
        )
        if score:
            found.append(RelatedItem("call", entry.key, entry.item.title, entry.home, why, score, entry.touched, entry=entry))
    for snapshot in snapshots:
        for report in snapshot.reports:
            if not report.waiting:
                continue
            directory = report.path.parent.name
            item = report_backlog_item(backlog[id(snapshot)], report)
            task = task_key(snapshot, item.id if item else directory, snapshots)
            score, why = evaluate(
                "", item.project if item else "", topics.get(task, ""),
                {task_key(snapshot, ref, snapshots) for ref in dependency_tokens(item)} if item else set(),
                [task_key(snapshot, target, snapshots) for target in report.related_to], task,
            )
            if score:
                found.append(RelatedItem(
                    "report", report.report_id, report.title, snapshot.spec.label, why, score, report.modified,
                    report=report, snapshot=snapshot,
                ))
    found.sort(key=lambda r: (-r.score, -(r.when.timestamp() if r.when else float("-inf")), r.title))
    return found


def related_html(
    items: list[RelatedItem], now: datetime, can_mark: bool = False, ambiguous_ids: set[str] | None = None,
    report_resolution_incomplete: bool = False, reactions: ReactionContext | None = None,
) -> str:
    """A collapsed section: never part of the desk, there for when the session turns to something older."""
    ambiguous_ids = ambiguous_ids or set()
    cards = []
    for item in items:
        chips = "".join(f'<span class="chip">{html.escape(reason)}</span>' for reason in item.why)
        label = "Report" if item.kind == "report" else "Captain call"
        when = f" · {html.escape(elapsed_text(item.when, now))} ago" if item.when else ""
        body, tail = "", ""
        if item.report is not None and item.snapshot is not None:
            report = item.report
            if report.recommendation:
                body = f'<p><strong>Recommendation:</strong> {html.escape(report.recommendation)}</p>'
            page = source_page_filename("report", item.snapshot.spec.home or Path("."), report.path)
            tail = f'<p class="links"><a href="{html.escape(page, quote=True)}">Read report</a></p>'
            if can_mark:
                tail += mark_buttons(
                    report.report_id, ["mark", "discard"], report.fingerprint,
                    report_mark_unavailable_reason(report.report_id, ambiguous_ids, report_resolution_incomplete),
                )
            tail += reaction_html(
                reactions, "report", report.report_id,
                not report_mark_unavailable_reason(report.report_id, ambiguous_ids, report_resolution_incomplete),
            )
        elif item.entry is not None:
            entry = item.entry
            body = f'<p>{html.escape(entry.item.ask)}</p>'
            if entry.deferred:
                chips += f'<span class="chip chip-deferred">{html.escape(entry.deferred)}</span>'
            tail = reaction_html(reactions, "call", entry.key)
        cards.append(
            f'<article class="card"><p class="eyebrow">{label} · {html.escape(item.home)}{when}</p>'
            f'<h3>{html.escape(item.title)}</h3>{body}<div class="board-chips">{chips}</div>{tail}</article>'
        )
    content = "".join(cards) or (
        '<p class="empty">Nothing older relates to what is on your desk or in flight right now.</p>'
    )
    return (
        '<details class="dashboard-section related" id="related" data-refresh-key="related">'
        f'<summary><h2>Related to this session</h2><span class="count">{len(items)}</span></summary>'
        '<p>Older reports and deferred or held calls that connect to what you are working on now, most related '
        f'first. They are not on your desk.</p><div class="cards">{content}</div></details>'
    )


# --- Board writer ------------------------------------------------------------
# `quarterdeck board` is the one command that writes into a home: it changes
# only <data>/captain-board.json, the file Firstmate already owns and
# Quarterdeck already reads. render and serve never call it.


def board_time_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def board_file_path(args: argparse.Namespace) -> Path:
    raw_home = args.home or os.environ.get("FM_HOME")
    if args.data_dir:
        data_dir = Path(args.data_dir).expanduser().resolve()
    elif raw_home:
        override = os.environ.get("FM_DATA_OVERRIDE")
        data_dir = Path(override).expanduser().resolve() if override else Path(raw_home).expanduser().resolve() / "data"
    else:
        raise ValueError("a Firstmate home is required; pass --home or --data-dir, or set FM_HOME")
    if not data_dir.is_dir():
        raise ValueError(f"board data directory is not a directory: {data_dir}")
    path = data_dir / BOARD_FILENAME
    if path.is_symlink():
        raise ValueError(f"refusing symbolic-link board file: {path}")
    return path


@contextmanager
def board_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on the board's directory, so no lock file is added to the home."""
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def load_board_document(path: Path) -> dict[str, object]:
    """Read the raw board for editing; refuse anything a write could damage."""
    try:
        source = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version": BOARD_VERSION, "items": []}
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc
    try:
        data = json.loads(source)
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON ({exc}); fix or remove it before writing") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != BOARD_VERSION:
        raise ValueError(f"{path} is not a version {BOARD_VERSION} board; refusing to overwrite it")
    if not isinstance(data.get("items"), list):
        raise ValueError(f"{path} has no items list; refusing to overwrite it")
    return data


def write_board_document(path: Path, data: dict[str, object], now: datetime) -> None:
    data["updated_at"] = board_time_text(now)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def board_items_for(items: list[object], task: str, item_id: str | None) -> list[dict[str, object]]:
    task = _board_text(task)
    if item_id is not None:
        item_id = _board_text(item_id)
        if item_id is None:
            raise ValueError("--id cannot be empty")
        matches = [raw for raw in items if isinstance(raw, dict) and _board_text(raw.get("id")) == item_id]
    else:
        if task is None:
            raise ValueError("task cannot be empty")
        matches = [raw for raw in items if isinstance(raw, dict) and _board_text(raw.get("task")) == task]
    if any(not _board_text(raw.get("id")) for raw in matches):
        raise ValueError("matched board item has a missing or invalid id; fix it before writing")
    return matches


def touched_value(args: argparse.Namespace, now: datetime) -> str | None:
    at = getattr(args, "touched_at", None) or getattr(args, "at", None)
    if at:
        parsed = parse_board_time(at)
        if parsed is None:
            raise ValueError(f"not an ISO 8601 time: {at}")
        return board_time_text(parsed)
    return board_time_text(now) if getattr(args, "touch", False) else None


def apply_board_upsert(raw: dict[str, object], args: argparse.Namespace, now: datetime) -> None:
    raw["task"] = args.task.strip()
    for key in ("group", "title", "ask", "detail", "topic", "project"):
        value = getattr(args, key)
        if value is None:
            continue
        if value.strip():
            raw[key] = value.strip()
        elif key in {"detail", "topic", "project"}:
            raw.pop(key, None)
        else:
            raise ValueError(f"--{key} cannot be empty")
    if args.group is not None and args.group not in BOARD_GROUP_KEYS:
        raise ValueError(f"--group must be one of {', '.join(BOARD_GROUP_KEYS)}")
    if args.links is not None:
        links = []
        for value in args.links:
            label, separator, url = value.partition("=")
            if not separator or not label.strip() or not _board_url(url):
                raise ValueError(f"--link needs LABEL=URL with an http(s) URL: {value}")
            links.append({"label": label.strip(), "url": url.strip()})
        if links:
            raw["links"] = links
        else:
            raw.pop("links", None)
    if args.brief_url is not None or args.recommendation is not None:
        brief = raw.get("brief") if isinstance(raw.get("brief"), dict) else {}
        assert isinstance(brief, dict)
        if args.brief_url is not None:
            if not args.brief_url.strip():
                brief.pop("url", None)
            elif not _board_url(args.brief_url):
                raise ValueError(f"--brief-url must be an http(s) URL: {args.brief_url}")
            else:
                brief["url"] = args.brief_url.strip()
        if args.recommendation is not None:
            if args.recommendation.strip():
                brief["recommendation"] = args.recommendation.strip()
            else:
                brief.pop("recommendation", None)
        if brief:
            raw["brief"] = brief
        else:
            raw.pop("brief", None)
    touched = touched_value(args, now)
    if touched:
        raw["touched_at"] = touched


def board_upsert(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    try:
        path = board_file_path(args)
        with board_lock(path):
            data = load_board_document(path)
            items = data["items"]
            assert isinstance(items, list)
            matches = board_items_for(items, args.task, args.id)
            if len(matches) > 1:
                raise ValueError(f"{len(matches)} board items name task {args.task}; pass --id to pick one")
            if matches:
                raw = matches[0]
                action = "updated"
            else:
                item_id = args.id.strip() if args.id is not None else re.sub(r"[^\w.-]+", "-", args.task.strip()).strip("-")
                if any(isinstance(other, dict) and _board_text(other.get("id")) == item_id for other in items):
                    raise ValueError(f"board item id {item_id} already names another task; pass --id")
                missing = [name for name in ("group", "title", "ask") if not getattr(args, name)]
                if missing:
                    raise ValueError("a new board item needs " + ", ".join(f"--{name}" for name in missing))
                raw = {"id": item_id}
                items.append(raw)
                action = "added"
            raw["id"] = _board_text(raw.get("id"))
            if sum(isinstance(other, dict) and _board_text(other.get("id")) == raw["id"] for other in items) != 1:
                raise ValueError(f"board item id {raw['id']} is a duplicate; fix it before writing")
            apply_board_upsert(raw, args, now)
            check = parse_captain_board(json.dumps({**data, "items": [raw]}), path)
            if not check.items:
                raise ValueError("the item would not be valid: " + " ".join(check.warnings))
            write_board_document(path, data, now)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"{action} {raw['id']} (task {args.task}) in {path}")
    return 0


def board_touch(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    try:
        path = board_file_path(args)
        with board_lock(path):
            data = load_board_document(path)
            items = data["items"]
            assert isinstance(items, list)
            matches = board_items_for(items, args.task, None)
            if not matches:
                print(
                    f"quarterdeck: no board item names task {args.task}; add it with `quarterdeck board upsert`",
                    file=sys.stderr,
                )
                return 1
            stamp = touched_value(args, now) or board_time_text(now)
            for raw in matches:
                previous = parse_board_time(raw.get("touched_at"))
                if previous is None or previous < parse_board_time(stamp):  # type: ignore[operator]
                    raw["touched_at"] = stamp
            write_board_document(path, data, now)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"touched {', '.join(str(raw['id']) for raw in matches)} at {stamp}")
    return 0


def board_active(args: argparse.Namespace) -> int:
    """Record an operator message; a gap longer than SESSION_IDLE (or --new-session) starts a new session."""
    now = datetime.now(timezone.utc)
    try:
        path = board_file_path(args)
        with board_lock(path):
            data = load_board_document(path)
            operator = data.get("operator") if isinstance(data.get("operator"), dict) else {}
            assert isinstance(operator, dict)
            previous = parse_board_time(operator.get("last_active_at"))
            started = parse_board_time(operator.get("session_started_at"))
            new_session = args.new_session or previous is None or started is None or now - previous > SESSION_IDLE
            if previous is None or now > previous:
                operator["last_active_at"] = board_time_text(now)
            if new_session:
                operator["session_started_at"] = board_time_text(now)
            data["operator"] = operator
            write_board_document(path, data, now)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"operator active at {operator['last_active_at']}; session started {operator['session_started_at']}")
    return 0


def board_remove(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    try:
        path = board_file_path(args)
        with board_lock(path):
            if not path.exists():
                print(f"no board file at {path}; nothing to remove")
                return 0
            data = load_board_document(path)
            items = data["items"]
            assert isinstance(items, list)
            matches = board_items_for(items, args.task, args.id)
            if not matches:
                print(f"no board item names task {args.task}; nothing to remove")
                return 0
            data["items"] = [raw for raw in items if not any(raw is match for match in matches)]
            write_board_document(path, data, now)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"removed {', '.join(str(raw.get('id')) for raw in matches)} from {path}")
    return 0


def board_list(args: argparse.Namespace) -> int:
    try:
        path = board_file_path(args)
        data = load_board_document(path)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    board = parse_captain_board(json.dumps(data), path)
    if args.as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    for warning in board.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for item in board.items:
        touched = board_time_text(item.touched_at) if item.touched_at else "-"
        print(f"{item.task}\t{item.id}\t{item.group}\t{touched}\t{item.title}")
    return 0


def load_config(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read config {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    unknown = sorted(set(config) - ALLOWED_CONFIG_KEYS)
    if unknown:
        raise ValueError(f"unsupported config key(s): {', '.join(unknown)}")
    if "page_title" in config and not isinstance(config["page_title"], str):
        raise ValueError("config page_title must be a string")
    if "output_dir" in config and not isinstance(config["output_dir"], str):
        raise ValueError("config output_dir must be a path string")
    if "homes" in config:
        homes = config["homes"]
        if not isinstance(homes, list) or not homes:
            raise ValueError("config homes must be a non-empty array")
        for index, home in enumerate(homes, start=1):
            if not isinstance(home, dict):
                raise ValueError(f"config homes entry {index} must be an object")
            unknown_home_keys = sorted(set(home) - {"label", "path", "data_dir"})
            if unknown_home_keys:
                raise ValueError(
                    f"unsupported key(s) in config homes entry {index}: {', '.join(unknown_home_keys)}"
                )
            if not isinstance(home.get("path"), str) or not home["path"].strip():
                raise ValueError(f"config homes entry {index} needs a non-empty path string")
            if "label" in home and (not isinstance(home["label"], str) or not home["label"].strip()):
                raise ValueError(f"config homes entry {index} label must be a non-empty string")
            if "data_dir" in home and (not isinstance(home["data_dir"], str) or not home["data_dir"].strip()):
                raise ValueError(f"config homes entry {index} data_dir must be a non-empty path string")
    return config


def default_config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
    return (config_home / "quarterdeck.json").resolve()


def selected_config_path(path: Path | None) -> Path:
    config_path = (path.expanduser() if path else default_config_path()).resolve()
    checkout = Path(__file__).resolve().parent
    try:
        config_path.relative_to(checkout)
    except ValueError:
        return config_path
    raise ValueError("config must be outside the Quarterdeck checkout")


def read_manage_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    return load_config(config_path)


def write_config(config_path: Path, config: dict[str, object]) -> None:
    if config_path.is_symlink():
        raise ValueError(f"refusing to replace symbolic-link config: {config_path}")
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=config_path.parent,
            prefix=f".{config_path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(config, stream, indent=2)
            stream.write("\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, config_path)
    except OSError:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def normalized_home_path(raw_path: str, config_path: Path | None = None) -> Path:
    path = Path(raw_path).expanduser()
    if config_path is not None and not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def add_home(args: argparse.Namespace) -> int:
    try:
        config_path = selected_config_path(args.config)
        home = normalized_home_path(args.home)
        data_dir = home / "data"
        if not home.is_dir() or not data_dir.is_dir() or not (data_dir / "backlog.md").is_file():
            raise ValueError(f"not a Firstmate home: {home} (expected data/backlog.md)")

        config = read_manage_config(config_path)
        homes = config.get("homes", [])
        assert isinstance(homes, list)
        for entry in homes:
            assert isinstance(entry, dict)
            existing_path = normalized_home_path(entry["path"], config_path)
            if existing_path == home:
                raise ValueError(f"home is already registered as {entry.get('label') or existing_path.name!r}")

        label = args.label if args.label is not None else (home.name or "Firstmate home")
        if not label.strip():
            raise ValueError("label must not be empty")
        if any(str(entry.get("label") or normalized_home_path(entry["path"], config_path).name).casefold() == label.casefold()
               for entry in homes):
            raise ValueError(f"label is already in use: {label}")

        new_homes = [dict(entry) for entry in homes]
        new_homes.append({"label": label, "path": str(home)})
        config["homes"] = new_homes
        write_config(config_path, config)
    except (OSError, ValueError, KeyError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2

    if args.label is None:
        print(f'Using default label "{label}".')
    print(f'Registered "{label}" ({home}).')
    return 0


def list_homes(args: argparse.Namespace) -> int:
    try:
        config_path = selected_config_path(args.config)
        config = read_manage_config(config_path)
        homes = config.get("homes", [])
        assert isinstance(homes, list)
        if not homes:
            print("No homes registered.")
            return 0
        for entry in homes:
            assert isinstance(entry, dict)
            home = normalized_home_path(entry["path"], config_path)
            label = entry.get("label") or home.name or "Firstmate home"
            print(f"{label}\t{home}")
    except (OSError, ValueError, KeyError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    return 0


def remove_home(args: argparse.Namespace) -> int:
    try:
        config_path = selected_config_path(args.config)
        config = read_manage_config(config_path)
        homes = config.get("homes", [])
        assert isinstance(homes, list)
        query_path = normalized_home_path(args.target)
        label_matches: list[int] = []
        path_matches: list[int] = []
        for index, entry in enumerate(homes):
            assert isinstance(entry, dict)
            label = entry.get("label") or normalized_home_path(entry["path"], config_path).name
            if label.casefold() == args.target.casefold():
                label_matches.append(index)
            if normalized_home_path(entry["path"], config_path) == query_path:
                path_matches.append(index)
        matches = sorted(set(label_matches + path_matches))
        if not matches:
            raise ValueError(f"no registered home matches: {args.target}")
        if len(matches) > 1:
            raise ValueError(f"ambiguous home name or path: {args.target}")
        removed = homes[matches[0]]
        remaining = [dict(entry) for index, entry in enumerate(homes) if index != matches[0]]
        if remaining:
            config["homes"] = remaining
        else:
            config.pop("homes", None)
        write_config(config_path, config)
    except (OSError, ValueError, KeyError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2

    label = removed.get("label") or normalized_home_path(removed["path"], config_path).name
    print(f'Removed "{label}". Firstmate data was not changed.')
    return 0


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CACHE_SECONDS = 30
DEFAULT_REFRESH_SECONDS = 60


def port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


class SiteCache:
    """Re-render the site from the homes on request, reusing the result for a short time."""

    def __init__(self, build: Callable[[], dict[str, str]], ttl: float = DEFAULT_CACHE_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.build = build
        self.ttl = ttl
        self.clock = clock
        self.lock = threading.Lock()
        self.built_at: float | None = None
        self.pages: dict[str, str] = {}

    def invalidate(self) -> None:
        with self.lock:
            self.built_at = None

    def get(self) -> dict[str, str]:
        with self.lock:
            now = self.clock()
            if self.built_at is None or now - self.built_at >= self.ttl:
                self.pages = self.build()
                self.built_at = now
            return self.pages


def site_builder(args: argparse.Namespace, allow_marks: bool = False) -> Callable[[], dict[str, str]]:
    def build() -> dict[str, str]:
        config, homes, bearings = load_site(args)
        configured_title = config.get("page_title")
        title = args.title or (configured_title if isinstance(configured_title, str) else None) or DEFAULT_TITLE
        pages = source_pages(homes, show_all=False, bearings=bearings)
        pages["index.html"] = build_html(
            homes, title, bearings=bearings, refresh_seconds=DEFAULT_REFRESH_SECONDS, allow_marks=allow_marks
        )
        return pages
    return build


MARK_BODY_LIMIT = 4096


def make_handler(
    cache: SiteCache,
    set_mark: Callable[[str, str, str], Report] | None = None,
    send_reaction_to: Callable[[dict[str, object]], dict[str, str]] | None = None,
    save_view_to: Callable[[dict[str, object]], dict[str, object]] | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Build the request handler; POST is refused unless set_mark, send_reaction_to or save_view_to is given."""
    class Handler(BaseHTTPRequestHandler):
        server_version = "Quarterdeck"

        def send_text(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            if not self.host_allowed():
                self.send_text(403, "Host is not allowed\n")
                return
            path = unquote(urlparse(self.path).path)
            name = "index.html" if path in {"/", "/index.html"} else path.lstrip("/")
            if "/" in name or not name.endswith(".html"):
                self.send_text(404, "Not found\n")
                return
            try:
                pages = cache.get()
            except (OSError, ValueError) as exc:
                self.send_text(500, f"Quarterdeck could not render: {exc}\n")
                return
            if name not in pages:
                self.send_text(404, "Not found\n")
                return
            self.send_text(200, pages[name], "text/html; charset=utf-8")

        def host_allowed(self) -> bool:
            return not (is_loopback(str(self.server.server_address[0])) and not request_host_is_loopback(
                self.headers.get("Host")
            ))

        def mark_request_problem(self) -> tuple[int, str] | None:
            """Return a refusal for a mark request that is cross-site or mistyped."""
            if not self.host_allowed():
                return 403, "Host is not allowed\n"
            origin = self.headers.get("Origin")
            if origin is not None and (urlparse(origin).netloc != (self.headers.get("Host") or "")):
                return 403, "Cross-origin requests are not allowed\n"
            if self.headers.get("Sec-Fetch-Site") not in {None, "same-origin"}:
                return 403, "Cross-site requests are not allowed\n"
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                return 415, "Content-Type must be application/json\n"
            return None

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            path = urlparse(self.path).path
            if send_reaction_to is not None and path == REACTION_ENDPOINT:
                self.post_reaction(send_reaction_to)
                return
            if save_view_to is not None and path == VIEWS_ENDPOINT:
                self.post_view(save_view_to)
                return
            if set_mark is None or path != MARK_ENDPOINT:
                self.refuse()
                return
            problem = self.mark_request_problem()
            if problem:
                self.send_text(*problem)
                return
            try:
                length = int(self.headers.get("Content-Length") or "")
                if not 0 < length <= MARK_BODY_LIMIT:
                    raise ValueError
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if (not isinstance(body, dict) or set(body) != {"report_id", "fingerprint", "action"}
                        or not isinstance(body["report_id"], str) or not isinstance(body["action"], str)
                        or not isinstance(body["fingerprint"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", body["fingerprint"])
                        or body["action"] not in REPORT_ACTIONS):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self.send_text(400, "Expected JSON {\"report_id\": ..., \"fingerprint\": ..., \"action\": \"mark\" | \"unmark\" | \"discard\" | \"undiscard\"}\n")
                return
            try:
                report = set_mark(body["report_id"], body["action"], body["fingerprint"])
            except ValueError as exc:
                self.send_text(404 if str(exc).startswith("no report matches") else 409, f"{exc}\n")
                return
            except OSError as exc:
                self.send_text(500, f"Quarterdeck could not save the mark: {exc}\n")
                return
            cache.invalidate()
            self.send_text(
                200, json.dumps({
                    "report_id": report.report_id, "reviewed": report.reviewed,
                    "state": report.state,
                }) + "\n",
                "application/json; charset=utf-8",
            )

        def post_view(self, save: Callable[[dict[str, object]], dict[str, object]]) -> None:
            problem = self.mark_request_problem()
            if problem:
                self.send_text(*problem)
                return
            try:
                length = int(self.headers.get("Content-Length") or "")
                if not 0 < length <= MARK_BODY_LIMIT:
                    raise ValueError
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if (not isinstance(body, dict) or set(body) != {"name", "sections", "default"}
                        or not isinstance(body["name"], str) or not isinstance(body["sections"], list)
                        or not isinstance(body["default"], bool)):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self.send_text(400, "Expected JSON {\"name\": ..., \"sections\": [{\"type\": \"waiting\" | \"recent\" | "
                               "\"done\" | \"priorities\" | \"information\" | \"project\", \"project\": ...}], \"default\": true | false}\n")
                return
            try:
                saved = save(body)
            except ValueError as exc:
                self.send_text(409, f"{exc}\n")
                return
            except OSError as exc:
                self.send_text(500, f"Quarterdeck could not save the view: {exc}\n")
                return
            cache.invalidate()
            self.send_text(200, json.dumps(saved) + "\n", "application/json; charset=utf-8")

        def post_reaction(self, deliver: Callable[[dict[str, object]], dict[str, str]]) -> None:
            problem = self.mark_request_problem()
            if problem:
                self.send_text(*problem)
                return
            try:
                length = int(self.headers.get("Content-Length") or "")
                if not 0 < length <= REACTION_BODY_LIMIT:
                    raise ValueError
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if (not isinstance(body, dict) or set(body) != {"id", "kind", "item_id", "reaction", "text"}
                        or not all(isinstance(value, str) for value in body.values())):
                    raise ValueError
                clean_reaction(body)
            except (ValueError, UnicodeDecodeError):
                self.send_text(400, "Expected JSON {\"id\": 8 to 64 letters, digits, dots, dashes or underscores, "
                               "\"kind\": \"call\" | \"report\", \"item_id\": ..., "
                               "\"reaction\": \"approve\" | \"hold\" | \"not-now\" | \"question\" | \"\", "
                               f"\"text\": ...}} with a reaction or text (text up to {REACTION_TEXT_LIMIT} characters)\n")
                return
            try:
                reaction = deliver(body)
            except ValueError as exc:
                missing = str(exc).startswith(("no report matches", "no captain call matches", "no home matches"))
                delivery = str(exc).startswith(("fm-inbox.sh", "no executable", "could not start"))
                self.send_text(404 if missing else 502 if delivery else 409, f"{exc}\n")
                return
            except OSError as exc:
                self.send_text(500, f"Quarterdeck could not send the reaction: {exc}\n")
                return
            cache.invalidate()
            self.send_text(200, json.dumps(reaction) + "\n", "application/json; charset=utf-8")

        def refuse(self) -> None:
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = refuse

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            sys.stderr.write("quarterdeck: %s - %s\n" % (self.address_string(), format % args))

    return Handler


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        mapped_address = getattr(address, "ipv4_mapped", None)
        return address.is_loopback or bool(mapped_address and mapped_address.is_loopback)
    except ValueError:
        return False


def request_host_is_loopback(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse("//" + value.strip())
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return False
        host = parsed.hostname
        if not host:
            return False
        if host.rstrip(".").lower() == "localhost":
            return True
        address = ipaddress.ip_address(host)
        mapped_address = getattr(address, "ipv4_mapped", None)
        return address.is_loopback or bool(mapped_address and mapped_address.is_loopback)
    except ValueError:
        return False


def bind_hosts(args: argparse.Namespace) -> list[str]:
    hosts = list(dict.fromkeys(args.host or [DEFAULT_HOST]))
    return hosts


def make_server(host: str, port: int, handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        daemon_threads = True

        def server_bind(self) -> None:
            if self.address_family == socket.AF_INET6:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            super().server_bind()

    return Server((host, port), handler)


def save_view_request(args: argparse.Namespace, body: dict[str, object]) -> dict[str, object]:
    """Save one view from the page's editor, as `quarterdeck views save` would."""
    sections = [dict(section) for section in body["sections"] if isinstance(section, dict)]  # type: ignore[union-attr]
    if len(sections) != len(body["sections"]):  # type: ignore[arg-type]
        raise ValueError("every section is an object")
    saved, startup = store_view(preferences_path(args), selected_homes(args), str(body["name"]), sections, bool(body["default"]))
    return {"views": [view["name"] for view in saved], "default_view": startup}


def serve(args: argparse.Namespace) -> int:
    servers: list[ThreadingHTTPServer] = []
    try:
        set_mark: Callable[[str, str, str], Report] | None = None
        if getattr(args, "allow_marks", False):
            set_mark = lambda report_id, action, fingerprint: set_report_action(  # noqa: E731
                args, report_id, action, fingerprint
            )
        send_reaction_to: Callable[[dict[str, object]], dict[str, str]] | None = None
        if getattr(args, "allow_marks", False):
            send_reaction_to = lambda raw: send_reaction(args, raw)  # noqa: E731
        save_view_to: Callable[[dict[str, object]], dict[str, object]] | None = None
        if getattr(args, "allow_marks", False):
            save_view_to = lambda raw: save_view_request(args, raw)  # noqa: E731
        cache = SiteCache(site_builder(args, getattr(args, "allow_marks", False)))
        cache.get()
        handler = make_handler(cache, set_mark, send_reaction_to, save_view_to)
        port = args.port
        for host in bind_hosts(args):
            server = make_server(host, port, handler)
            port = server.server_address[1]
            servers.append(server)
    except (OSError, ValueError) as exc:
        for server in servers:
            server.server_close()
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    for server in servers:
        host = str(server.server_address[0])
        shown = f"[{host}]" if ":" in host else host
        access = "report review marks, reactions and view saves enabled" if getattr(args, "allow_marks", False) else "read-only"
        print(f"Serving Quarterdeck at http://{shown}:{port}/ ({access}; Ctrl+C to stop)", flush=True)
    exposed = [host for host in bind_hosts(args) if not is_loopback(host)]
    if exposed:
        print(
            f"quarterdeck: warning: {', '.join(exposed)} is not a loopback address; anyone who can reach this "
            "port can read your Firstmate work data, and there is no login.", file=sys.stderr, flush=True,
        )
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers[1:]]
    for thread in threads:
        thread.start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown() if server is not servers[0] else None
            server.server_close()
    return 0


def systemd_quote(value: str, *, literal_dollars: bool = False) -> str:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("systemd values cannot contain control characters")
    if literal_dollars:
        value = value.replace("$", "$$")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_unit(args: argparse.Namespace) -> str:
    command = [sys.executable, str(Path(__file__).resolve()), "serve"]
    for host in bind_hosts(args):
        command += ["--host", host]
    command += ["--port", str(args.port)]
    if getattr(args, "allow_marks", False):
        command.append("--allow-marks")
    config_path = args.config.expanduser().resolve() if args.config else default_config_path()
    config = load_config(config_path) if args.config or config_path.is_file() else {}
    single_home = bool(args.home) or ("homes" not in config and bool(os.environ.get("FM_HOME")))
    if args.home:
        command += ["--home", str(Path(args.home).expanduser().resolve())]
    elif "homes" not in config and os.environ.get("FM_HOME"):
        command += ["--home", str(Path(os.environ["FM_HOME"]).expanduser().resolve())]
    if args.config or config_path.is_file():
        command += ["--config", str(config_path)]
    exec_start = " ".join(systemd_quote(part, literal_dollars=True) for part in command)
    environment = []
    if single_home and os.environ.get("FM_DATA_OVERRIDE"):
        data_override = Path(os.environ["FM_DATA_OVERRIDE"]).expanduser().resolve()
        environment.append("Environment=" + systemd_quote("FM_DATA_OVERRIDE=" + str(data_override)))
    if os.environ.get("XDG_STATE_HOME"):
        state_home = Path(os.environ["XDG_STATE_HOME"]).expanduser().resolve()
        environment.append("Environment=" + systemd_quote("XDG_STATE_HOME=" + str(state_home)))
    environment_lines = "\n".join(environment)
    if environment_lines:
        environment_lines += "\n"
    return (
        "[Unit]\nDescription=Quarterdeck read-only Firstmate bearings page\nAfter=network.target\n\n"
        f"[Service]\n{environment_lines}ExecStart={exec_start}\nRestart=on-failure\nRestartSec=5\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def service(args: argparse.Namespace) -> int:
    try:
        unit = service_unit(args)
        if not args.write:
            sys.stdout.write(unit)
            return 0
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
        unresolved_unit_path = config_home / "systemd" / "user" / "quarterdeck.service"
        if unresolved_unit_path.is_symlink():
            raise ValueError(f"refusing to replace symbolic-link unit: {unresolved_unit_path}")
        unit_path = unresolved_unit_path.parent.resolve() / unresolved_unit_path.name
        checkout = Path(__file__).resolve().parent
        try:
            unit_path.relative_to(checkout)
        except ValueError:
            pass
        else:
            raise ValueError("the unit file must be outside the Quarterdeck checkout")
        if unit_path.is_symlink():
            raise ValueError(f"refusing to replace symbolic-link unit: {unit_path}")
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        write_generated_page(unit_path, unit)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {unit_path}")
    print("Start it with: systemctl --user daemon-reload && systemctl --user enable --now quarterdeck.service")
    exposed = [host for host in bind_hosts(args) if not is_loopback(host)]
    if exposed:
        print(
            f"quarterdeck: warning: {', '.join(exposed)} is not a loopback address; anyone who can reach the "
            "port can read your Firstmate work data, and there is no login.", file=sys.stderr,
        )
    return 0


def configured_service_addresses() -> tuple[list[str], int, str]:
    """Return addresses from the installed unit, or serve's default bind settings."""
    unit_path = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "systemd/user/quarterdeck.service"
    if not unit_path.is_file():
        return [DEFAULT_HOST], DEFAULT_PORT, "default"
    source = unit_path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^ExecStart=(.*)$", source)
    if not match:
        raise ValueError(f"installed service unit has no ExecStart: {unit_path}")
    try:
        command = shlex.split(match.group(1).replace("%%", "%"))
    except ValueError as exc:
        raise ValueError(f"could not read installed service unit: {exc}") from exc
    try:
        serve_index = command.index("serve")
    except ValueError as exc:
        raise ValueError("installed service unit does not run quarterdeck serve") from exc
    hosts = []
    port = DEFAULT_PORT
    index = serve_index + 1
    while index < len(command):
        if command[index] in {"--host", "--port"} and index + 1 < len(command):
            option, value = command[index], command[index + 1]
            if option == "--host":
                hosts.append(value)
            else:
                try:
                    port = port_number(value)
                except argparse.ArgumentTypeError as exc:
                    raise ValueError(f"installed service unit has an invalid port: {value}") from exc
            index += 2
        else:
            index += 1
    return list(dict.fromkeys(hosts or [DEFAULT_HOST])), port, f"installed service unit {unit_path}"


def url(args: argparse.Namespace) -> int:
    try:
        hosts, port, source = configured_service_addresses()
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    addresses = [
        f"http://[{host.replace('%', '%25', 1)}]:{port}/" if ":" in host else f"http://{host}:{port}/"
        for host in hosts
    ]
    if args.as_json:
        result = {"urls": addresses, "hosts": hosts, "source": source}
        if source == "default":
            result["note"] = (
                "No installed Quarterdeck service was found. A plain `quarterdeck serve` listens on the default "
                "address shown. A foreground serve started with custom --host/--port is not detected and serves "
                "wherever those flags point."
            )
        print(json.dumps(result, indent=2))
    else:
        for address in addresses:
            print(address)
        if source == "default":
            print("Source: default serve settings (no installed Quarterdeck service was found).")
            print("A plain `quarterdeck serve` listens on the default address shown.")
            print("A foreground serve started with custom --host/--port is not detected and serves wherever those flags point.")
            print("Run `quarterdeck serve` to start the page, or `quarterdeck service` to configure a systemd user service.")
        else:
            print(f"Source: {source}")
    return 0


COMMAND_DOCS = {
    "render": ["how-to/serve-the-page.md", "how-to/view-in-lavish.md", "how-to/request-lavish-page.md", "explanation/attention-model.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "reports": ["how-to/review-reports.md", "how-to/keep-the-desk-relevant.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "calls": ["how-to/keep-the-desk-relevant.md", "reference/cli-and-config.md"],
    "export": ["how-to/use-the-cloud-artifact.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "import": ["how-to/use-the-cloud-artifact.md", "reference/cli-and-config.md"],
    "reactions": ["how-to/use-the-cloud-artifact.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "serve": ["how-to/serve-the-page.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "service": ["how-to/serve-the-page.md", "reference/cli-and-config.md"],
    "url": ["how-to/serve-the-page.md", "reference/cli-and-config.md"],
    "add": ["how-to/configure-multiple-homes.md", "reference/cli-and-config.md"],
    "list": ["how-to/configure-multiple-homes.md", "reference/cli-and-config.md"],
    "remove": ["how-to/configure-multiple-homes.md", "reference/cli-and-config.md"],
    "priorities": ["how-to/see-recent-work.md"],
    "views": ["how-to/see-recent-work.md", "reference/cli-and-config.md"],
    "attention": ["how-to/see-recent-work.md", "reference/attention-json.md", "explanation/privacy-and-architecture.md"],
    "board": ["how-to/keep-the-board-current.md", "reference/captain-board.md", "explanation/privacy-and-architecture.md"],
    "install": ["how-to/install.md", "how-to/update.md", "how-to/uninstall.md"],
    "update": ["how-to/update.md", "reference/cli-and-config.md"],
    "help": ["README.md", "reference/cli-and-config.md"],
}


def run_installer(args: argparse.Namespace) -> int:
    installer = Path(__file__).resolve().with_name("install.sh")
    if not installer.is_file():
        print(f"quarterdeck: installer script not found: {installer}", file=sys.stderr)
        return 2
    command = ["sh", str(installer)]
    if args.uninstall:
        command.append("--uninstall")
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as exc:
        print(f"quarterdeck: could not run installer: {exc}", file=sys.stderr)
        return 2


def show_help(args: argparse.Namespace) -> int:
    docs_root = (Path(__file__).resolve().parent / "docs").resolve()
    if args.as_json:
        commands = []
        for name, command_parser in args.command_parsers.items():
            commands.append({
                "name": name,
                "usage": command_parser.format_usage().strip(),
                "summary": command_parser.description or command_parser.prog,
                "docs": COMMAND_DOCS.get(name, []),
            })
        print(json.dumps({"program": "quarterdeck", "docs_root": str(docs_root), "commands": commands}, indent=2))
        return 0

    if args.topic:
        command_parser = args.command_parsers.get(args.topic)
        if command_parser is None:
            print(f"quarterdeck: unknown help topic: {args.topic}", file=sys.stderr)
            return 2
        command_parser.print_help()
        doc_paths = COMMAND_DOCS.get(args.topic, [])
    else:
        args.root_parser.print_help()
        doc_paths = ["how-to/install.md", "reference/cli-and-config.md"]

    print(f"\nDocumentation tree: {docs_root}")
    if doc_paths:
        print("Read: " + ", ".join(str(docs_root / item) for item in doc_paths))
    print("Machine-readable command index: quarterdeck help --json")
    return 0


def default_output_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser()
    return base / "quarterdeck"


def configured_path(value: str, config_path: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def resolve_home_specs(args: argparse.Namespace, config: dict[str, object]) -> tuple[list[HomeSpec], bool]:
    if args.home:
        home = Path(args.home).expanduser().resolve()
        override = os.environ.get("FM_DATA_OVERRIDE")
        data_dir = Path(override).expanduser().resolve() if override else None
        return [HomeSpec(home.name or "Firstmate home", home, data_dir)], True

    if "homes" in config:
        assert args.config is not None
        config_path = args.config.expanduser().resolve()
        specs: list[HomeSpec] = []
        for entry in config["homes"]:
            assert isinstance(entry, dict)
            home = configured_path(entry["path"], config_path)
            label = entry.get("label") or home.name or "Firstmate home"
            data_dir = configured_path(entry["data_dir"], config_path) if entry.get("data_dir") else None
            specs.append(HomeSpec(label, home, data_dir))
        return specs, False

    raw_home = os.environ.get("FM_HOME")
    if raw_home:
        home = Path(raw_home).expanduser().resolve()
        override = os.environ.get("FM_DATA_OVERRIDE")
        data_dir = Path(override).expanduser().resolve() if override else None
        return [HomeSpec(home.name or "Firstmate home", home, data_dir)], True
    raise ValueError("a Firstmate home is required; pass --home, set FM_HOME, or configure homes")


def reports_list(args: argparse.Namespace) -> int:
    try:
        snapshots, _state_path, _marks = load_report_context(args)
        reports = [
            (snapshot, report)
            for snapshot in all_snapshots(snapshots)
            for report in snapshot.reports
        ]
        discovery_errors = report_discovery_errors(snapshots)
        if not reports and not discovery_errors:
            print("No reports found.")
            return 0
        if reports:
            identifiers: dict[str, int] = {}
            for _snapshot, report in reports:
                identifiers[report.report_id] = identifiers.get(report.report_id, 0) + 1
            print("ID\tReview state\tTitle\tHome\tRelevance")
            for snapshot, report in reports:
                state = "ID collision" if identifiers[report.report_id] > 1 else report.state
                print(f"{report.report_id}\t{state}\t{report.title}\t{snapshot.spec.label}\t{report.relevance}")
        for snapshot, error in discovery_errors:
            print(f"Discovery error ({snapshot.spec.label}): {error}")
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    return 0


def reports_read(args: argparse.Namespace) -> int:
    try:
        snapshots, _state_path, _marks = load_report_context(args)
        snapshot, report = unique_report(snapshots, args.report_id)
        home = snapshot.spec.home or report.path.parent.parent.parent
        output_dir = default_output_dir().resolve()
        output_path = output_dir / source_page_filename("report", home, report.path)
        validate_output_path(output_path, snapshots)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_generated_page(
            output_path,
            source_page_html(report.title, display_path(report.path, home), report.markdown),
        )
        print(f"Report ID: {report.report_id}")
        print(f"Title: {report.title}")
        print(f"Source: {display_path(report.path, home)}")
        print("\n--- Markdown ---")
        sys.stdout.write(report.markdown)
        if not report.markdown.endswith("\n"):
            sys.stdout.write("\n")
        print(f"\nReadable HTML: {output_path}")
        if args.open and not webbrowser.open(output_path.as_uri()):
            print("quarterdeck: could not open report page in a browser", file=sys.stderr)
            return 2
    except (OSError, ValueError, webbrowser.Error) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    return 0


def reports_mark_reviewed(args: argparse.Namespace) -> int:
    try:
        report = set_report_mark(args, args.report_id, True)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"Marked {report.report_id} reviewed for its current content.")
    return 0


def reports_unmark_reviewed(args: argparse.Namespace) -> int:
    try:
        report = set_report_mark(args, args.report_id, False)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"Removed the reviewed mark for {report.report_id}.")
    return 0


def reports_discard(args: argparse.Namespace) -> int:
    try:
        report = set_report_action(args, args.report_id, "discard")
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(REPORT_ACTIONS["discard"][1].format(id=report.report_id))
    return 0


def reports_undiscard(args: argparse.Namespace) -> int:
    try:
        report = set_report_action(args, args.report_id, "undiscard")
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(REPORT_ACTIONS["undiscard"][1].format(id=report.report_id))
    return 0


def reports_set_relevance(args: argparse.Namespace) -> int:
    try:
        snapshots, state_path = load_report_snapshots(args)
        apply_review_marks(snapshots, {})
        _snapshot, report = unique_report(snapshots, args.report_id)
        update_relevance(state_path, "report", report.report_id, args.relevance, args.reason or "")
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"Marked {report.report_id} {args.relevance}.")
    return 0


def check_relation_task(task: str) -> str:
    task = task.strip()
    if not task or any(character.isspace() for character in task):
        raise ValueError("--to needs a backlog task id")
    return task


def reports_relate(args: argparse.Namespace) -> int:
    try:
        task = check_relation_task(args.to)
        snapshots, state_path = load_report_snapshots(args)
        apply_review_marks(snapshots, {})
        _snapshot, report = unique_report(snapshots, args.report_id)
        update_relation(state_path, "report", report.report_id, task, not args.remove)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"{'Unrelated' if args.remove else 'Related'} {report.report_id} {'from' if args.remove else 'to'} task {task}.")
    return 0


def call_owners(snapshots: list[HomeSnapshot]) -> dict[str, list[HomeSnapshot]]:
    """Every <home-label-slug>/<task> a captain call can carry (board items and backlog tasks) and the homes owning it."""
    owners: dict[str, list[HomeSnapshot]] = {}

    def add(key: str, owner: HomeSnapshot) -> None:
        homes = owners.setdefault(key, [])
        if not any(home is owner for home in homes):
            homes.append(owner)
    for snapshot in all_snapshots(snapshots):
        if snapshot.board is not None and snapshot.board.supported:
            for item in snapshot.board.items:
                target, task = task_owner(snapshot, item.task)
                add(report_reference(target.spec.label, task), target)
        if snapshot.backlog_markdown is not None and not snapshot.error:
            for item in backlog_items(snapshot.backlog_markdown, snapshot):
                add(report_reference(snapshot.spec.label, item.id), snapshot)
    return owners


def known_call_keys(snapshots: list[HomeSnapshot]) -> list[str]:
    return list(call_owners(snapshots))


def resolve_call_key(snapshots: list[HomeSnapshot], requested: str) -> str:
    keys = known_call_keys(snapshots)
    if requested in keys:
        return requested
    matches = [key for key in keys if key.partition("/")[2] == requested]
    if not matches:
        raise ValueError(f"no captain call or backlog task matches: {requested}")
    if len(matches) > 1:
        raise ValueError(f"task is ambiguous across homes: {requested}; use one of: {', '.join(matches)}")
    return matches[0]


def calls_set_relevance(args: argparse.Namespace) -> int:
    try:
        snapshots, state_path = load_report_snapshots(args)
        key = resolve_call_key(snapshots, args.task)
        update_relevance(state_path, "call", key, args.relevance, args.reason or "")
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"Marked call {key} {args.relevance}.")
    return 0


def calls_relate(args: argparse.Namespace) -> int:
    try:
        task = check_relation_task(args.to)
        snapshots, state_path = load_report_snapshots(args)
        key = resolve_call_key(snapshots, args.task)
        update_relation(state_path, "call", key, task, not args.remove)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"{'Unrelated' if args.remove else 'Related'} call {key} {'from' if args.remove else 'to'} task {task}.")
    return 0


def calls_list(args: argparse.Namespace) -> int:
    try:
        snapshots, _state_path, state = load_report_context(args)
        view = board_view(snapshots, fallback=True)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    assert view is not None
    entries = [e for band in BOARD_BAND_KEYS for e in view.bands[band]] + view.stale
    if not entries:
        print("No captain calls found.")
        return 0
    print("ID\tRelevance\tBand\tTitle")
    stale_keys = {entry.key for entry in view.stale}
    for entry in entries:
        band = "stale" if entry.key in stale_keys else entry.band
        related = ",".join(state.relations.get(("call", entry.key), []))
        print(f"{entry.key}\t{entry.relevance}\t{band}\t{entry.item.title}" + (f"\trelated: {related}" if related else ""))
    return 0


# --- Reactions ----------------------------------------------------------------
# A reaction is the captain's quick answer or short reply on a call or report. It is delivered to Firstmate as a
# note through the home's own bin/fm-inbox.sh, and only after delivery is it recorded in the local state, so the
# state's rows are exactly the delivered reactions. fm-inbox.sh dedupes on the request id, so a retry is harmless.

REACTION_ENDPOINT = "/api/reactions"
VIEWS_ENDPOINT = "/api/views"
REACTION_BODY_LIMIT = 8192
REACTION_TEXT_LIMIT = 1000
REACTIONS_VERSION = 1
REACTION_LABELS = {"approve": "Approve", "hold": "Hold", "not-now": "Not now", "question": "Question"}
REACTION_KINDS = ("call", "report")
REACTION_ID_RE = re.compile(r"[A-Za-z0-9._-]{8,64}")
INBOX_SCRIPT = Path("bin") / "fm-inbox.sh"
INBOX_TIMEOUT_SECONDS = 15


def clean_reaction(raw: dict[str, object], where: str = "reaction") -> dict[str, str]:
    """Validate one reaction's fields; the task is filled in later from the item it names."""
    reaction_id = raw.get("id")
    if not isinstance(reaction_id, str) or not REACTION_ID_RE.fullmatch(reaction_id):
        raise ValueError(f"{where} needs an id of 8 to 64 letters, digits, dots, dashes or underscores")
    kind, item_id, reaction, text = raw.get("kind"), raw.get("item_id"), raw.get("reaction", ""), raw.get("text", "")
    if kind not in REACTION_KINDS:
        raise ValueError(f"{where} needs a kind of {' or '.join(REACTION_KINDS)}")
    if not isinstance(item_id, str) or not item_id.strip() or len(item_id) > 200:
        raise ValueError(f"{where} needs an item_id")
    if reaction not in ("", *REACTION_LABELS):
        raise ValueError(f"{where} has an unknown reaction {reaction!r}; expected one of {', '.join(REACTION_LABELS)}")
    if not isinstance(text, str) or len(text) > REACTION_TEXT_LIMIT:
        raise ValueError(f"{where} text must be at most {REACTION_TEXT_LIMIT} characters")
    text = " ".join(text.split())
    if not reaction and not text:
        raise ValueError(f"{where} needs a reaction or some text")
    return {"id": reaction_id, "item_id": item_id.strip(), "kind": str(kind), "reaction": str(reaction), "text": text}


def reaction_time(value: datetime) -> str:
    """A reaction's time as fixed-width UTC with milliseconds, so text order is time order."""
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reaction_summary(reaction: dict[str, str]) -> str:
    label = REACTION_LABELS.get(reaction["reaction"], "")
    text = reaction["text"]
    return f"{label}: {text}" if label and text else label or text


def reaction_note(reaction: dict[str, str], home_label: str) -> str:
    """The one line Firstmate receives: the item, task, reaction, text and time."""
    label = REACTION_LABELS.get(reaction["reaction"], "reply")
    body = f"Quarterdeck reaction: {label} on {reaction['kind']} {reaction['item_id']}"
    if reaction["task"]:
        body += f" (task {reaction['task']})"
    body += f", home {home_label}, at {reaction['at']}"
    if reaction["text"]:
        body += f': "{reaction["text"]}"'
    return body


def reaction_target(snapshots: list[HomeSnapshot], kind: str, item_id: str) -> tuple[HomeSnapshot, str]:
    """Resolve a reaction's item to (the configured home to notify, the item's task id)."""
    if kind == "report":
        owner, report = unique_report(snapshots, item_id)
        items = backlog_items(owner.backlog_markdown, owner) if (
            owner.backlog_markdown is not None and not owner.error
        ) else []
        item = report_backlog_item(items, report)
        task = item.id if item else report.path.parent.name
    else:
        owners = call_owners(snapshots).get(item_id, [])
        if not owners:
            raise ValueError(f"no captain call matches: {item_id}")
        if len(owners) > 1:
            raise ValueError(f"captain call is ambiguous across homes: {item_id}; "
                             f"matching homes: {', '.join(owner.spec.label for owner in owners)}")
        owner = owners[0]
        task = item_id.partition("/")[2]
    top = next(s for s in snapshots if any(member is owner for member in all_snapshots([s])))
    return top, task


def deliver_reaction(
    home: HomeSpec, reaction: dict[str, str], timeout: float = INBOX_TIMEOUT_SECONDS,
) -> None:
    """Hand one reaction to the home's fm-inbox.sh note; raise ValueError saying why it was not delivered."""
    if home.home is None or home.remote_host is not None:
        raise ValueError(f"{home.label} is not a local home, so it cannot receive reactions")
    script = home.home / INBOX_SCRIPT
    if not script.is_file() or not os.access(script, os.X_OK):
        raise ValueError(f"no executable bin/fm-inbox.sh in {home.label}")
    env = dict(os.environ, FM_HOME=str(home.home))
    command = [str(script), "note", "--request-id", f"qd-{reaction['id']}", "--json", "--",
               reaction_note(reaction, home.label)]
    try:
        process = subprocess.Popen(
            command, cwd=home.home, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError as exc:
        raise ValueError(f"could not start fm-inbox.sh: {exc}") from exc
    try:
        process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, 9)
        except OSError:
            process.kill()
        process.communicate()
        raise ValueError(f"fm-inbox.sh timed out after {timeout:g} seconds") from None
    if process.returncode == 3:
        raise ValueError("fm-inbox.sh saved the note but could not wake Firstmate; try again")
    if process.returncode != 0:
        raise ValueError(f"fm-inbox.sh exited with status {process.returncode}")


def store_reaction(state_path: Path, reaction: dict[str, str]) -> None:
    write_state(state_path, [(
        "INSERT OR IGNORE INTO reactions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (reaction["id"], reaction["item_id"], reaction["kind"], reaction["task"], reaction["reaction"],
         reaction["text"], reaction["at"], reaction["source"]),
    )])


def reaction_delivered(state_path: Path, reaction_id: str) -> bool:
    if not state_db_path(state_path).exists():
        return False
    with state_connection(state_path) as connection:
        return connection.execute("SELECT 1 FROM reactions WHERE id = ?", (reaction_id,)).fetchone() is not None


def send_reaction(args: argparse.Namespace, raw: dict[str, object]) -> dict[str, str]:
    """Deliver a reaction made on the served page and record it; shared with the page's Send button."""
    reaction = clean_reaction(raw)
    snapshots, state_path = load_report_snapshots(args)
    apply_review_marks(snapshots, {})
    top, task = reaction_target(snapshots, reaction["kind"], reaction["item_id"])
    reaction.update(task=task, at=reaction_time(datetime.now(timezone.utc)), source="page")
    deliver_reaction(top.spec, reaction)
    store_reaction(state_path, reaction)
    return reaction


def parse_artifact_reactions(source: str) -> list[dict[str, object]]:
    """Read the artifact's recorded reactions: a list, or {"reactions": [...]}, of qd-inbox documents."""
    try:
        data = json.loads(source)
    except ValueError as exc:
        raise ValueError(f"reactions file is not valid JSON: {exc}") from exc
    if isinstance(data, dict):
        if data.get("version", REACTIONS_VERSION) != REACTIONS_VERSION:
            raise ValueError(f"unsupported reactions version {data.get('version')!r}; expected {REACTIONS_VERSION}")
        data = data.get("reactions")
    if not isinstance(data, list):
        raise ValueError("reactions file must be a list of reactions or an object with a reactions list")
    return [raw["data"] if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw for raw in data]


def import_reactions(args: argparse.Namespace) -> int:
    """Deliver the artifact's recorded reactions to Firstmate, oldest first, skipping any already delivered."""
    try:
        source = sys.stdin.read() if args.file == "-" else Path(args.file).expanduser().read_text(encoding="utf-8")
        documents = parse_artifact_reactions(source)
        snapshots, state_path = load_report_snapshots(args)
        apply_review_marks(snapshots, {})
        delivered = 0
        skipped: list[str] = []
        pending: list[tuple[datetime, int, dict[str, str]]] = []
        for index, raw in enumerate(documents, start=1):
            where = f"reaction {index}"
            try:
                if not isinstance(raw, dict):
                    raise ValueError(f"{where} is not an object")
                reaction = clean_reaction(raw, where)
                when = parse_board_time(raw.get("at"))
                if when is None:
                    raise ValueError(f"{where} needs a valid at")
                reaction.update(at=reaction_time(when), source="artifact", task="")
                pending.append((when, index, reaction))
            except ValueError as exc:
                skipped.append(str(exc))
        for _when, _index, reaction in sorted(pending, key=lambda item: item[:2]):
            label = f"{reaction['id']} ({reaction['kind']} {reaction['item_id']})"
            try:
                if reaction_delivered(state_path, reaction["id"]):
                    raise ValueError("already delivered")
                top, reaction["task"] = reaction_target(snapshots, reaction["kind"], reaction["item_id"])
                if not args.dry_run:
                    deliver_reaction(top.spec, reaction)
                    store_reaction(state_path, reaction)
                delivered += 1
            except ValueError as exc:
                skipped.append(f"{label}: {exc}")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    verb = "Would deliver" if args.dry_run else "Delivered"
    print(f"{verb} {delivered} of {len(documents)} artifact reaction{'s' if len(documents) != 1 else ''}.")
    for line in skipped:
        print(f"skipped: {line}")
    return 0


@dataclass
class ReactionContext:
    """What the served page needs to draw reactions: whether Send is on, and the latest reaction per item."""
    enabled: bool = False
    latest: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    now: datetime | None = None


def reaction_latest_text(reaction: dict[str, str] | None, now: datetime | None) -> str:
    if not reaction:
        return ""
    when = parse_board_time(reaction["at"])
    ago = f" · {elapsed_text(when, now)} ago" if when and now and when <= now else ""
    sent = "from the artifact" if reaction["source"] == "artifact" else "from this page"
    return f"Latest reaction: {reaction_summary(reaction)} ({sent}{ago})"


def reaction_html(ctx: ReactionContext | None, kind: str, item_id: str, available: bool = True) -> str:
    """The reaction row for a card: latest reaction, quick reactions, a reply box and Send."""
    if ctx is None:
        return ""
    key = html.escape(f"{kind}:{item_id}", quote=True)
    text = reaction_latest_text(ctx.latest.get((kind, item_id)), ctx.now)
    latest = f'<p class="reaction-latest" data-reaction-latest="{key}" aria-live="polite"{"" if text else " hidden"}>{html.escape(text)}</p>'
    if not ctx.enabled or not available:
        return latest if text else ""
    buttons = "".join(
        f'<button class="request-lavish reaction-choice" type="button" aria-pressed="false" '
        f'data-reaction-choice="{name}">{label}</button>' for name, label in REACTION_LABELS.items()
    )
    return (
        f'<div class="reaction" data-reaction-kind="{kind}" data-reaction-item="{html.escape(item_id, quote=True)}">'
        f'{latest}<div class="request-row">{buttons}</div>'
        f'<div class="request-row"><input class="reaction-text" type="text" maxlength="{REACTION_TEXT_LIMIT}" '
        f'placeholder="Reply to Firstmate (optional)" aria-label="Reply to Firstmate">'
        f'<button class="request-lavish reaction-send" type="button">Send</button>'
        f'<span class="request-status reaction-status" aria-live="polite"></span></div></div>'
    )


# --- Snapshot export and action import (the cloud artifact round trip) -------

SNAPSHOT_VERSION = 1
ACTIONS_VERSION = 1
ARTIFACT_TEMPLATE = Path(__file__).resolve().parent / "templates" / "artifact.html.tmpl"
SNAPSHOT_PLACEHOLDER = "__QUARTERDECK_SNAPSHOT__"
TEMPLATE_TITLE_PLACEHOLDER = "__QUARTERDECK_TITLE__"


def iso_or_none(value: datetime | None) -> str | None:
    return board_time_text(value) if value else None


BODIES_MODES = ("all", "desk", "none")


def linked_report_ids(homes: list[HomeSnapshot], view: BoardView, state: ReviewState) -> dict[str, list[str]]:
    """Captain call key -> ids of the reports linked to it: the report of that task, a report related to it by
    Firstmate, or a report of a task the call is related to."""
    snapshots = all_snapshots(homes)
    entries = [e for band in BOARD_BAND_KEYS for e in view.bands[band]] + list(view.stale)
    keys = {e.key for e in entries}
    owners = {key: next((s for s in snapshots if report_reference(s.spec.label, key.partition("/")[2]) == key), None) for key in keys}
    report_task: dict[str, str] = {}
    linked: dict[str, set[str]] = {key: set() for key in keys}
    for snapshot in snapshots:
        items = backlog_items(snapshot.backlog_markdown or "", snapshot) if (
            snapshot.backlog_markdown is not None and not snapshot.error
        ) else []
        for report in snapshot.reports:
            item = report_backlog_item(items, report)
            task = task_key(snapshot, item.id if item else report.path.parent.name, snapshots)
            report_task[report.report_id] = task
            for key in {task, *(task_key(snapshot, target, snapshots) for target in report.related_to)} & keys:
                linked[key].add(report.report_id)
    for key in keys:
        owner = owners[key]
        if owner is None:
            continue
        targets = {task_key(owner, target, snapshots) for target in state.relations.get(("call", key), [])}
        linked[key].update(rid for rid, task in report_task.items() if task in targets)
    return {key: sorted(ids) for key, ids in linked.items()}


def call_snapshot(
    entry: BoardEntry, band: str, desk_heading: str, state: ReviewState, reports: list[str] | None = None,
) -> dict[str, object]:
    item = entry.item
    return {
        "id": entry.key, "title": item.title, "ask": item.ask, "group": item.group, "band": band,
        "desk_group": desk_heading, "home": entry.home, "project": item.project, "topic": item.topic,
        "detail": item.detail, "touched_at": iso_or_none(entry.touched), "deferred": entry.deferred,
        **({"activity_reason": entry.activity_reason} if entry.activity_reason else {}),
        "sweep": entry.sweep, "recommendation": item.recommendation, "brief_url": item.brief_url,
        "links": [{"label": label, "url": url} for label, url in item.links],
        "relevance": entry.relevance, "relevance_reason": entry.reason,
        "related_to": state.relations.get(("call", entry.key), []),
        "reports": reports or [],
    }


def build_snapshot(
    homes: list[HomeSnapshot], title: str, now: datetime, bodies: str = "all", *,
    attention: dict[str, object] | None = None,
) -> dict[str, object]:
    """The data the cloud artifact renders: the desk, this week, related items, the report list and the latest
    reaction per item. Nothing else. `bodies` picks which waiting reports carry their Markdown: "all" (the
    default), "desk" (only reports linked to a desk or this-week call, or related to the session) or "none"."""
    if attention is not None and not attention["ok"]:
        raise ValueError(attention_check_line(attention))
    if bodies not in BODIES_MODES:
        raise ValueError(f"unknown --bodies {bodies!r}; use one of {', '.join(BODIES_MODES)}")
    ambiguous = assign_report_ids(homes)
    state = next((s.review_state for s in all_snapshots(homes) if s.review_state is not None), None) or ReviewState()
    view = board_view(homes, now, fallback=True, decisions=decision_rows(homes, attention))
    calls: list[dict[str, object]] = []
    session: dict[str, object] | None = None
    assert view is not None
    linked = linked_report_ids(homes, view, state)
    headings = {id(e): heading for heading, members in view.desk_groups for e in members}
    for band in BOARD_BAND_KEYS:
        calls.extend(call_snapshot(e, band, headings.get(id(e), ""), state, linked.get(e.key)) for e in view.bands[band])
    calls.extend(call_snapshot(e, "stale", "", state, linked.get(e.key)) for e in view.stale)
    related_found = related_items(homes, view)
    wanted = {rid for band in ("desk", "week") for e in view.bands[band] for rid in linked.get(e.key, [])}
    wanted.update(item.key for item in related_found if item.kind == "report")
    if view.session is not None:
        session = {
            "idle": view.session.idle, "started_at": iso_or_none(view.session.started),
            "last_active_at": iso_or_none(view.session.last_active),
        }
    reports = [
        {
            "id": report.report_id, "title": report.title, "home": snapshot.spec.label,
            "recommendation": report.recommendation or "", "state": report.state,
            "fingerprint": report.fingerprint, "relevance": report.relevance,
            "relevance_reason": report.relevance_reason, "related_to": report.related_to,
            "modified_at": iso_or_none(report.modified), "ambiguous": report.report_id in ambiguous,
            "markdown": report.markdown if report.waiting and (
                bodies == "all" or bodies == "desk" and report.report_id in wanted) else "",
        }
        for snapshot in all_snapshots(homes) for report in snapshot.reports
    ]
    related = [
        {"kind": item.kind, "id": item.key, "title": item.title, "home": item.home, "why": item.why, "score": item.score}
        for item in related_found
    ]
    shown = {("call", call["id"]) for call in calls} | {("report", report["id"]) for report in reports} | {
        ("report" if item["kind"] == "report" else "call", item["id"]) for item in related
    }
    latest = sorted((r for key, r in state.reactions.items() if key in shown), key=lambda r: r["at"])
    reactions = [
        {key: r[key] for key in ("kind", "item_id", "reaction", "text", "at", "source")} for r in latest
    ]
    delivered = [reaction_id for key, ids in state.delivered.items() if key in shown for reaction_id in ids]
    return {
        "version": SNAPSHOT_VERSION, "title": title,
        "generated_at": now.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "session": session, "calls": calls, "reports": reports, "related": related,
        **({"decision_window_days": DEFAULT_RECENT_DAYS} if view.shared_relevance else {}),
        "reactions": reactions, "delivered": delivered,
    }


def artifact_page(snapshot: dict[str, object]) -> str:
    try:
        template = ARTIFACT_TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not read the artifact template {ARTIFACT_TEMPLATE}: {exc}") from exc
    if template.count(SNAPSHOT_PLACEHOLDER) != 1:
        raise ValueError(f"{ARTIFACT_TEMPLATE} must contain {SNAPSHOT_PLACEHOLDER} exactly once")
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return template.replace(TEMPLATE_TITLE_PLACEHOLDER, html.escape(str(snapshot["title"]))).replace(SNAPSHOT_PLACEHOLDER, payload)


def export_snapshot(args: argparse.Namespace) -> int:
    try:
        config, homes, bearings = load_site(args)
        configured_title = config.get("page_title")
        title = args.title or (configured_title if isinstance(configured_title, str) else None) or DEFAULT_TITLE
        snapshot = build_snapshot(homes, title, bearings.generated, args.bodies, attention=bearings.attention)
        document = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
        outputs: list[Path] = []
        if args.artifact:
            page_path = Path(args.artifact).expanduser().resolve()
            validate_output_path(page_path, homes)
            page_path.parent.mkdir(parents=True, exist_ok=True)
            write_generated_page(page_path, artifact_page(snapshot))
            outputs.append(page_path)
        if args.output:
            json_path = Path(args.output).expanduser().resolve()
            validate_output_path(json_path, homes)
            json_path.parent.mkdir(parents=True, exist_ok=True)
            write_generated_page(json_path, document)
            outputs.append(json_path)
        if not outputs:
            sys.stdout.write(document)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    for path in outputs:
        print(path)
    return 0


def parse_artifact_actions(source: str) -> list[dict[str, str]]:
    """Read the artifact's recorded interactions: a list, or {"actions": [...]}, of report actions."""
    try:
        data = json.loads(source)
    except ValueError as exc:
        raise ValueError(f"actions file is not valid JSON: {exc}") from exc
    if isinstance(data, dict):
        if data.get("version", ACTIONS_VERSION) != ACTIONS_VERSION:
            raise ValueError(f"unsupported actions version {data.get('version')!r}; expected {ACTIONS_VERSION}")
        cutoff = data.get("generated_at")
        if cutoff is not None and (not isinstance(cutoff, str) or parse_board_time(cutoff) is None):
            raise ValueError("actions file has an invalid generated_at")
        data = data.get("actions")
    else:
        cutoff = None
    if not isinstance(data, list):
        raise ValueError("actions file must be a list of actions or an object with an actions list")
    actions: list[dict[str, str]] = []
    for index, raw in enumerate(data, start=1):
        if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
            raw = raw["data"]  # a database document as listed by the artifact's storage
        where = f"action {index}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} is not an object")
        report_id, action, fingerprint = raw.get("report_id"), raw.get("action"), raw.get("fingerprint")
        at = raw.get("at", "")
        if not isinstance(report_id, str) or not report_id.strip():
            raise ValueError(f"{where} has no report_id")
        if action not in REPORT_ACTIONS:
            raise ValueError(f"{where} has an unknown action {action!r}; expected one of {', '.join(REPORT_ACTIONS)}")
        if not isinstance(fingerprint, str) or not FINGERPRINT_RE.fullmatch(fingerprint):
            raise ValueError(f"{where} needs the report's 64-character fingerprint")
        if not isinstance(at, str):
            raise ValueError(f"{where} has a non-text at")
        resulting_state = raw.get("state")
        if resulting_state is not None and resulting_state not in (*REPORT_STATES, "needs review"):
            raise ValueError(f"{where} has an invalid state")
        if cutoff is not None:
            when = parse_board_time(at)
            if when is None:
                raise ValueError(f"{where} needs a valid at")
            if when <= parse_board_time(cutoff):
                continue
        parsed = {"report_id": report_id.strip(), "action": action, "fingerprint": fingerprint, "at": at}
        if resulting_state is not None:
            parsed["state"] = resulting_state
        actions.append(parsed)
    return actions


def import_actions(args: argparse.Namespace) -> int:
    """Apply the artifact's recorded mark and discard actions to the local state, oldest first."""
    try:
        source = sys.stdin.read() if args.file == "-" else Path(args.file).expanduser().read_text(encoding="utf-8")
        actions = parse_artifact_actions(source)
        snapshots, state_path = load_report_snapshots(args)
        apply_review_marks(snapshots, {})
        applied = 0
        skipped: list[str] = []
        ordered = sorted(enumerate(actions), key=lambda pair: (parse_board_time(pair[1]["at"]) or datetime.min.replace(tzinfo=timezone.utc), pair[0]))
        for _index, action in ordered:
            try:
                if args.dry_run:
                    _s, report = unique_report(snapshots, action["report_id"])
                    if report.fingerprint != action["fingerprint"]:
                        raise ValueError("report changed since the artifact was exported")
                else:
                    if "state" in action:
                        _s, report = unique_report(snapshots, action["report_id"])
                        if report.fingerprint != action["fingerprint"]:
                            raise ValueError("report changed since the artifact was exported")
                        update_report_state(
                            state_path, report.report_id,
                            None if action["state"] == "needs review" else action["state"], report.fingerprint,
                        )
                    else:
                        apply_report_action(snapshots, state_path, action["report_id"], action["action"], action["fingerprint"])
                applied += 1
            except ValueError as exc:
                skipped.append(f"{action['report_id']} ({action['action']}): {exc}")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    verb = "Would apply" if args.dry_run else "Applied"
    print(f"{verb} {applied} of {len(actions)} artifact action{'s' if len(actions) != 1 else ''}.")
    for line in skipped:
        print(f"skipped: {line}")
    return 0


def validate_output_path(output_path: Path, homes: list[HomeSnapshot]) -> None:
    checkout = Path(__file__).resolve().parent
    roots = [checkout] + [
        snapshot.spec.home.resolve()
        for snapshot in all_snapshots(homes)
        if snapshot.spec.home is not None
    ]
    resolved_output = output_path.resolve()
    for root in roots:
        try:
            resolved_output.relative_to(root)
        except ValueError:
            continue
        raise ValueError("rendered output must be outside the Quarterdeck checkout and all selected Firstmate homes")


def parse_secondmates(source: str, parent_label: str) -> tuple[list[HomeSpec], list[str]]:
    specs: list[HomeSpec] = []
    errors: list[str] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        if not re.match(r"^\s*[-*]\s+", line):
            continue
        match = SECONDMATE_ENTRY_RE.match(line)
        if not match:
            errors.append(f"unrecognized route on line {line_number}")
            continue
        attributes = match.group("attributes")
        home_match = ROUTE_HOME_RE.search(attributes)
        host_match = ROUTE_HOST_RE.search(attributes)
        raw_home = home_match.group(1).strip().strip("`") if home_match else ""
        remote_host = host_match.group(1).strip() if host_match else None
        route_id = match.group("id")
        summary = markdown_text(match.group("summary"))
        reason = None if raw_home else "The registered route does not contain a home: path."
        route_home = Path(raw_home).expanduser() if raw_home else None
        if route_home and not remote_host and not route_home.is_absolute():
            reason = "The registered local home path is not absolute."
        specs.append(HomeSpec(
            label=f"{route_id} — {summary}",
            home=route_home,
            parent_label=parent_label,
            route_id=route_id,
            remote_host=remote_host,
            unavailable_reason=reason,
        ))
    return specs, errors


def load_home_snapshot(spec: HomeSpec, discover_secondmates: bool = False) -> HomeSnapshot:
    snapshot = HomeSnapshot(spec)
    if spec.unavailable_reason:
        snapshot.error = spec.unavailable_reason
    elif spec.remote_host:
        snapshot.error = f"This secondmate is on remote host {spec.remote_host}; Quarterdeck does not read its files directly."
    elif spec.home is None:
        snapshot.error = "The registered route does not contain a local home path."
    else:
        try:
            if not spec.home.is_dir():
                raise FileNotFoundError(f"Firstmate home is not a directory: {spec.home}")
            data_dir = spec.data_dir or (spec.home / "data")
            snapshot.data_dir = data_dir
            backlog_path = data_dir / "backlog.md"
            if not backlog_path.is_file():
                raise FileNotFoundError(f"backlog not found: {backlog_path}")
            source = backlog_path.read_text(encoding="utf-8", errors="replace")
            snapshot.backlog_path = backlog_path.resolve()
            snapshot.backlog_changed = datetime.fromtimestamp(backlog_path.stat().st_mtime, timezone.utc)
            snapshot.backlog_markdown = source
            snapshot.reports = read_reports(data_dir, snapshot.warnings)
            snapshot.records = parse_backlog(source, data_dir, snapshot.reports)
        except (OSError, ValueError) as exc:
            snapshot.error = str(exc)
        if snapshot.data_dir is not None:
            snapshot.board = read_captain_board(snapshot.data_dir)

    if discover_secondmates and spec.home is not None and not spec.remote_host:
        registry_path = spec.home / "data" / "secondmates.md"
        try:
            registry = registry_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            registry = ""
        except OSError as exc:
            snapshot.registry_error = f"{registry_path}: {exc}"
            registry = ""
        if registry:
            children, parse_errors = parse_secondmates(registry, spec.label)
            snapshot.children = [load_home_snapshot(child) for child in children]
            if parse_errors:
                snapshot.registry_error = "; ".join(parse_errors)
    return snapshot


def load_site(args: argparse.Namespace) -> tuple[dict[str, object], list[HomeSnapshot], Bearings]:
    """Read the configured homes and review marks, then classify them into bearings."""
    if args.config is None:
        default_path = default_config_path()
        args.config = default_path if default_path.is_file() else None
    config = load_config(args.config)
    specs, legacy_single_home = resolve_home_specs(args, config)
    homes = [load_home_snapshot(spec, discover_secondmates=True) for spec in specs]
    load_error = legacy_home_load_error(homes, legacy_single_home)
    if load_error:
        raise ValueError(load_error)
    state_path = validate_review_state_location(homes)
    apply_review_marks(homes, load_review_state(state_path))
    use_snapshot = not getattr(args, "no_snapshot", False)
    bearings = build_bearings(homes, use_snapshot=use_snapshot)
    try:
        document = read_preferences(preferences_path(args))
    except ValueError:
        document = {}  # a damaged preference file must not stop the page
    bearings.attention = attention_view(
        homes, bearings.generated, all_decisions=True, use_feed=use_snapshot,
        priority_projects=list(document.get("priority_projects", [])),  # type: ignore[call-overload]
        saved_views=effective_views(document), all_views=True,
    )
    return config, homes, bearings


def render(args: argparse.Namespace) -> int:
    try:
        config, homes, bearings = load_site(args)
        configured_output = config.get("output_dir")
        output_dir = Path(configured_output).expanduser() if isinstance(configured_output, str) else default_output_dir()
        output_path = Path(args.output).expanduser() if args.output else output_dir / "index.html"
        if args.lavish:
            output_path = output_path.with_name(f"{output_path.stem}.lavish{output_path.suffix or '.html'}")
        configured_title = config.get("page_title")
        title = args.title or (configured_title if isinstance(configured_title, str) else None) or DEFAULT_TITLE
        output_path = output_path.resolve()
        validate_output_path(output_path, homes)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_source_pages(homes, output_path.parent, show_all=args.all, bearings=bearings)
        write_generated_page(
            output_path, build_html(homes, title, lavish=args.lavish, show_all=args.all, bearings=bearings)
        )
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2

    print(output_path)
    if args.lavish:
        executable = shutil.which("lavish-axi")
        if executable is None:
            print(f"Lavish page ready; open it later with: lavish-axi {output_path}")
            return 0
        try:
            opened = subprocess.run([executable, str(output_path)], check=False, capture_output=True, text=True)
        except OSError as exc:
            print(f"quarterdeck: could not open Lavish page: {exc}", file=sys.stderr)
            return 2
        output = "\n".join(part.strip() for part in (opened.stdout, opened.stderr) if part.strip())
        session_url = re.search(r"https?://[^\s<>]+", output)
        if opened.returncode != 0:
            if output:
                print(output, file=sys.stderr)
            return opened.returncode
        if session_url:
            print(session_url.group(0).rstrip(".,;"))
        elif output:
            print(output)
    return 0


def add_serve_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", action="append", metavar="ADDRESS", help=f"address to bind; repeat to listen on several, all on the same port (default {DEFAULT_HOST} only; a non-loopback address exposes your work data without a login)")
    parser.add_argument("--port", type=port_number, default=DEFAULT_PORT, help=f"port to listen on (default {DEFAULT_PORT})")
    parser.add_argument("--allow-marks", action="store_true", help="enable report marks, reactions to Firstmate and saved-view edits (default: read-only; no key, anyone who can open the page can mark, react or edit views)")
    parser.add_argument("--home", help="serve this Firstmate home instead of configured homes or FM_HOME")
    parser.add_argument("--config", type=Path, help="optional JSON config file")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quarterdeck", description="Render and manage a read-only Firstmate review page.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command_parsers: dict[str, argparse.ArgumentParser] = {}

    render_parser = subparsers.add_parser(
        "render", help="regenerate the local HTML page", description="Render registered or explicitly selected homes using the attention view; --all includes queued, finished, closed, and unlinked report records. --lavish writes a separate Lavish review page and opens it when available."
    )
    command_parsers["render"] = render_parser
    render_parser.add_argument("--home", help="render this Firstmate home instead of configured homes or FM_HOME")
    render_parser.add_argument("--output", help="HTML file path; defaults to the configured state directory")
    render_parser.add_argument("--config", type=Path, help="optional JSON config file")
    render_parser.add_argument("--title", help="override the page title")
    render_parser.add_argument("--all", action="store_true", help="show every backlog item and scout report, including queued and finished work")
    render_parser.add_argument("--lavish", action="store_true", help="write a separate Lavish-ready page and open it when lavish-axi is available")
    render_parser.add_argument("--no-snapshot", action="store_true", help="skip the Firstmate bearings snapshot script and parse backlogs directly")
    render_parser.set_defaults(handler=render)

    serve_parser = subparsers.add_parser(
        "serve", help="serve the always-current bearings page over HTTP",
        description="Serve the bearings page and its readable report and backlog pages, re-rendered from the homes on request. Read-only by default: GET only, no login. With --allow-marks the only writes are a request to mark, unmark, discard or undiscard a known report, a reaction sent to Firstmate through the home's bin/fm-inbox.sh, and saving a briefing view to the preferences file. Binds loopback by default.",
    )
    command_parsers["serve"] = serve_parser
    add_serve_options(serve_parser)
    serve_parser.add_argument("--title", help="override the page title")
    serve_parser.add_argument("--no-snapshot", action="store_true", help="skip the Firstmate bearings snapshot script and parse backlogs directly")
    serve_parser.set_defaults(handler=serve)

    service_parser = subparsers.add_parser(
        "service", help="print or write a systemd user unit that runs serve",
        description="Print a systemd user unit for 'quarterdeck serve', or write it to ~/.config/systemd/user/quarterdeck.service with --write. It never runs systemctl.",
    )
    command_parsers["service"] = service_parser
    add_serve_options(service_parser)
    service_parser.add_argument("--write", action="store_true", help="write the unit file instead of printing it")
    service_parser.set_defaults(handler=service)

    url_parser = subparsers.add_parser(
        "url", help="show the URL for the configured serve addresses",
        description=(
            "Show URLs configured in the installed systemd user unit, or Quarterdeck's default serve address when "
            "no unit is installed. Does not detect foreground serves or whether a service is running."
        ),
    )
    command_parsers["url"] = url_parser
    url_parser.add_argument("--json", dest="as_json", action="store_true", help="print URLs and their source as JSON")
    url_parser.set_defaults(handler=url)

    reports_parser = subparsers.add_parser(
        "reports", help="list, read, and track report reviews",
        description="List reports by stable ID, read their Markdown and HTML page, or change their local review mark.",
    )
    command_parsers["reports"] = reports_parser
    reports_subparsers = reports_parser.add_subparsers(dest="reports_command", required=True)

    reports_list_parser = reports_subparsers.add_parser(
        "list", help="list reports and their review state", description="List discovered reports and review state.",
    )
    reports_list_parser.add_argument("--home", help="use only this Firstmate home")
    reports_list_parser.add_argument("--config", type=Path, help="optional JSON config file")
    reports_list_parser.set_defaults(handler=reports_list)

    reports_read_parser = reports_subparsers.add_parser(
        "read", help="print a report and generate its readable HTML page",
        description="Print report Markdown and generate a readable HTML copy in Quarterdeck's state directory.",
    )
    reports_read_parser.add_argument("report_id", help="stable report ID: <home-label-slug>/<task-id>")
    reports_read_parser.add_argument("--home", help="use only this Firstmate home")
    reports_read_parser.add_argument("--config", type=Path, help="optional JSON config file")
    reports_read_parser.add_argument("--open", action="store_true", help="open the generated HTML page in a browser")
    reports_read_parser.set_defaults(handler=reports_read)

    reports_mark_parser = reports_subparsers.add_parser(
        "mark-reviewed", help="mark a report reviewed for its current content",
        description="Store the report's current content fingerprint in Quarterdeck's private state directory.",
    )
    reports_mark_parser.add_argument("report_id", help="stable report ID: <home-label-slug>/<task-id>")
    reports_mark_parser.add_argument("--home", help="use only this Firstmate home")
    reports_mark_parser.add_argument("--config", type=Path, help="optional JSON config file")
    reports_mark_parser.set_defaults(handler=reports_mark_reviewed)

    reports_unmark_parser = reports_subparsers.add_parser(
        "unmark-reviewed", help="remove a report's reviewed mark",
        description="Remove the local reviewed mark so the report needs review again.",
    )
    reports_unmark_parser.add_argument("report_id", help="stable report ID: <home-label-slug>/<task-id>")
    reports_unmark_parser.add_argument("--home", help="use only this Firstmate home")
    reports_unmark_parser.add_argument("--config", type=Path, help="optional JSON config file")
    reports_unmark_parser.set_defaults(handler=reports_unmark_reviewed)

    def report_target(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--home", help="use only this Firstmate home")
        command_parser.add_argument("--config", type=Path, help="optional JSON config file")

    discard_parser = reports_subparsers.add_parser(
        "discard", help="take a report off the list without marking it reviewed",
        description="Take the report off your list without saying you reviewed it. A report that changes afterwards returns on its own.",
    )
    discard_parser.add_argument("report_id", help="stable report ID: <home-label-slug>/<task-id>")
    report_target(discard_parser)
    discard_parser.set_defaults(handler=reports_discard)

    undiscard_parser = reports_subparsers.add_parser(
        "undiscard", help="return a discarded report to the list", description="Remove the discard so the report needs review again.",
    )
    undiscard_parser.add_argument("report_id", help="stable report ID: <home-label-slug>/<task-id>")
    report_target(undiscard_parser)
    undiscard_parser.set_defaults(handler=reports_undiscard)

    set_relevance_parser = reports_subparsers.add_parser(
        "set-relevance", help="mark a report current or stale",
        description="Stale reports (one-offs or already decided) are hidden from the review list by default. Unclassified reports are current.",
    )
    set_relevance_parser.add_argument("report_id", help="stable report ID: <home-label-slug>/<task-id>")
    set_relevance_parser.add_argument("relevance", choices=RELEVANCE_VALUES)
    set_relevance_parser.add_argument("--reason", help="why, shown beside the stale report")
    report_target(set_relevance_parser)
    set_relevance_parser.set_defaults(handler=reports_set_relevance)

    relate_parser = reports_subparsers.add_parser(
        "relate", help="relate a report to a task",
        description="Record that the report relates to TASK, so it appears under 'Related to this session' while that task is on the desk or in flight.",
    )
    relate_parser.add_argument("report_id", help="stable report ID: <home-label-slug>/<task-id>")
    relate_parser.add_argument("--to", required=True, metavar="TASK", help="backlog task id")
    relate_parser.add_argument("--remove", action="store_true", help="remove the relation instead")
    report_target(relate_parser)
    relate_parser.set_defaults(handler=reports_relate)

    calls_parser = subparsers.add_parser(
        "calls", help="set relevance and relations for past captain calls",
        description="Keep past captain calls (board items and held backlog items) relevant. Stored in Quarterdeck's local state, never in a home.",
    )
    command_parsers["calls"] = calls_parser
    calls_subparsers = calls_parser.add_subparsers(dest="calls_command", required=True)
    calls_list_parser = calls_subparsers.add_parser("list", help="list captain calls and their relevance", description="List captain calls with relevance and band.")
    report_target(calls_list_parser)
    calls_list_parser.set_defaults(handler=calls_list)
    calls_relevance_parser = calls_subparsers.add_parser(
        "set-relevance", help="mark a captain call current or stale",
        description="Stale calls (one-offs or already decided) are set aside from the desk, week and parked bands. Unclassified calls are current.",
    )
    calls_relevance_parser.add_argument("task", help="backlog task id, or <home-label-slug>/<task-id>")
    calls_relevance_parser.add_argument("relevance", choices=RELEVANCE_VALUES)
    calls_relevance_parser.add_argument("--reason", help="why, shown beside the stale call")
    report_target(calls_relevance_parser)
    calls_relevance_parser.set_defaults(handler=calls_set_relevance)
    calls_relate_parser = calls_subparsers.add_parser(
        "relate", help="relate a captain call to a task",
        description="Record that the call relates to TASK, so it appears under 'Related to this session' while that task is on the desk or in flight.",
    )
    calls_relate_parser.add_argument("task", help="backlog task id, or <home-label-slug>/<task-id>")
    calls_relate_parser.add_argument("--to", required=True, metavar="TASK", help="backlog task id")
    calls_relate_parser.add_argument("--remove", action="store_true", help="remove the relation instead")
    report_target(calls_relate_parser)
    calls_relate_parser.set_defaults(handler=calls_relate)

    attention_parser = subparsers.add_parser(
        "attention", help="print recent work and the decisions still in play",
        description=(
            "Print the last 12, 12-24 and 24-48 hours of work from the home's fleet ledger, and the open decisions "
            "split into relevant and background, as JSON (schema quarterdeck-attention.v1). Read-only. Exits 1 with "
            "a structured error when a Firstmate source is missing or in a version Quarterdeck does not understand."
        ),
    )
    command_parsers["attention"] = attention_parser
    attention_parser.add_argument("--json", action="store_true", help="print the view as JSON")
    attention_parser.add_argument("--check", action="store_true", help="validate the Firstmate sources and print a one-line summary")
    attention_parser.add_argument("--all-decisions", action="store_true", help="also list the background decisions, not just their count")
    attention_parser.add_argument("--project", help="show this project's waiting items instead of the saved priority projects")
    attention_parser.add_argument("--view", nargs="?", const="", metavar="NAME", help="also build this saved view's sections (the startup view when no name is given)")
    attention_parser.add_argument("--more", action="store_true", help="also list the waiting items outside the priority projects")
    attention_parser.add_argument("--preferences", type=Path, help="preferences file; defaults to ~/.config/quarterdeck/preferences.json")
    attention_parser.add_argument("--home", help="use this Firstmate home instead of configured homes or FM_HOME")
    attention_parser.add_argument("--config", type=Path, help="optional JSON config file")
    attention_parser.set_defaults(handler=attention)

    priorities_parser = subparsers.add_parser(
        "priorities", help="show, set or clear up to three priority projects",
        description=(
            "Keep the captain's priority projects (at most three, each a project registered in a selected home) in the "
            "preferences file. Cadence adopts this file in its own change. Writes only that file."
        ),
    )
    command_parsers["priorities"] = priorities_parser
    priorities_subparsers = priorities_parser.add_subparsers(dest="priorities_command", required=True)

    def priorities_target(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--preferences", type=Path, help="preferences file; defaults to ~/.config/quarterdeck/preferences.json")
        command_parser.add_argument("--home", help="use this Firstmate home instead of configured homes or FM_HOME")
        command_parser.add_argument("--config", type=Path, help="optional JSON config file")

    priorities_show_parser = priorities_subparsers.add_parser("show", help="print the priority projects")
    priorities_show_parser.add_argument("--json", dest="as_json", action="store_true", help="print JSON")
    priorities_target(priorities_show_parser)
    priorities_show_parser.set_defaults(handler=priorities)
    priorities_set_parser = priorities_subparsers.add_parser(
        "set", help="replace the priority projects", description="Replace the priority projects with one to three registered project names.",
    )
    priorities_set_parser.add_argument("projects", nargs="+", metavar="PROJECT", help=f"up to {PRIORITY_LIMIT} registered project names")
    priorities_target(priorities_set_parser)
    priorities_set_parser.set_defaults(handler=priorities)
    priorities_clear_parser = priorities_subparsers.add_parser("clear", help="remove all priority projects")
    priorities_target(priorities_clear_parser)
    priorities_clear_parser.set_defaults(handler=priorities)

    views_parser = subparsers.add_parser(
        "views", help="list, show, save or choose the startup briefing view",
        description=(
            "Keep saved briefing views (ordered sections: waiting, recent, done, priorities, project:NAME, information) beside "
            "the priority projects in the preferences file, and choose the one that opens at startup. Writes only that file."
        ),
    )
    command_parsers["views"] = views_parser
    views_subparsers = views_parser.add_subparsers(dest="views_command", required=True)

    def views_target(command_parser: argparse.ArgumentParser, homes: bool = False) -> None:
        command_parser.add_argument("--preferences", type=Path, help="preferences file; defaults to ~/.config/quarterdeck/preferences.json")
        if homes:
            command_parser.add_argument("--home", help="use this Firstmate home instead of configured homes or FM_HOME")
            command_parser.add_argument("--config", type=Path, help="optional JSON config file")

    for command, text in (("list", "print the saved views"), ("show", "print one saved view")):
        views_read_parser = views_subparsers.add_parser(command, help=text)
        if command == "show":
            views_read_parser.add_argument("name", metavar="NAME", help="saved view name")
        views_read_parser.add_argument("--json", dest="as_json", action="store_true", help="print JSON")
        views_target(views_read_parser)
        views_read_parser.set_defaults(handler=views)
    views_save_parser = views_subparsers.add_parser(
        "save", help="save a view", description="Save a view (replacing one with the same name) from ordered sections.",
    )
    views_save_parser.add_argument("name", metavar="NAME", help=f"view name, up to {VIEW_NAME_LIMIT} characters")
    views_save_parser.add_argument("--section", action="append", required=True, metavar="SECTION",
                                   help="waiting, recent, done, priorities, information or project:NAME; repeat, top first")
    views_save_parser.add_argument("--default", action="store_true", help="also open this view at startup")
    views_target(views_save_parser, homes=True)
    views_save_parser.set_defaults(handler=views)
    views_default_parser = views_subparsers.add_parser("default", help="choose the startup view")
    views_default_parser.add_argument("name", metavar="NAME", help="saved view name")
    views_target(views_default_parser, homes=True)
    views_default_parser.set_defaults(handler=views)

    export_parser = subparsers.add_parser(
        "export", help="write the data snapshot for the cloud artifact",
        description=(
            "Write the desk, this week, related items and the report list as JSON (stdout by default), and with "
            "--artifact a ready-to-publish page built from templates/artifact.html.tmpl. Reads homes only; "
            "report Markdown travels for waiting reports (see --bodies)."
        ),
    )
    command_parsers["export"] = export_parser
    export_parser.add_argument("--output", help="write the snapshot JSON to this file")
    export_parser.add_argument("--artifact", help="write the artifact page (snapshot embedded) to this file")
    export_parser.add_argument(
        "--bodies", choices=BODIES_MODES, default="all",
        help=("which waiting reports carry their Markdown: all (default), desk (only reports linked to a desk or "
              "this-week call, or related to the session), or none"),
    )
    export_parser.add_argument("--home", help="use this Firstmate home instead of configured homes or FM_HOME")
    export_parser.add_argument("--config", type=Path, help="optional JSON config file")
    export_parser.add_argument("--title", help="override the page title")
    export_parser.add_argument("--no-snapshot", action="store_true", help="skip the Firstmate bearings snapshot script")
    export_parser.set_defaults(handler=export_snapshot)

    import_parser = subparsers.add_parser(
        "import", help="apply the cloud artifact's mark and discard actions",
        description=(
            "Apply the actions the artifact recorded (a JSON list, or {\"actions\": [...]}) to Quarterdeck's local "
            "state, oldest first. An action whose report changed since the export is skipped."
        ),
    )
    command_parsers["import"] = import_parser
    import_parser.add_argument("file", help="actions JSON file, or - for standard input")
    import_parser.add_argument("--dry-run", action="store_true", help="report what would apply without changing state")
    import_parser.add_argument("--home", help="use this Firstmate home instead of configured homes or FM_HOME")
    import_parser.add_argument("--config", type=Path, help="optional JSON config file")
    import_parser.set_defaults(handler=import_actions)

    reactions_parser = subparsers.add_parser(
        "reactions", help="deliver the cloud artifact's reactions to Firstmate",
        description=(
            "Deliver the reactions recorded in the artifact (collection qd-inbox) to Firstmate as notes through each "
            "home's bin/fm-inbox.sh. Reactions already delivered are skipped."
        ),
    )
    command_parsers["reactions"] = reactions_parser
    reactions_subparsers = reactions_parser.add_subparsers(dest="reactions_command", required=True)
    reactions_import_parser = reactions_subparsers.add_parser(
        "import", help="deliver reactions from a JSON file",
        description=(
            "Read a JSON list, or {\"reactions\": [...]}, of qd-inbox documents and send each one that has not been "
            "delivered to Firstmate, oldest first. Delivered ids are kept in the local state, so none is sent twice."
        ),
    )
    reactions_import_parser.add_argument("file", help="reactions JSON file, or - for standard input")
    reactions_import_parser.add_argument("--dry-run", action="store_true", help="report what would be delivered without sending")
    reactions_import_parser.add_argument("--home", help="use this Firstmate home instead of configured homes or FM_HOME")
    reactions_import_parser.add_argument("--config", type=Path, help="optional JSON config file")
    reactions_import_parser.set_defaults(handler=import_reactions)

    add_parser = subparsers.add_parser("add", help="register a Firstmate home", description="Validate and register a Firstmate home.")
    command_parsers["add"] = add_parser
    add_parser.add_argument("home", help="Firstmate home containing data/backlog.md")
    add_parser.add_argument("--label", help="display label; defaults to the home's last path component")
    add_parser.add_argument("--config", type=Path, help="JSON config file; defaults to the private user config")
    add_parser.set_defaults(handler=add_home)

    list_parser = subparsers.add_parser("list", help="show registered Firstmate homes", description="List registered Firstmate homes.")
    command_parsers["list"] = list_parser
    list_parser.add_argument("--config", type=Path, help="JSON config file; defaults to the private user config")
    list_parser.set_defaults(handler=list_homes)

    remove_parser = subparsers.add_parser(
        "remove", help="unregister a home by label or path", description="Unregister a home without changing its data."
    )
    command_parsers["remove"] = remove_parser
    remove_parser.add_argument("target", help="registered label or Firstmate home path")
    remove_parser.add_argument("--config", type=Path, help="JSON config file; defaults to the private user config")
    remove_parser.set_defaults(handler=remove_home)

    board_parser = subparsers.add_parser(
        "board", help="add, touch, remove, or list captain board items, or record operator activity",
        description=(
            "Keep a home's captain board current by task id. Writes only <data>/captain-board.json, atomically "
            "and under a lock, and refuses to overwrite a board it cannot read. render and serve stay read-only."
        ),
    )
    command_parsers["board"] = board_parser
    board_subparsers = board_parser.add_subparsers(dest="board_command", required=True)

    def board_target(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--home", help="Firstmate home whose data/captain-board.json to use (default FM_HOME)")
        command_parser.add_argument("--data-dir", help="use this data directory instead of <home>/data")

    upsert_parser = board_subparsers.add_parser(
        "upsert", help="add or update the item for a task",
        description=(
            "Add the board item for TASK, or update the one that already names it. A new item needs --group, "
            "--title and --ask. An empty value clears --detail, --topic, --project, --brief-url or --recommendation. "
            "touched_at changes only with --touch or --touched-at."
        ),
    )
    upsert_parser.add_argument("task", help="backlog task id; route/id names a secondmate's task")
    upsert_parser.add_argument("--id", help="board item id (default: the task id)")
    upsert_parser.add_argument("--group", help=f"one of {', '.join(BOARD_GROUP_KEYS)}")
    upsert_parser.add_argument("--title", help="short name the captain can say aloud")
    upsert_parser.add_argument("--ask", help="one plain-language line: what is wanted")
    upsert_parser.add_argument("--detail", help="short Markdown shown when the item is opened")
    upsert_parser.add_argument("--topic", help="optional topic label")
    upsert_parser.add_argument("--project", help="project the item belongs to")
    upsert_parser.add_argument("--brief-url", help="http(s) link to the decision brief")
    upsert_parser.add_argument("--recommendation", help="one-line recommendation shown as a chip")
    upsert_parser.add_argument("--link", dest="links", action="append", metavar="LABEL=URL", help="replace the links; repeat for several")
    upsert_parser.add_argument("--touch", action="store_true", help="also record that the captain engaged with it now")
    upsert_parser.add_argument("--touched-at", metavar="ISO", help="record the captain's engagement at this time")
    board_target(upsert_parser)
    upsert_parser.set_defaults(handler=board_upsert)

    touch_parser = board_subparsers.add_parser(
        "touch", help="record that the captain engaged with a task now",
        description=(
            "Set touched_at on every board item that names TASK; a newer recorded time is kept. "
            "Exits 1 when no item names it."
        ),
    )
    touch_parser.add_argument("task", help="backlog task id")
    touch_parser.add_argument("--at", metavar="ISO", help="engagement time (default now)")
    board_target(touch_parser)
    touch_parser.set_defaults(handler=board_touch)

    active_parser = board_subparsers.add_parser(
        "active", help="record that the operator sent a message now",
        description=(
            "Record the operator's latest message, which groups the desk by session. A gap of more than "
            f"{int(SESSION_IDLE.total_seconds() // 3600)} hours since the previous message, or --new-session, "
            "starts a new session."
        ),
    )
    active_parser.add_argument("--new-session", action="store_true", help="start a new session at this message")
    board_target(active_parser)
    active_parser.set_defaults(handler=board_active)

    board_remove_parser = board_subparsers.add_parser(
        "remove", help="remove the items for a task",
        description="Remove every board item that names TASK, or only --id. Removing nothing succeeds.",
    )
    board_remove_parser.add_argument("task", help="backlog task id")
    board_remove_parser.add_argument("--id", help="remove only this board item id")
    board_target(board_remove_parser)
    board_remove_parser.set_defaults(handler=board_remove)

    board_list_parser = board_subparsers.add_parser(
        "list", help="print the board items", description="Print task, id, group, touched_at and title per item.",
    )
    board_list_parser.add_argument("--json", dest="as_json", action="store_true", help="print the raw board JSON")
    board_target(board_list_parser)
    board_list_parser.set_defaults(handler=board_list)

    install_parser = subparsers.add_parser(
        "install", help="install or uninstall Quarterdeck", description="Run the user-local shell installer."
    )
    command_parsers["install"] = install_parser
    install_parser.add_argument("--uninstall", action="store_true", help="remove installer-created files and keep config/data")
    install_parser.set_defaults(handler=run_installer)

    update_parser = subparsers.add_parser(
        "update", help="update an installed Quarterdeck", description="Run the user-local shell installer to update the installed checkout."
    )
    command_parsers["update"] = update_parser
    update_parser.set_defaults(handler=run_installer, uninstall=False)

    help_parser = subparsers.add_parser(
        "help", help="show command and documentation help", description="Discover commands and documentation."
    )
    command_parsers["help"] = help_parser
    help_parser.add_argument("topic", nargs="?", help="show help for one command")
    help_parser.add_argument("--json", dest="as_json", action="store_true", help="print a machine-readable command index")
    help_parser.set_defaults(handler=show_help, root_parser=parser, command_parsers=command_parsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
