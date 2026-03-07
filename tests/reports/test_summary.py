"""Tests for the Summarizer — integration across store + all analysers."""
from datetime import datetime, timedelta, timezone

import pytest

from testmind.analysis.flaky import FlakyDetector
from testmind.analysis.predictor import FailurePredictor
from testmind.analysis.regression import RegressionDetector, SpikeDetector
from testmind.analysis.stability import StabilityAnalyzer
from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.reports.summary import RunSummary, Summarizer
from testmind.storage.sqlite_store import SQLiteStore

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

P, F = TestStatus.PASSED, TestStatus.FAILED


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _result(name: str, status: TestStatus, duration: float = 0.1) -> TestResult:
    return TestResult(name=name, status=status, duration=duration)


def _report(
    name: str,
    tests: list[TestResult],
    ts_offset_hours: int = 0,
    project: str = "proj",
) -> TestReport:
    passed = sum(1 for t in tests if t.status == P)
    failed = sum(1 for t in tests if t.status == F)
    return TestReport(
        name=name,
        project=project,
        tests=tests,
        timestamp=_T0 + timedelta(hours=ts_offset_hours),
        passed=passed,
        failed=failed,
        skipped=0,
        errors=0,
        duration=sum(t.duration for t in tests),
    )


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


def _make_summarizer(**kwargs) -> Summarizer:
    """Summarizer with low min_runs so tests don't need dozens of reports."""
    return Summarizer(
        flaky_detector=FlakyDetector(min_runs=3),
        regression_detector=RegressionDetector(min_runs=4, recent_window=2),
        spike_detector=SpikeDetector(min_baseline=2),
        stability_analyzer=StabilityAnalyzer(min_runs=2),
        predictor=FailurePredictor(min_runs=2),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


class TestSummarizerStructure:
    def test_raises_when_no_reports(self, store):
        summarizer = _make_summarizer()
        with pytest.raises(ValueError, match="No reports found"):
            summarizer.summarize("proj", store)

    def test_returns_run_summary(self, store):
        report = _report("r1", [_result("t1", P), _result("t2", P)])
        store.save_report(report)
        result = _make_summarizer().summarize("proj", store)
        assert isinstance(result, RunSummary)
        assert result.project == "proj"

    def test_current_report_is_most_recent(self, store):
        r1 = _report("older", [_result("t", P)], ts_offset_hours=0)
        r2 = _report("newer", [_result("t", P)], ts_offset_hours=5)
        store.save_report(r1)
        store.save_report(r2)
        result = _make_summarizer().summarize("proj", store)
        assert result.report.name == "newer"

    def test_stability_sorted_worst_first(self, store):
        # t_bad: alternating → low score; t_good: always passing → high score
        for i in range(5):
            status_bad = F if i % 2 == 0 else P
            store.save_report(
                _report(
                    f"r{i}",
                    [_result("t_bad", status_bad), _result("t_good", P)],
                    ts_offset_hours=i,
                )
            )
        result = _make_summarizer().summarize("proj", store)
        scores = [s.score for s in result.stability if not s.insufficient_data]
        assert scores == sorted(scores)

    def test_predictions_sorted_highest_risk_first(self, store):
        # t_bad: always failing; t_good: always passing
        for i in range(5):
            store.save_report(
                _report(
                    f"r{i}",
                    [_result("t_bad", F), _result("t_good", P)],
                    ts_offset_hours=i,
                )
            )
        result = _make_summarizer().summarize("proj", store)
        probs = [p.failure_probability for p in result.predictions if not p.insufficient_data]
        assert probs == sorted(probs, reverse=True)


# ---------------------------------------------------------------------------
# Flaky detection
# ---------------------------------------------------------------------------


class TestSummarizerFlaky:
    def test_flaky_test_appears_in_summary(self, store):
        # 6 alternating runs
        for i in range(6):
            store.save_report(
                _report(f"r{i}", [_result("t_flaky", F if i % 2 == 0 else P)], ts_offset_hours=i)
            )
        result = _make_summarizer().summarize("proj", store)
        flaky_names = {f.test_name for f in result.flaky}
        assert "t_flaky" in flaky_names

    def test_stable_test_not_in_flaky(self, store):
        for i in range(6):
            store.save_report(
                _report(f"r{i}", [_result("t_stable", P)], ts_offset_hours=i)
            )
        result = _make_summarizer().summarize("proj", store)
        assert result.flaky == []


# ---------------------------------------------------------------------------
# Regression detection
# ---------------------------------------------------------------------------


class TestSummarizerRegression:
    def test_regression_detected_in_summary(self, store):
        # 4 passing runs, then 2 failing (using min_runs=4, recent_window=2)
        for i in range(4):
            store.save_report(
                _report(f"r{i}", [_result("t_reg", P)], ts_offset_hours=i)
            )
        for i in range(4, 6):
            store.save_report(
                _report(f"r{i}", [_result("t_reg", F)], ts_offset_hours=i)
            )
        result = _make_summarizer().summarize("proj", store)
        reg_names = {r.test_name for r in result.regressions}
        assert "t_reg" in reg_names

    def test_stable_test_not_regression(self, store):
        for i in range(6):
            store.save_report(
                _report(f"r{i}", [_result("t_ok", P)], ts_offset_hours=i)
            )
        result = _make_summarizer().summarize("proj", store)
        assert result.regressions == []


# ---------------------------------------------------------------------------
# Spike detection
# ---------------------------------------------------------------------------


class TestSummarizerSpike:
    def test_spike_detected(self, store):
        # 3 low-fail-rate reports, then one high-fail-rate
        for i in range(3):
            store.save_report(
                _report(
                    f"r{i}",
                    [_result("t1", P), _result("t2", P), _result("t3", P), _result("t4", P),
                     _result("t5", P), _result("t6", P), _result("t7", P), _result("t8", P),
                     _result("t9", P), _result("t10", P)],
                    ts_offset_hours=i,
                )
            )
        store.save_report(
            _report(
                "spike_run",
                [_result(f"t{j}", F) for j in range(1, 11)],
                ts_offset_hours=3,
            )
        )
        result = _make_summarizer().summarize("proj", store)
        assert result.spike is not None
        assert result.spike.is_spike is True

    def test_no_spike_normal_run(self, store):
        for i in range(4):
            store.save_report(
                _report(f"r{i}", [_result("t", P)], ts_offset_hours=i)
            )
        result = _make_summarizer().summarize("proj", store)
        assert result.spike is None


# ---------------------------------------------------------------------------
# Multi-test report
# ---------------------------------------------------------------------------


class TestSummarizerMultiTest:
    def test_all_tests_have_stability_entry(self, store):
        tests = [_result(f"t{i}", P) for i in range(5)]
        for j in range(3):
            store.save_report(_report(f"r{j}", tests, ts_offset_hours=j))
        result = _make_summarizer().summarize("proj", store)
        names_in_stability = {s.test_name for s in result.stability}
        assert names_in_stability == {f"t{i}" for i in range(5)}

    def test_all_tests_have_prediction_entry(self, store):
        tests = [_result(f"t{i}", P) for i in range(4)]
        for j in range(3):
            store.save_report(_report(f"r{j}", tests, ts_offset_hours=j))
        result = _make_summarizer().summarize("proj", store)
        names_in_preds = {p.test_name for p in result.predictions}
        assert names_in_preds == {f"t{i}" for i in range(4)}
