from datetime import datetime, timedelta, timezone

import pytest

from testmind.analysis.regression import RegressionDetector, SpikeDetector
from testmind.domain.models import TestReport, TestResult, TestStatus

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

P, F = TestStatus.PASSED, TestStatus.FAILED


def _history(statuses: list[TestStatus]) -> list[tuple[datetime, TestResult]]:
    return [
        (_T0 + timedelta(hours=i), TestResult(name="t", status=s, duration=0.1))
        for i, s in enumerate(statuses)
    ]


def _report(fail_rate: float, n: int = 10, ts_offset_hours: int = 0) -> TestReport:
    failed = round(fail_rate * n)
    passed = n - failed
    return TestReport(
        name="run",
        project="proj",
        tests=[],
        timestamp=_T0 + timedelta(hours=ts_offset_hours),
        passed=passed,
        failed=failed,
        skipped=0,
        errors=0,
        duration=1.0,
    )


# ---------------------------------------------------------------------------
# RegressionDetector
# ---------------------------------------------------------------------------


class TestRegressionDetector:
    @pytest.fixture
    def detector(self):
        return RegressionDetector()

    def test_regression_detected(self, detector):
        # 7 passing, then 3 failing
        result = detector.analyze("t", _history([P] * 7 + [F, F, F]))
        assert result.is_regression is True

    def test_consistently_passing_not_regression(self, detector):
        result = detector.analyze("t", _history([P] * 10))
        assert result.is_regression is False

    def test_consistently_failing_not_regression(self, detector):
        result = detector.analyze("t", _history([F] * 10))
        assert result.is_regression is False

    def test_was_unstable_not_regression(self, detector):
        # reference window not stable (50% fail) → not a regression
        result = detector.analyze("t", _history([P, F] * 5))
        assert result.is_regression is False

    def test_regression_reference_and_recent_rates(self, detector):
        result = detector.analyze("t", _history([P] * 7 + [F, F, F]))
        assert result.reference_pass_rate == pytest.approx(1.0)
        assert result.recent_fail_rate == pytest.approx(1.0)

    def test_partial_recent_failure(self, detector):
        # 7 passing, then 2 failing, 1 passing (fail_rate=0.67 in recent)
        result = detector.analyze("t", _history([P] * 7 + [F, F, P]))
        assert result.is_regression is True

    def test_one_recent_failure_below_threshold(self, detector):
        # recent: 1/3 = 0.33 < 0.60 → not a regression
        result = detector.analyze("t", _history([P] * 7 + [F, P, P]))
        assert result.is_regression is False

    def test_insufficient_data(self, detector):
        result = detector.analyze("t", _history([P] * 4))  # 4 < 6
        assert result.insufficient_data is True
        assert result.is_regression is False

    def test_order_independence(self, detector):
        hist = _history([P] * 7 + [F, F, F])
        forward = detector.analyze("t", hist)
        backward = detector.analyze("t", list(reversed(hist)))
        assert forward.is_regression == backward.is_regression


# ---------------------------------------------------------------------------
# SpikeDetector
# ---------------------------------------------------------------------------


class TestSpikeDetector:
    @pytest.fixture
    def detector(self):
        return SpikeDetector()

    def _reports_with_current(
        self, baseline_fail_rates: list[float], current_fail_rate: float
    ) -> list[TestReport]:
        reports = [
            _report(r, ts_offset_hours=i)
            for i, r in enumerate(baseline_fail_rates)
        ]
        reports.append(
            _report(current_fail_rate, ts_offset_hours=len(baseline_fail_rates))
        )
        return reports

    def test_spike_detected(self, detector):
        baseline = [0.05, 0.05, 0.05, 0.05, 0.05]
        result = detector.analyze(self._reports_with_current(baseline, 0.80))
        assert result.is_spike is True
        assert result.z_score > 2.0

    def test_no_spike_when_similar_to_baseline(self, detector):
        baseline = [0.05, 0.10, 0.05, 0.08, 0.06]
        result = detector.analyze(self._reports_with_current(baseline, 0.09))
        assert result.is_spike is False

    def test_no_spike_when_current_below_mean(self, detector):
        baseline = [0.50, 0.50, 0.50, 0.50, 0.50]
        result = detector.analyze(self._reports_with_current(baseline, 0.10))
        assert result.is_spike is False

    def test_insufficient_baseline(self, detector):
        # Only 2 baseline reports (need at least min_baseline=3)
        result = detector.analyze(self._reports_with_current([0.5, 0.5], 0.9))
        assert result.insufficient_data is True
        assert result.is_spike is False

    def test_spike_current_fail_rate_correct(self, detector):
        baseline = [0.0, 0.0, 0.0, 0.0, 0.0]
        result = detector.analyze(self._reports_with_current(baseline, 0.50))
        assert result.current_fail_rate == pytest.approx(0.50)

    def test_spike_z_score_infinite_when_zero_std(self, detector):
        # baseline all identical → std=0; current > mean → z=inf → spike
        baseline = [0.0] * 5
        result = detector.analyze(self._reports_with_current(baseline, 0.50))
        assert result.is_spike is True

    def test_reports_sorted_by_timestamp_internally(self, detector):
        # Pass reports in reverse order — detector must still pick the newest as current
        reports = [
            _report(0.5, ts_offset_hours=5),   # newest
            _report(0.0, ts_offset_hours=0),
            _report(0.0, ts_offset_hours=1),
            _report(0.0, ts_offset_hours=2),
            _report(0.0, ts_offset_hours=3),
        ]
        result = detector.analyze(reports)
        assert result.current_fail_rate == pytest.approx(0.5)
        assert result.is_spike is True
