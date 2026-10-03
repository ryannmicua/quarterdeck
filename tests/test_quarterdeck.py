from __future__ import annotations

import json
import html
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import quarterdeck
import privacy_guard


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
            result = subprocess.run(
                [sys.executable, str(ROOT / "quarterdeck.py"), "render", "--home", str(home), "--output", str(output)],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            page = output.read_text(encoding="utf-8")
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
            self.assertNotIn("Finished Finch report", page)
            self.assertNotIn("Unlinked old report", page)
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
            self.assertIn('name="quarterdeck-' + "generated" + '"', page)
            self.assertNotIn("https://fonts.", page)
            report_pages = list(output.parent.glob("report-*.html"))
            self.assertEqual(len(report_pages), 3)
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
            self.assertNotRegex(page, r'<article class="card" id="review-report-[0-9a-f]{12}">')
            self.assertEqual(page, quarterdeck.build_html(
                [quarterdeck.load_home_snapshot(quarterdeck.HomeSpec(home.name, home), discover_secondmates=True)],
                "Quarterdeck", lavish=True,
            ))

    def test_all_mode_restores_queued_finished_and_unlinked_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = self.make_home(root)
            output = root / "outside-output" / "index.html"
            result = self.run_cli(["render", "--all", "--home", str(home), "--output", str(output)], root, root / "config")
            self.assertEqual(result.returncode, 0, result.stderr)
            page = output.read_text(encoding="utf-8")
            self.assertIn("Gather the Finch field notes", page)
            self.assertIn("File the old Finch notes", page)
            self.assertIn("Unlinked old report", page)
            self.assertIn("Other backlog items", page)
            self.assertIn("All scout reports", page)
            self.assertRegex(page, r'<b>5</b><span>Other backlog items</span>')
            self.assertRegex(page, r'<b>6</b><span>Scout reports</span>')
            self.assertEqual(page.count('<p class="eyebrow">Other backlog item</p>'), 5)
            self.assertEqual(page.count('<p class="eyebrow">Scout report</p>'), 6)

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
            self.assertEqual(len(payloads), 5)
            self.assertEqual(payloads[0], {
                "item_id": "amber-18",
                "title": "Choose the Amber Kite import format",
                "kind": "report",
                "home_label": "fictional-firstmate",
                "source_path": "data/amber-18/report.md",
            })
            self.assertIn("backlog item", {payload["kind"] for payload in payloads})
            self.assertIn("fictional-firstmate", {payload["home_label"] for payload in payloads})
            self.assertIn("kestrel — Maintains the Willow Quay field guide.", {payload["home_label"] for payload in payloads})

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
            general = self.run_cli(["help"], cli_home, config_home)
            self.assertEqual(general.returncode, 0, general.stderr)
            self.assertIn("Documentation tree:", general.stdout)
            self.assertIn("quarterdeck help --json", general.stdout)

            machine = self.run_cli(["help", "--json"], cli_home, config_home)
            self.assertEqual(machine.returncode, 0, machine.stderr)
            index = json.loads(machine.stdout)
            commands = {entry["name"]: entry for entry in index["commands"]}
            self.assertTrue({"install", "add", "list", "remove", "render", "help"}.issubset(commands))
            self.assertIn("how-to/install.md", commands["install"]["docs"])
            self.assertIn("--lavish", commands["render"]["usage"])
            self.assertIn("--all", commands["render"]["usage"])
            self.assertIn("explanation/attention-model.md", commands["render"]["docs"])
            self.assertIn("how-to/request-lavish-page.md", commands["render"]["docs"])
            self.assertIn("Lavish", commands["render"]["summary"])

            for command in ("install", "add", "list", "remove", "render"):
                with self.subTest(command=command):
                    result = self.run_cli([command, "--help"], cli_home, config_home)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if command == "render":
                        self.assertIn("--lavish", result.stdout)
                        self.assertIn("--all", result.stdout)

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
            (source / "README.md").write_text("fixture version three\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
            subprocess.run(["git", "commit", "-m", "fixture v3"], cwd=source, check=True, capture_output=True, text=True)
            subprocess.run(["git", "push"], cwd=source, check=True, capture_output=True, text=True)
            refused = subprocess.run(["sh", str(ROOT / "install.sh")], env=env, text=True, capture_output=True)
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
