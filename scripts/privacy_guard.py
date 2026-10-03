#!/usr/bin/env python3
"""Reject staged generated pages and likely Firstmate work data."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


HOST_PATH_RE = re.compile(rb"/(?:home|Users)/[A-Za-z0-9._-]+(?:/|$)")
PRIVATE_IP_RE = re.compile(
    rb"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    rb"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)
PULL_REQUEST_RE = re.compile(rb"https://github\.com/[^\s/]+/[^\s/]+/pull/\d+", re.I)
TICKET_KEY_RE = re.compile(rb"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,11}-\d{1,8}\b")
GENERATED_MARKER = b'<meta name="quarterdeck-' + b'generated"'
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def forbidden_path(path: str) -> str | None:
    normalized = str(PurePosixPath(path)).lower()
    parts = PurePosixPath(normalized).parts
    name = PurePosixPath(normalized).name
    if any(part in {"screenshots", "quarterdeck-output", "output"} for part in parts):
        return "generated output or screenshots directory"
    if any(part == "data" for part in parts):
        return "Firstmate data directory"
    if name in {".quarterdeck.json", "quarterdeck.local.json", "config.local.json", "backlog.md", "report.md"}:
        return "local config or Firstmate work-data filename"
    if name.endswith((".report.md", ".local.json")):
        return "local config or report filename"
    if PurePosixPath(normalized).suffix in IMAGE_SUFFIXES:
        return "screenshot or image file"
    if PurePosixPath(normalized).suffix in {".html", ".htm"}:
        return "generated HTML page"
    return None


def content_findings(content: bytes) -> list[str]:
    findings: list[str] = []
    if GENERATED_MARKER in content:
        findings.append("Quarterdeck-generated HTML marker")
    if HOST_PATH_RE.search(content):
        findings.append("host-specific absolute home path")
    if PRIVATE_IP_RE.search(content):
        findings.append("private IP address")
    if PULL_REQUEST_RE.search(content):
        findings.append("pull-request link")
    if TICKET_KEY_RE.search(content):
        findings.append("ticket-shaped work key")
    return findings


def staged_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [entry.decode("utf-8", "replace") for entry in result.stdout.split(b"\0") if entry]


def check_staged(root: Path) -> list[str]:
    problems: list[str] = []
    for name in staged_paths(root):
        path_reason = forbidden_path(name)
        if path_reason:
            problems.append(f"{name}: {path_reason}")
            continue
        if name == "scripts/privacy_guard.py":
            continue
        staged = subprocess.run(
            ["git", "show", f":{name}"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if staged.returncode != 0:
            problems.append(f"{name}: could not read staged content")
            continue
        for finding in content_findings(staged.stdout):
            problems.append(f"{name}: {finding}")
    return problems


def main() -> int:
    try:
        problems = check_staged(Path.cwd())
    except subprocess.CalledProcessError as exc:
        print(f"privacy guard: git check failed: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("privacy guard: staged files need review:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("privacy guard: staged paths and content look safe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
