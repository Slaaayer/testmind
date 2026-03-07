"""Integration tests for the Typer CLI."""
import json
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testmind.cli.app import app
from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.storage.sqlite_store import SQLiteStore

runner = CliRunner()

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_junit_xml(
    suite_name: str = "MySuite",
    timestamp: str = "2024-01-01T12:00:00",
    tests: list[tuple[str, str]] | None = None,  # (name, outcome)
) -> str:
    """outcome: 'pass' | 'fail' | 'skip' | 'error'"""
    if tests is None:
        tests = [("test_a", "pass"), ("test_b", "pass")]

    cases = []
    for name, outcome in tests:
        if outcome == "fail":
            cases.append(
                f'<testcase name="{name}" classname="C" time="0.1">'
                f'<failure message="boom">stack</failure></testcase>'
            )
        elif outcome == "skip":
            cases.append(
                f'<testcase name="{name}" classname="C" time="0.0"><skipped/></testcase>'
            )
        elif outcome == "error":
            cases.append(
                f'<testcase name="{name}" classname="C" time="0.0">'
                f'<error message="oops">trace</error></testcase>'
            )
        else:
            cases.append(f'<testcase name="{name}" classname="C" time="0.1"/>')

    body = "\n    ".join(cases)
    return textwrap.dedent(f"""\
        <testsuite name="{suite_name}" timestamp="{timestamp}">
            {body}
        </testsuite>
    """)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def xml_file(tmp_path: Path) -> Path:
    p = tmp_path / "report.xml"
    p.write_text(_make_junit_xml())
    return p


def _ingest(
    xml_paths: Path | list[Path],
    db: Path,
    project: str = "proj",
    extra: list[str] | None = None,
) -> object:
    if isinstance(xml_paths, Path):
        xml_paths = [xml_paths]
    args = ["ingest", *[str(p) for p in xml_paths], "--project", project, "--db", str(db)]
    if extra:
        args += extra
    return runner.invoke(app, args)


def _seed_history(db_path: Path, project: str, n: int) -> None:
    """Write n reports with varied timestamps directly into the store."""
    store = SQLiteStore(db_path)
    try:
        for i in range(n):
            tests = [
                TestResult(name="test_stable", status=TestStatus.PASSED, duration=0.1),
                TestResult(name="test_flaky",  status=TestStatus.FAILED if i % 2 == 0 else TestStatus.PASSED, duration=0.2),
            ]
            passed = sum(1 for t in tests if t.status == TestStatus.PASSED)
            failed = sum(1 for t in tests if t.status == TestStatus.FAILED)
            report = TestReport(
                name=f"run-{i}",
                project=project,
                tests=tests,
                timestamp=_T0 + timedelta(hours=i),
                passed=passed,
                failed=failed,
                skipped=0,
                errors=0,
                duration=0.3,
            )
            store.save_report(report)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# ingest command
# ---------------------------------------------------------------------------


