from __future__ import annotations

import json
import os
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
        (data / "amber-18").mkdir(parents=True)
        (data / "backlog.md").write_text(
            """# Work queue

## In flight
| ID | Task | State |
| --- | --- | --- |
| maple-21 | Chart the Maple Harbor catalog | working |

## Held for review
| ID | Task | hold_kind | hold_reason | hold_bucket | report |
| --- | --- | --- | --- | --- | --- |
| amber-18 | Choose the Amber Kite import format | decision | Pick one format | live | |

## Review ready
| ID | Task | State | PR |
| --- | --- | --- | --- |
| paper-27 | Review the Paper Finch patch | review ready | """ + "https://github.com/example/quarterdeck-demo/" + "pull/42" + """ |
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
        secondmate = root / "fictional-secondmate"
        secondmate_data = secondmate / "data"
        secondmate_data.mkdir(parents=True)
        (secondmate_data / "backlog.md").write_text(
            """# Secondmate queue

## In flight
- Chart the Willow Quay field guide [working]
""",
            encoding="utf-8",
        )
        (data / "secondmates.md").write_text(
            f"""# Registered secondmates
- kestrel - Maintains the Willow Quay field guide. (home: {secondmate}; scope: Field guide work; projects: quarterdeck-demo; added 2026-01-04)
- lantern - Maintains remote map notes. (host: sample-remote; root: /srv/sample-firstmate; home: /srv/sample-homes/lantern; scope: Map notes; projects: map-demo; added 2026-01-04)
""",
            encoding="utf-8",
        )
        return home

    def test_render_groups_holds_reports_active_work_and_reviews(self) -> None:
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
            self.assertIn('name="quarterdeck-generated"', page)
            self.assertNotIn("https://fonts.", page)

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
            with patch.dict(os.environ, {"FM_HOME": str(home)}, clear=True):
                result = quarterdeck.main(["render", "--output", str(output)])
            self.assertEqual(result, 0)
            self.assertIn("Chart the Maple Harbor catalog", output.read_text(encoding="utf-8"))

    def test_render_requires_home_or_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = quarterdeck.main(["render"])
        self.assertEqual(result, 2)

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
