#!/usr/bin/env python3
"""Build a local, read-only review page from a Firstmate home."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_TITLE = "Quarterdeck"
ALLOWED_CONFIG_KEYS = {"page_title", "output_dir"}
PR_URL_RE = re.compile(r"https://github\.com/[^\s|)]+/pull/\d+", re.IGNORECASE)
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


def read_reports(data_dir: Path) -> list[Report]:
    reports: list[Report] = []
    if not data_dir.is_dir():
        return reports
    for report_path in sorted(data_dir.glob("*/report.md")):
        try:
            source = report_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
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
    return f'<section><div class="section-head"><div><h2>{html.escape(title)}</h2><p>{html.escape(description)}</p></div><span class="count">{len(cards)}</span></div><div class="cards">{content}</div></section>'


def build_html(home: Path, data_dir: Path, records: list[Record], reports: list[Report], title: str) -> str:
    held = sorted((record for record in records if is_held(record)), key=lambda r: (r.fields.get("hold_bucket", "live") != "live", r.title.lower()))
    reviews = [record for record in records if is_review_ready(record)]
    active = [record for record in records if is_active(record) and record not in held and record not in reviews]
    review_records = [card(record, home, data_dir, "Review-ready pull request") for record in reviews]
    held_cards = [card(record, home, data_dir, "Held for captain review") for record in held]
    active_cards = [card(record, home, data_dir, "Active task") for record in active]
    report_cards = [report_card(report, home) for report in reports]

    sections = "".join([
        section_html("Held for review", "Items that have a recorded hold or need a decision.", held_cards, "Nothing is waiting on a recorded hold."),
        section_html("Review-ready pull requests", "Open the linked change when a task marks it ready for review.", review_records, "No review-ready pull requests are recorded."),
        section_html("Scout reports", "Reports found in the active data directory.", report_cards, "No scout reports were found."),
        section_html("Active tasks", "In-flight and queued work from the backlog.", active_cards, "No active backlog items were found."),
    ])
    counts = [len(held), len(reviews), len(reports), len(active)]
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
    section {{ margin: 2rem 0 2.5rem; }}
    .section-head {{ display: flex; align-items: start; justify-content: space-between; gap: 1rem; margin-bottom: .8rem; }}
    h2 {{ margin: 0; color: var(--navy); font: 700 1.65rem Georgia, serif; }}
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
    footer {{ border-top: 1px solid var(--line); margin-top: 3rem; padding-top: 1rem; color: var(--muted); font-size: .85rem; }}
    @media (max-width: 640px) {{ .summary {{ grid-template-columns: repeat(2, 1fr); }} }}
    @media print {{ body {{ background: #fff; }} header {{ padding: 1.2rem; }} main {{ width: 100%; margin: 1rem 0; }} .card {{ break-inside: avoid; }} }}
  </style>
</head>
<body>
  <header>
    <div class="kicker">Firstmate · local review</div>
    <h1>⚓ {html.escape(title)}</h1>
    <p>A quiet lookout over held decisions, scout reports, active work, and changes ready for review.</p>
  </header>
  <main>
    <div class="summary" aria-label="Review counts">
      <div class="metric"><b>{counts[0]}</b><span>Held items</span></div>
      <div class="metric"><b>{counts[1]}</b><span>Review-ready PRs</span></div>
      <div class="metric"><b>{counts[2]}</b><span>Scout reports</span></div>
      <div class="metric"><b>{counts[3]}</b><span>Active tasks</span></div>
    </div>
    {sections}
    <footer>Read-only snapshot · refreshed {timestamp} · source: {html.escape(str(data_dir))}</footer>
  </main>
</body>
</html>
'''


def load_config(path: Path | None) -> dict[str, str]:
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
    return config


def default_output_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser()
    return base / "quarterdeck"


def render(args: argparse.Namespace) -> int:
    raw_home = args.home or os.environ.get("FM_HOME")
    if not raw_home:
        print("quarterdeck: Firstmate home is required; pass --home or set FM_HOME.", file=sys.stderr)
        return 2
    home = Path(raw_home).expanduser().resolve()
    if not home.is_dir():
        print(f"quarterdeck: Firstmate home is not a directory: {home}", file=sys.stderr)
        return 2

    data_override = os.environ.get("FM_DATA_OVERRIDE")
    data_dir = Path(data_override).expanduser().resolve() if data_override else home / "data"
    backlog_path = data_dir / "backlog.md"
    if not backlog_path.is_file():
        print(f"quarterdeck: backlog not found: {backlog_path}", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
        source = backlog_path.read_text(encoding="utf-8", errors="replace")
        reports = read_reports(data_dir)
        records = parse_backlog(source, data_dir, reports)
        configured_output = config.get("output_dir")
        output_dir = Path(configured_output).expanduser() if configured_output else default_output_dir()
        output_path = Path(args.output).expanduser() if args.output else output_dir / "index.html"
        title = args.title or config.get("page_title") or DEFAULT_TITLE
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(build_html(home, data_dir, records, reports, title), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"quarterdeck: {exc}", file=sys.stderr)
        return 2

    print(output_path)
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quarterdeck", description="Render a read-only Firstmate review page.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render", help="regenerate the local HTML page")
    render_parser.add_argument("--home", help="Firstmate home (or use FM_HOME)")
    render_parser.add_argument("--output", help="HTML file path; defaults to the configured state directory")
    render_parser.add_argument("--config", type=Path, help="optional JSON config file")
    render_parser.add_argument("--title", help="override the page title")
    render_parser.set_defaults(handler=render)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
