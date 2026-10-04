#!/usr/bin/env python3
"""Build a local, read-only review page from a Firstmate home."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


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
    r"hold_set|state|status|closed|review_ready|pr_url|pr|report|report_path|"
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


FALSE_VALUES = {"", "-", "none", "null", "false", "no", "0", "off"}
TRUE_VALUES = {"true", "yes", "1", "on"}


def normalized_state(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def state_for(record: Record) -> str:
    return normalized_state(value_for(record.fields, "state", "status") or record.section)


def field_is_set(value: str) -> bool:
    return value.strip().lower() not in FALSE_VALUES


def is_closed(record: Record) -> bool:
    if field_is_set(value_for(record.fields, "closed")):
        return True
    return state_for(record) in {"done", "closed", "finished", "resolved", "complete", "completed", "archived", "cancelled", "canceled"}


def is_captain_held(record: Record) -> bool:
    return (
        not is_closed(record)
        and value_for(record.fields, "held").strip().lower() in TRUE_VALUES
        and normalized_state(value_for(record.fields, "hold_kind")) == "captain"
    )


def is_review_ready(record: Record) -> bool:
    if is_closed(record) or not record.pr_url:
        return False
    parsed_pr = urlparse(record.pr_url)
    if parsed_pr.scheme != "https" or parsed_pr.netloc.lower() != "github.com" or not PR_URL_RE.search(record.pr_url):
        return False
    review_flag = normalized_state(value_for(record.fields, "review_ready"))
    state = state_for(record)
    return review_flag in {"true", "yes", "1", "on", "ready", "review_ready", "reviewready"} or state in {
        "review_ready", "reviewready", "ready_for_review", "open_for_review"
    }


def is_in_flight(record: Record) -> bool:
    if is_closed(record):
        return False
    return state_for(record) in {"in_flight", "inflight", "working", "active", "running", "in_progress"}


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
            if id(record) not in assigned and predicate(record):
                groups[name].append(record)
                assigned.add(id(record))

    take("held", is_captain_held)
    take("reviews", is_review_ready)
    take("in_flight", is_in_flight)
    take("blocked", is_waiting_on_captain_or_external)
    if show_all:
        groups["other"] = [record for record in snapshot.records if id(record) not in assigned]
    return groups


def snapshot_counts(snapshot: HomeSnapshot, show_all: bool = False) -> tuple[int, ...]:
    groups = attention_groups(snapshot, show_all)
    counts = tuple(len(groups[name]) for name in ("held", "reviews", "in_flight", "blocked"))
    if show_all:
        return counts + (len(groups["other"]), len(snapshot.reports) if not snapshot.error else 0)
    return counts


def hidden_summary(snapshot: HomeSnapshot, groups: dict[str, list[Record]]) -> str | None:
    visible = {id(record) for name in ("held", "reviews", "in_flight", "blocked") for record in groups[name]}
    hidden = [record for record in snapshot.records if id(record) not in visible]
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


def write_source_pages(homes: list[HomeSnapshot], output_dir: Path, show_all: bool = False) -> None:
    for snapshot in all_snapshots(homes):
        if snapshot.spec.home is None:
            continue
        if snapshot.backlog_path and snapshot.backlog_markdown is not None:
            name = source_page_filename("backlog", snapshot.spec.home, snapshot.backlog_path)
            snapshot_path = display_path(snapshot.backlog_path, snapshot.spec.home)
            write_generated_page(
                output_dir / name,
                source_page_html(f"{snapshot.spec.label} backlog", snapshot_path, snapshot.backlog_markdown),
            )
        if show_all:
            linked_reports = {report.path for report in snapshot.reports}
        else:
            linked_reports = {
                record.report for record in snapshot.records
                if record.report and (is_captain_held(record) or is_review_ready(record))
            }
            linked_reports.update(report.path for report in snapshot.reports if not report.reviewed)
        for report in snapshot.reports:
            if report.path not in linked_reports:
                continue
            name = source_page_filename("report", snapshot.spec.home, report.path)
            write_generated_page(
                output_dir / name,
                source_page_html(report.title, display_path(report.path, snapshot.spec.home), report.markdown),
            )


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
    roots = [Path(__file__).resolve().parent] + [
        snapshot.spec.home.resolve()
        for snapshot in all_snapshots(snapshots)
        if snapshot.spec.home is not None
    ]
    for root in roots:
        try:
            state_dir.relative_to(root)
        except ValueError:
            continue
        raise ValueError("review state must be outside the Quarterdeck checkout and all selected Firstmate homes")
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


def report_matches(snapshots: list[HomeSnapshot], requested_id: str) -> list[tuple[HomeSnapshot, Report]]:
    return [
        (snapshot, report)
        for snapshot in all_snapshots(snapshots)
        for report in snapshot.reports
        if report.report_id == requested_id
    ]


def unique_report(snapshots: list[HomeSnapshot], requested_id: str) -> tuple[HomeSnapshot, Report]:
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


def load_report_context(args: argparse.Namespace) -> tuple[list[HomeSnapshot], Path, dict[str, str]]:
    if args.config is None:
        configured_path = default_config_path()
        args.config = configured_path if configured_path.is_file() else None
    config = load_config(args.config)
    specs, legacy_single_home = resolve_home_specs(args, config)
    snapshots = [load_home_snapshot(spec, discover_secondmates=True) for spec in specs]
    if legacy_single_home and snapshots[0].error:
        raise ValueError(snapshots[0].error)
    state_path = validate_review_state_location(snapshots)
    marks = load_review_marks(state_path)
    apply_review_marks(snapshots, marks)
    return snapshots, state_path, marks


def build_html(homes: list[HomeSnapshot], title: str, lavish: bool = False, show_all: bool = False) -> str:
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
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
    :root {{ color-scheme: light; --navy: #17324d; --sea: #176b69; --sand: #f3eddf; --paper: #fffdf8; --ink: #20303d; --muted: #657482; --line: #d8d3c8; --gold: #c98635; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--sand); color: var(--ink); font: 16px/1.55 system-ui, -apple-system, Segoe UI, sans-serif; }}
    header {{ background: var(--navy); color: #fffdf8; padding: clamp(2rem, 7vw, 5rem) max(1.2rem, calc((100vw - 1080px) / 2)); border-bottom: 5px solid var(--gold); }}
    .kicker {{ color: #d8c49b; text-transform: uppercase; letter-spacing: .14em; font-size: .76rem; font-weight: 700; }}
    h1 {{ margin: .25rem 0 .35rem; font: 700 clamp(2.5rem, 7vw, 4.6rem)/1 Georgia, serif; letter-spacing: -.04em; }}
    header p {{ max-width: 46rem; margin: .7rem 0 0; color: #e4e8e8; }}
    main {{ width: min(1080px, calc(100% - 2.4rem)); margin: 2rem auto 4rem; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .8rem; margin-bottom: 2rem; }}
    .metric {{ background: var(--paper); border: 1px solid var(--line); border-radius: 12px; padding: 1rem 1.1rem; }}
    .metric b {{ display: block; color: var(--sea); font: 700 1.8rem Georgia, serif; }}
    .metric span {{ color: var(--muted); font-size: .9rem; }}
    .home {{ margin: 2rem 0 2.5rem; padding: 1.2rem; background: #ffffff70; border: 1px solid var(--line); border-radius: 16px; }}
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
    .request-lavish {{ border: 1px solid var(--sea); border-radius: 7px; background: #e6f1ee; color: #124f4c; padding: .45rem .7rem; font: inherit; font-size: .86rem; font-weight: 700; cursor: pointer; }}
    .request-lavish:focus-visible {{ outline: 3px solid var(--gold); outline-offset: 2px; }}
    .request-lavish:disabled {{ opacity: .7; cursor: default; }}
    .request-status {{ color: var(--muted); font-size: .82rem; }}
    .quiet-summary {{ color: var(--muted); font-size: .82rem; margin: -.5rem 0 1rem; }}
    a {{ color: var(--sea); font-weight: 650; }}
    a:focus-visible {{ outline: 3px solid var(--gold); outline-offset: 3px; }}
    .path {{ color: var(--muted); font-weight: 400; overflow-wrap: anywhere; }}
    .empty {{ background: #ffffff80; border: 1px dashed var(--line); border-radius: 10px; color: var(--muted); padding: 1rem; }}
    .failure, .warning {{ margin: 1rem 0; border-radius: 10px; padding: 1rem; }}
    .failure {{ border: 1px solid #b8503c; background: #fff2ee; color: #6f251b; }}
    .warning {{ border: 1px solid #c98635; background: #fff8e9; color: #624313; }}
    .failure p, .warning p {{ margin: .35rem 0 0; overflow-wrap: anywhere; }}
    .secondmates {{ margin: 1.7rem 0 0; padding-left: 1rem; border-left: 3px solid var(--line); }}
    .secondmates > h3 {{ margin: 0 0 .8rem; color: var(--navy); font: 700 1.2rem Georgia, serif; }}
    .secondmates .home {{ margin: 1rem 0; background: var(--paper); }}
    footer {{ border-top: 1px solid var(--line); margin-top: 3rem; padding-top: 1rem; color: var(--muted); font-size: .85rem; }}
    @media (max-width: 640px) {{ .summary {{ grid-template-columns: repeat(2, 1fr); }} }}
    @media print {{ body {{ background: #fff; }} header {{ padding: 1.2rem; }} main {{ width: 100%; margin: 1rem 0; }} .card {{ break-inside: avoid; }} }}
  </style>
</head>
<body>
  <header>
    <div class="kicker">Firstmate · local review</div>
    <h1>⚓ {html.escape(title)}</h1>
    <p>{html.escape(introductory_text)}</p>
  </header>
  <main>
    <div class="summary" aria-label="Review counts">
      {metrics}
    </div>
    {''.join(home_panel(home, lavish, show_all) for home in homes)}
    <footer>Read-only snapshot · refreshed {timestamp} · source files open as readable pages beside this one.</footer>
  </main>
  {lavish_script}
</body>
</html>
'''


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


COMMAND_DOCS = {
    "render": ["how-to/view-in-lavish.md", "how-to/request-lavish-page.md", "explanation/attention-model.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "reports": ["how-to/review-reports.md", "reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
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
        if not reports:
            print("No reports found.")
            return 0
        identifiers: dict[str, int] = {}
        for _snapshot, report in reports:
            identifiers[report.report_id] = identifiers.get(report.report_id, 0) + 1
        print("ID\tReview state\tTitle\tHome")
        for snapshot, report in reports:
            state = "ID collision" if identifiers[report.report_id] > 1 else (
                "reviewed" if report.reviewed else "needs review"
            )
            print(f"{report.report_id}\t{state}\t{report.title}\t{snapshot.spec.label}")
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
        if args.open:
            webbrowser.open(output_path.as_uri())
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    return 0


def reports_mark_reviewed(args: argparse.Namespace) -> int:
    try:
        snapshots, state_path, marks = load_report_context(args)
        _snapshot, report = unique_report(snapshots, args.report_id)
        marks[report.report_id] = report.fingerprint
        write_review_marks(state_path, marks)
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2
    print(f"Marked {report.report_id} reviewed for its current content.")
    return 0


def reports_unmark_reviewed(args: argparse.Namespace) -> int:
    try:
        snapshots, state_path, marks = load_report_context(args)
        _snapshot, report = unique_report(snapshots, args.report_id)
        marks.pop(report.report_id, None)
        write_review_marks(state_path, marks)
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
        snapshot.error = f"This secondmate is on remote host {spec.remote_host}; Quarterdeck reads local files only."
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
            snapshot.backlog_markdown = source
            snapshot.reports = read_reports(data_dir, snapshot.warnings)
            snapshot.records = parse_backlog(source, data_dir, snapshot.reports)
        except (OSError, ValueError) as exc:
            snapshot.error = str(exc)

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


def render(args: argparse.Namespace) -> int:
    try:
        if args.config is None:
            default_path = default_config_path()
            args.config = default_path if default_path.is_file() else None
        config = load_config(args.config)
        specs, legacy_single_home = resolve_home_specs(args, config)
        homes = [load_home_snapshot(spec, discover_secondmates=True) for spec in specs]
        if legacy_single_home and homes[0].error:
            print(f"quarterdeck: {homes[0].error}", file=sys.stderr)
            return 2
        state_path = validate_review_state_location(homes)
        apply_review_marks(homes, load_review_marks(state_path))
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
        write_source_pages(homes, output_path.parent, show_all=args.all)
        write_generated_page(output_path, build_html(homes, title, lavish=args.lavish, show_all=args.all))
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
    render_parser.set_defaults(handler=render)

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
