from datetime import datetime, timedelta, timezone

import pytest

from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_result(
    name: str = "test_foo",
    status: TestStatus = TestStatus.PASSED,
    duration: float = 0.1,
    classname: str | None = "pkg.MyTest",
    suite: str | None = "MySuite",
    message: str | None = None,
    stack_trace: str | None = None,
) -> TestResult:
    return TestResult(
        name=name,
        classname=classname,
        suite=suite,
        status=status,
        duration=duration,
        message=message,
        stack_trace=stack_trace,
    )


def make_report(
    project: str = "my-project",
    name: str = "run-1",
    tests: list[TestResult] | None = None,
    timestamp: datetime | None = None,
    passed: int = 1,
    failed: int = 0,
    skipped: int = 0,
    errors: int = 0,
    duration: float = 0.1,
) -> TestReport:
    if tests is None:
        tests = [make_result()]
    if timestamp is None:
        timestamp = _BASE_TS
    return TestReport(
        name=name,
        project=project,
        tests=tests,
        timestamp=timestamp,
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=errors,
        duration=duration,
    )


@pytest.fixture
def store() -> SQLiteStore:
    s = SQLiteStore(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# save_report / report_exists
# ---------------------------------------------------------------------------


class TestSaveAndExists:
    def test_save_then_exists(self, store):
        report = make_report()
        store.save_report(report)
        assert store.report_exists(report.id)

    def test_nonexistent_report(self, store):
        assert not store.report_exists("deadbeef")

    def test_duplicate_save_is_idempotent(self, store):
        report = make_report()
        store.save_report(report)
        store.save_report(report)  # should not raise or duplicate
        assert len(store.get_reports("my-project")) == 1

    def test_save_report_with_no_tests(self, store):
        report = make_report(tests=[], passed=0, duration=0.0)
        store.save_report(report)
        retrieved = store.get_reports("my-project")
        assert len(retrieved) == 1
        assert retrieved[0].tests == []


# ---------------------------------------------------------------------------
# get_reports
# ---------------------------------------------------------------------------


class TestGetReports:
    def test_retrieves_saved_report(self, store):
        report = make_report()
        store.save_report(report)
        results = store.get_reports("my-project")
        assert len(results) == 1
        r = results[0]
        assert r.name == report.name
        assert r.project == report.project
        assert r.passed == report.passed
        assert r.failed == report.failed
        assert r.skipped == report.skipped
        assert r.errors == report.errors
        assert abs(r.duration - report.duration) < 1e-9

    def test_reports_ordered_newest_first(self, store):
        r1 = make_report(name="run-1", timestamp=_BASE_TS)
        r2 = make_report(name="run-2", timestamp=_BASE_TS + timedelta(hours=1))
        r3 = make_report(name="run-3", timestamp=_BASE_TS + timedelta(hours=2))
        store.save_report(r1)
        store.save_report(r2)
        store.save_report(r3)
        reports = store.get_reports("my-project")
        assert [r.name for r in reports] == ["run-3", "run-2", "run-1"]

    def test_limit_respected(self, store):
        for i in range(10):
            store.save_report(
                make_report(name=f"run-{i}", timestamp=_BASE_TS + timedelta(hours=i))
            )
        reports = store.get_reports("my-project", limit=3)
        assert len(reports) == 3

    def test_unknown_project_returns_empty(self, store):
        assert store.get_reports("no-such-project") == []

    def test_test_results_are_restored(self, store):
        results = [
            make_result("test_a", TestStatus.PASSED, 0.1, "pkg.A", "Suite1"),
            make_result("test_b", TestStatus.FAILED, 0.2, "pkg.B", "Suite1",
                        message="AssertionError", stack_trace="traceback here"),
        ]
        report = make_report(tests=results, passed=1, failed=1)
        store.save_report(report)

        restored = store.get_reports("my-project")[0]
        assert len(restored.tests) == 2

        by_name = {t.name: t for t in restored.tests}
        assert by_name["test_a"].status == TestStatus.PASSED
        assert by_name["test_a"].classname == "pkg.A"
        assert by_name["test_a"].suite == "Suite1"
        assert by_name["test_b"].status == TestStatus.FAILED
        assert by_name["test_b"].message == "AssertionError"
        assert by_name["test_b"].stack_trace == "traceback here"

    def test_all_statuses_round_trip(self, store):
        tests = [
            make_result("t_pass",  TestStatus.PASSED),
            make_result("t_fail",  TestStatus.FAILED),
            make_result("t_skip",  TestStatus.SKIPPED),
            make_result("t_error", TestStatus.ERROR),
        ]
        store.save_report(make_report(tests=tests, passed=1, failed=1, skipped=1, errors=1))
        statuses = {t.name: t.status for t in store.get_reports("my-project")[0].tests}
        assert statuses["t_pass"]  == TestStatus.PASSED
        assert statuses["t_fail"]  == TestStatus.FAILED
        assert statuses["t_skip"]  == TestStatus.SKIPPED
        assert statuses["t_error"] == TestStatus.ERROR


# ---------------------------------------------------------------------------
# Multi-project isolation
# ---------------------------------------------------------------------------


class TestProjectIsolation:
    def test_projects_are_isolated(self, store):
        store.save_report(make_report(project="alpha", name="r1"))
        store.save_report(make_report(project="beta",  name="r2"))

        alpha = store.get_reports("alpha")
        beta  = store.get_reports("beta")

        assert len(alpha) == 1 and alpha[0].name == "r1"
        assert len(beta)  == 1 and beta[0].name  == "r2"

    def test_list_projects(self, store):
        store.save_report(make_report(project="alpha"))
        store.save_report(make_report(project="beta"))
        assert set(store.list_projects()) == {"alpha", "beta"}

    def test_list_projects_empty(self, store):
        assert store.list_projects() == []

    def test_list_projects_no_duplicates(self, store):
        store.save_report(make_report(project="alpha", name="r1", timestamp=_BASE_TS))
        store.save_report(make_report(project="alpha", name="r2", timestamp=_BASE_TS + timedelta(hours=1)))
        assert store.list_projects().count("alpha") == 1


# ---------------------------------------------------------------------------
# get_test_history
# ---------------------------------------------------------------------------


class TestGetTestHistory:
    def test_returns_history_for_test(self, store):
        r1 = make_report(name="r1", timestamp=_BASE_TS,
                         tests=[make_result("test_foo", TestStatus.PASSED)])
        r2 = make_report(name="r2", timestamp=_BASE_TS + timedelta(hours=1),
                         tests=[make_result("test_foo", TestStatus.FAILED,
                                             message="boom")])
        store.save_report(r1)
        store.save_report(r2)

        history = store.get_test_history("my-project", "test_foo")
        assert len(history) == 2
        # newest first
        assert history[0][1].status == TestStatus.FAILED
        assert history[1][1].status == TestStatus.PASSED

    def test_history_timestamps_are_correct(self, store):
        ts1 = _BASE_TS
        ts2 = _BASE_TS + timedelta(hours=3)
        store.save_report(make_report(name="r1", timestamp=ts1,
                                      tests=[make_result("test_foo")]))
        store.save_report(make_report(name="r2", timestamp=ts2,
                                      tests=[make_result("test_foo")]))

        history = store.get_test_history("my-project", "test_foo")
        retrieved_ts = {h[0] for h in history}
        assert ts1 in retrieved_ts
        assert ts2 in retrieved_ts

    def test_history_unknown_test_returns_empty(self, store):
        store.save_report(make_report())
        assert store.get_test_history("my-project", "no_such_test") == []

    def test_history_limit_respected(self, store):
        for i in range(10):
            store.save_report(
                make_report(
                    name=f"r{i}",
                    timestamp=_BASE_TS + timedelta(hours=i),
                    tests=[make_result("test_foo")],
                )
            )
        history = store.get_test_history("my-project", "test_foo", limit=4)
        assert len(history) == 4

    def test_history_isolated_by_project(self, store):
        store.save_report(make_report(project="alpha",
                                      tests=[make_result("shared_test")]))
        store.save_report(make_report(project="beta",
                                      tests=[make_result("shared_test")]))

        assert len(store.get_test_history("alpha", "shared_test")) == 1
        assert len(store.get_test_history("beta",  "shared_test")) == 1


# ---------------------------------------------------------------------------
# Timestamp round-trip
# ---------------------------------------------------------------------------


class TestTimestampRoundTrip:
    def test_tz_aware_timestamp_preserved(self, store):
        ts = datetime(2024, 6, 15, 14, 30, 45, tzinfo=timezone.utc)
        store.save_report(make_report(timestamp=ts))
        restored = store.get_reports("my-project")[0]
        assert restored.timestamp == ts

    def test_report_id_stable_after_round_trip(self, store):
        report = make_report()
        store.save_report(report)
        restored = store.get_reports("my-project")[0]
        assert restored.id == report.id


# ---------------------------------------------------------------------------
# Soft-delete
# ---------------------------------------------------------------------------


class TestSoftDelete:
    def test_delete_hides_project_from_list(self, store):
        store.save_report(make_report(project="proj-a"))
        store.delete_project("proj-a")
        assert "proj-a" not in store.list_projects()

    def test_delete_hides_project_from_get_reports(self, store):
        store.save_report(make_report(project="proj-a"))
        store.delete_project("proj-a")
        assert store.get_reports("proj-a") == []

    def test_delete_hides_project_from_test_history(self, store):
        store.save_report(make_report(project="proj-a", tests=[make_result("t1")]))
        store.delete_project("proj-a")
        assert store.get_test_history("proj-a", "t1") == []

    def test_include_deleted_shows_deleted_project(self, store):
        store.save_report(make_report(project="proj-a"))
        store.delete_project("proj-a")
        assert "proj-a" in store.list_projects(include_deleted=True)

    def test_restore_makes_project_visible_again(self, store):
        store.save_report(make_report(project="proj-a"))
        store.delete_project("proj-a")
        store.restore_project("proj-a")
        assert "proj-a" in store.list_projects()

    def test_restore_makes_reports_accessible_again(self, store):
        store.save_report(make_report(project="proj-a"))
        store.delete_project("proj-a")
        store.restore_project("proj-a")
        assert store.get_reports("proj-a") != []

    def test_delete_idempotent(self, store):
        store.save_report(make_report(project="proj-a"))
        store.delete_project("proj-a")
        store.delete_project("proj-a")  # second call should not raise
        assert "proj-a" not in store.list_projects()

    def test_other_projects_unaffected_by_delete(self, store):
        store.save_report(make_report(project="alpha"))
        store.save_report(make_report(project="beta", name="r2",
                                      timestamp=_BASE_TS + timedelta(hours=1)))
        store.delete_project("alpha")
        assert "beta" in store.list_projects()
        assert store.get_reports("beta") != []
