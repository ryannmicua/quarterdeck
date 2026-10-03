#!/usr/bin/env python3
"""Build a local, read-only review page from a Firstmate home."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
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
    r"(?i)(captain_actionable|hold_bucket|hold_kind|hold_reason|hold_until|"
    r"hold_set|state|status|review_ready|pr_url|pr|report|report_path|"
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


@dataclass
class Report:
    title: str
    path: Path
    recommendation: str | None


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
    value = re.sub(r"[*_~]", "", value)
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
            source = report_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            if warnings is not None:
                warnings.append(f"Could not read report {report_path}: {exc}")
            continue
        title = next(
            (markdown_text(match.group(1)) for line in source.splitlines()
             if (match := re.match(r"^\s{0,3}#{1,2}\s+(.+)$", line))),
            report_path.parent.name.replace("-", " ").replace("_", " "),
        )
        reports.append(Report(title, report_path.resolve(), report_recommendation(source)))
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
        "report_file": "report_path", "recommendation": "recommendation",
    }
    return aliases.get(normalized, normalized)


def _record_from_line(line: str, section: str, headers: list[str] | None) -> Record | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("<!--") or stripped.startswith("<!--"):
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

    fields = fields_from(raw, columns)
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
        text=markdown_text(raw),
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
        if report and not record.recommendation:
            record.recommendation = report.recommendation

        match = PR_URL_RE.search(line)
        if match:
            record.pr_url = match.group(0)
        records.append(record)
    return records


def is_held(record: Record) -> bool:
    fields = record.fields
    affirmative = {"true", "yes", "1", "on"}
    state = " ".join((record.section, value_for(fields, "state", "status"))).lower()
    bucket = value_for(fields, "hold_bucket").lower()
    if value_for(fields, "captain_actionable").lower() in affirmative:
        return True
    if bucket and bucket not in {"none", "null", "false", "-"}:
        return True
    if any(value_for(fields, key) for key in ("hold_kind", "hold_reason", "hold_until", "hold_set")):
        return True
    return any(term in state for term in ("held", "hold", "needs-decision", "waiting for captain", "captain's call"))


def is_active(record: Record) -> bool:
    state = " ".join((record.section, value_for(record.fields, "state", "status"))).lower()
    if any(term in state for term in ("done", "complete", "archived", "cancelled", "canceled")):
        return False
    return any(term in state for term in ("in flight", "in-flight", "active", "working", "queued", "running", "blocked"))


def is_review_ready(record: Record) -> bool:
    fields = record.fields
    affirmative = {"true", "yes", "1", "on", "ready"}
    review_flag = value_for(fields, "review_ready").lower()
    status = " ".join((record.section, value_for(fields, "state", "status", "pr_status"))).lower()
    marked = review_flag in affirmative or ("review" in status and any(word in status for word in ("ready", "open", "pending")))
    return bool(record.pr_url and marked)


def file_link(path: Path) -> str:
    return path.resolve().as_uri()


def card(record: Record, home: Path, data_dir: Path, badge: str) -> str:
    safe_title = html.escape(record.title)
    chunks = [f'<article class="card"><p class="eyebrow">{html.escape(badge)}</p>', f"<h3>{safe_title}</h3>"]
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
        if parsed.scheme == "https" and parsed.netloc == "github.com":
            links.append(f'<a href="{html.escape(record.pr_url, quote=True)}">Open pull request</a>')
    if record.report and record.report.is_file():
        relative = record.report.relative_to(home) if record.report.is_relative_to(home) else record.report
        links.append(f'<a href="{html.escape(file_link(record.report), quote=True)}">Read report <span class="path">{html.escape(str(relative))}</span></a>')
    else:
        backlog_path = data_dir / "backlog.md"
        if backlog_path.is_file():
            links.append(f'<a href="{html.escape(file_link(backlog_path), quote=True)}">Open backlog</a>')
    if links:
        chunks.append('<p class="links">' + " · ".join(links) + "</p>")
    chunks.append("</article>")
    return "".join(chunks)


def report_card(report: Report, home: Path) -> str:
    relative = report.path.relative_to(home) if report.path.is_relative_to(home) else report.path
    recommendation = (
        f'<p><strong>Recommendation:</strong> {html.escape(report.recommendation)}</p>'
        if report.recommendation else ""
    )
    return (
        '<article class="card"><p class="eyebrow">Scout report</p>'
        f'<h3>{html.escape(report.title)}</h3>{recommendation}'
        f'<p class="links"><a href="{html.escape(file_link(report.path), quote=True)}">Read report '
        f'<span class="path">{html.escape(str(relative))}</span></a></p></article>'
    )


def section_html(title: str, description: str, cards: list[str], empty: str) -> str:
    content = "".join(cards) if cards else f'<p class="empty">{html.escape(empty)}</p>'
    return f'<div class="dashboard-section"><div class="section-head"><div><h3>{html.escape(title)}</h3><p>{html.escape(description)}</p></div><span class="count">{len(cards)}</span></div><div class="cards">{content}</div></div>'


def snapshot_counts(snapshot: HomeSnapshot) -> tuple[int, int, int, int]:
    if snapshot.error:
        return (0, 0, 0, 0)
    held = [record for record in snapshot.records if is_held(record)]
    reviews = [record for record in snapshot.records if is_review_ready(record)]
    active = [record for record in snapshot.records if is_active(record) and record not in held and record not in reviews]
    return (len(held), len(reviews), len(snapshot.reports), len(active))


def home_panel(snapshot: HomeSnapshot) -> str:
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
        held = sorted(
            (record for record in snapshot.records if is_held(record)),
            key=lambda record: (record.fields.get("hold_bucket", "live") != "live", record.title.lower()),
        )
        reviews = [record for record in snapshot.records if is_review_ready(record)]
        active = [
            record for record in snapshot.records
            if is_active(record) and record not in held and record not in reviews
        ]
        counts = snapshot_counts(snapshot)
        body.append(
            '<div class="home-counts">'
            f'<span>{counts[0]} held</span><span>{counts[1]} review-ready</span>'
            f'<span>{counts[2]} reports</span><span>{counts[3]} active</span></div>'
        )
        review_cards = [card(record, spec.home, snapshot.data_dir, "Review-ready pull request") for record in reviews]
        held_cards = [card(record, spec.home, snapshot.data_dir, "Held for captain review") for record in held]
        active_cards = [card(record, spec.home, snapshot.data_dir, "Active task") for record in active]
        report_cards = [report_card(report, spec.home) for report in snapshot.reports]
        body.extend([
            section_html("Held for review", "Items that have a recorded hold or need a decision.", held_cards, "Nothing is waiting on a recorded hold."),
            section_html("Review-ready pull requests", "Open the linked change when a task marks it ready for review.", review_cards, "No review-ready pull requests are recorded."),
            section_html("Scout reports", "Reports found in this home's data directory.", report_cards, "No scout reports were found."),
            section_html("Active tasks", "In-flight and queued work from this home's backlog.", active_cards, "No active backlog items were found."),
        ])
    if snapshot.registry_error:
        body.append(
            '<div class="warning"><strong>Secondmate registry could not be read</strong>'
            f'<p>{html.escape(snapshot.registry_error)}</p></div>'
        )
    if snapshot.warnings:
        body.append(
            '<div class="warning"><strong>Some report files could not be read</strong>'
            f'<p>{html.escape("; ".join(snapshot.warnings))}</p></div>'
        )
    if snapshot.children:
        body.append('<div class="secondmates"><h3>Registered secondmates</h3>')
        body.extend(home_panel(child) for child in snapshot.children)
        body.append('</div>')
    return ''.join(heading + body + ['</section>'])


def all_snapshots(snapshots: list[HomeSnapshot]) -> list[HomeSnapshot]:
    result: list[HomeSnapshot] = []
    for snapshot in snapshots:
        result.append(snapshot)
        result.extend(all_snapshots(snapshot.children))
    return result


def build_html(homes: list[HomeSnapshot], title: str) -> str:
    snapshots = all_snapshots(homes)
    counts = [sum(snapshot_counts(snapshot)[index] for snapshot in snapshots) for index in range(4)]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
    .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .8rem; margin-bottom: 2rem; }}
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
    <p>A read-only lookout over configured Firstmate homes and their registered secondmates.</p>
  </header>
  <main>
    <div class="summary" aria-label="Review counts">
      <div class="metric"><b>{counts[0]}</b><span>Held items</span></div>
      <div class="metric"><b>{counts[1]}</b><span>Review-ready PRs</span></div>
      <div class="metric"><b>{counts[2]}</b><span>Scout reports</span></div>
      <div class="metric"><b>{counts[3]}</b><span>Active tasks</span></div>
    </div>
    {''.join(home_panel(home) for home in homes)}
    <footer>Read-only snapshot · refreshed {timestamp} · sources are identified within each home section.</footer>
  </main>
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
    "render": ["reference/cli-and-config.md", "explanation/privacy-and-architecture.md"],
    "add": ["how-to/configure-multiple-homes.md", "reference/cli-and-config.md"],
    "list": ["how-to/configure-multiple-homes.md", "reference/cli-and-config.md"],
    "remove": ["how-to/configure-multiple-homes.md", "reference/cli-and-config.md"],
    "install": ["how-to/install.md", "how-to/update.md", "how-to/uninstall.md"],
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
        configured_output = config.get("output_dir")
        output_dir = Path(configured_output).expanduser() if isinstance(configured_output, str) else default_output_dir()
        output_path = Path(args.output).expanduser() if args.output else output_dir / "index.html"
        configured_title = config.get("page_title")
        title = args.title or (configured_title if isinstance(configured_title, str) else None) or DEFAULT_TITLE
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(build_html(homes, title), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2

    print(output_path)
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quarterdeck", description="Render and manage a read-only Firstmate review page.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command_parsers: dict[str, argparse.ArgumentParser] = {}

    render_parser = subparsers.add_parser(
        "render", help="regenerate the local HTML page", description="Render registered or explicitly selected homes."
    )
    command_parsers["render"] = render_parser
    render_parser.add_argument("--home", help="render this Firstmate home instead of configured homes or FM_HOME")
    render_parser.add_argument("--output", help="HTML file path; defaults to the configured state directory")
    render_parser.add_argument("--config", type=Path, help="optional JSON config file")
    render_parser.add_argument("--title", help="override the page title")
    render_parser.set_defaults(handler=render)

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
        "install", help="install, update, or uninstall Quarterdeck", description="Run the user-local shell installer."
    )
    command_parsers["install"] = install_parser
    install_parser.add_argument("--uninstall", action="store_true", help="remove installer-created files and keep config/data")
    install_parser.set_defaults(handler=run_installer)

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
