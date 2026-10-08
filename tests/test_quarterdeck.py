from __future__ import annotations

import json
import html
import io
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import quarterdeck
import privacy_guard


def parse_systemd_unit(source: str) -> dict[str, dict[str, list[object]]]:
    unit: dict[str, dict[str, list[object]]] = {}
    section = ""
    for line in source.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            unit.setdefault(section, {})
            continue
        key, separator, value = line.partition("=")
        if not separator or not section:
            raise ValueError(f"invalid systemd directive: {line}")
        if key == "ExecStart":
            value = value.replace("$$", "$").replace("%%", "%")
            parsed: object = shlex.split(value)
        elif key == "Environment":
            words = shlex.split(value.replace("%%", "%"))
            if len(words) != 1:
                raise ValueError(f"invalid systemd environment assignment: {line}")
            parsed = words[0]
        elif key == "WantedBy":
            parsed = value.split()
        else:
            parsed = value
        unit[section].setdefault(key, []).append(parsed)
    return unit


class QuarterdeckTests(unittest.TestCase):
    def make_home(self, root: Path) -> Path:
        home = root / "fictional-firstmate"
        data = home / "data"
        for task_id in ("amber-18", "paper-27", "maple-21", "queued-31", "finished-32", "blocked-33", "internal-34", "captain-36", "orphan-35"):
            (data / task_id).mkdir(parents=True, exist_ok=True)
        (data / "backlog.md").write_text(
            """# Work queue

## Held for review
| ID | Task | State | Held | Hold kind | Hold reason | Report | Closed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| amber-18 | Choose the Amber Kite import format | queued | yes | captain | Pick one format | data/amber-18/report.md | - |

## Review ready
| ID | Task | State | Review ready | PR | Report |
| --- | --- | --- | --- | --- | --- |
| paper-27 | Review the Paper Finch patch | review_ready | yes | """ + "https://github.com/example/quarterdeck-demo/" + "pull/42" + """ | data/paper-27/report.md |

## In flight
| ID | Task | State | Held | Hold kind | Closed |
| --- | --- | --- | --- | --- | --- |
| maple-21 | Chart the Maple Harbor catalog | in_flight | no | - | - |

## Queued
| ID | Task | State | Held | Hold kind | Report | Closed |
| --- | --- | --- | --- | --- | --- | --- |
| queued-31 | Gather the Finch field notes | queued | no | captain | data/queued-31/report.md | - |

## Blocked
| ID | Task | State | Blocked | Waiting on | Blocked reason |
| --- | --- | --- | --- | --- | --- |
| blocked-33 | Confirm the sample vendor schedule | blocked | yes | external | Waiting for the sample vendor response |
| captain-36 | Confirm the sample release window | blocked | yes | captain | Waiting for a captain response |
| internal-34 | Merge the local sample dependency | blocked | yes | maple-21 | Waiting for another backlog item |

## Done
| ID | Task | State | Held | Hold kind | Closed | Report |
| --- | --- | --- | --- | --- | --- | --- |
| finished-32 | File the old Finch notes | done | no | captain | 2026-01-04 | data/finished-32/report.md |
""",
            encoding="utf-8",
        )
        (data / "amber-18" / "report.md").write_text(
            """# Amber Kite import review

## Recommendation

Use the compact format for the first release.
""",
            encoding="utf-8",
        )
        (data / "paper-27" / "report.md").write_text("# Paper Finch patch review\n\nReady for review.\n", encoding="utf-8")
        (data / "queued-31" / "report.md").write_text("# Queued Finch notes\n\nNot ready for review.\n", encoding="utf-8")
        (data / "finished-32" / "report.md").write_text("# Finished Finch report\n\nThis old work is closed.\n", encoding="utf-8")
        (data / "orphan-35" / "report.md").write_text("# Unlinked old report\n\nNo open review task points here.\n", encoding="utf-8")
        secondmate = root / "fictional-secondmate"
        secondmate_data = secondmate / "data"
        (secondmate_data / "kestrel-hold").mkdir(parents=True)
        (secondmate_data / "backlog.md").write_text(
            """# Secondmate queue

| ID | Task | State | Held | Hold kind | Hold reason | Report |
| --- | --- | --- | --- | --- | --- | --- |
| kestrel-work | Chart the Willow Quay field guide | in_flight | no | - | - | - |
| kestrel-hold | Choose the Willow Quay map legend | queued | yes | captain | Select a legend | data/kestrel-hold/report.md |
| kestrel-queued | Collect later map notes | queued | no | - | - | - |
| kestrel-done | Archive the old map draft | done | no | - | - | - |
""",
            encoding="utf-8",
        )
        (secondmate_data / "kestrel-hold" / "report.md").write_text("# Willow Quay legend review\n\nChoose a legend.\n", encoding="utf-8")
        (data / "secondmates.md").write_text(
            f"""# Registered secondmates
- kestrel - Maintains the Willow Quay field guide. (home: {secondmate}; scope: Field guide work; projects: quarterdeck-demo; added 2026-01-04)
- lantern - Maintains remote map notes. (host: sample-remote; root: /srv/sample-firstmate; home: /srv/sample-homes/lantern; scope: Map notes; projects: map-demo; added 2026-01-04)
""",
            encoding="utf-8",
        )
        return home

    def run_cli(self, args: list[str], home: Path, config_home: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(config_home)
        env["XDG_STATE_HOME"] = str(config_home.parent / "state")
        env.pop("FM_HOME", None)
        env.pop("FM_DATA_OVERRIDE", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "quarterdeck.py"), *args],
            check=False,
            text=True,
            capture_output=True,
            env=env,
        )

    def test_render_shows_only_actionable_items_and_matches_header_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = root / "outside-output" / "index.html"
            result = self.run_cli(["render", "--home", str(home), "--output", str(output)], root, root / "config")
            self.assertEqual(result.returncode, 0, result.stderr)
            full_page = output.read_text(encoding="utf-8")
            page = full_page.split("Details by home", 1)[1]
            self.assertIn("Choose the Amber Kite import format", page)
            self.assertIn("Use the compact format for the first release.", page)
            self.assertIn("https://github.com/example/quarterdeck-demo/" + "pull/42", page)
            self.assertIn("Chart the Maple Harbor catalog", page)
            self.assertIn("kestrel — Maintains the Willow Quay field guide.", page)
            self.assertIn("Chart the Willow Quay field guide", page)
            self.assertIn("Secondmate · registered by fictional-firstmate", page)
            self.assertIn("remote host sample-remote", page)
            self.assertIn("Read report", page)
            self.assertNotIn("Gather the Finch field notes", page)
            self.assertIn("Reports needing review", page)
            self.assertIn("Finished Finch report", page)
            self.assertIn("Unlinked old report", page)
            orphan_path = home / "data" / "orphan-35" / "report.md"
            orphan_page = quarterdeck.source_page_filename("report", home, orphan_path)
            self.assertIn('<code>fictional-firstmate/orphan-35</code>', page)
            self.assertIn(f'href="{orphan_page}">Read report <code>fictional-firstmate/orphan-35</code>', page)
            self.assertNotIn("Merge the local sample dependency", page)
            self.assertNotIn("file://", page)
            self.assertIn("Read backlog item", page)
            self.assertRegex(page, r'<b>2</b><span>Held for captain</span>')
            self.assertRegex(page, r'<b>1</b><span>Review-ready PRs</span>')
            self.assertRegex(page, r'<b>2</b><span>In-flight work</span>')
            self.assertRegex(page, r'<b>2</b><span>Blocked for captain/external</span>')
            for label, badge in (
                ("Held for captain", "Held for captain"),
                ("Review-ready PRs", "Review-ready pull request"),
                ("In-flight work", "In-flight work"),
                ("Blocked for captain/external", "Blocked for captain or external party"),
            ):
                metric = re.search(r'<div class="metric"><b>(\d+)</b><span>' + re.escape(label) + r"</span></div>", page)
                self.assertIsNotNone(metric)
                self.assertEqual(int(metric.group(1)), page.count(f'<p class="eyebrow">{badge}</p>'))
            self.assertIn('name="quarterdeck-' + "generated" + '"', full_page)
            self.assertNotIn("https://fonts.", page)
            report_pages = list(output.parent.glob("report-*.html"))
            self.assertEqual(len(report_pages), 6)
            self.assertTrue(all(path.is_file() for path in report_pages))
            backlog_pages = list(output.parent.glob("backlog-*.html"))
            self.assertEqual(len(backlog_pages), 2)
            self.assertTrue(any("<h1>Work queue</h1>" in path.read_text(encoding="utf-8") for path in backlog_pages))

    def test_lavish_render_creates_annotatable_page_and_opens_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = root / "output" / "index.html"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            stub = bin_dir / "lavish-axi"
            stub.write_text("#!/bin/sh\nprintf '%s\\n' 'Session: https://review.example.invalid/session/fictional'\n", encoding="utf-8")
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
            env["XDG_STATE_HOME"] = str(root / "state")
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--lavish", "--home", str(home), "--output", str(output)],
                check=False, text=True, capture_output=True, env=env,
            )
            lavish_page = output.with_name("index.lavish.html")
            page = lavish_page.read_text(encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.parent.exists())
            self.assertFalse(output.exists())
            self.assertIn("https://review.example.invalid/session/fictional", result.stdout)
            self.assertRegex(page, r'<article class="card" id="review-item-[0-9a-f]{12}">')
            self.assertIn("queuePrompt", page)
            self.assertIn("Request a Lavish page", page)
            self.assertRegex(page, r'<article class="card" id="review-report-[0-9a-f]{12}">')
            self.assertIn("Reports needing review", page)
            homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec(home.name, home), discover_secondmates=True)]
            expected = quarterdeck.build_html(
                homes, "Quarterdeck", lavish=True, bearings=quarterdeck.build_bearings(homes),
            )
            stamp = re.compile(r"[Gg]enerated(?: at)? \d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC")
            self.assertEqual(stamp.sub("", page), stamp.sub("", expected))

    def test_all_mode_restores_queued_finished_and_unlinked_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = root / "outside-output" / "index.html"
            result = self.run_cli(["render", "--all", "--home", str(home), "--output", str(output)], root, root / "config")
            self.assertEqual(result.returncode, 0, result.stderr)
            page = output.read_text(encoding="utf-8").split("Details by home", 1)[1]
            self.assertIn("Gather the Finch field notes", page)
            self.assertIn("File the old Finch notes", page)
            self.assertIn("Unlinked old report", page)
            self.assertIn("Other backlog items", page)
            self.assertIn("All scout reports", page)
            self.assertRegex(page, r'<b>5</b><span>Other backlog items</span>')
            self.assertRegex(page, r'<b>6</b><span>Scout reports</span>')
            self.assertEqual(page.count('<p class="eyebrow">Other backlog item</p>'), 5)
            self.assertEqual(page.count("<h3>Finished Finch report</h3>"), 1)
            self.assertEqual(page.count("<h3>Unlinked old report</h3>"), 1)

    def test_all_lavish_request_control_is_limited_to_unreviewed_report_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            config_home = root / "config"
            report_id = "fictional-firstmate/amber-18"

            marked = self.run_cli(
                ["reports", "mark-reviewed", report_id, "--home", str(home)],
                root, config_home,
            )
            self.assertEqual(marked.returncode, 0, marked.stderr)

            empty_bin = root / "empty-bin"
            empty_bin.mkdir()
            output = root / "dashboard.html"
            env = os.environ.copy()
            env["HOME"] = str(root)
            env["XDG_CONFIG_HOME"] = str(config_home)
            env["XDG_STATE_HOME"] = str(root / "state")
            env["PATH"] = str(empty_bin)
            env.pop("FM_HOME", None)
            env.pop("FM_DATA_OVERRIDE", None)
            rendered = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--all", "--lavish",
                 "--home", str(home), "--output", str(output)],
                check=False, text=True, capture_output=True, env=env,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)

            page = output.with_name("dashboard.lavish.html").read_text(encoding="utf-8")
            report_cards = re.findall(r'<article class="card" id="review-report-[^"]+">.*?</article>', page, re.DOTALL)
            reviewed_card = next(card for card in report_cards if "<h3>Amber Kite import review</h3>" in card)
            unreviewed_card = next(card for card in report_cards if "<h3>Unlinked old report</h3>" in card)
            self.assertIn("Scout report · Reviewed", reviewed_card)
            self.assertNotIn("data-lavish-request", reviewed_card)
            self.assertIn("Scout report · Needs review", unreviewed_card)
            self.assertIn("data-lavish-request", unreviewed_card)

            payloads = [
                json.loads(html.unescape(value))
                for value in re.findall(r'data-lavish-request="([^"]+)"', page)
            ]
            self.assertTrue(any(payload.get("report_id") == report_id for payload in payloads))

    def test_lavish_request_control_has_structured_payload_and_is_hidden_on_plain_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            plain = root / "plain.html"
            plain_result = self.run_cli(["render", "--home", str(home), "--output", str(plain)], root, root / "config")
            self.assertEqual(plain_result.returncode, 0, plain_result.stderr)
            self.assertNotIn("data-lavish-request", plain.read_text(encoding="utf-8"))
            self.assertNotIn("Request a Lavish page", plain.read_text(encoding="utf-8"))

            lavish_base = root / "lavish.html"
            bin_dir = root / "empty-bin"
            bin_dir.mkdir()
            stub = bin_dir / "lavish-axi"
            stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir)
            env["XDG_STATE_HOME"] = str(root / "state")
            lavish_result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--lavish", "--home", str(home), "--output", str(lavish_base)],
                text=True, capture_output=True, env=env,
            )
            self.assertEqual(lavish_result.returncode, 0, lavish_result.stderr)
            page = lavish_base.with_name("lavish.lavish.html").read_text(encoding="utf-8")
            payloads = [
                json.loads(html.unescape(value))
                for value in re.findall(r'data-lavish-request="([^"]+)"', page)
            ]
            self.assertEqual(len(payloads), 11)
            self.assertEqual(payloads[0], {
                "item_id": "fictional-firstmate/amber-18",
                "title": "Amber Kite import review",
                "kind": "report",
                "home_label": "fictional-firstmate",
                "source_path": "data/amber-18/report.md",
                "report_id": "fictional-firstmate/amber-18",
            })
            self.assertIn({
                "item_id": "fictional-firstmate/amber-18",
                "report_id": "fictional-firstmate/amber-18",
                "title": "Amber Kite import review",
                "kind": "report",
                "home_label": "fictional-firstmate",
                "source_path": "data/amber-18/report.md",
            }, payloads)
            self.assertIn("backlog item", {payload["kind"] for payload in payloads})
            self.assertIn("fictional-firstmate", {payload["home_label"] for payload in payloads})
            self.assertIn("kestrel — Maintains the Willow Quay field guide.", {payload["home_label"] for payload in payloads})

    def test_report_commands_read_and_track_review_state_by_content_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            config_home = root / "config"
            report_id = "fictional-firstmate/amber-18"

            listed = self.run_cli(["reports", "list", "--home", str(home)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(f"{report_id}\tneeds review\tAmber Kite import review", listed.stdout)

            read = self.run_cli(
                ["reports", "read", report_id, "--home", str(home)],
                root, config_home,
            )
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertIn("# Amber Kite import review", read.stdout)
            page_path = Path(read.stdout.split("Readable HTML: ", 1)[1].strip())
            self.assertEqual(
                page_path,
                root / "state" / "quarterdeck" / quarterdeck.source_page_filename(
                    "report", home, home / "data" / "amber-18" / "report.md",
                ),
            )
            self.assertTrue(page_path.is_file())
            self.assertIn("<h1>Amber Kite import review</h1>", page_path.read_text(encoding="utf-8"))

            marked = self.run_cli(["reports", "mark-reviewed", report_id, "--home", str(home)], root, config_home)
            self.assertEqual(marked.returncode, 0, marked.stderr)
            listed = self.run_cli(["reports", "list", "--home", str(home)], root, config_home)
            self.assertIn(f"{report_id}\treviewed\tAmber Kite import review", listed.stdout)

            state_path = root / "state" / "quarterdeck" / "review-state" / "marks.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            report_path = home / "data" / "amber-18" / "report.md"
            self.assertEqual(state["reviewed"][report_id], quarterdeck.hashlib.sha256(report_path.read_bytes()).hexdigest())
            self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o600)
            self.assertFalse(state_path.is_relative_to(home))
            self.assertFalse(state_path.is_relative_to(ROOT))

            output = root / "dashboard.html"
            rendered = self.run_cli(["render", "--home", str(home), "--output", str(output)], root, config_home)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            dashboard = output.read_text(encoding="utf-8")
            self.assertIn("Reports needing review", dashboard)
            self.assertNotIn("<h3>Amber Kite import review</h3>", dashboard)

            report_path.write_text(report_path.read_text(encoding="utf-8") + "\nUpdated sample.\n", encoding="utf-8")
            listed = self.run_cli(["reports", "list", "--home", str(home)], root, config_home)
            self.assertIn(f"{report_id}\tneeds review\tAmber Kite import review", listed.stdout)

            unmarked = self.run_cli(["reports", "unmark-reviewed", report_id, "--home", str(home)], root, config_home)
            self.assertEqual(unmarked.returncode, 0, unmarked.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn(report_id, state["reviewed"])

    def test_report_id_collisions_are_listed_and_refused_for_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_home = self.make_home(root / "first")
            second_home = self.make_home(root / "second")
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "Sample Bay", "path": str(first_home)},
            ]}), encoding="utf-8")
            report_id = "sample-bay/amber-18"

            marked = self.run_cli(["reports", "mark-reviewed", report_id, "--config", str(config)], root, config_home)
            self.assertEqual(marked.returncode, 0, marked.stderr)

            config.write_text(json.dumps({"homes": [
                {"label": "Sample Bay", "path": str(first_home)},
                {"label": "sample-bay", "path": str(second_home)},
            ]}), encoding="utf-8")

            listed = self.run_cli(["reports", "list", "--config", str(config)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(listed.stdout.count(f"{report_id}\tID collision"), 2)

            output = root / "dashboard.html"
            rendered = self.run_cli(["render", "--config", str(config), "--output", str(output)], root, config_home)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            dashboard = output.read_text(encoding="utf-8")
            report_card = f'<p class="eyebrow">Scout report · Needs review · <code>{report_id}</code></p>'
            self.assertEqual(dashboard.count(report_card), 2)

            read = self.run_cli(["reports", "read", report_id, "--config", str(config)], root, config_home)
            self.assertEqual(read.returncode, 2)
            self.assertIn("report ID is ambiguous", read.stderr)

            marked = self.run_cli(["reports", "mark-reviewed", report_id, "--config", str(config)], root, config_home)
            self.assertEqual(marked.returncode, 2)
            self.assertIn("report ID is ambiguous", marked.stderr)

    def test_report_list_surfaces_failed_homes_and_resolution_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root / "available")
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "Available Sample", "path": str(home)},
                {"label": "Missing Sample", "path": str(root / "missing-firstmate")},
            ]}), encoding="utf-8")
            report_id = "available-sample/amber-18"

            listed = self.run_cli(["reports", "list", "--config", str(config)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(f"{report_id}\tneeds review", listed.stdout)
            self.assertIn("Discovery error (Missing Sample):", listed.stdout)
            self.assertIn("Firstmate home is not a directory", listed.stdout)

            for action in ("read", "mark-reviewed", "unmark-reviewed"):
                with self.subTest(action=action):
                    result = self.run_cli(["reports", action, report_id, "--config", str(config)], root, config_home)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("cannot resolve report ID", result.stderr)
                    self.assertIn("Missing Sample", result.stderr)

    def test_unreadable_report_is_listed_and_blocks_ambiguous_id_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_home = self.make_home(root / "first")
            second_home = self.make_home(root / "second")
            unreadable_report = second_home / "data" / "amber-18" / "report.md"
            unreadable_report.unlink()
            unreadable_report.mkdir()
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "Sample Bay", "path": str(first_home)},
                {"label": "sample-bay", "path": str(second_home)},
            ]}), encoding="utf-8")
            report_id = "sample-bay/amber-18"

            listed = self.run_cli(["reports", "list", "--config", str(config)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(f"{report_id}\tneeds review", listed.stdout)
            self.assertIn("Discovery error (sample-bay):", listed.stdout)
            self.assertIn("Could not read report", listed.stdout)

            for action in ("read", "mark-reviewed", "unmark-reviewed"):
                with self.subTest(action=action):
                    result = self.run_cli(["reports", action, report_id, "--config", str(config)], root, config_home)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("cannot resolve report ID", result.stderr)
                    self.assertIn("sample-bay", result.stderr)

    def test_concurrent_review_mark_updates_preserve_both_marks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marks_path = Path(temp) / "review-state" / "marks.json"
            first_loaded = threading.Event()
            release_first = threading.Event()
            second_started = threading.Event()
            second_loaded = threading.Event()
            errors: list[BaseException] = []
            original_load = quarterdeck.load_review_marks

            def controlled_load(path: Path) -> dict[str, str]:
                marks = original_load(path)
                thread_name = threading.current_thread().name
                if thread_name == "first-mark":
                    first_loaded.set()
                    if not release_first.wait(timeout=5):
                        raise TimeoutError("first mark update was not released")
                elif thread_name == "second-mark":
                    second_loaded.set()
                return marks

            def apply_mark(report_id: str, fingerprint: str, started: threading.Event | None = None) -> None:
                if started is not None:
                    started.set()
                try:
                    quarterdeck.update_review_mark(marks_path, report_id, fingerprint)
                except BaseException as exc:
                    errors.append(exc)

            with patch.object(quarterdeck, "load_review_marks", side_effect=controlled_load):
                first = threading.Thread(
                    target=apply_mark, args=("sample/first", "a" * 64), name="first-mark",
                )
                second = threading.Thread(
                    target=apply_mark, args=("sample/second", "b" * 64, second_started), name="second-mark",
                )
                first.start()
                try:
                    self.assertTrue(first_loaded.wait(timeout=3))
                    second.start()
                    self.assertTrue(second_started.wait(timeout=3))
                    self.assertFalse(second_loaded.wait(timeout=0.5))
                finally:
                    release_first.set()
                    first.join(timeout=3)
                    if second.ident is not None:
                        second.join(timeout=3)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(errors, [])
            marks = original_load(marks_path)
            self.assertEqual(marks, {"sample/first": "a" * 64, "sample/second": "b" * 64})

    def test_reports_read_retains_html_path_when_browser_open_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            environment = {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
            }

            with patch.dict(os.environ, environment, clear=True):
                with patch.object(quarterdeck.webbrowser, "open", return_value=False):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = quarterdeck.main([
                            "reports", "read", "fictional-firstmate/amber-18",
                            "--home", str(home), "--open",
                        ])

            self.assertEqual(result, 2)
            self.assertIn("Readable HTML:", stdout.getvalue())
            output_path = Path(stdout.getvalue().split("Readable HTML: ", 1)[1].strip())
            self.assertTrue(output_path.is_file())
            self.assertIn("could not open report page in a browser", stderr.getvalue())

    def test_reports_read_reports_browser_exception_and_retains_html_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            environment = {
                "HOME": str(root),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
            }

            with patch.dict(os.environ, environment, clear=True):
                with patch.object(quarterdeck.webbrowser, "open", side_effect=quarterdeck.webbrowser.Error("no browser registered")):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = quarterdeck.main([
                            "reports", "read", "fictional-firstmate/amber-18",
                            "--home", str(home), "--open",
                        ])

            self.assertEqual(result, 2)
            self.assertIn("Readable HTML:", stdout.getvalue())
            output_path = Path(stdout.getvalue().split("Readable HTML: ", 1)[1].strip())
            self.assertTrue(output_path.is_file())
            self.assertIn("no browser registered", stderr.getvalue())

    def test_review_state_inside_configured_data_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            data_dir = root / "external-data"
            (data_dir / "amber-18").mkdir(parents=True)
            (data_dir / "backlog.md").write_text("# Work queue\n", encoding="utf-8")
            (data_dir / "amber-18" / "report.md").write_text("# External report\n", encoding="utf-8")
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "External Sample", "path": str(home), "data_dir": str(data_dir)},
            ]}), encoding="utf-8")
            env = os.environ.copy()
            env["HOME"] = str(root)
            env["XDG_CONFIG_HOME"] = str(config_home)
            env["XDG_STATE_HOME"] = str(data_dir)
            env.pop("FM_HOME", None)
            env.pop("FM_DATA_OVERRIDE", None)

            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "reports", "mark-reviewed", "external-sample/amber-18", "--config", str(config)],
                check=False, text=True, capture_output=True, env=env,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("review state must be outside", result.stderr)
            self.assertFalse((data_dir / "quarterdeck" / "review-state").exists())

    def test_long_distinct_home_labels_keep_distinct_report_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_home = self.make_home(root / "first")
            second_home = self.make_home(root / "second")
            config_home = root / "config"
            config_home.mkdir()
            config = config_home / "quarterdeck.json"
            config.write_text(json.dumps({"homes": [
                {"label": "northstar-field-research-center-east", "path": str(first_home)},
                {"label": "northstar-field-research-center-west", "path": str(second_home)},
            ]}), encoding="utf-8")

            listed = self.run_cli(["reports", "list", "--config", str(config)], root, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("northstar-field-research-center-east/amber-18\tneeds review", listed.stdout)
            self.assertIn("northstar-field-research-center-west/amber-18\tneeds review", listed.stdout)

    def test_review_state_inside_a_firstmate_home_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            env = os.environ.copy()
            env["HOME"] = str(root)
            env["XDG_CONFIG_HOME"] = str(root / "config")
            env["XDG_STATE_HOME"] = str(home)
            env.pop("FM_HOME", None)
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "reports", "mark-reviewed", "fictional-firstmate/amber-18", "--home", str(home)],
                check=False, text=True, capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("review state must be outside", result.stderr)
            self.assertFalse((home / "quarterdeck" / "review-state").exists())

    def test_rendered_report_markdown_escapes_html_and_formats_common_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            report = home / "data" / "amber-18" / "report.md"
            report.write_text(
                """# Safe sample report

<script>runFixture()</script>

**Bold text** and *emphasized text* with [a safe link](https://example.invalid/guide).

- First item
- Second item

| Name | Value |
| --- | --- |
| Example | visible |

\x60\x60\x60html
<img src=x onerror=runFixture()>
\x60\x60\x60
""",
                encoding="utf-8",
            )
            output = root / "outside-output" / "index.html"
            result = self.run_cli(["render", "--home", str(home), "--output", str(output)], root, root / "config")
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered_path = output.parent / quarterdeck.source_page_filename("report", home, report)
            rendered = rendered_path.read_text(encoding="utf-8")
            self.assertIn("<h1>Safe sample report</h1>", rendered)
            self.assertIn("&lt;script&gt;runFixture()&lt;/script&gt;", rendered)
            self.assertNotIn("<script>runFixture()</script>", rendered)
            self.assertIn("<strong>Bold text</strong>", rendered)
            self.assertIn("<em>emphasized text</em>", rendered)
            self.assertIn("<ul><li>First item</li><li>Second item</li></ul>", rendered)
            self.assertIn("<table>", rendered)
            self.assertIn("<a href=\"https://example.invalid/guide\">a safe link</a>", rendered)
            self.assertIn("<pre><code>&lt;img src=x onerror=runFixture()&gt;</code></pre>", rendered)

    def test_lavish_render_without_cli_prints_open_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            empty_path = root / "empty-bin"
            empty_path.mkdir()
            env = os.environ.copy()
            env["PATH"] = str(empty_path)
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--lavish", "--home", str(home), "--output", str(root / "page.html")],
                check=False, text=True, capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Lavish page ready; open it later with: lavish-axi", result.stdout)
            self.assertTrue((root / "page.lavish.html").is_file())

    def test_configured_homes_render_together_and_isolate_a_missing_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            config = root / "quarterdeck.json"
            config.write_text(
                json.dumps({
                    "page_title": "Fleet Example",
                    "homes": [
                        {"label": "Main Harbor", "path": home.name},
                        {"label": "Unreadable Lantern", "path": "missing-home"},
                    ],
                }),
                encoding="utf-8",
            )
            output = root / "outside-output" / "index.html"
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--config", str(config), "--output", str(output)],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            page = output.read_text(encoding="utf-8")
            self.assertIn("Main Harbor", page)
            self.assertIn("Unreadable Lantern", page)
            self.assertIn("Home could not be read", page)
            self.assertIn("Chart the Maple Harbor catalog", page)

    def test_home_argument_overrides_configured_home_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            config = root / "quarterdeck.json"
            config.write_text(
                json.dumps({"homes": [{"label": "Configured Other", "path": "missing-home"}]}),
                encoding="utf-8",
            )
            output = root / "outside-output" / "index.html"
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--home", str(home), "--config", str(config), "--output", str(output)],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            page = output.read_text(encoding="utf-8")
            self.assertIn("Chart the Maple Harbor catalog", page)
            self.assertNotIn("Configured Other", page)

    def test_render_uses_fm_home_for_backward_compatible_single_home_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = root / "outside-output" / "index.html"
            with patch.dict(os.environ, {"FM_HOME": str(home), "XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                result = quarterdeck.main(["render", "--output", str(output)])
            self.assertEqual(result, 0)
            self.assertIn("Chart the Maple Harbor catalog", output.read_text(encoding="utf-8"))

    def test_render_requires_home_or_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"HOME": temp, "XDG_CONFIG_HOME": str(Path(temp) / "config")}, clear=True
        ):
            result = quarterdeck.main(["render"])
        self.assertEqual(result, 2)

    def test_unreadable_single_home_uses_secondmate_board_only_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            (home / "data" / "backlog.md").unlink()
            secondmate = root / "fictional-secondmate"
            (secondmate / "data" / "captain-board.json").write_text(json.dumps({
                "version": 1,
                "updated_at": "2026-10-08T10:00:00Z",
                "items": [{
                    "id": "secondmate-approval",
                    "group": "approve",
                    "title": "Secondmate approval",
                    "ask": "Approve the field guide handover.",
                    "task": "kestrel-hold",
                }],
            }), encoding="utf-8")
            output = root / "outside-output" / "index.html"
            result = self.run_cli(
                ["render", "--home", str(home), "--no-snapshot", "--output", str(output)],
                root, root / "config",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Secondmate approval", output.read_text(encoding="utf-8"))

            (secondmate / "data" / "captain-board.json").unlink()
            no_board_output = root / "outside-output" / "without-board.html"
            failed = self.run_cli(
                ["render", "--home", str(home), "--no-snapshot", "--output", str(no_board_output)],
                root, root / "config",
            )
            self.assertEqual(failed.returncode, 2)
            self.assertIn("backlog not found", failed.stderr)
            self.assertFalse(no_board_output.exists())

    def test_render_refuses_output_inside_a_selected_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = home / "index.html"
            result = self.run_cli(["render", "--home", str(home), "--output", str(output)], root, root / "config")
            self.assertEqual(result.returncode, 2)
            self.assertIn("outside the Quarterdeck checkout", result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(list(home.rglob("report-*.html")))

    def test_add_list_remove_default_config_and_render(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli_home = root / "user"
            config_home = cli_home / ".config"
            firstmate_home = self.make_home(root)

            added = self.run_cli(["add", str(firstmate_home)], cli_home, config_home)
            self.assertEqual(added.returncode, 0, added.stderr)
            self.assertIn('Using default label "fictional-firstmate".', added.stdout)
            config_path = config_home / "quarterdeck.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["homes"], [{"label": "fictional-firstmate", "path": str(firstmate_home.resolve())}])
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

            listed = self.run_cli(["list"], cli_home, config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn(f"fictional-firstmate\t{firstmate_home.resolve()}", listed.stdout)

            output = root / "outside-output" / "index.html"
            rendered = self.run_cli(["render", "--output", str(output)], cli_home, config_home)
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertIn("Chart the Maple Harbor catalog", output.read_text(encoding="utf-8"))

            removed = self.run_cli(["remove", str(firstmate_home)], cli_home, config_home)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertIn("Firstmate data was not changed", removed.stdout)
            self.assertTrue((firstmate_home / "data" / "backlog.md").is_file())
            self.assertEqual(self.run_cli(["list"], cli_home, config_home).stdout.strip(), "No homes registered.")
            self.assertNotIn("homes", json.loads(config_path.read_text(encoding="utf-8")))

    def test_add_preserves_config_keys_and_refuses_invalid_or_duplicate_homes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli_home = root / "user"
            config_home = cli_home / ".config"
            config_home.mkdir(parents=True)
            config_path = config_home / "quarterdeck.json"
            config_path.write_text(json.dumps({"page_title": "Private fleet", "output_dir": "~/review"}), encoding="utf-8")
            firstmate_home = self.make_home(root)
            other_home = root / "fictional-other"
            (other_home / "data").mkdir(parents=True)
            (other_home / "data" / "backlog.md").write_text("# Empty queue\n", encoding="utf-8")

            added = self.run_cli(["add", str(firstmate_home), "--label", "Maple Harbor"], cli_home, config_home)
            self.assertEqual(added.returncode, 0, added.stderr)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["page_title"], "Private fleet")
            self.assertEqual(config["output_dir"], "~/review")
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)

            duplicate_path = self.run_cli(["add", str(firstmate_home / ".." / firstmate_home.name)], cli_home, config_home)
            self.assertEqual(duplicate_path.returncode, 2)
            self.assertIn("already registered", duplicate_path.stderr)

            duplicate_label = self.run_cli(["add", str(other_home), "--label", "maple harbor"], cli_home, config_home)
            self.assertEqual(duplicate_label.returncode, 2)
            self.assertIn("label is already in use", duplicate_label.stderr)

            invalid_home = root / "not-a-firstmate"
            invalid_home.mkdir()
            invalid = self.run_cli(["add", str(invalid_home)], cli_home, config_home)
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("expected data/backlog.md", invalid.stderr)
            self.assertEqual(len(json.loads(config_path.read_text(encoding="utf-8"))["homes"]), 1)

            second_added = self.run_cli(["add", str(other_home), "--label", "Willow Quay"], cli_home, config_home)
            self.assertEqual(second_added.returncode, 0, second_added.stderr)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["page_title"], "Private fleet")
            self.assertEqual([entry["label"] for entry in config["homes"]], ["Maple Harbor", "Willow Quay"])

            removed = self.run_cli(["remove", "Maple Harbor"], cli_home, config_home)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertEqual(len(json.loads(config_path.read_text(encoding="utf-8"))["homes"]), 1)
            missing = self.run_cli(["remove", "Maple Harbor"], cli_home, config_home)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("no registered home matches", missing.stderr)
            self.assertTrue((other_home / "data" / "backlog.md").is_file())

    def test_manage_commands_refuse_config_inside_checkout(self) -> None:
        home = Path(tempfile.mkdtemp())
        config = ROOT / "quarterdeck.local.json"
        try:
            result = self.run_cli(["add", str(home), "--config", str(config)], home, home / ".config")
            self.assertEqual(result.returncode, 2)
            self.assertIn("outside the Quarterdeck checkout", result.stderr)
            self.assertFalse(config.exists())
        finally:
            home.rmdir()

    def test_help_exposes_all_commands_docs_and_machine_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cli_home = root / "user"
            config_home = cli_home / ".config"
            root_help = self.run_cli(["--help"], cli_home, config_home)
            self.assertEqual(root_help.returncode, 0, root_help.stderr)
            self.assertIn("update", root_help.stdout)

            general = self.run_cli(["help"], cli_home, config_home)
            self.assertEqual(general.returncode, 0, general.stderr)
            self.assertIn("Documentation tree:", general.stdout)
            self.assertIn("quarterdeck help --json", general.stdout)

            machine = self.run_cli(["help", "--json"], cli_home, config_home)
            self.assertEqual(machine.returncode, 0, machine.stderr)
            index = json.loads(machine.stdout)
            commands = {entry["name"]: entry for entry in index["commands"]}
            self.assertTrue({"install", "update", "add", "list", "remove", "render", "reports", "help"}.issubset(commands))
            self.assertIn("how-to/install.md", commands["install"]["docs"])
            self.assertIn("how-to/update.md", commands["update"]["docs"])
            self.assertIn("--lavish", commands["render"]["usage"])
            self.assertIn("--all", commands["render"]["usage"])
            self.assertIn("explanation/attention-model.md", commands["render"]["docs"])
            self.assertIn("how-to/request-lavish-page.md", commands["render"]["docs"])
            self.assertIn("Lavish", commands["render"]["summary"])
            self.assertIn("how-to/review-reports.md", commands["reports"]["docs"])

            for command in ("install", "update", "add", "list", "remove", "render", "reports"):
                with self.subTest(command=command):
                    result = self.run_cli([command, "--help"], cli_home, config_home)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if command == "update":
                        self.assertIn("Run the user-local shell installer to update", result.stdout)
                    if command == "render":
                        self.assertIn("--lavish", result.stdout)
                        self.assertIn("--all", result.stdout)

            for subcommand in ("list", "read", "mark-reviewed", "unmark-reviewed"):
                result = self.run_cli(["reports", subcommand, "--help"], cli_home, config_home)
                self.assertEqual(result.returncode, 0, result.stderr)
                if subcommand == "read":
                    self.assertIn("--open", result.stdout)
                    self.assertNotIn("--output-dir", result.stdout)

    def test_installer_clones_updates_reuses_and_uninstalls_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = root / "quarterdeck-fixture.git"
            source = root / "source"
            user_home = root / "user"
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True, text=True)
            source.mkdir()
            subprocess.run(["git", "init", "--initial-branch=main"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.name", "Quarterdeck Fixture"], cwd=source, check=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=source, check=True)
            (source / "install.sh").write_bytes((ROOT / "install.sh").read_bytes())
            (source / "quarterdeck.py").write_text("#!/usr/bin/env python3\nprint('fixture')\n", encoding="utf-8")
            (source / "README.md").write_text("fixture version one\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture v1"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=source, check=True)
            subprocess.run(["git", "push", "-u", "origin", "main"], cwd=source, check=True, capture_output=True, text=True)

            env = os.environ.copy()
            env["HOME"] = str(user_home)
            env["QUARTERDECK_REPO_URL"] = str(remote)
            env["PATH"] = os.environ.get("PATH", "")
            config_path = user_home / ".config" / "quarterdeck.json"
            installer = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, text=True, capture_output=True)
            self.assertEqual(installer.returncode, 0, installer.stderr)
            self.assertTrue((user_home / ".local/share/quarterdeck/README.md").is_file())
            self.assertEqual(config_path.read_text(encoding="utf-8"), "{}\n")
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((user_home / ".config").stat().st_mode), 0o700)
            self.assertEqual(os.readlink(user_home / ".local/bin/quarterdeck"), str(user_home / ".local/share/quarterdeck/quarterdeck.py"))
            self.assertIn("not on PATH", installer.stdout)

            repeated = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, text=True, capture_output=True)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(config_path.read_text(encoding="utf-8"), "{}\n")

            (user_home / ".config").mkdir(parents=True, exist_ok=True)
            config_path.write_text('{"page_title":"Private setup"}\n', encoding="utf-8")
            (source / "README.md").write_text("fixture version two\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture v2"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "push"], cwd=source, check=True, capture_output=True, text=True)
            updated = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "install"], env=env, text=True, capture_output=True
            )
            self.assertEqual(updated.returncode, 0, updated.stderr)
            installed = user_home / ".local/share/quarterdeck"
            self.assertEqual((installed / "README.md").read_text(encoding="utf-8"), "fixture version two\n")
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"page_title":"Private setup"}\n')

            (source / "README.md").write_text("fixture version three\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture v3"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "push"], cwd=source, check=True, capture_output=True, text=True)
            updated_again = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "update"], env=env, text=True, capture_output=True
            )
            self.assertEqual(updated_again.returncode, 0, updated_again.stderr)
            self.assertEqual((installed / "README.md").read_text(encoding="utf-8"), "fixture version three\n")
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"page_title":"Private setup"}\n')

            uninstall = subprocess.run(["sh", str(installed / "install.sh"), "--uninstall"], env=env, text=True, capture_output=True)
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertFalse((user_home / ".local/bin/quarterdeck").exists())
            self.assertFalse(installed.exists())
            self.assertTrue(config_path.is_file())
            self.assertEqual(config_path.read_text(encoding="utf-8"), '{"page_title":"Private setup"}\n')

            reused_install = user_home / ".local/share/quarterdeck"
            subprocess.run(["git", "clone", str(remote), str(reused_install)], check=True, capture_output=True, text=True)
            reused = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, text=True, capture_output=True)
            self.assertEqual(reused.returncode, 0, reused.stderr)
            (reused_install / "README.md").write_text("local edit\n", encoding="utf-8")
            (source / "README.md").write_text("fixture version four\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture v4"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "push"], cwd=source, check=True, capture_output=True, text=True)
            refused = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "update"], env=env, text=True, capture_output=True
            )
            self.assertEqual(refused.returncode, 1)
            self.assertIn("has local changes", refused.stderr)
            self.assertEqual((reused_install / "README.md").read_text(encoding="utf-8"), "local edit\n")

            reused_uninstall = subprocess.run(["sh", str(reused_install / "install.sh"), "--uninstall"], env=env, text=True, capture_output=True)
            self.assertEqual(reused_uninstall.returncode, 0, reused_uninstall.stderr)
            self.assertFalse((user_home / ".local/bin/quarterdeck").exists())
            self.assertTrue(reused_install.is_dir())
            self.assertTrue(config_path.is_file())

    def test_recommendation_requires_an_explicit_report_label(self) -> None:
        self.assertIsNone(quarterdeck.report_recommendation("# Notes\n\nA strong recommendation is hidden here."))
        self.assertEqual(
            quarterdeck.report_recommendation("## Recommendation\n\nChoose the north path."),
            "Choose the north path.",
        )


