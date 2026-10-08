#!/usr/bin/env python3
"""Build a local, read-only review page from a Firstmate home."""

from __future__ import annotations

import argparse
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
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
        reports.append(Report(
            title, report_path.resolve(), report_recommendation(source), source,
            hashlib.sha256(raw_source).hexdigest(),
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
        "done", "closed", "finished", "resolved", "complete", "completed", "archived", "merged",
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


def snapshot_counts(snapshot: HomeSnapshot, show_all: bool = False) -> tuple[int, ...]:
    groups = attention_groups(snapshot, show_all)
    counts = tuple(len(groups[name]) for name in ("held", "reviews", "in_flight", "blocked"))
    if show_all:
        return counts + (len(groups["other"]), len(snapshot.reports) if not snapshot.error else 0)
    return counts


def hidden_summary(snapshot: HomeSnapshot, groups: dict[str, list[Record]]) -> str | None:
    visible = {id(record) for name in ("held", "reviews", "in_flight", "blocked") for record in groups[name]}
    hidden = [
        record for record in snapshot.records
        if id(record) not in visible and not is_cancelled_state(state_for(record))
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
            linked_reports.update(report.path for report in snapshot.reports if not report.reviewed)
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
    review_label = "Reviewed" if report.reviewed else "Needs review"
    parts = [
        f'<article class="card"{element_id}><p class="eyebrow">Scout report · {review_label} · '
        f'<code>{html.escape(report.report_id)}</code></p>',
        f'<h3>{html.escape(report.title)}</h3>{recommendation}',
        f'<p class="links"><a href="{html.escape(name, quote=True)}">Read report '
        f'<code>{html.escape(report.report_id)}</code> <span class="path">{html.escape(relative)}</span></a></p>',
    ]
    if lavish and not report.reviewed:
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


def home_panel(snapshot: HomeSnapshot, lavish: bool = False, show_all: bool = False) -> str:
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
        counts = snapshot_counts(snapshot, show_all)
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
            needs_review = [report for report in snapshot.reports if not report.reviewed]
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
            summary = hidden_summary(snapshot, groups)
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
        body.extend(home_panel(child, lavish, show_all) for child in snapshot.children)
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


def load_review_marks(path: Path) -> dict[str, str]:
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
           or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
           for key, fingerprint in marks.items()):
        raise ValueError(f"invalid review state entry: {path}")
    return dict(marks)


def write_review_marks(path: Path, marks: dict[str, str]) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to replace symbolic-link review state: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump({"version": 1, "reviewed": marks}, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


@contextmanager
def review_marks_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(f"{path.name}.lock")
    if lock_path.is_symlink():
        raise ValueError(f"refusing symbolic-link review state lock: {lock_path}")
    with lock_path.open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def update_review_mark(path: Path, report_id: str, fingerprint: str | None) -> None:
    with review_marks_lock(path):
        marks = load_review_marks(path)
        if fingerprint is None:
            marks.pop(report_id, None)
        else:
            marks[report_id] = fingerprint
        write_review_marks(path, marks)


def apply_review_marks(snapshots: list[HomeSnapshot], marks: dict[str, str]) -> None:
    snapshots = all_snapshots(snapshots)
    seen_report_ids: set[str] = set()
    ambiguous_report_ids: set[str] = set()
    for snapshot in snapshots:
        for report in snapshot.reports:
            report.report_id = report_reference(snapshot.spec.label, report.path.parent.name)
            if report.report_id in seen_report_ids:
                ambiguous_report_ids.add(report.report_id)
            else:
                seen_report_ids.add(report.report_id)
    for snapshot in snapshots:
        for report in snapshot.reports:
            report.reviewed = (
                report.report_id not in ambiguous_report_ids
                and marks.get(report.report_id) == report.fingerprint
            )


def report_discovery_errors(snapshots: list[HomeSnapshot]) -> list[tuple[HomeSnapshot, str]]:
    errors: list[tuple[HomeSnapshot, str]] = []
    for snapshot in all_snapshots(snapshots):
        if snapshot.error:
            errors.append((snapshot, f"home could not be read: {snapshot.error}"))
        if snapshot.registry_error:
            errors.append((snapshot, f"secondmate registry could not be read: {snapshot.registry_error}"))
        errors.extend((snapshot, warning) for warning in snapshot.warnings)
    return errors


def report_matches(snapshots: list[HomeSnapshot], requested_id: str) -> list[tuple[HomeSnapshot, Report]]:
    return [
        (snapshot, report)
        for snapshot in all_snapshots(snapshots)
        for report in snapshot.reports
        if report.report_id == requested_id
    ]


def unique_report(snapshots: list[HomeSnapshot], requested_id: str) -> tuple[HomeSnapshot, Report]:
    incomplete_local_homes = [
        (snapshot, error)
        for snapshot, error in report_discovery_errors(snapshots)
        if snapshot.spec.remote_host is None
    ]
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


def load_report_snapshots(args: argparse.Namespace) -> tuple[list[HomeSnapshot], Path]:
    if args.config is None:
        configured_path = default_config_path()
        args.config = configured_path if configured_path.is_file() else None
    config = load_config(args.config)
    specs, legacy_single_home = resolve_home_specs(args, config)
    snapshots = [load_home_snapshot(spec, discover_secondmates=True) for spec in specs]
    if legacy_single_home and snapshots[0].error:
        raise ValueError(snapshots[0].error)
    state_path = validate_review_state_location(snapshots)
    return snapshots, state_path


def load_report_context(args: argparse.Namespace) -> tuple[list[HomeSnapshot], Path, dict[str, str]]:
    snapshots, state_path = load_report_snapshots(args)
    marks = load_review_marks(state_path)
    apply_review_marks(snapshots, marks)
    return snapshots, state_path, marks


def build_html(
    homes: list[HomeSnapshot], title: str, lavish: bool = False, show_all: bool = False,
    bearings: Bearings | None = None, refresh_seconds: int | None = None,
) -> str:
    snapshots = all_snapshots(homes)
    for snapshot in snapshots:
        for report in snapshot.reports:
            if not report.report_id:
                report.report_id = report_reference(snapshot.spec.label, report.path.parent.name)
    counts = [sum(snapshot_counts(snapshot, show_all)[index] for snapshot in snapshots) for index in range(6 if show_all else 4)]
    metric_labels = ["Held for captain", "Review-ready PRs", "In-flight work", "Blocked for captain/external"]
    if show_all:
        metric_labels.extend(["Other backlog items", "Scout reports"])
    counts.append(sum(not report.reviewed for snapshot in snapshots for report in snapshot.reports))
    metric_labels.append("Reports needing review")
    generated = bearings.generated if bearings else datetime.now(timezone.utc)
    timestamp = generated.strftime("%Y-%m-%d %H:%M:%S UTC")
    board = board_view(homes)
    board_block = board_html(board, generated) if board else ""
    board_status = board_status_html(board, generated) if board else ""
    bearings_block = bearings_html(bearings, collapse_reviews=board is not None) if bearings else ""
    refresh_script = (
        f"""
  <script>
    setTimeout(function reload() {{
      if (document.querySelector("details[open]")) {{ setTimeout(reload, 15000); return; }}
      location.reload();
    }}, {int(refresh_seconds) * 1000});
  </script>""" if refresh_seconds else ""
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
    header {{ background: var(--head); color: #fffdf8; padding: clamp(2rem, 7vw, 5rem) max(1.2rem, calc((100vw - 1080px) / 2)); border-bottom: 5px solid var(--gold); }}
    .kicker {{ color: #d8c49b; text-transform: uppercase; letter-spacing: .14em; font-size: .76rem; font-weight: 700; }}
    h1 {{ margin: .25rem 0 .35rem; font: 700 clamp(2.5rem, 7vw, 4.6rem)/1 Georgia, serif; letter-spacing: -.04em; }}
    header p {{ max-width: 46rem; margin: .7rem 0 0; color: #e4e8e8; }}
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
    .board-topic {{ margin: 1.2rem 0 .2rem; color: var(--sea); font-size: 1rem; text-transform: uppercase; letter-spacing: .08em; }}
    .stale {{ color: #ffd48a; }}
    details.reviews > summary {{ display: flex; justify-content: space-between; gap: 1rem; align-items: center; min-height: 3rem; cursor: pointer; list-style: none; }}
    details.reviews > summary h2 {{ display: inline; }}
    @media (prefers-color-scheme: dark) {{
      :root {{ --navy: #a9cdee; --sea: #5fc2b9; --sand: #11181f; --paper: #1a242d; --ink: #e3e8ec; --muted: #9aa8b3; --line: #33434f; --gold: #e0a24e; --head: #0d2438; --soft: #ffffff10; --warn-bg: #2d2412; --warn-ink: #f0d9a8; --fail-bg: #331b17; --fail-ink: #f3c4bb; --chip: #1f3a37; }}
      .board-num {{ color: #0b1a18; }}
    }}
    @media (max-width: 640px) {{ .summary {{ grid-template-columns: repeat(2, 1fr); }} }}
    @media print {{ body {{ background: #fff; }} header {{ padding: 1.2rem; }} main {{ width: 100%; margin: 1rem 0; }} .card {{ break-inside: avoid; }} }}
  </style>
</head>
<body>
  <header>
    <div class="kicker">Firstmate · local review</div>
    <h1>⚓ {html.escape(title)}</h1>
    <p>{html.escape(introductory_text)}</p>
    <p class="generated">Generated at {timestamp}{refresh_note}</p>
    {board_status}
  </header>
  <main>
    {board_block}
    {bearings_block}
    <h2 class="detail-heading">Details by home</h2>
    <div class="summary" aria-label="Review counts">
      {metrics}
    </div>
    {''.join(home_panel(home, lavish, show_all) for home in homes)}
    <footer>Read-only snapshot · generated {timestamp} · source files open as readable pages beside this one.</footer>
  </main>
  {lavish_script}{refresh_script}
</body>
</html>
'''


# --- Bearings view -----------------------------------------------------------

SNAPSHOT_SCRIPT = Path("bin") / "fm-bearings-snapshot.sh"
SNAPSHOT_TIMEOUT_SECONDS = 15
ITEM_ATTRIBUTE_RE = re.compile(
    r"\((?P<key>repo|kind|since|hold-kind|hold|held|merged|done|closed|until|hold-until|priority|pr|state|status|review_ready)(?::\s*|\s+)", re.I
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
    first_attr = len(rest)
    position = 0
    while True:
        match = ITEM_ATTRIBUTE_RE.search(rest, position)
        if not match:
            break
        close = balanced_close(rest, match.start())
        key = match.group("key").lower()
        attrs.setdefault(key, rest[match.end():close].strip())
        first_attr = min(first_attr, match.start())
        position = close + 1
    title_part = rest[:first_attr]
    blocked_by = ""
    blocker = re.search(r"\bblocked-by:\s*(\S+)", title_part)
    if blocker:
        blocked_by = blocker.group(1)
        title_part = title_part.replace(blocker.group(0), " ")
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
        hold_reason=markdown_text(attrs.get("hold", "")), hold_kind=hold_kind,
        held=attrs.get("held", "").strip().lower() in TRUE_VALUES,
        blocked_by=blocked_by or attrs.get("blocked-by", ""),
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
            attrs={"merged": merged_date} if merged_date else {},
            hold_reason=_blank(value_for(record.fields, "hold_reason")),
            hold_kind=normalized_state(_blank(value_for(record.fields, "hold_kind"))),
            held=value_for(record.fields, "held").lower() in TRUE_VALUES,
            blocked_by=_blank(value_for(record.fields, "blocked_by", "waiting_on", "waiting_for")),
            report_dir=record.report.parent.name if record.report else "",
            pr_url=record.pr_url or "", review_ready=review_flag,
        )
        closed = value_for(record.fields, "closed")
        if field_is_set(closed):
            item.attrs["done"] = closed
        reason = _blank(value_for(record.fields, "blocked_reason", "block_reason"))
        if reason and not item.hold_reason:
            item.hold_reason = reason
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
        return None, "the snapshot script printed invalid UTF-8"
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
    def lookup(self, snapshot: HomeSnapshot, owner: str, item_id: str) -> tuple[HomeSnapshot, BacklogItem | None]:
        """Find the backlog entry for an id, using the secondmate's home when the owner is one."""
        target = snapshot
        if owner and owner != "(main)":
            target = next((child for child in snapshot.children if child.spec.route_id == owner), snapshot)
        if target.error or target.backlog_markdown is None:
            return target, None
        items = getattr(target, "_items", None)
        if items is None:
            items = {item.id: item for item in backlog_items(target.backlog_markdown, target)}
            setattr(target, "_items", items)
        return target, items.get(item_id)

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

    def in_flight_owner(self, snapshot: HomeSnapshot, item_id: str) -> str:
        route_id, separator, _ = item_id.partition("/")
        return self.home_label(snapshot, route_id if separator else "(main)")

    # -- snapshot path --
    def from_snapshot(self, snapshot: HomeSnapshot, data: dict[str, object]) -> None:
        for row in _rows(data, "decisions_open"):
            owner = _text(row.get("owner"))
            key = _text(row.get("key")) or _text(row.get("id"))
            target, item = self.lookup(snapshot, owner, key)
            if item and is_cancelled_state(item.state):
                continue
            summary = _text(row.get("summary")) or key
            bitem = BItem(title=summary, owner=self.home_label(snapshot, owner),
                          item_id=key, status=_text(row.get("verb")))
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
            target, item = self.lookup(snapshot, owner, item_id)
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
            _, item = self.lookup(snapshot, owner, item_id.split("/")[-1])
            if is_cancelled_state(_text(row.get("state"))) or (item and is_cancelled_state(item.state)):
                continue
            self.result.underway.append(BItem(
                title=_text(row.get("name")) or item_id, owner=self.in_flight_owner(snapshot, item_id), item_id=item_id,
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
            item_id = _text(row.get("id")).split("/")[-1]
            target, item = self.lookup(snapshot, owner, item_id)
            if item and is_cancelled_state(item.state):
                continue
            waits = " — ".join(part for part in (_blank(_text(row.get("blocked_by"))), _blank(_text(row.get("reason")))) if part)
            bitem = BItem(title=_text(row.get("title")) or item_id, owner=self.home_label(snapshot, owner),
                          item_id=item_id, filed=_text(row.get("filed")), waits_on=waits or "queue order")
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
        self.result.call.sort(key=lambda item: parse_when(item.filed) or datetime.max.replace(tzinfo=timezone.utc))
        self.result.reports = [
            (snapshot, report)
            for snapshot in all_snapshots(self.homes) for report in snapshot.reports if not report.reviewed
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
        label = "Landed" if section == "landed" else "Filed"
        when.append(f"{label} {html.escape(item.filed[:16].replace('T', ' '))}")
        if waited and section != "landed":
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
        parts.append(f'<details><summary>Full task description</summary><div class="body">{render_markdown(item.body)}</div></details>')
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


def bearings_html(bearings: Bearings, collapse_reviews: bool = False) -> str:
    now = bearings.generated
    sections = [
        ("call", "Captain's Call", "Decisions and reviews waiting on you, with everything needed to answer.",
         bearings.call, "Nothing is waiting on a captain decision."),
        ("landed", "Recently Landed", "Merged pull requests and finished work.",
         bearings.landed, "Nothing has landed recently."),
        ("underway", "Underway", "Work that is live right now.", bearings.underway, "Nothing is underway."),
        ("charted", "Charted Next", "Queued or gated work, and what each item waits on.",
         bearings.charted, "Nothing is queued or gated."),
    ]
    review_cards = []
    for snapshot, report in bearings.reports:
        home = snapshot.spec.home or Path(".")
        page = source_page_filename("report", home, report.path)
        recommendation = (
            f'<p><strong>Recommendation:</strong> {html.escape(report.recommendation)}</p>' if report.recommendation else ""
        )
        review_cards.append(
            f'<article class="card"><p class="eyebrow">{html.escape(snapshot.spec.label)} · <code>{html.escape(report.report_id)}</code></p>'
            f'<h3>{html.escape(report.title)}</h3>{recommendation}'
            f'<p class="links"><a href="{html.escape(page, quote=True)}">Read report</a> · '
            f'<span class="path">after reading: quarterdeck reports mark-reviewed {html.escape(report.report_id)}</span></p></article>'
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


# --- Captain board -----------------------------------------------------------

BOARD_FILENAME = "captain-board.json"
BOARD_VERSION = 1
BOARD_GROUPS = (
    ("merge", "Ready to merge", "Pull requests ready for your word."),
    ("approve", "Approvals and handovers", "Things waiting for your approval or a handover."),
    ("decide", "Decisions", "Choices only you can make."),
    ("forward", "Questions to forward", "Questions to pass on to other people."),
    ("read", "Reviews at your leisure", "Reading you can do when you have time."),
)
BOARD_GROUP_KEYS = tuple(key for key, _title, _description in BOARD_GROUPS)
UNSORTED_TITLE = "Not yet sorted"
UNSORTED_DESCRIPTION = "Waiting on you in the backlog, but not on the board yet. Raw note shown."
OTHER_TOPIC = "Other decisions"


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


@dataclass
class CaptainBoard:
    path: Path
    updated_at: datetime | None = None
    items: list[BoardItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class BoardEntry:
    item: BoardItem
    home: str
    number: int = 0


@dataclass
class BoardView:
    groups: dict[str, list[BoardEntry]]
    unsorted: list[BoardEntry]
    hidden: int
    warnings: list[str]
    status: list[tuple[str, datetime | None, str]]  # (home label, updated_at, stale reason)
    multiple_homes: bool = False


def _board_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


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
    if data.get("version") != BOARD_VERSION:
        board.warnings.append(f"{name} has version {data.get('version')!r}, expected {BOARD_VERSION}; reading it anyway.")
    updated = data.get("updated_at")
    if isinstance(updated, str):
        try:
            parsed = datetime.fromisoformat(updated.strip().replace("Z", "+00:00"))
            board.updated_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    if board.updated_at is None:
        board.warnings.append(f"{name} has a missing or invalid updated_at (expected ISO 8601), so its age is unknown.")
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
        if not title:
            problems.append("title is missing")
        if not ask:
            problems.append("ask is missing")
        if problems:
            board.warnings.append(f"{where} was skipped: {'; '.join(problems)}.")
            continue
        assert identifier and title and ask
        seen.add(identifier)
        optional: dict[str, str] = {}
        for key in ("detail", "topic", "task"):
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
            url = _board_text(link.get("url")) if isinstance(link, dict) else None
            if not label or not url or urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
                board.warnings.append(f"{where}: a link needs a label and an http(s) url and was ignored.")
                continue
            links.append((label, url))
        board.items.append(BoardItem(
            id=identifier, group=str(group), title=title, ask=ask, links=links, **optional,
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


def board_view(homes: list[HomeSnapshot]) -> BoardView | None:
    """Merge the homes' boards, drop settled items, and add backlog items the boards miss."""
    boarded = [home for home in homes if home.board is not None]
    if not boarded:
        return None
    view = BoardView({key: [] for key in BOARD_GROUP_KEYS}, [], 0, [], [])
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
        stale = ""
        if board.updated_at is None:
            stale = "its update time is unknown"
        elif home.backlog_changed and board.updated_at < home.backlog_changed:
            stale = "the backlog changed after the board was last updated"
        view.status.append((label, board.updated_at, stale))
        if home.error:
            view.warnings.append(f"{label}: the backlog could not be read, so board items were not cross-checked.")
        for item in board.items:
            if item.task and not home.error:
                target, task_id = home, item.task
                route, separator, rest = item.task.partition("/")
                child = next((c for c in home.children if separator and c.spec.route_id == route), None)
                if child is not None:
                    target, task_id = child, rest
                if not target.error:
                    referenced.add((id(target), task_id))
                    backlog_item = items_of(target).get(task_id)
                    if backlog_item is None or not needs_captain(backlog_item):
                        view.hidden += 1
                        continue
            view.groups[item.group].append(BoardEntry(item, label))
    for home in boarded:
        for snapshot in all_snapshots([home]):
            for backlog_item in items_of(snapshot).values():
                if (id(snapshot), backlog_item.id) in referenced or not needs_captain(backlog_item):
                    continue
                links = [("Pull request", backlog_item.pr_url)] if PR_URL_RE.fullmatch(backlog_item.pr_url or "") else []
                view.unsorted.append(BoardEntry(BoardItem(
                    id=backlog_item.id, group="unsorted", title=backlog_item.title, ask=unsorted_note(backlog_item),
                    detail=f"Backlog item `{backlog_item.id}`", task=backlog_item.id, links=links,
                ), snapshot.spec.label))
    view.multiple_homes = len({entry.home for entries in view.groups.values() for entry in entries} | {e.home for e in view.unsorted}) > 1
    number = 0
    for key in BOARD_GROUP_KEYS:
        entries = view.groups[key]
        if key == "decide":
            topics = list(dict.fromkeys(entry.item.topic for entry in entries if entry.item.topic))
            order = {topic: index for index, topic in enumerate(topics)}
            entries.sort(key=lambda entry: order.get(entry.item.topic, len(topics)))
        for entry in entries:
            number += 1
            entry.number = number
    for entry in view.unsorted:
        number += 1
        entry.number = number
    return view


def board_age_text(updated: datetime | None, now: datetime) -> str:
    if updated is None:
        return "at an unknown time"
    if (now - updated).total_seconds() < 120:
        return "just now"
    return f"{waited_text(updated.isoformat(), now)} ago"


def board_status_html(view: BoardView, now: datetime) -> str:
    lines = []
    for label, updated, stale in view.status:
        prefix = f"{html.escape(label)}: " if len(view.status) > 1 else ""
        stamp = f" ({updated.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})" if updated else ""
        flag = f' <strong class="stale">⚠ Board may be out of date: {html.escape(stale)}.</strong>' if stale else ""
        lines.append(f'<p class="generated">{prefix}Board updated {html.escape(board_age_text(updated, now))}{stamp}{flag}</p>')
    return "".join(lines)


def board_entry_html(entry: BoardEntry, show_home: bool) -> str:
    item = entry.item
    home = f'<span class="board-home">{html.escape(entry.home)}</span>' if show_home else ""
    parts = [
        f'<p class="board-ask">{html.escape(item.ask)}</p>',
    ]
    if item.detail:
        parts.append(f'<div class="body">{render_markdown(item.detail)}</div>')
    if item.links:
        links = "".join(
            f'<li><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a> '
            f'<span class="path">{html.escape(url)}</span></li>'
            for label, url in item.links
        )
        parts.append(f'<ul class="board-links">{links}</ul>')
    return (
        f'<details class="board-item" id="item-{entry.number}"><summary>'
        f'<span class="board-num" aria-label="Number {entry.number}">{entry.number}</span>'
        f'<span class="board-text"><span class="board-title">{html.escape(item.title)}</span>{home}'
        f'<span class="board-line">{html.escape(item.ask)}</span></span></summary>'
        f'<div class="board-body">{"".join(parts)}</div></details>'
    )


def board_html(view: BoardView, now: datetime) -> str:
    out = ['<section class="board" id="board" aria-label="Waiting on you">']
    out.extend(f'<div class="warning"><p>{html.escape(warning)}</p></div>' for warning in view.warnings)
    if view.hidden:
        out.append(f'<p class="source-note">{view.hidden} board item{"s" if view.hidden != 1 else ""} already settled in the backlog and hidden.</p>')
    shown = 0
    sections = [(key, title, description, view.groups[key]) for key, title, description in BOARD_GROUPS]
    sections.append(("unsorted", UNSORTED_TITLE, UNSORTED_DESCRIPTION, view.unsorted))
    for key, title, description, entries in sections:
        if not entries:
            continue
        shown += len(entries)
        body = []
        if key == "decide" and any(entry.item.topic for entry in entries):
            current: object = object()
            for entry in entries:
                topic = entry.item.topic or OTHER_TOPIC
                if topic != current:
                    body.append(f'<h3 class="board-topic">{html.escape(topic)}</h3>')
                    current = topic
                body.append(board_entry_html(entry, view.multiple_homes))
        else:
            body.extend(board_entry_html(entry, view.multiple_homes) for entry in entries)
        out.append(
            f'<div class="dashboard-section board-group" id="board-{key}"><div class="section-head"><div>'
            f'<h2>{html.escape(title)}</h2><p>{html.escape(description)}</p></div>'
            f'<span class="count">{len(entries)}</span></div>{"".join(body)}</div>'
        )
    if not shown:
        out.append('<p class="empty">Nothing is waiting on you right now.</p>')
    out.append("</section>")
    return "".join(out)


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

    def get(self) -> dict[str, str]:
        with self.lock:
            now = self.clock()
            if self.built_at is None or now - self.built_at >= self.ttl:
                self.pages = self.build()
                self.built_at = now
            return self.pages


def site_builder(args: argparse.Namespace) -> Callable[[], dict[str, str]]:
    def build() -> dict[str, str]:
        config, homes, bearings = load_site(args)
        configured_title = config.get("page_title")
        title = args.title or (configured_title if isinstance(configured_title, str) else None) or DEFAULT_TITLE
        pages = source_pages(homes, show_all=False, bearings=bearings)
        pages["index.html"] = build_html(
            homes, title, bearings=bearings, refresh_seconds=DEFAULT_REFRESH_SECONDS
        )
        return pages
    return build


def make_handler(cache: SiteCache) -> type[BaseHTTPRequestHandler]:
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
            if is_loopback(str(self.server.server_address[0])) and not request_host_is_loopback(
                self.headers.get("Host")
            ):
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

        def refuse(self) -> None:
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = refuse

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


def serve(args: argparse.Namespace) -> int:
    servers: list[ThreadingHTTPServer] = []
    try:
        cache = SiteCache(site_builder(args))
        cache.get()
        handler = make_handler(cache)
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
        print(f"Serving Quarterdeck at http://{shown}:{port}/ (read-only; Ctrl+C to stop)", flush=True)
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
    "reports": ["how-to/review-reports.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "serve": ["how-to/serve-the-page.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "service": ["how-to/serve-the-page.md", "reference/cli-and-config.md"],
    "url": ["how-to/serve-the-page.md", "reference/cli-and-config.md"],
    "add": ["how-to/configure-multiple-homes.md", "reference/cli-and-config.md"],
    "list": ["how-to/configure-multiple-homes.md", "reference/cli-and-config.md"],
    "remove": ["how-to/configure-multiple-homes.md", "reference/cli-and-config.md"],
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
            print("ID\tReview state\tTitle\tHome")
            for snapshot, report in reports:
                state = "ID collision" if identifiers[report.report_id] > 1 else (
                    "reviewed" if report.reviewed else "needs review"
                )
                print(f"{report.report_id}\t{state}\t{report.title}\t{snapshot.spec.label}")
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
        snapshots, state_path = load_report_snapshots(args)
        apply_review_marks(snapshots, {})
        _snapshot, report = unique_report(snapshots, args.report_id)
        update_review_mark(state_path, report.report_id, report.fingerprint)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"Marked {report.report_id} reviewed for its current content.")
    return 0


def reports_unmark_reviewed(args: argparse.Namespace) -> int:
    try:
        snapshots, state_path = load_report_snapshots(args)
        apply_review_marks(snapshots, {})
        _snapshot, report = unique_report(snapshots, args.report_id)
        update_review_mark(state_path, report.report_id, None)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"Removed the reviewed mark for {report.report_id}.")
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
    if legacy_single_home and homes[0].error:
        raise ValueError(homes[0].error)
    state_path = validate_review_state_location(homes)
    apply_review_marks(homes, load_review_marks(state_path))
    bearings = build_bearings(homes, use_snapshot=not getattr(args, "no_snapshot", False))
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
        description="Serve the bearings page and its readable report and backlog pages, re-rendered from the homes on request. Read-only: GET only, no write endpoints, no login. Binds loopback by default.",
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
