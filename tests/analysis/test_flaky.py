from datetime import datetime, timedelta, timezone

import pytest

from testmind.analysis.flaky import FlakyDetector
from testmind.domain.models import TestResult, TestStatus

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _history(statuses: list[TestStatus]) -> list[tuple[datetime, TestResult]]:
    return [
        (
            _T0 + timedelta(hours=i),
            TestResult(name="test_foo", status=s, duration=0.1),
        )
        for i, s in enumerate(statuses)
    ]


P, F = TestStatus.PASSED, TestStatus.FAILED
E = TestStatus.ERROR


@pytest.fixture
def detector():
    return FlakyDetector()


# ---------------------------------------------------------------------------
# Flaky detection
# ---------------------------------------------------------------------------


class TestFlakyDetection:
    def test_alternating_pass_fail_is_flaky(self, detector):
        result = detector.analyze("t", _history([P, F, P, F, P, F, P, F, P, F]))
        assert result.is_flaky is True

    def test_all_passing_not_flaky(self, detector):
        result = detector.analyze("t", _history([P] * 10))
        assert result.is_flaky is False

    def test_all_failing_not_flaky(self, detector):
        result = detector.analyze("t", _history([F] * 10))
        assert result.is_flaky is False

    def test_error_counts_as_failure(self, detector):
        # alternating pass / error should be flaky
        result = detector.analyze("t", _history([P, E, P, E, P, E, P, E, P, E]))
        assert result.is_flaky is True

    def test_mostly_passing_with_occasional_fail_below_flip_threshold(self, detector):
        # 1 fail out of 10 → fail_rate=0.1 which equals the lower bound → NOT flaky
        result = detector.analyze("t", _history([F] + [P] * 9))
        assert result.is_flaky is False

    def test_mostly_failing_above_high_threshold_not_flaky(self, detector):
        # 9 fail out of 10 → fail_rate=0.9 which equals the upper bound → NOT flaky
        result = detector.analyze("t", _history([P] + [F] * 9))
        assert result.is_flaky is False


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestFlakyMetrics:
    def test_flip_rate_perfect_alternation(self, detector):
        result = detector.analyze("t", _history([P, F, P, F, P, F]))
        assert result.flip_rate == pytest.approx(1.0)

    def test_flip_rate_no_flips(self, detector):
        result = detector.analyze("t", _history([P] * 6))
        assert result.flip_rate == pytest.approx(0.0)

    def test_pass_rate(self, detector):
        result = detector.analyze("t", _history([P, P, F, P, P, F, P, P, P, F]))
        assert result.pass_rate == pytest.approx(0.7)

    def test_fail_rate(self, detector):
        result = detector.analyze("t", _history([P, P, F, P, P, F, P, P, P, F]))
        assert result.fail_rate == pytest.approx(0.3)

    def test_run_count(self, detector):
        result = detector.analyze("t", _history([P, F, P, F, P]))
        assert result.run_count == 5


# ---------------------------------------------------------------------------
# Insufficient data
# ---------------------------------------------------------------------------


class TestFlakyInsufficientData:
    def test_fewer_than_min_runs_returns_insufficient(self, detector):
        result = detector.analyze("t", _history([P, F, P, F]))  # 4 < 5
        assert result.insufficient_data is True
        assert result.is_flaky is False

    def test_empty_history_returns_insufficient(self, detector):
        result = detector.analyze("t", [])
        assert result.insufficient_data is True

    def test_custom_min_runs(self):
        detector = FlakyDetector(min_runs=3)
        result = detector.analyze("t", _history([P, F, P]))
        assert result.insufficient_data is False


# ---------------------------------------------------------------------------
# History order independence
# ---------------------------------------------------------------------------


class TestFlakyOrderIndependence:
    def test_reversed_history_same_result(self, detector):
        hist = _history([P, F, P, F, P, F, P, F, P, F])
        forward = detector.analyze("t", hist)
        backward = detector.analyze("t", list(reversed(hist)))
        assert forward.is_flaky == backward.is_flaky
        assert forward.flip_rate == pytest.approx(backward.flip_rate)