PIER_PR = "https://github.com/example/pier-demo/" + "pull/7"
BULLET_BACKLOG = """# Backlog

## In flight

- [ ] harbor-chart - Harbor chart: draw the tide tables (repo: harbor-demo) (kind: ship) (since 2026-09-30)
  Drawing tides for the invented harbor.

## Queued

- [ ] kelp-survey - Kelp survey: map the invented reef data/kelp-survey/report.md blocked-by: harbor-chart (repo: reef-demo) (kind: ship) (since 2026-09-20) (held: yes) (hold: Waiting on the invented diver roster. Options: (a) survey now, (b) wait for spring. Recommendation: wait for spring (cheaper).) (hold-kind: captain)

  Filed 2026-09-20T08:00:00Z. The reef survey needs a choice (see data/kelp-survey/report.md).

  Second paragraph with the long body text that must stay on the page.
- [ ] later-work - Later work: sort the invented buoys (repo: buoy-demo) (kind: ship) (since 2026-09-25)

## Done

- [x] pier-fix - Pier fix: patch the invented pier (repo: pier-demo) (kind: ship) (merged 2026-10-01 """ + PIER_PR + """)
- [x] old-dock - Old dock: retire the invented dock (repo: dock-demo) (done 2026-08-01)
"""

SNAPSHOT_JSON = {
    "schema": "fm-bearings.v1",
    "home": "invented",
    "generated": "2026-10-04T00:00:00Z",
    "in_flight": [{"id": "harbor-chart", "kind": "ship", "state": "working", "repo": "harbor-demo",
                   "name": "Harbor chart", "doing": "drawing tides"}],
    "secondmates": [{"id": "kestrel", "state": "no_active_work", "doing": "mapping", "reason": "-"}],
    "decisions_open": [{"id": "kelp-survey", "key": "kelp-survey", "verb": "captain-hold",
                        "summary": "Kelp survey: map the invented reef (truncated sum", "owner": "(main)"}],
    "landed": [{"id": "pier-fix", "what": "Pier fix: patch the invented pier", "artifact":
                PIER_PR, "owner": "(main)"}],
    "gates": [{"id": "later-work", "title": "Later work", "blocked_by": "harbor-chart",
               "reason": "queued behind the chart", "owner": "(main)", "filed": "2026-09-25"}],
    "reports": [], "recorded_prs": [],
}


