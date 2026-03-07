from datetime import datetime, timedelta, timezone

import pytest

from testmind.analysis.stability import StabilityAnalyzer
from testmind.domain.models import TestResult, TestStatus

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

P, F, S = TestStatus.PASSED, TestStatus.FAILED, TestStatus.SKIPPED


def _history(
    statuses: list[TestStatus],
    durations: list[float] | None = None,
) -> list[tuple[datetime, TestResult]]:
    if durations is None:
        durations = [0.1] * len(statuses)
    return [
        (
            _T0 + timedelta(hours=i),
            TestResult(name="t", status=s, duration=d),
        )
        for i, (s, d) in enumerate(zip(statuses, durations))
    ]


@pytest.fixture
def analyzer():
    return StabilityAnalyzer()


# ---------------------------------------------------------------------------
# Score bounds
# ---------------------------------------------------------------------------


class TestStabilityScore:
    def test_perfect_score_all_passing_consistent(self, analyzer):
        result = analyzer.analyze("t", _history([P] * 10, [0.1] * 10))
        assert result.score == pytest.approx(100.0)

    def test_worst_score_all_failing_flaky_duration(self, analyzer):
        # All failing → pass_rate=0, max flip_rate depends on consistency
        result = analyzer.analyze("t", _history([F] * 10))
        # pass_rate=0 → 0 points; all same outcome so flip_rate=0 → 20 points
        # durations all same → consistency=1 → 20 points
        # score = 40
        assert result.score == pytest.approx(40.0)

    def test_score_between_0_and_100(self, analyzer):
        result = analyzer.analyze("t", _history([P, F, P, F, P, F, P, F, P, F]))
        assert 0.0 <= result.score <= 100.0

    def test_alternating_lowers_score(self, analyzer):
        stable = analyzer.analyze("t", _history([P] * 10))
        flaky = analyzer.analyze("t", _history([P, F] * 5))
        assert stable.score > flaky.score

    def test_failing_tests_lower_than_passing(self, analyzer):
        passing = analyzer.analyze("t", _history([P] * 5))
        failing = analyzer.analyze("t", _history([F] * 5))
        assert passing.score > failing.score


# ---------------------------------------------------------------------------
# Duration consistency
# ---------------------------------------------------------------------------


class TestDurationConsistency:
    def test_identical_durations_perfect_consistency(self, analyzer):
        result = analyzer.analyze("t", _history([P] * 5, [0.5] * 5))
        assert result.duration_consistency == pytest.approx(1.0)

    def test_zero_durations_perfect_consistency(self, analyzer):
        result = analyzer.analyze("t", _history([P] * 5, [0.0] * 5))
        assert result.duration_consistency == pytest.approx(1.0)

    def test_high_variance_lowers_consistency(self, analyzer):
        uniform = analyzer.analyze("t", _history([P] * 5, [0.1] * 5))
        varied = analyzer.analyze("t", _history([P] * 5, [0.01, 10.0, 0.01, 10.0, 0.01]))
        assert uniform.duration_consistency > varied.duration_consistency

    def test_single_run_returns_insufficient(self, analyzer):
        result = analyzer.analyze("t", _history([P], [0.1]))
        assert result.insufficient_data is True


# ---------------------------------------------------------------------------
# Insufficient data
# ---------------------------------------------------------------------------


class TestStabilityInsufficientData:
    def test_fewer_than_min_runs(self, analyzer):
        result = analyzer.analyze("t", _history([P, P]))  # 2 < 3
        assert result.insufficient_data is True
        assert result.score == 0.0

    def test_empty_history(self, analyzer):
        result = analyzer.analyze("t", [])
        assert result.insufficient_data is True

    def test_custom_min_runs(self):
        a = StabilityAnalyzer(min_runs=2)
        result = a.analyze("t", _history([P, P]))
        assert result.insufficient_data is False
        assert result.score == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------


class TestStabilityFields:
    def test_pass_rate(self, analyzer):
        result = analyzer.analyze("t", _history([P, P, F, P, P]))
        assert result.pass_rate == pytest.approx(0.8)

    def test_run_count(self, analyzer):
        result = analyzer.analyze("t", _history([P] * 7))
        assert result.run_count == 7

    def test_flip_rate_perfect_alternation(self, analyzer):
        result = analyzer.analyze("t", _history([P, F, P, F, P]))
        assert result.flip_rate == pytest.approx(1.0)
