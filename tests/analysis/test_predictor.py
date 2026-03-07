from datetime import datetime, timedelta, timezone

import pytest

from testmind.analysis.models import Trend
from testmind.analysis.predictor import FailurePredictor
from testmind.domain.models import TestResult, TestStatus

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)

P, F = TestStatus.PASSED, TestStatus.FAILED


def _history(statuses: list[TestStatus]) -> list[tuple[datetime, TestResult]]:
    return [
        (_T0 + timedelta(hours=i), TestResult(name="t", status=s, duration=0.1))
        for i, s in enumerate(statuses)
    ]


@pytest.fixture
def predictor():
    return FailurePredictor()


# ---------------------------------------------------------------------------
# Trend direction
# ---------------------------------------------------------------------------


class TestPredictorTrend:
    def test_increasing_failures_is_degrading(self, predictor):
        # Fail rate clearly rising: 0,0,0,0,0,1,1,1,1,1
        result = predictor.analyze("t", _history([P] * 5 + [F] * 5))
        assert result.trend == Trend.DEGRADING

    def test_decreasing_failures_is_improving(self, predictor):
        # Fail rate clearly falling: 1,1,1,1,1,0,0,0,0,0
        result = predictor.analyze("t", _history([F] * 5 + [P] * 5))
        assert result.trend == Trend.IMPROVING

    def test_constant_passing_is_stable(self, predictor):
        result = predictor.analyze("t", _history([P] * 10))
        assert result.trend == Trend.STABLE

    def test_constant_failing_is_stable(self, predictor):
        result = predictor.analyze("t", _history([F] * 10))
        assert result.trend == Trend.STABLE

    def test_alternating_is_stable(self, predictor):
        result = predictor.analyze("t", _history([P, F] * 5))
        assert result.trend == Trend.STABLE


# ---------------------------------------------------------------------------
# Failure probability
# ---------------------------------------------------------------------------


class TestPredictorProbability:
    def test_all_passing_low_probability(self, predictor):
        result = predictor.analyze("t", _history([P] * 10))
        assert result.failure_probability < 0.2

    def test_all_failing_high_probability(self, predictor):
        result = predictor.analyze("t", _history([F] * 10))
        assert result.failure_probability > 0.8

    def test_probability_clamped_to_0_1(self, predictor):
        result = predictor.analyze("t", _history([P] * 5 + [F] * 10))
        assert 0.0 <= result.failure_probability <= 1.0

    def test_degrading_probability_above_half(self, predictor):
        result = predictor.analyze("t", _history([P] * 5 + [F] * 5))
        # trend is up and last 3 are fails → probability should be elevated
        assert result.failure_probability >= 0.5


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------


class TestPredictorConfidence:
    def test_confidence_scales_with_runs(self, predictor):
        few = predictor.analyze("t", _history([P] * 3))
        many = predictor.analyze("t", _history([P] * 15))
        assert many.confidence > few.confidence

    def test_confidence_capped_at_1(self, predictor):
        result = predictor.analyze("t", _history([P] * 100))
        assert result.confidence == pytest.approx(1.0)

    def test_confidence_above_zero_with_enough_runs(self, predictor):
        result = predictor.analyze("t", _history([P] * 5))
        assert result.confidence > 0.0


# ---------------------------------------------------------------------------
# Insufficient data
# ---------------------------------------------------------------------------


class TestPredictorInsufficientData:
    def test_fewer_than_min_runs(self, predictor):
        result = predictor.analyze("t", _history([P, P]))  # 2 < 3
        assert result.insufficient_data is True
        assert result.failure_probability == 0.0
        assert result.trend == Trend.STABLE
        assert result.confidence == 0.0

    def test_empty_history(self, predictor):
        result = predictor.analyze("t", [])
        assert result.insufficient_data is True

    def test_custom_min_runs(self):
        p = FailurePredictor(min_runs=2)
        result = p.analyze("t", _history([P, F]))
        assert result.insufficient_data is False


# ---------------------------------------------------------------------------
# Order independence
# ---------------------------------------------------------------------------


class TestPredictorOrderIndependence:
    def test_reversed_history_same_trend(self, predictor):
        hist = _history([P] * 5 + [F] * 5)
        forward = predictor.analyze("t", hist)
        backward = predictor.analyze("t", list(reversed(hist)))
        assert forward.trend == backward.trend