BOARD_BACKLOG = """# Backlog

## Queued

- [ ] kelp-hold - Kelp hold: pick a survey date (repo: reef-demo) (held: yes) (hold: Raw note: choose spring or autumn.) (hold-kind: captain)
- [ ] reef-hold - Reef hold: choose the buoy colour (repo: reef-demo) (held: yes) (hold: Raw note: red or green.) (hold-kind: captain)
- [ ] pier-pr - Pier patch (repo: pier-demo) (state: review_ready) (review_ready: yes) (pr """ + PIER_PR + """)
- [ ] waiting-work - Waiting work (repo: pier-demo) (held: yes) (hold: Raw note: waits on a vendor.) (hold-kind: external)

## Done

- [x] dock-fix - Dock fix: patch the invented dock (repo: dock-demo) (merged 2026-10-01)
"""


def board_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {"id": "pier-merge", "group": "merge", "title": "Pier merge",
                               "ask": "Merge the pier patch when you are ready.", "task": "pier-pr",
                               "links": [{"label": "Pier PR", "url": PIER_PR}]}
    item.update(overrides)
    return item


class CaptainBoardTests(unittest.TestCase):
    NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)

    def make_home(self, root: Path, items: object = None, raw: str | None = None, **board: object) -> Path:
        home = root / "invented-home"
        (home / "data").mkdir(parents=True)
        (home / "data" / "backlog.md").write_text(BOARD_BACKLOG, encoding="utf-8")
        if raw is not None:
            (home / "data" / "captain-board.json").write_text(raw, encoding="utf-8")
        elif items is not None:
            payload = {"version": 1, "updated_at": "2099-01-01T00:00:00Z", "items": items}
            payload.update(board)
            (home / "data" / "captain-board.json").write_text(json.dumps(payload), encoding="utf-8")
        return home

    def view(self, home: Path):
        homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("invented", home), discover_secondmates=True)]
        return homes, quarterdeck.board_view(homes)

    def titles(self, view, group: str) -> list[str]:
        return [entry.item.title for entry in view.groups[group]]

    def test_absent_board_leaves_page_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            homes, view = self.view(self.make_home(Path(temp)))
            self.assertIsNone(view)
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertNotIn('id="board"', page)
            self.assertNotIn("Not yet sorted", page)
            self.assertNotIn("<details class=\"dashboard-section reviews\"", page)
            self.assertIn('<div class="dashboard-section reviews" id="reviews">', page)

    def test_valid_board_parses_every_field(self) -> None:
        board = quarterdeck.parse_captain_board(json.dumps({
            "version": 1, "updated_at": "2026-10-08T10:00:00Z",
            "items": [board_item(detail="Short *detail*.", topic="Harbor", task="pier-pr")],
        }), Path("x"))
        self.assertEqual(board.warnings, [])
        self.assertEqual(board.updated_at, datetime(2026, 10, 8, 10, tzinfo=timezone.utc))
        item = board.items[0]
        self.assertEqual((item.id, item.group, item.title, item.topic, item.task), ("pier-merge", "merge", "Pier merge", "Harbor", "pier-pr"))
        self.assertEqual(item.links, [("Pier PR", PIER_PR)])

    def test_malformed_input_warns_without_crashing(self) -> None:
        for source in ("{not json", "[]", '{"version": 1, "updated_at": "2026-10-08T00:00:00Z"}'):
            board = quarterdeck.parse_captain_board(source, Path("x"))
            self.assertEqual(board.items, [])
            self.assertTrue(board.warnings)
        board = quarterdeck.parse_captain_board(json.dumps({
            "version": 1, "updated_at": "yesterday",
            "items": [
                board_item(),
                "nope",
                board_item(id="bad-group", group="shout"),
                board_item(id="no-ask", ask=""),
                board_item(id="pier-merge"),
                board_item(id="missing-task", task=None),
                board_item(id="odd-link", links=[{"label": "x", "url": "javascript:alert(1)"}], detail=7),
                board_item(id="bad-url", links=[{"label": "x", "url": "http://["}]),
            ],
        }), Path("x"))
        self.assertEqual([item.id for item in board.items], ["pier-merge", "odd-link", "bad-url"])
        self.assertEqual(board.items[1].links, [])
        self.assertEqual(board.items[2].links, [])
        self.assertIsNone(board.updated_at)
        text = "\n".join(board.warnings)
        for fragment in ("updated_at", "item 2", "bad-group", "no-ask", "task", "duplicate", "odd-link", "bad-url"):
            self.assertIn(fragment, text)

    def test_unsupported_versions_skip_items(self) -> None:
        for version in (2, True, 1.0):
            board = quarterdeck.parse_captain_board(json.dumps({
                "version": version, "items": [board_item()],
            }), Path("x"))
            self.assertFalse(board.supported)
            self.assertEqual(board.items, [])
            self.assertIn(f"unsupported version {version!r}", " ".join(board.warnings))

    def test_malformed_board_renders_warning_and_keeps_unsorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            homes, view = self.view(self.make_home(Path(temp), raw="{broken"))
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertIn("not valid JSON", page)
            self.assertEqual(len(view.unsorted), 3)
            self.assertIn("Raw note: choose spring or autumn.", page)

    def test_cross_check_hides_settled_and_lists_unsorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [
                board_item(),
                board_item(id="dock", title="Dock merge", task="dock-fix"),
                board_item(id="gone", title="Gone merge", task="no-such-task"),
                board_item(id="waiting", title="Vendor wait", task="waiting-work", group="forward"),
                board_item(id="note", title="Just a note", group="read", task="kelp-hold"),
                board_item(id="kelp", title="Kelp date", group="decide", task="kelp-hold"),
            ])
            homes, view = self.view(home)
            self.assertEqual(self.titles(view, "merge"), ["Pier merge"])
            self.assertEqual(self.titles(view, "forward"), [])
            self.assertEqual(self.titles(view, "read"), ["Just a note"])
            self.assertEqual(view.hidden, 3)
            self.assertEqual([entry.item.id for entry in view.unsorted], ["reef-hold"])
            self.assertEqual(view.unsorted[0].item.ask, "Raw note: red or green.")
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertNotIn("Dock merge", page)
            self.assertNotIn("Gone merge", page)
            self.assertNotIn("Vendor wait", page)
            self.assertIn("Not yet sorted", page)
            self.assertIn("Raw note: red or green.", page)
            board_section = page[page.index('id="board"'):page.index('id="reviews"')]
            self.assertNotIn("Raw note: waits on a vendor.", board_section)
            self.assertNotIn("Dock fix", board_section)

    def test_released_holds_are_settled_in_board_and_unsorted_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(
                id="released-board-item", title="Released board item", task="released-board-task",
            )])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                "- [ ] released-board-task - Released board task (state: released) (held: yes) (hold-kind: captain)\n"
                "- [ ] released-unlisted-task - Released unlisted task (state: released) (held: yes) (hold-kind: captain)\n",
                encoding="utf-8",
            )
            homes, view = self.view(home)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(view.hidden, 1)
            self.assertEqual(view.groups["merge"], [])
            self.assertEqual(view.unsorted, [])

    def test_bullet_blocker_fields_survive_attributes_for_board_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(task="board-blocked-task")])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                "- [ ] board-blocked-task - Board blocker (state: blocked) (blocked_by: internal-task) (waiting_on: captain)\n"
                "- [ ] hyphen-blocked-task - Hyphen blocker (state: blocked) (blocked-by: captain)\n"
                "- [ ] waiting-on-task - Waiting on blocker (state: blocked) (waiting_on: captain)\n"
                "- [ ] waiting-for-task - Waiting for blocker (state: blocked) (blocked_by: internal-task) (waiting_for: captain)\n"
                "- [ ] trailing-blocked-task - Trailing blocker (state: blocked) blocked-by: captain\n",
                encoding="utf-8",
            )
            homes, view = self.view(home)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(self.titles(view, "merge"), ["Pier merge"])
            self.assertEqual(
                [entry.item.id for entry in view.unsorted],
                ["hyphen-blocked-task", "waiting-on-task", "waiting-for-task", "trailing-blocked-task"],
            )

    def test_record_blocker_target_prioritizes_waiting_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(task="table-board-task")])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                "| id | title | state | held | hold_kind | blocked_by | waiting_on | waiting_for |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| table-board-task | Board table blocker | blocked | yes | captain | internal-task | captain | |\n"
                "| table-unlisted-task | Unlisted table blocker | blocked | yes | captain | internal-task | | captain |\n",
                encoding="utf-8",
            )
            homes, view = self.view(home)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(self.titles(view, "merge"), ["Pier merge"])
            self.assertEqual([entry.item.id for entry in view.unsorted], ["table-unlisted-task"])

    def test_group_order_numbers_topics_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [
                board_item(id="r", group="read", title="Read one", task="kelp-hold"),
                board_item(id="f", group="forward", title="Forward one", task="kelp-hold"),
                board_item(id="d1", group="decide", title="Decide A1", topic="Alpha", task="kelp-hold"),
                board_item(id="d2", group="decide", title="Decide B1", topic="Beta", task="kelp-hold"),
                board_item(id="d3", group="decide", title="Decide A2", topic="Alpha", task="kelp-hold"),
                board_item(id="a", group="approve", title="Approve one", task="kelp-hold", detail="More **context**."),
                board_item(id="m"),
            ])
            homes, view = self.view(home)
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            order = [page.index(f'id="board-{key}"') for key in ("merge", "approve", "decide", "forward", "read", "unsorted")]
            self.assertEqual(order, sorted(order))
            self.assertLess(page.index('id="board"'), page.index('id="reviews"'))
            self.assertEqual([e.item.title for e in view.groups["decide"]], ["Decide A1", "Decide A2", "Decide B1"])
            numbers = [(e.number, e.item.title) for key in quarterdeck.BOARD_GROUP_KEYS for e in view.groups[key]]
            self.assertEqual([n for n, _ in numbers], list(range(1, 8)))
            self.assertEqual(numbers[0][1], "Pier merge")
            self.assertIn('<h3 class="board-topic">Alpha</h3>', page)
            self.assertIn(f'<a href="{PIER_PR}">Pier PR</a>', page)
            for entry in [*(entry for group in view.groups.values() for entry in group), *view.unsorted]:
                self.assertIn(
                    f'data-refresh-key="{html.escape(entry.refresh_key, quote=True)}"', page,
                )
            self.assertIn("<strong>context</strong>", page)
            self.assertNotIn("<details class=\"dashboard-section reviews\" id=\"reviews\" open", page)
            self.assertIn('<details class="dashboard-section reviews" id="reviews">', page)
            self.assertIn('width=device-width', page)

    def test_board_age_and_stale_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item()], updated_at="2026-10-08T09:00:00Z")
            backlog = home / "data" / "backlog.md"
            for when, stale in (("2026-10-08T08:00:00Z", False), ("2026-10-08T10:00:00Z", True)):
                stamp = datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()
                os.utime(backlog, (stamp, stamp))
                homes, view = self.view(home)
                page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
                self.assertEqual(bool(view.status[0][2]), stale)
                self.assertEqual("Board may be out of date" in page, stale)
                self.assertIn("Board updated", page)

    def test_future_board_timestamp_reports_clock_skew(self) -> None:
        recent_past = datetime.fromtimestamp(self.NOW.timestamp() - 60, timezone.utc)
        near_future = datetime.fromtimestamp(self.NOW.timestamp() + 60, timezone.utc)
        later_future = datetime.fromtimestamp(self.NOW.timestamp() + 180, timezone.utc)
        self.assertEqual(quarterdeck.board_age_text(recent_past, self.NOW), "just now")
        for future in (near_future, later_future):
            label = quarterdeck.board_age_text(future, self.NOW)
            self.assertIn("clock skew", label)
            self.assertIn("ahead", label)
            self.assertNotIn("ago", label)

    def test_refresh_restores_open_disclosures_and_continues_on_storage_failure(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not available to execute the generated refresh script")
        page = quarterdeck.build_html([], "Q", refresh_seconds=30)
        refresh_script = re.findall(r"<script>(.*?)</script>", page, flags=re.S)[-1]
        harness = r"""
const storageKey = "quarterdeck-open-details";
const details = [
  { id: "item-9", dataset: { refreshKey: "/home/data/captain-board.json:merge" }, open: false },
  { id: "reviews", dataset: {}, open: false },
  { id: "", dataset: { refreshKey: "bearing:backlog-task-1.html" }, open: false }
];
const values = new Map([[storageKey, JSON.stringify([
  "/home/data/captain-board.json:merge", "reviews", "bearing:backlog-task-1.html"
])]]);
let reloaded = false;
globalThis.document = {
  querySelectorAll(selector) {
    if (selector === "details") return details;
    if (selector === "details[open]") return details.filter(detail => detail.open);
    throw new Error("unexpected selector " + selector);
  }
};
globalThis.sessionStorage = {
  getItem(key) { return values.get(key) || null; },
  setItem(key, value) { values.set(key, value); },
  removeItem(key) { values.delete(key); }
};
globalThis.location = { reload() { reloaded = true; } };
globalThis.setTimeout = (callback, delay) => {
  if (delay !== 30000) throw new Error("refresh cadence changed");
  callback();
};
"""
        verify = r"""
if (!details.every(detail => detail.open)) throw new Error("open disclosures were not restored");
if (!reloaded) throw new Error("page did not reload");
const saved = JSON.parse(values.get(storageKey));
if (saved.length !== 3) throw new Error("open disclosures were not saved");
"""
        result = subprocess.run(
            [node, "-e", harness + refresh_script + verify],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        no_storage = r"""
let reloaded = false;
globalThis.document = { querySelectorAll() { return []; } };
globalThis.sessionStorage = new Proxy({}, { get() { throw new Error("storage unavailable"); } });
globalThis.location = { reload() { reloaded = true; } };
globalThis.setTimeout = callback => callback();
"""
        unavailable_verify = "if (!reloaded) throw new Error('reload stopped when storage was unavailable');"
        result = subprocess.run(
            [node, "-e", no_storage + refresh_script + unavailable_verify],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bearing_task_details_have_stable_refresh_keys(self) -> None:
        item = quarterdeck.BItem(
            title="Example task", owner="home", item_id="task-1", body="Task details.",
        )
        rendered = quarterdeck.bearings_item_html(item, self.NOW, "call")
        self.assertIn('data-refresh-key="bearing:home:task-1"', rendered)

    def test_board_text_is_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(title="<script>x</script>", task="kelp-hold")])
            homes, _ = self.view(home)
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertNotIn("<script>x</script>", page)

    def test_tasks_axi_hold_shape_without_held_flag_is_still_waiting_on_captain(self) -> None:
        # tasks-axi writes (hold: reason) (hold-kind: captain) and no (held: yes).
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(id="reef-item", task="reef-approval", links=[])])
            (home / "data" / "backlog.md").write_text(
                "# Backlog\n\n## Queued\n\n"
                "- [ ] reef-approval - Approve the reef plan (repo: reef-demo) (kind: captain) (since 2026-10-07) "
                "(hold: Plan needs captain word) (hold-kind: captain)\n"
                "- [ ] reef-unboarded - Pick a buoy (kind: captain) (hold: Red or green) (hold-kind: captain)\n"
                "- [ ] reef-vendor - Await vendor quote (hold: Vendor owes a quote) (hold-kind: external)\n"
                "- [ ] reef-placeholder - No real hold (hold: -) (hold-kind: captain)\n",
                encoding="utf-8",
            )
            homes, view = self.view(home)
            self.assertEqual(view.hidden, 0)
            self.assertEqual(self.titles(view, "merge"), ["Pier merge"])
            self.assertEqual([entry.item.id for entry in view.unsorted], ["reef-unboarded"])
            bearings = quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False)
            self.assertEqual({item.item_id for item in bearings.call}, {"reef-approval", "reef-unboarded"})

    def test_unsorted_covers_unboarded_roots_and_secondmates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            board_home, other_home = root / "A", root / "B"
            child_home, child_board_home = root / "B-child", root / "B-board-child"

            def write_held(home: Path, task_id: str, title: str) -> None:
                data = home / "data"
                data.mkdir(parents=True)
                (data / "backlog.md").write_text(
                    f"# Backlog\n\n## Queued\n\n- [ ] {task_id} - {title} (held: yes) (hold: Raw note: {title}.) (hold-kind: captain)\n",
                    encoding="utf-8",
                )

            write_held(board_home, "anchor", "Anchor task")
            (board_home / "data" / "captain-board.json").write_text(
                json.dumps({"version": 1, "updated_at": "2026-10-08T10:00:00Z", "items": [board_item(task="anchor", links=[])]}),
                encoding="utf-8",
            )
            write_held(other_home, "root-hold", "Unboarded root hold")
            write_held(child_home, "child-hold", "Unboarded child hold")
            write_held(child_board_home, "child-anchor", "Child board task")
            (child_board_home / "data" / "captain-board.json").write_text(
                json.dumps({"version": 1, "updated_at": "2026-10-08T10:00:00Z", "items": [
                    board_item(id="child-board-item", title="Child merge", task="child-anchor", links=[])
                ]}),
                encoding="utf-8",
            )
            (other_home / "data" / "secondmates.md").write_text(
                f"- wren - Keeps the field guide (home: {child_home}; scope: Field guide)\n"
                f"- lark - Keeps the chart (home: {child_board_home}; scope: Chart)\n",
                encoding="utf-8",
            )
            homes = [
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("A", board_home), discover_secondmates=True),
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("B", other_home), discover_secondmates=True),
            ]
            view = quarterdeck.board_view(homes)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual([entry.item.title for entry in view.groups["merge"]], ["Pier merge", "Child merge"])
            self.assertEqual(
                [entry.item.title for entry in view.unsorted],
                ["Unboarded root hold", "Unboarded child hold"],
            )

    def test_merge_keeps_backlog_pr_with_only_lavish_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lavish_url = "https://example.com/lavish/review"
            home = self.make_home(Path(temp), [board_item(
                id="fallback", links=[{"label": "Review page", "url": lavish_url}],
            )])
            homes, view = self.view(home)
            self.assertIsNotNone(view)
            fallback = next(entry for entry in view.groups["merge"] if entry.item.id == "fallback")
            self.assertEqual(fallback.item.links, [("Review page", lavish_url), ("Pull request", PIER_PR)])
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertIn(f'<a href="{lavish_url}">Review page</a>', page)
            self.assertIn(f'<a href="{PIER_PR}">Pull request</a>', page)

    def test_unreadable_backlogs_keep_board_items_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(id="unverified", title="Unverified item")])
            (home / "data" / "backlog.md").unlink()
            homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("root", home), discover_secondmates=True)]
            view = quarterdeck.board_view(homes)
            self.assertIsNotNone(view)
            self.assertEqual([entry.item.title for entry in view.groups["merge"]], ["Unverified item"])
            page = quarterdeck.build_html(homes, "Q")
            self.assertIn('class="warning"', page)
            self.assertIn("Could not check board items for root", page)
            self.assertIn("the backlog could not be read", page)
            self.assertIn("Unverified item", page)

        with tempfile.TemporaryDirectory() as temp:
            child = Path(temp) / "child"
            home = self.make_home(Path(temp), [board_item(
                id="remote", title="Secondmate item", task="wren/remote-task",
            )])
            (child / "data").mkdir(parents=True)
            (home / "data" / "secondmates.md").write_text(
                f"- wren - Keeps the guide (home: {child}; scope: Guide)\n", encoding="utf-8",
            )
            homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("root", home), discover_secondmates=True)]
            view = quarterdeck.board_view(homes)
            self.assertIsNotNone(view)
            self.assertEqual([entry.item.title for entry in view.groups["merge"]], ["Secondmate item"])
            page = quarterdeck.build_html(homes, "Q")
            self.assertIn('class="warning"', page)
            self.assertIn("Could not check remote", page)
            self.assertIn("backlog could not be read", page)
            self.assertIn("the item remains visible", page)

    def test_unsupported_board_shows_warning_and_unsorted_holds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            raw = json.dumps({
                "version": 2,
                "updated_at": "2026-10-08T10:00:00Z",
                "items": [board_item(title="Unsupported board item")],
            })
            homes, view = self.view(self.make_home(Path(temp), raw=raw))
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(view.status, [])
            self.assertEqual(view.groups["merge"], [])
            self.assertIn("kelp-hold", [entry.item.id for entry in view.unsorted])
            self.assertIn("reef-hold", [entry.item.id for entry in view.unsorted])
            page = quarterdeck.build_html(homes, "Q")
            self.assertIn("unsupported version 2", page)
            self.assertNotIn("Unsupported board item", page)
            self.assertIn("Not yet sorted", page)

    def test_malformed_detail_markdown_warns_and_renders_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), [board_item(detail="Malformed [link](http://[)")])
            homes, _ = self.view(home)
            page = quarterdeck.build_html(homes, "Q", bearings=quarterdeck.build_bearings(homes, now=self.NOW, use_snapshot=False))
            self.assertIn("detail contains a malformed Markdown URL", page)
            self.assertIn("Malformed [link](http://[)", page)