class TestIngestCommand:
    def test_ingest_succeeds(self, xml_file, db_path):
        result = _ingest(xml_file, db_path)
        assert result.exit_code == 0

    def test_ingest_prints_report_name(self, xml_file, db_path):
        result = _ingest(xml_file, db_path)
        assert "MySuite" in result.output

    def test_ingest_stores_report(self, xml_file, db_path):
        _ingest(xml_file, db_path)
        store = SQLiteStore(db_path)
        try:
            assert store.get_reports("proj") != []
        finally:
            store.close()

    def test_ingest_missing_file_exits_1(self, tmp_path, db_path):
        # All files missing → exit 1
        result = runner.invoke(
            app, ["ingest", str(tmp_path / "nope.xml"), "--project", "p", "--db", str(db_path)]
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_ingest_invalid_xml_exits_1(self, tmp_path, db_path):
        # All files invalid → exit 1
        bad = tmp_path / "bad.xml"
        bad.write_text("<not valid xml<<>>")
        result = runner.invoke(
            app, ["ingest", str(bad), "--project", "p", "--db", str(db_path)]
        )
        assert result.exit_code == 1
        assert "error" in result.output.lower()

    def test_ingest_duplicate_skips_save(self, xml_file, db_path):
        _ingest(xml_file, db_path)
        result = _ingest(xml_file, db_path)
        assert result.exit_code == 0
        assert "already stored" in result.output.lower()
        # DB still has exactly 1 report
        store = SQLiteStore(db_path)
        try:
            assert len(store.get_reports("proj")) == 1
        finally:
            store.close()

    def test_ingest_json_format(self, xml_file, db_path):
        result = _ingest(xml_file, db_path, extra=["--format", "json"])
        assert result.exit_code == 0
        # JSON block starts at the first '{' in the output
        json_part = result.output[result.output.index("{"):]
        parsed = json.loads(json_part)
        assert "project" in parsed

    def test_ingest_text_format_default(self, xml_file, db_path):
        result = _ingest(xml_file, db_path)
        assert "OVERVIEW" in result.output
        assert "Pass rate" in result.output

    def test_ingest_with_failures(self, tmp_path, db_path):
        xml = _make_junit_xml(
            tests=[("test_ok", "pass"), ("test_bad", "fail"), ("test_skip", "skip")]
        )
        p = tmp_path / "mixed.xml"
        p.write_text(xml)
        result = _ingest(p, db_path)
        assert result.exit_code == 0
        assert "1✗" in result.output or "Failed: 1" in result.output

    def test_ingest_creates_db_dir(self, xml_file, tmp_path):
        nested_db = tmp_path / "a" / "b" / "c" / "test.db"
        result = _ingest(xml_file, nested_db)
        assert result.exit_code == 0
        assert nested_db.exists()


# ---------------------------------------------------------------------------
# ingest — multi-file
# ---------------------------------------------------------------------------


class TestIngestMultiFile:
    def _make_files(self, tmp_path: Path, n: int) -> list[Path]:
        files = []
        for i in range(n):
            p = tmp_path / f"run_{i}.xml"
            p.write_text(
                _make_junit_xml(
                    suite_name=f"Suite-{i}",
                    timestamp=f"2024-01-{i + 1:02d}T10:00:00",
                    tests=[("test_a", "pass"), ("test_b", "fail" if i % 2 == 0 else "pass")],
                )
            )
            files.append(p)
        return files

    def test_multiple_files_all_stored(self, tmp_path, db_path):
        files = self._make_files(tmp_path, 5)
        result = _ingest(files, db_path)
        assert result.exit_code == 0
        store = SQLiteStore(db_path)
        try:
            assert len(store.get_reports("proj")) == 5
        finally:
            store.close()

    def test_multiple_files_shows_per_file_progress(self, tmp_path, db_path):
        files = self._make_files(tmp_path, 3)
        result = _ingest(files, db_path)
        assert result.exit_code == 0
        assert "[1/3]" in result.output
        assert "[2/3]" in result.output
        assert "[3/3]" in result.output

    def test_multiple_files_summary_line(self, tmp_path, db_path):
        files = self._make_files(tmp_path, 4)
        result = _ingest(files, db_path)
        assert "4 stored" in result.output

    def test_partial_failure_continues_and_exits_0(self, tmp_path, db_path):
        good = tmp_path / "good.xml"
        good.write_text(_make_junit_xml(timestamp="2024-01-01T10:00:00"))
        bad = tmp_path / "bad.xml"
        bad.write_text("<broken<<")
        result = _ingest([good, bad], db_path)
        # One good file succeeds → exit 0
        assert result.exit_code == 0
        assert "error" in result.output.lower()
        assert "1 stored" in result.output

    def test_all_files_fail_exits_1(self, tmp_path, db_path):
        bad1 = tmp_path / "bad1.xml"
        bad1.write_text("<broken<<")
        bad2 = tmp_path / "bad2.xml"
        bad2.write_text("<also broken<<")
        result = _ingest([bad1, bad2], db_path)
        assert result.exit_code == 1

    def test_duplicate_files_counted_as_skipped(self, tmp_path, db_path):
        files = self._make_files(tmp_path, 2)
        _ingest(files, db_path)  # first pass
        result = _ingest(files, db_path)  # second pass — all duplicates
        assert "2 duplicate(s) skipped" in result.output

    def test_bulk_ingest_enables_pattern_detection(self, tmp_path, db_path):
        # 10 files with an alternating test → enough history for flaky detection
        files = []
        for i in range(10):
            p = tmp_path / f"run_{i}.xml"
            outcome = "fail" if i % 2 == 0 else "pass"
            p.write_text(
                _make_junit_xml(
                    suite_name=f"Run-{i}",
                    timestamp=f"2024-01-{i + 1:02d}T10:00:00",
                    tests=[("test_stable", "pass"), ("test_flaky", outcome)],
                )
            )
            files.append(p)
        result = _ingest(files, db_path)
        assert result.exit_code == 0
        assert "FLAKY" in result.output

    def test_missing_file_in_batch_reported(self, tmp_path, db_path):
        good = tmp_path / "good.xml"
        good.write_text(_make_junit_xml(timestamp="2024-01-01T10:00:00"))
        missing = tmp_path / "missing.xml"
        result = _ingest([good, missing], db_path)
        assert result.exit_code == 0
        assert "not found" in result.output.lower()

    def test_analysis_shown_after_bulk_ingest(self, tmp_path, db_path):
        files = self._make_files(tmp_path, 3)
        result = _ingest(files, db_path)
        assert "OVERVIEW" in result.output

    def test_json_format_multi_file(self, tmp_path, db_path):
        files = self._make_files(tmp_path, 3)
        result = _ingest(files, db_path, extra=["--format", "json"])
        assert result.exit_code == 0
        json_part = result.output[result.output.index("{"):]
        parsed = json.loads(json_part)
        assert parsed["project"] == "proj"


# ---------------------------------------------------------------------------
# analyze command
# ---------------------------------------------------------------------------


class TestAnalyzeCommand:
    def test_analyze_after_ingest(self, xml_file, db_path):
        _ingest(xml_file, db_path)
        result = runner.invoke(app, ["analyze", "proj", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "OVERVIEW" in result.output

    def test_analyze_unknown_project_exits_1(self, db_path):
        result = runner.invoke(app, ["analyze", "no-such-project", "--db", str(db_path)])
        assert result.exit_code == 1
        assert "no reports found" in result.output.lower()

    def test_analyze_json_format(self, xml_file, db_path):
        _ingest(xml_file, db_path)
        result = runner.invoke(
            app, ["analyze", "proj", "--db", str(db_path), "--format", "json"]
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["project"] == "proj"

    def test_analyze_shows_stability(self, xml_file, db_path):
        _seed_history(db_path, "proj", n=5)
        result = runner.invoke(app, ["analyze", "proj", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "STABILITY" in result.output

    def test_analyze_with_history_detects_patterns(self, tmp_path, db_path):
        _seed_history(db_path, "proj", n=10)
        result = runner.invoke(app, ["analyze", "proj", "--db", str(db_path)])
        assert result.exit_code == 0
        # With 10 runs of alternating flaky test, flaky section should appear
        assert "FLAKY" in result.output


# ---------------------------------------------------------------------------
# projects command
# ---------------------------------------------------------------------------


class TestProjectsCommand:
    def test_no_projects_message(self, db_path):
        result = runner.invoke(app, ["projects", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "no projects" in result.output.lower()

    def test_lists_ingested_project(self, xml_file, db_path):
        _ingest(xml_file, db_path, project="alpha")
        result = runner.invoke(app, ["projects", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "alpha" in result.output

    def test_lists_multiple_projects(self, tmp_path, db_path):
        for proj in ("alpha", "beta", "gamma"):
            p = tmp_path / f"{proj}.xml"
            p.write_text(_make_junit_xml(timestamp=f"2024-01-0{1 + ['alpha','beta','gamma'].index(proj)}T00:00:00"))
            _ingest(p, db_path, project=proj)
        result = runner.invoke(app, ["projects", "--db", str(db_path)])
        for proj in ("alpha", "beta", "gamma"):
            assert proj in result.output

    def test_shows_report_count(self, xml_file, db_path):
        _ingest(xml_file, db_path, project="proj")
        result = runner.invoke(app, ["projects", "--db", str(db_path)])
        assert "1" in result.output  # at least one report count visible


# ---------------------------------------------------------------------------
# history command
# ---------------------------------------------------------------------------


class TestHistoryCommand:
    def test_history_after_ingest(self, xml_file, db_path):
        _ingest(xml_file, db_path)
        result = runner.invoke(app, ["history", "proj", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "MySuite" in result.output

    def test_history_unknown_project_exits_1(self, db_path):
        result = runner.invoke(app, ["history", "no-such-project", "--db", str(db_path)])
        assert result.exit_code == 1

    def test_history_limit(self, db_path):
        _seed_history(db_path, "proj", n=8)
        result = runner.invoke(app, ["history", "proj", "--db", str(db_path), "--limit", "3"])
        assert result.exit_code == 0
        assert "3 run(s)" in result.output

    def test_history_shows_pass_fail_columns(self, xml_file, db_path):
        _ingest(xml_file, db_path)
        result = runner.invoke(app, ["history", "proj", "--db", str(db_path)])
        assert "Pass" in result.output
        assert "Fail" in result.output

    def test_history_newest_first(self, db_path):
        _seed_history(db_path, "proj", n=3)
        result = runner.invoke(app, ["history", "proj", "--db", str(db_path)])
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if "run-" in l]
        # run-2 should appear before run-0 (newest first)
        idx_2 = next(i for i, l in enumerate(lines) if "run-2" in l)
        idx_0 = next(i for i, l in enumerate(lines) if "run-0" in l)
        assert idx_2 < idx_0


# ---------------------------------------------------------------------------
# delete / restore commands
# ---------------------------------------------------------------------------


class TestDeleteRestoreCommands:
    def test_delete_succeeds(self, xml_file, db_path):
        _ingest(xml_file, db_path, project="proj")
        result = runner.invoke(app, ["delete", "proj", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "soft-deleted" in result.output.lower() or "deleted" in result.output.lower()

    def test_delete_hides_from_projects(self, xml_file, db_path):
        _ingest(xml_file, db_path, project="my-special-proj")
        runner.invoke(app, ["delete", "my-special-proj", "--db", str(db_path)])
        result = runner.invoke(app, ["projects", "--db", str(db_path)])
        assert "my-special-proj" not in result.output

    def test_projects_all_shows_deleted(self, xml_file, db_path):
        _ingest(xml_file, db_path, project="proj")
        runner.invoke(app, ["delete", "proj", "--db", str(db_path)])
        result = runner.invoke(app, ["projects", "--db", str(db_path), "--all"])
        assert "proj" in result.output
        assert "deleted" in result.output.lower()

    def test_delete_nonexistent_project_exits_1(self, db_path):
        result = runner.invoke(app, ["delete", "no-such", "--db", str(db_path)])
        assert result.exit_code == 1

    def test_delete_already_deleted_exits_1(self, xml_file, db_path):
        _ingest(xml_file, db_path, project="proj")
        runner.invoke(app, ["delete", "proj", "--db", str(db_path)])
        result = runner.invoke(app, ["delete", "proj", "--db", str(db_path)])
        assert result.exit_code == 1

    def test_restore_succeeds(self, xml_file, db_path):
        _ingest(xml_file, db_path, project="proj")
        runner.invoke(app, ["delete", "proj", "--db", str(db_path)])
        result = runner.invoke(app, ["restore", "proj", "--db", str(db_path)])
        assert result.exit_code == 0
        assert "restored" in result.output.lower()

    def test_restore_makes_project_visible(self, xml_file, db_path):
        _ingest(xml_file, db_path, project="proj")
        runner.invoke(app, ["delete", "proj", "--db", str(db_path)])
        runner.invoke(app, ["restore", "proj", "--db", str(db_path)])
        result = runner.invoke(app, ["projects", "--db", str(db_path)])
        assert "proj" in result.output

    def test_restore_nonexistent_exits_1(self, db_path):
        result = runner.invoke(app, ["restore", "no-such", "--db", str(db_path)])
        assert result.exit_code == 1

    def test_restore_active_project_exits_1(self, xml_file, db_path):
        _ingest(xml_file, db_path, project="proj")
        result = runner.invoke(app, ["restore", "proj", "--db", str(db_path)])
        assert result.exit_code == 1
