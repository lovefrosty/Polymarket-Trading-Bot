import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import repo_audit


class TestRepoAuditWorkflow(unittest.TestCase):
    def test_trigger_contract_phrases_match(self) -> None:
        for phrase in repo_audit.TRIGGER_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertTrue(repo_audit.should_run_full_audit(phrase))

    def test_section_order_locked(self) -> None:
        expected = (
            "A) Executive Summary",
            "B) Repo Map + Key Modules",
            "C) Goal Alignment Matrix",
            "D) Invariant Checks",
            "E) Risk Register",
            "F) Efficiency Findings",
            "G) Test Coverage + Replay Parity",
            "H) Recommended Next Steps",
            "I) Do Not Do List",
        )
        self.assertEqual(repo_audit.SECTION_TITLES, expected)

    def test_recommendation_contract(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        recommendations = repo_audit.default_recommendations(repo_root)
        ok, errors = repo_audit.validate_recommendation_contract(recommendations)
        self.assertTrue(ok)
        self.assertEqual(errors, ())

    def test_rendered_report_contains_sections_in_order(self) -> None:
        report = repo_audit.AuditReport(
            generated_at="2026-02-17T00:00:00Z",
            trigger_text="run audit",
            trigger_matched=True,
            repo_state_summary=("Branch/worktree: ## main",),
            repo_map_rows=(("Market discovery", ("/tmp/core/market_discovery.py",)),),
            goal_alignment=(
                repo_audit.GoalMatrixRow(goal="g", evidence="e", gaps="x"),
            ),
            invariant_checks=(("Determinism", ("detail",)),),
            risks=(
                repo_audit.RiskItem(
                    severity="High",
                    likelihood="High",
                    title="risk",
                    impact="impact",
                    evidence="evidence",
                ),
            ),
            efficiency_findings=("finding",),
            test_coverage=("test",),
            recommendations=(
                repo_audit.Recommendation(
                    priority="HIGH",
                    title="rec",
                    file_targets=("/tmp/file.py",),
                    expected_behavior_change="change",
                    acceptance_criteria=("criterion",),
                    regression_risk="low",
                ),
            ),
            do_not_do=("do not",),
            insufficient_evidence=(),
        )

        rendered = repo_audit.render_report_markdown(report)
        positions = []
        for section in repo_audit.SECTION_TITLES:
            marker = f"## {section}"
            idx = rendered.find(marker)
            self.assertNotEqual(idx, -1, marker)
            positions.append(idx)
        self.assertEqual(positions, sorted(positions))

    def test_rg_missing_falls_back_and_finds_evidence(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            core_dir = repo_root / "core"
            core_dir.mkdir(parents=True, exist_ok=True)
            target_file = core_dir / "runtime.py"
            target_file.write_text("import uuid\nx = uuid.uuid4()\n", encoding="utf-8")

            with patch("scripts.repo_audit.shutil.which", return_value=None):
                matches, diagnostic = repo_audit._rg_search(
                    repo_root=repo_root,
                    pattern=r"uuid\.uuid4",
                    targets=["core"],
                    max_matches=10,
                )

        self.assertTrue(matches)
        self.assertIsNotNone(diagnostic)
        self.assertEqual(
            diagnostic,
            "tool_unavailable:rg;fallback:python_scan;result:matches_found",
        )
        self.assertIn(target_file.as_posix(), matches[0].path)
        self.assertEqual(matches[0].line, 2)

    def test_rg_missing_falls_back_and_reports_no_matches_explicitly(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            core_dir = repo_root / "core"
            core_dir.mkdir(parents=True, exist_ok=True)
            (core_dir / "runtime.py").write_text("value = 42\n", encoding="utf-8")

            with patch("scripts.repo_audit.shutil.which", return_value=None):
                matches, diagnostic = repo_audit._rg_search(
                    repo_root=repo_root,
                    pattern=r"uuid\.uuid4",
                    targets=["core"],
                    max_matches=10,
                )

        self.assertEqual(matches, [])
        self.assertIsNotNone(diagnostic)
        self.assertEqual(
            diagnostic,
            "tool_unavailable:rg;fallback:python_scan;result:no_matches",
        )

    def test_scan_invariants_reports_specific_fallback_diagnostics(self) -> None:
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            for folder in ("core", "data", "scripts", "dashboard", "src", "tests"):
                (repo_root / folder).mkdir(parents=True, exist_ok=True)

            (repo_root / "core/market_discovery.py").write_text(
                "state = 'CANDIDATE_NOT_LIVE'\n",
                encoding="utf-8",
            )
            (repo_root / "scripts/run_system.py").write_text(
                "mode = 'OBSERVE'\n",
                encoding="utf-8",
            )

            with patch("scripts.repo_audit.shutil.which", return_value=None):
                _, insufficient = repo_audit._scan_invariants(repo_root)

        self.assertTrue(insufficient)
        self.assertTrue(
            any(
                note.startswith(
                    "tradability_rollover:tool_unavailable:rg;fallback:python_scan;result:matches_found"
                )
                for note in insufficient
            )
        )
        self.assertTrue(
            any(
                note.startswith(
                    "replay_parity:tool_unavailable:rg;fallback:python_scan;result:no_matches"
                )
                for note in insufficient
            )
        )


if __name__ == "__main__":
    unittest.main()