class BearingsTests(unittest.TestCase):
    def make_home(self, root: Path, snapshot_script: str | None = None) -> Path:
        home = root / "invented-home"
        (home / "data" / "kelp-survey").mkdir(parents=True)
        (home / "data" / "backlog.md").write_text(BULLET_BACKLOG, encoding="utf-8")
        (home / "data" / "kelp-survey" / "report.md").write_text(
            "# Kelp survey report\n\n## Recommendation\n\nWait for spring.\n", encoding="utf-8")
        if snapshot_script is not None:
            script = home / "bin" / "fm-bearings-snapshot.sh"
            script.parent.mkdir()
            script.write_text(snapshot_script, encoding="utf-8")
            script.chmod(0o755)
        return home

    def snapshots(self, home: Path) -> list:
        homes = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("invented", home), discover_secondmates=True)]
        quarterdeck.apply_review_marks(homes, {})
        return homes

    def assert_four_sections(self, page: str) -> None:
        order = [page.index(f'id="{key}"') for key in ("call", "landed", "underway", "charted")]
        self.assertEqual(order, sorted(order))
        for title in ("Captain&#x27;s Call", "Recently Landed", "Underway", "Charted Next",
                      "Reports waiting on your review"):
            self.assertIn(title, page)

    def test_snapshot_path_classifies_fields_and_enriches_from_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp), "#!/bin/sh\ncat <<'JSON'\n" + json.dumps(SNAPSHOT_JSON) + "\nJSON\n")
            now = datetime(2026, 10, 4, tzinfo=timezone.utc)
            bearings = quarterdeck.build_bearings(self.snapshots(home), now=now)
            self.assertEqual(bearings.sources[0].mode, "snapshot")
            self.assertEqual([i.item_id for i in bearings.call], ["kelp-survey"])
            self.assertEqual([i.item_id for i in bearings.landed], ["pier-fix"])
            self.assertEqual([i.title for i in bearings.underway], ["Harbor chart"])
            self.assertEqual([i.item_id for i in bearings.charted], ["later-work"])
            page = quarterdeck.bearings_html(bearings)
            self.assert_four_sections(page)
            # The full backlog text replaces the snapshot's truncated summary.
            self.assertIn("Waiting on the invented diver roster", page)
            self.assertIn("Second paragraph with the long body text", page)
            self.assertNotIn("truncated sum", page)
            self.assertIn("(a) survey now, (b) wait for spring.", page)
            self.assertIn("wait for spring (cheaper).", page)
            self.assertIn("waiting 14 days", page)
            self.assertIn("project reef-demo", page)
            self.assertIn("Read report", page)
            self.assertIn(f'href="{PIER_PR}">{PIER_PR}<', page)
            self.assertIn("harbor-chart — queued behind the chart", page)
            self.assertIn("Firstmate bearings snapshot", page)

    def test_snapshot_secondmate_work_uses_registered_home_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            child_home = root / "kestrel-home"
            (child_home / "data").mkdir(parents=True)
            (child_home / "data" / "backlog.md").write_text("# Secondmate backlog\n", encoding="utf-8")
            (home / "data" / "secondmates.md").write_text(
                f"- kestrel - Maintains the field guide (home: {child_home}; scope: Field guide)\n",
                encoding="utf-8",
            )
            snapshots = self.snapshots(home)
            child = next(child for child in snapshots[0].children if child.spec.route_id == "kestrel")
            payload = {
                **SNAPSHOT_JSON,
                "decisions_open": [{"owner": "kestrel", "key": "remote-decision",
                                    "summary": "Approve the field guide", "verb": "review"}],
                "landed": [{"owner": "kestrel", "id": "remote-landed", "what": "Field guide update",
                            "artifact": "-"}],
                "in_flight": [{"id": "kestrel/kestrel-work", "name": "Willow Quay field guide",
                               "repo": None, "state": "working", "doing": "mapping"}],
                "gates": [{"owner": "kestrel", "id": "remote-gate", "title": "Wait for the field guide",
                           "filed": None, "blocked_by": "field guide", "reason": "review pending"}],
            }
            calls = []
            bearings = quarterdeck.build_bearings(
                snapshots, runner=lambda selected_home, _data_dir: (calls.append(selected_home) or payload, ""))
            task = next(item for item in bearings.underway if item.item_id == "kestrel/kestrel-work")
            self.assertEqual(task.title, "Willow Quay field guide")
            self.assertEqual(task.owner, child.spec.label)
            self.assertEqual(bearings.call[0].owner, child.spec.label)
            self.assertEqual(bearings.call[0].title, "Approve the field guide")
            self.assertEqual(bearings.call[0].detail,
                             f"Full background lives in the secondmate home: {child.spec.label}.")
            self.assertEqual(bearings.landed[0].owner, child.spec.label)
            self.assertEqual(bearings.charted[-1].owner, child.spec.label)
            self.assertEqual(calls, [home])

    def test_secondmate_summaries_do_not_duplicate_live_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            payload = {
                **SNAPSHOT_JSON,
                "decisions_open": [*SNAPSHOT_JSON["decisions_open"],
                                    {"owner": "decision-route", "key": "decision-route/task",
                                     "summary": "Resolve the route decision", "verb": "review"}],
                "in_flight": [*SNAPSHOT_JSON["in_flight"],
                              {"id": "kestrel/kestrel-task", "name": "Live child task", "repo": None,
                               "state": "working", "doing": "moving buoys"}],
                "secondmates": [
                    {"id": "kestrel", "state": "active_child_work", "doing": "moving buoys", "reason": ""},
                    {"id": "held-route", "state": "externally_held", "doing": "waiting for access",
                     "reason": "owner approval"},
                    {"id": "unknown-route", "state": "unrecognized", "doing": "status unavailable",
                     "reason": ""},
                    {"id": "idle-route", "state": "no_active_work", "doing": "", "reason": ""},
                    {"id": "decision-route", "state": "captain_decision", "doing": "one decision",
                     "reason": ""},
                ],
            }
            bearings = quarterdeck.build_bearings(
                self.snapshots(home), runner=lambda _home, _data_dir: (payload, ""))
            self.assertEqual([item.item_id for item in bearings.underway], ["harbor-chart", "kestrel/kestrel-task"])
            self.assertCountEqual([item.item_id for item in bearings.charted],
                                  ["later-work", "held-route", "unknown-route"])
            self.assertEqual([item.item_id for item in bearings.call], ["kelp-survey", "decision-route/task"])
            all_ids = [item.item_id for items in (bearings.call, bearings.landed, bearings.underway, bearings.charted)
                       for item in items]
            self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_snapshot_omissions_are_visible_in_the_page(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            payload = {
                **SNAPSHOT_JSON,
                "omitted": [{"surface": "landed rows", "reveal": "run bearings with <all>"}],
            }
            bearings = quarterdeck.build_bearings(
                self.snapshots(home), runner=lambda _home, _data_dir: (payload, ""))
            page = quarterdeck.bearings_html(bearings)
            self.assertIn("Snapshot omissions", page)
            self.assertIn("invented", page)
            self.assertIn("landed rows", page)
            self.assertIn("run bearings with &lt;all&gt;", page)

    def test_snapshot_script_receives_fm_home_and_json_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            script = ('#!/bin/sh\nprintf \'{"schema":"fm-bearings.v1","home":"%s","args":"%s",'
                      '"decisions_open":[],"landed":[],"in_flight":[],"secondmates":[],"gates":[]}\' "$FM_HOME" "$*"\n')
            home = self.make_home(Path(temp), script)
            data, reason = quarterdeck.run_bearings_snapshot(home)
            self.assertEqual(reason, "")
            self.assertEqual(data["home"], str(home))
            self.assertEqual(data["args"], "--json")

    def test_fallback_path_classifies_backlog_and_says_why(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            now = datetime(2026, 10, 4, tzinfo=timezone.utc)
            bearings = quarterdeck.build_bearings(self.snapshots(home), now=now)
            self.assertEqual(bearings.sources[0].mode, "fallback")
            self.assertEqual([i.item_id for i in bearings.call], ["kelp-survey"])
            self.assertEqual([i.item_id for i in bearings.landed], ["pier-fix", "old-dock"])
            self.assertEqual([i.item_id for i in bearings.underway], ["harbor-chart"])
            self.assertEqual([i.item_id for i in bearings.charted], ["later-work"])
            self.assertEqual(bearings.charted[0].waits_on, "queue order")
            page = quarterdeck.bearings_html(bearings)
            self.assert_four_sections(page)
            self.assertIn("fallback", page)
            self.assertIn("no executable bin/fm-bearings-snapshot.sh", page)
            self.assertIn("waiting on the invented diver roster".lower(), page.lower())
            self.assertIn("Second paragraph with the long body text", page)
            self.assertIn("(a) survey now, (b) wait for spring.", page)
            self.assertIn(PIER_PR, page)
            self.assertIn("waiting 14 days", page)
            self.assertIn("Kelp survey report", page)  # report is listed as needing review

    def test_fallback_when_script_fails_or_times_out_or_prints_garbage(self) -> None:
        cases = {
            "exit": ("#!/bin/sh\nexit 3\n", "status 3"),
            "garbage": ("#!/bin/sh\necho not json\n", "did not print JSON"),
            "schema": ('#!/bin/sh\necho \'{"schema":"other"}\'\n', "unrecognized schema"),
            "slow": ("#!/bin/sh\nsleep 5\n", "timed out"),
            "invalid utf-8": ("#!/bin/sh\nprintf '\\377'\n", "invalid UTF-8"),
        }
        for name, (script, expected) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as temp:
                home = self.make_home(Path(temp), script)
                bearings = quarterdeck.build_bearings(
                    self.snapshots(home), runner=lambda h, data_dir: quarterdeck.run_bearings_snapshot(
                        h, data_dir=data_dir, timeout=0.5))
                self.assertEqual(bearings.sources[0].mode, "fallback")
                self.assertIn(expected, bearings.sources[0].reason)
                self.assertEqual([i.item_id for i in bearings.call], ["kelp-survey"])

    def test_snapshot_allows_nullable_fields_and_uses_selected_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            data_dir = root / "archive" / "data"
            data_dir.mkdir(parents=True)
            (data_dir / "backlog.md").write_text(
                (home / "data" / "backlog.md").read_text(encoding="utf-8"), encoding="utf-8")
            payload = {
                "schema": "fm-bearings.v1",
                "decisions_open": [],
                "landed": [],
                "in_flight": [{"id": "archive-task", "name": "", "repo": None,
                               "state": "working", "doing": ""}],
                "secondmates": [],
                "gates": [{"owner": "(main)", "id": "archive-gate", "title": "Archive gate",
                           "filed": None, "blocked_by": "", "reason": ""}],
            }
            payload_script = (
                f"#!{sys.executable}\n"
                "import json, os\n"
                f"payload = {payload!r}\n"
                "payload['in_flight'][0]['name'] = os.environ['FM_DATA_OVERRIDE']\n"
                "print(json.dumps(payload))\n"
            )
            script = home / "bin" / "fm-bearings-snapshot.sh"
            script.parent.mkdir()
            script.write_text(payload_script, encoding="utf-8")
            script.chmod(0o755)
            snapshot = quarterdeck.load_home_snapshot(
                quarterdeck.HomeSpec("archive-home", home, data_dir=data_dir))
            with patch.dict(os.environ, {"FM_DATA_OVERRIDE": str(root / "wrong-data")}):
                bearings = quarterdeck.build_bearings([snapshot])
            self.assertEqual(bearings.sources[0].mode, "snapshot")
            self.assertEqual(bearings.underway[0].title, str(data_dir.resolve()))
            self.assertEqual(bearings.charted[0].filed, "")

    def test_malformed_snapshot_shapes_fall_back_to_the_backlog(self) -> None:
        malformed = [
            ({**SNAPSHOT_JSON, "schema": "fm-bearings.future"}, "unrecognized schema"),
            ({key: value for key, value in SNAPSHOT_JSON.items() if key != "decisions_open"}, "decisions_open is not an array"),
            ({**SNAPSHOT_JSON, "in_flight": [{**SNAPSHOT_JSON["in_flight"][0], "repo": 3}]}, "invalid repo"),
            ({**SNAPSHOT_JSON, "omitted": [{"surface": 3, "reveal": "run with --all"}]}, "invalid details"),
        ]
        for payload, expected in malformed:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                script = "#!/bin/sh\nprintf '%s' '" + json.dumps(payload) + "'\n"
                home = self.make_home(Path(temp), script)
                bearings = quarterdeck.build_bearings(self.snapshots(home))
                self.assertEqual(bearings.sources[0].mode, "fallback")
                self.assertIn(expected, bearings.sources[0].reason)
                self.assertEqual([item.item_id for item in bearings.call], ["kelp-survey"])

    def test_bullet_fallback_uses_closed_review_and_live_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            backlog = home / "data" / "backlog.md"
            extra = """

## More work
- [ ] review-patch - Review the added patch (repo: patch-demo) (state: review_ready) (pr: https://github.com/example/quarterdeck-demo/pull/84)
- [ ] running-task - Resume the running survey (state: running)
- [ ] progress-task - Continue the active chart (status: in_progress)

## Finished
- [ ] finished-task - File the completed sample
"""
            backlog.write_text(backlog.read_text(encoding="utf-8") + extra, encoding="utf-8")
            bearings = quarterdeck.build_bearings(self.snapshots(home))
            self.assertIn("review-patch", [item.item_id for item in bearings.call])
            self.assertTrue({"running-task", "progress-task"}.issubset({item.item_id for item in bearings.underway}))
            self.assertIn("finished-task", [item.item_id for item in bearings.landed])

    def test_bullet_fallback_requires_an_affirmative_hold_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            backlog = home / "data" / "backlog.md"
            backlog.write_text(backlog.read_text(encoding="utf-8") + """

## Hold markers
- [ ] released-hold - A released sample (held: no) (hold: stale reason) (hold-kind: captain)
- [ ] missing-hold - A queued sample (hold: -) (hold-kind: captain)
- [ ] current-hold - A current sample (held: yes) (hold: choose a route) (hold-kind: captain)
""", encoding="utf-8")
            bearings = quarterdeck.build_bearings(self.snapshots(home))
            self.assertEqual(
                {item.item_id for item in bearings.call},
                {"kelp-survey", "current-hold"},
            )
            self.assertTrue({"released-hold", "missing-hold"}.issubset(
                {item.item_id for item in bearings.charted}
            ))

    def test_merged_state_and_date_are_done_in_bullet_and_table_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            backlog = home / "data" / "backlog.md"
            backlog.write_text(backlog.read_text(encoding="utf-8") + """

## Merged bullets
- [ ] merged-state-bullet - Merged from state (state: merged)
- [ ] merged-date-bullet - Merged by date (state: working) (merged: 2026-10-02)
- [ ] merged-none-bullet - Not merged (state: working) (merged: (none))

## Merged table
| ID | Task | State | Merged |
| --- | --- | --- | --- |
| merged-state-table | Merged table state | merged | - |
| merged-date-table | Merged table date | working | 2026-10-03 |
| merged-none-table | Not merged table | working | (none) |
""", encoding="utf-8")
            snapshot = self.snapshots(home)[0]
            bearings = quarterdeck.build_bearings([snapshot], use_snapshot=False)
            landed = {item.item_id: item for item in bearings.landed}
            for item_id in (
                "merged-state-bullet", "merged-date-bullet", "merged-state-table", "merged-date-table",
            ):
                self.assertIn(item_id, landed)
                self.assertNotIn(item_id, {item.item_id for item in bearings.charted})
            for item_id in ("merged-none-bullet", "merged-none-table"):
                self.assertNotIn(item_id, landed)
                self.assertIn(item_id, {item.item_id for item in bearings.underway})
            self.assertEqual(landed["merged-date-bullet"].filed, "2026-10-02")
            self.assertEqual(landed["merged-date-table"].filed, "2026-10-03")

    def test_cancelled_tasks_are_omitted_from_all_bearings_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = self.make_home(Path(temp))
            backlog = home / "data" / "backlog.md"
            backlog.write_text(backlog.read_text(encoding="utf-8") + f"""

## Cancelled tasks
- [ ] cancelled-review - Cancelled review task (state: cancelled) (held: yes) (hold-kind: captain) (review_ready: yes) (pr: {PIER_PR})
- [x] canceled-checked - Canceled checked task (state: canceled) (merged: 2026-10-02)
- [ ] cancelled-live - Cancelled active task (state: cancelled)
- [ ] canceled-gate - Canceled queued task (state: canceled)

## Cancelled table
| ID | Task | State | Closed | Blocked by |
| --- | --- | --- | --- | --- |
| canceled-table | Canceled table task | canceled | 2026-10-03 | captain |
""", encoding="utf-8")
            snapshot = self.snapshots(home)[0]
            cancelled_ids = {"cancelled-review", "canceled-checked", "cancelled-live", "canceled-gate", "canceled-table"}
            groups = quarterdeck.attention_groups(snapshot, show_all=True)
            grouped_ids = {
                quarterdeck.value_for(record.fields, "id")
                for records in groups.values() for record in records
            }
            self.assertTrue(cancelled_ids.isdisjoint(grouped_ids))

            fallback = quarterdeck.build_bearings([snapshot], use_snapshot=False)
            for section in (fallback.call, fallback.landed, fallback.underway, fallback.charted):
                self.assertTrue(cancelled_ids.isdisjoint(item.item_id for item in section))

            payload = {
                **SNAPSHOT_JSON,
                "decisions_open": [{"owner": "(main)", "key": "cancelled-review", "id": "cancelled-review",
                                    "summary": "Cancelled review task", "verb": "captain-hold"}],
                "landed": [{"owner": "(main)", "id": "canceled-table", "what": "Canceled table task",
                            "artifact": "-"}],
                "in_flight": [{"id": "cancelled-live", "name": "Cancelled active task", "repo": None,
                               "state": "working", "doing": "-"}],
                "gates": [{"owner": "(main)", "id": "canceled-gate", "title": "Canceled queued task",
                           "filed": None, "blocked_by": "-", "reason": "-"}],
            }
            snapshot_result = quarterdeck.build_bearings(
                [snapshot], runner=lambda _home, _data_dir: (payload, ""))
            for section in (snapshot_result.call, snapshot_result.landed, snapshot_result.underway,
                            snapshot_result.charted):
                self.assertTrue(cancelled_ids.isdisjoint(item.item_id for item in section))

    def test_no_snapshot_option_skips_the_script(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "ran"
            home = self.make_home(Path(temp), f"#!/bin/sh\ntouch {marker}\n")
            bearings = quarterdeck.build_bearings(self.snapshots(home), use_snapshot=False)
            self.assertFalse(marker.exists())
            self.assertIn("--no-snapshot", bearings.sources[0].reason)

    def test_every_section_renders_an_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "empty-home"
            (home / "data").mkdir(parents=True)
            (home / "data" / "backlog.md").write_text("# Backlog\n", encoding="utf-8")
            page = quarterdeck.bearings_html(quarterdeck.build_bearings(self.snapshots(home)))
            self.assertEqual(page.count('class="empty"'), 5)
            self.assertIn("Nothing is waiting on a captain decision.", page)
            self.assertIn("No reports are waiting on your review.", page)


class ServeTests(unittest.TestCase):
    def make_args(self, home: Path, **extra: object) -> object:
        import argparse
        values = dict(home=str(home), config=None, title=None, no_snapshot=True)
        values.update(extra)
        return argparse.Namespace(**values)

    def start(self, cache: quarterdeck.SiteCache):
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), quarterdeck.make_handler(cache))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return server.server_address[1]

    def fetch(self, port: int, path: str, method: str = "GET", headers: dict[str, str] | None = None):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        connection.close()
        return response.status, response.headers, body

    def test_cache_reuses_render_until_ttl_then_rerenders(self) -> None:
        calls: list[int] = []
        clock = [100.0]
        cache = quarterdeck.SiteCache(lambda: calls.append(1) or {"index.html": str(len(calls))}, 30, lambda: clock[0])
        self.assertEqual(cache.get()["index.html"], "1")
        clock[0] += 29
        self.assertEqual(cache.get()["index.html"], "1")
        clock[0] += 1
        self.assertEqual(cache.get()["index.html"], "2")
        self.assertEqual(len(calls), 2)

    def test_server_renders_per_request_and_serves_linked_pages_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            home = BearingsTests().make_home(Path(temp))
            clock = [0.0]
            cache = quarterdeck.SiteCache(
                quarterdeck.site_builder(self.make_args(home)), 30, lambda: clock[0])
            port = self.start(cache)
            status, headers, body = self.fetch(port, "/")
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers["Content-Type"])
            self.assertIn("Kelp survey: map the invented reef", body)
            self.assertIn("Generated at", body)
            self.assertIn("}, 60000);", body)
            links = re.findall(r'href="((?:item|report|backlog)-[0-9a-f]{12}\.html)"', body)
            self.assertTrue(any(link.startswith("item-") for link in links))
            self.assertTrue(any(link.startswith("report-") for link in links))
            for link in set(links):
                self.assertEqual(self.fetch(port, "/" + link)[0], 200, link)
            # Edits show up only after the cache expires.
            backlog = home / "data" / "backlog.md"
            backlog.write_text(backlog.read_text(encoding="utf-8").replace("Later work: sort", "Renamed work: sort"),
                               encoding="utf-8")
            self.assertIn("Later work: sort", self.fetch(port, "/")[2])
            clock[0] += 31
            fresh = self.fetch(port, "/index.html?x=1")[2]
            self.assertIn("Renamed work: sort", fresh)
            self.assertNotIn("Later work: sort", fresh)
            # Read only: nothing but GET, and only generated pages.
            for method in ("POST", "PUT", "DELETE", "HEAD"):
                self.assertEqual(self.fetch(port, "/", method)[0], 405, method)
            self.assertEqual(self.fetch(port, "/../data/backlog.md")[0], 404)
            self.assertEqual(self.fetch(port, "/item-000000000000.html")[0], 404)

    def test_serve_listens_on_every_requested_address_on_one_port(self) -> None:
        import socket
        import urllib.request
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "s")}):
            home = BearingsTests().make_home(Path(temp))
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            args = quarterdeck.make_parser().parse_args(
                ["serve", "--home", str(home), "--no-snapshot", "--port", str(port),
                 "--host", "127.0.0.1", "--host", "127.0.0.2"])
            self.assertEqual(quarterdeck.bind_hosts(args), ["127.0.0.1", "127.0.0.2"])
            thread = threading.Thread(target=quarterdeck.serve, args=(args,), daemon=True)
            with redirect_stdout(io.StringIO()):
                thread.start()
                for host in ("127.0.0.1", "127.0.0.2"):
                    for _ in range(100):
                        try:
                            body = urllib.request.urlopen(f"http://{host}:{port}/", timeout=2).read().decode()
                            break
                        except OSError:
                            time.sleep(0.1)
                    else:
                        self.fail(f"not listening on {host}")
                    self.assertIn("Captain&#x27;s Call", body)

    def test_ipv6_wildcard_and_ipv4_wildcard_can_share_a_port(self) -> None:
        import socket
        handler = quarterdeck.make_handler(quarterdeck.SiteCache(lambda: {"index.html": "ok"}))
        try:
            ipv6 = quarterdeck.make_server("::", 0, handler)
        except OSError as exc:
            self.skipTest(f"IPv6 wildcard binding is unavailable: {exc}")
        self.addCleanup(ipv6.server_close)
        try:
            ipv4 = quarterdeck.make_server("0.0.0.0", ipv6.server_address[1], handler)
        except OSError as exc:
            self.fail(f"IPv4 wildcard could not share the IPv6 listener port: {exc}")
        self.addCleanup(ipv4.server_close)
        self.assertEqual(ipv6.address_family, socket.AF_INET6)
        self.assertEqual(ipv4.address_family, socket.AF_INET)

    def test_ipv4_wildcard_classification_does_not_require_ipv6_mapping(self) -> None:
        self.assertFalse(quarterdeck.is_loopback("0.0.0.0"))
        self.assertFalse(quarterdeck.request_host_is_loopback("0.0.0.0"))

    def start_marking(self, temp: str, **extra: object):
        home = BearingsTests().make_home(Path(temp))
        args = self.make_args(home, allow_marks=True, **extra)
        token = quarterdeck.load_or_create_mark_token(quarterdeck.load_report_snapshots(args)[1])
        cache = quarterdeck.SiteCache(quarterdeck.site_builder(args, True), 30)
        handler = quarterdeck.make_handler(
            cache, token, lambda report_id, reviewed, fingerprint: quarterdeck.set_report_mark(
                args, report_id, reviewed, fingerprint
            ))
        return self.start_handler(handler), token, cache, home

    def start_handler(self, handler):
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        return server.server_address[1]

    def post_mark(self, port: int, payload: object, token: str | None, headers: dict[str, str] | None = None,
                  path: str = quarterdeck.MARK_ENDPOINT):
        request_headers = {"Content-Type": "application/json"}
        if token is not None:
            request_headers["X-Quarterdeck-Token"] = token
        request_headers.update(headers or {})
        body = payload if isinstance(payload, str) else json.dumps(payload)
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("POST", path, body=body, headers=request_headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8")
        connection.close()
        return response.status, text

    def test_marks_endpoint_marks_and_unmarks_through_the_cli_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            port, token, _cache, _home = self.start_marking(temp)
            status, page = self.fetch(port, "/")[0::2]
            self.assertEqual(status, 200)
            self.assertNotIn(token, page)
            self.assertNotIn('name="quarterdeck-mark-token"', page)
            self.assertIn("Mark reviewed", page)
            self.assertNotIn("after reading: quarterdeck reports mark-reviewed", page)
            button = re.search(
                r'data-mark-report="([^"]+)" data-mark-fingerprint="([0-9a-f]{64})" data-mark-action="mark"', page
            )
            report_id, fingerprint = button.groups()
            status, body = self.post_mark(port, {
                "report_id": report_id, "fingerprint": fingerprint, "action": "mark"
            }, token,
                                          {"Origin": f"http://127.0.0.1:{port}", "Host": f"127.0.0.1:{port}"})
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body), {"report_id": report_id, "reviewed": True})
            marks = quarterdeck.load_review_marks(quarterdeck.review_state_path())
            self.assertIn(report_id, marks)
            page = self.fetch(port, "/")[2]  # the cache was invalidated, so no manual re-render is needed
            self.assertNotIn(
                f'data-mark-report="{report_id}" data-mark-fingerprint="{fingerprint}" data-mark-action="mark"',
                page,
            )
            self.assertIn(f'data-mark-report="{report_id}" data-mark-fingerprint="{fingerprint}" data-mark-action="unmark"', page)
            status, body = self.post_mark(port, {
                "report_id": report_id, "fingerprint": fingerprint, "action": "unmark"
            }, token)
            self.assertEqual(status, 200, body)
            self.assertNotIn(report_id, quarterdeck.load_review_marks(quarterdeck.review_state_path()))
            self.assertIn(f'data-mark-report="{report_id}" data-mark-fingerprint="{fingerprint}" data-mark-action="mark"', self.fetch(port, "/")[2])
            token_path = quarterdeck.review_state_path().with_name("mark-token")
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(quarterdeck.load_or_create_mark_token(quarterdeck.review_state_path()), token)

    def test_marks_endpoint_refuses_bad_requests_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            port, token, _cache, _home = self.start_marking(temp)
            page = self.fetch(port, "/")[2]
            button = re.search(
                r'data-mark-report="([^"]+)" data-mark-fingerprint="([0-9a-f]{64})"', page
            )
            report_id, fingerprint = button.groups()
            good = {"report_id": report_id, "fingerprint": fingerprint, "action": "mark"}
            self.assertEqual(self.post_mark(port, good, None)[0], 403)
            self.assertEqual(self.post_mark(port, good, "wrong-token")[0], 403)
            self.assertEqual(self.post_mark(port, good, token, {"Origin": "http://attacker.example"})[0], 403)
            self.assertEqual(self.post_mark(port, good, token, {"Sec-Fetch-Site": "cross-site"})[0], 403)
            self.assertEqual(self.post_mark(port, good, token, {"Host": "attacker.example"})[0], 403)
            self.assertEqual(self.post_mark(port, good, token, {"Content-Type": "text/plain"})[0], 415)
            self.assertEqual(self.post_mark(port, {
                "report_id": "nope/unknown-1", "fingerprint": fingerprint, "action": "mark"
            }, token)[0], 404)
            self.assertEqual(self.post_mark(port, {**good, "action": "delete"}, token)[0], 400)
            self.assertEqual(self.post_mark(port, {**good, "action": []}, token)[0], 400)
            self.assertEqual(self.post_mark(port, {**good, "extra": 1}, token)[0], 400)
            self.assertEqual(self.post_mark(port, "not json", token)[0], 400)
            self.assertEqual(self.post_mark(port, good, token, path="/api/other")[0], 405)
            self.assertEqual(self.post_mark(port, good, token, path="/")[0], 405)
            for method in ("PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                self.assertEqual(self.fetch(port, quarterdeck.MARK_ENDPOINT, method)[0], 405, method)
            self.assertEqual(self.fetch(port, quarterdeck.MARK_ENDPOINT)[0], 404)
            self.assertEqual(quarterdeck.load_review_marks(quarterdeck.review_state_path()), {})

    def test_marks_endpoint_rejects_a_stale_page_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            port, token, _cache, home = self.start_marking(temp)
            page = self.fetch(port, "/")[2]
            button = re.search(
                r'data-mark-report="([^"]+)" data-mark-fingerprint="([0-9a-f]{64})" data-mark-action="mark"', page
            )
            report_id, fingerprint = button.groups()
            report_path = home / "data" / report_id.split("/", 1)[1] / "report.md"
            report_path.write_text(report_path.read_text(encoding="utf-8") + "\nChanged after the page loaded.\n", encoding="utf-8")
            status, body = self.post_mark(port, {
                "report_id": report_id, "fingerprint": fingerprint, "action": "mark"
            }, token)
            self.assertEqual(status, 409, body)
            self.assertIn("report changed since the page was loaded", body)
            self.assertEqual(quarterdeck.load_review_marks(quarterdeck.review_state_path()), {})

    def test_marks_are_refused_and_hidden_unless_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(temp) / "state")}):
            home = BearingsTests().make_home(Path(temp))
            args = self.make_args(home)
            port = self.start(quarterdeck.SiteCache(quarterdeck.site_builder(args), 30))
            page = self.fetch(port, "/")[2]
            self.assertNotIn("data-mark-report", page)
            self.assertNotIn("quarterdeck-mark-token", page)
            self.assertIn("after reading: quarterdeck reports mark-reviewed", page)
            report_id = re.search(r"quarterdeck reports mark-reviewed ([^<]+)<", page).group(1)
            self.assertEqual(self.post_mark(port, {"report_id": report_id, "fingerprint": "0" * 64, "action": "mark"}, "x" * 43)[0], 405)
            self.assertFalse(quarterdeck.review_state_path().exists())
            self.assertFalse(quarterdeck.review_state_path().with_name("mark-token").exists())
            # Static render never carries the buttons or token.
            snapshots = [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("demo", home), discover_secondmates=True)]
            quarterdeck.apply_review_marks(snapshots, {})
            self.assertNotIn("data-mark-report", quarterdeck.build_html(snapshots, "T"))

    def test_allow_marks_option_parses_and_reaches_service_unit(self) -> None:
        parser = quarterdeck.make_parser()
        self.assertFalse(parser.parse_args(["serve"]).allow_marks)
        args = parser.parse_args(["service", "--allow-marks"])
        self.assertTrue(args.allow_marks)
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": tempfile.gettempdir()}, clear=True):
            command = parse_systemd_unit(quarterdeck.service_unit(args))["Service"]["ExecStart"][0]
            self.assertIn("--allow-marks", command)

    def test_ambiguous_report_ids_render_disabled_mark_buttons(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = BearingsTests().make_home(root / "first")
            second = BearingsTests().make_home(root / "second")
            homes = [
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("Shared Home", first), discover_secondmates=True),
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("Shared Home", second), discover_secondmates=True),
            ]
            quarterdeck.apply_review_marks(homes, {})
            bearings = quarterdeck.build_bearings(homes, use_snapshot=False)
            page = quarterdeck.build_html(homes, "T", bearings=bearings, allow_marks=True)
            self.assertEqual(page.count('data-mark-report="shared-home/amber-18"'), 0)
            disabled = 'button class="request-lavish" type="button" disabled>Mark reviewed</button>'
            self.assertEqual(page.count(disabled), len(bearings.reports))
            self.assertEqual(page.count("ID is shared by multiple homes; marking is disabled."), len(bearings.reports))

    def test_incomplete_local_report_discovery_disables_mark_and_unmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            readable_home = BearingsTests().make_home(root / "readable")
            (readable_home / "data" / "later-report").mkdir()
            (readable_home / "data" / "later-report" / "report.md").write_text(
                "# Later report\n\nStill needs review.\n", encoding="utf-8"
            )
            incomplete_home = BearingsTests().make_home(root / "incomplete")
            (incomplete_home / "data" / "backlog.md").unlink()
            homes = [
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("Readable", readable_home)),
                quarterdeck.load_home_snapshot(quarterdeck.HomeSpec("Incomplete", incomplete_home)),
            ]
            quarterdeck.assign_report_ids(homes)
            reviewed_report = homes[0].reports[0]
            quarterdeck.apply_review_marks(
                homes, {reviewed_report.report_id: reviewed_report.fingerprint}
            )
            bearings = quarterdeck.build_bearings(homes, use_snapshot=False)
            page = quarterdeck.build_html(homes, "T", bearings=bearings, allow_marks=True)
            self.assertIn('button class="request-lavish" type="button" disabled>Mark reviewed</button>', page)
            self.assertIn('button class="request-lavish" type="button" disabled>Unmark</button>', page)
            self.assertNotIn("data-mark-report=", page)
            self.assertEqual(
                page.count("Review changes are disabled while a selected local home is incomplete."), 2
            )

            static_page = quarterdeck.build_html(homes, "T", bearings=bearings)
            self.assertNotIn("after reading: quarterdeck reports mark-reviewed", static_page)
            self.assertIn(
                "Review changes are disabled while a selected local home is incomplete.", static_page
            )

    def test_server_reports_render_failure_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cache = quarterdeck.SiteCache(lambda: (_ for _ in ()).throw(ValueError("broken home")), 30)
            port = self.start(cache)
            status, _headers, body = self.fetch(port, "/")
            self.assertEqual(status, 500)
            self.assertIn("broken home", body)

    def test_loopback_server_rejects_nonlocal_host_on_all_pages(self) -> None:
        cache = quarterdeck.SiteCache(
            lambda: {"index.html": "private index", "report-secret.html": "private report"}, 30)
        port = self.start(cache)
        self.assertEqual(self.fetch(port, "/")[0], 200)
        for host in ("localhost", "localhost.", "127.0.0.1", "[::1]"):
            self.assertEqual(self.fetch(port, "/", headers={"Host": f"{host}:{port}"})[0], 200)
        for path in ("/", "/report-secret.html"):
            status, _headers, body = self.fetch(
                port, path, headers={"Host": f"attacker.example:{port}"})
            self.assertEqual(status, 403)
            self.assertNotIn("private", body)

    def test_serve_defaults_to_loopback_and_service_unit_runs_serve(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_CONFIG_HOME": temp}, clear=True):
            parser = quarterdeck.make_parser()
            args = parser.parse_args(["serve"])
            self.assertEqual(args.port, 8765)
            self.assertEqual(quarterdeck.bind_hosts(args), ["127.0.0.1"])
            unit = quarterdeck.service_unit(parser.parse_args(["service", "--port", "9100"]))
            directives = parse_systemd_unit(unit)
            command = directives["Service"]["ExecStart"][0]
            self.assertEqual(command[-4:], ["--host", "127.0.0.1", "--port", "9100"])
            multi = quarterdeck.service_unit(parser.parse_args(
                ["service", "--host", "127.0.0.1", "--host", "::1"]))
            command = parse_systemd_unit(multi)["Service"]["ExecStart"][0]
            self.assertEqual(command[-6:], ["--host", "127.0.0.1", "--host", "::1", "--port", "8765"])
            self.assertEqual(directives["Install"]["WantedBy"][0], ["default.target"])

    def test_service_unit_preserves_effective_home_and_custom_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_home = root / "${CONFIG}" / "shell-config"
            config_home.mkdir(parents=True)
            home = root / "selected-home"
            alternate_home = root / "${USER}" / "shell-home"
            config_path = config_home / "quarterdeck.json"
            config_path.write_text(json.dumps({"homes": [{"path": str(home)}]}), encoding="utf-8")
            data_override = root / "${DATA}" / "shell-data"
            state_home = root / "${STATE}" / "shell-state"
            env = {
                "XDG_CONFIG_HOME": str(config_home),
                "FM_HOME": str(alternate_home),
                "FM_DATA_OVERRIDE": str(data_override),
                "XDG_STATE_HOME": str(state_home),
            }
            with patch.dict(os.environ, env):
                parser = quarterdeck.make_parser()
                unit = quarterdeck.service_unit(parser.parse_args(["service"]))
                directives = parse_systemd_unit(unit)
                command = directives["Service"]["ExecStart"][0]
                unit_environment = directives["Service"]["Environment"]
                self.assertIn(["--config", str(config_path.resolve())], [command[index:index + 2]
                              for index in range(len(command) - 1)])
                self.assertNotIn(str(alternate_home.resolve()), command)
                self.assertNotIn(f"FM_DATA_OVERRIDE={data_override.resolve()}", unit_environment)
                self.assertIn(f"XDG_STATE_HOME={state_home.resolve()}", unit_environment)

                config_path.write_text(json.dumps({"page_title": "Selected"}), encoding="utf-8")
                with patch.object(quarterdeck.sys, "executable", str(root / "python${PYTHON}")):
                    unit = quarterdeck.service_unit(parser.parse_args(["service"]))
                directives = parse_systemd_unit(unit)
                command = directives["Service"]["ExecStart"][0]
                unit_environment = directives["Service"]["Environment"]
                self.assertIn(["--home", str(alternate_home.resolve())], [command[index:index + 2]
                              for index in range(len(command) - 1)])
                self.assertEqual(command[0], str(root / "python${PYTHON}"))
                self.assertIn(["--config", str(config_path.resolve())], [command[index:index + 2]
                              for index in range(len(command) - 1)])
                self.assertIn(f"FM_DATA_OVERRIDE={data_override.resolve()}", unit_environment)
                self.assertIn(f"XDG_STATE_HOME={state_home.resolve()}", unit_environment)

    def test_service_reports_config_errors_without_tracebacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "quarterdeck.json"
            config_path.write_text('{"unsupported": true}', encoding="utf-8")
            for write in (False, True):
                args = ["service", "--config", str(config_path)]
                if write:
                    args.append("--write")
                stderr = io.StringIO()
                with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
                    result = quarterdeck.main(args)
                self.assertEqual(result, 2)
                self.assertIn("unsupported config key", stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())

    def test_url_reports_installed_service_addresses_and_default_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_home = Path(temp) / "config"
            unit_path = config_home / "systemd" / "user" / "quarterdeck.service"
            env = {"XDG_CONFIG_HOME": str(config_home), "HOME": temp}
            with patch.dict(os.environ, env, clear=True):
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(quarterdeck.main(["url"]), 0)
                self.assertIn("http://127.0.0.1:8765/", output.getvalue())
                self.assertIn("no installed Quarterdeck service was found", output.getvalue())
                self.assertIn("A plain `quarterdeck serve` listens on the default address shown", output.getvalue())
                self.assertIn("custom --host/--port is not detected", output.getvalue())
                self.assertIn("quarterdeck serve", output.getvalue())

                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(quarterdeck.main(["url", "--json"]), 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result["source"], "default")
                self.assertIn("No installed Quarterdeck service was found", result["note"])
                self.assertIn("custom --host/--port is not detected", result["note"])

                unit_path.parent.mkdir(parents=True)
                unit_path.write_text(quarterdeck.service_unit(
                    quarterdeck.make_parser().parse_args(
                        [
                            "service", "--host", "127.0.0.1", "--host", "::1",
                            "--host", "fe80::1%eth0", "--port", "9100",
                        ]
                    )
                ), encoding="utf-8")
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(quarterdeck.main(["url"]), 0)
                self.assertIn("http://[fe80::1%25eth0]:9100/", output.getvalue())

                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(quarterdeck.main(["url", "--json"]), 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result["urls"], [
                    "http://127.0.0.1:9100/", "http://[::1]:9100/", "http://[fe80::1%25eth0]:9100/",
                ])
                self.assertEqual(result["hosts"], ["127.0.0.1", "::1", "fe80::1%eth0"])
                self.assertIn("quarterdeck.service", result["source"])

    def test_url_is_discoverable_through_command_index(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(quarterdeck.main(["help", "--json"]), 0)
        index = json.loads(output.getvalue())
        command = next(item for item in index["commands"] if item["name"] == "url")
        self.assertIn("how-to/serve-the-page.md", command["docs"])

    def test_server_options_reject_removed_aliases_and_invalid_ports(self) -> None:
        parser = quarterdeck.make_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["serve", "--bind", "127.0.0.1"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["service", "--bind", "127.0.0.1"])
        for command in ("serve", "service"):
            for value in ("-1", "0", "65536", "nan"):
                with self.subTest(command=command, port=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parser.parse_args([command, "--port", value])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["serve", "--cache", "-1"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["serve", "--refresh", "-1"])

    def test_service_rejects_control_characters_in_serialized_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_home = str(Path(temp) / "config")
            cases = [
                (["service", "--host", "127.0.0.1\nRestart=no"], {}),
                (["service", "--home", "/tmp/home\nRestart=no"], {}),
                (["service", "--home", "/tmp/home"], {"FM_DATA_OVERRIDE": "/tmp/data\nRestart=no"}),
                (["service", "--home", "/tmp/home"], {"XDG_STATE_HOME": "/tmp/state\nRestart=no"}),
            ]
            for args, environment in cases:
                env = {"HOME": temp, "XDG_CONFIG_HOME": config_home, **environment}
                with self.subTest(args=args, environment=environment), patch.dict(os.environ, env, clear=True), \
                        redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
                    result = quarterdeck.main(args)
                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("cannot contain control characters", stderr.getvalue())

    def test_service_write_refuses_a_symlink_unit_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_home = root / "config"
            unit = config_home / "systemd" / "user" / "quarterdeck.service"
            unit.parent.mkdir(parents=True)
            target = root / "unrelated.txt"
            target.write_text("preserve this file", encoding="utf-8")
            unit.symlink_to(target)
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}), redirect_stderr(io.StringIO()):
                code = quarterdeck.main(["service", "--write"])
            self.assertEqual(code, 2)
            self.assertTrue(unit.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "preserve this file")

    def test_service_write_goes_to_user_unit_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {"XDG_CONFIG_HOME": str(Path(temp) / "config")}
            with patch.dict(os.environ, env), redirect_stdout(io.StringIO()):
                code = quarterdeck.main(["service", "--write"])
            self.assertEqual(code, 0)
            unit = Path(temp) / "config" / "systemd" / "user" / "quarterdeck.service"
            directives = parse_systemd_unit(unit.read_text(encoding="utf-8"))
            self.assertEqual(len(directives["Service"]["ExecStart"]), 1)
            self.assertEqual(directives["Service"]["ExecStart"][0][2], "serve")
            self.assertEqual(directives["Install"]["WantedBy"][0], ["default.target"])


class PrivacyGuardTests(unittest.TestCase):
    def initialize_git(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    def stage(self, root: Path, name: str, content: str = "fictional example\n") -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "--", name], cwd=root, check=True)

    def test_rejects_staged_generated_output_and_real_data_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.initialize_git(root)
            self.stage(root, "quarterdeck-output/index.html")
            self.assertTrue(any("generated output" in item for item in privacy_guard.check_staged(root)))

    def test_rejects_generated_page_marker_in_an_unusual_filename(self) -> None:
        marker = b'<meta name="quarterdeck-' + b'generated"'
        self.assertIn("Quarterdeck-generated HTML marker", privacy_guard.content_findings(marker))

    def test_rejects_host_specific_content_but_accepts_fictional_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.initialize_git(root)
            self.stage(root, "README.md", "Example path: " + "/" + "home/unknown-user/private-work\n")
            self.assertTrue(any("host-specific" in item for item in privacy_guard.check_staged(root)))

            subprocess.run(["git", "reset", "-q"], cwd=root, check=True)
            self.stage(root, "README.md", "Example project: Maple Harbor inventory\n")
            self.assertEqual(privacy_guard.check_staged(root), [])

    def test_rejects_ticket_shaped_work_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.initialize_git(root)
            work_key = "FABLE" + "-918"
            self.stage(root, "README.md", "Example task key: " + work_key + "\n")
            self.assertTrue(any("ticket-shaped" in item for item in privacy_guard.check_staged(root)))


if __name__ == "__main__":
    unittest.main()
