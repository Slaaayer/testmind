"""
Failure probability predictor.

Approach
--------
1. Sort history oldest → newest.
2. Compute a per-run binary outcome (1 = fail, 0 = pass).
3. Fit a linear trend to those outcomes using OLS (no external deps).
4. Predict the next value = last_outcome + slope.
5. Clamp prediction to [0, 1] and interpret trend.

Confidence scales with the number of runs up to MAX_CONF_RUNS.

Trend thresholds
----------------
  slope > +SLOPE_THRESHOLD  → DEGRADING
  slope < -SLOPE_THRESHOLD  → IMPROVING
  otherwise                 → STABLE
"""

from datetime import datetime

from testmind.domain.models import TestResult, TestStatus
from testmind.analysis.models import PredictionResult, Trend

_MIN_RUNS = 3
_MAX_CONF_RUNS = 20
_SLOPE_THRESHOLD = 0.05


def _is_failure(status: TestStatus) -> bool:
    return status in (TestStatus.FAILED, TestStatus.ERROR)


def _ols_slope(ys: list[float]) -> float:
    """Slope of OLS regression of ys against integer indices 0, 1, …, n-1."""
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = (n - 1) / 2.0      # exact for 0..n-1
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den != 0.0 else 0.0


class FailurePredictor:
    def __init__(
        self,
        min_runs: int = _MIN_RUNS,
        slope_threshold: float = _SLOPE_THRESHOLD,
        max_conf_runs: int = _MAX_CONF_RUNS,
    ) -> None:
        self.min_runs = min_runs
        self.slope_threshold = slope_threshold
        self.max_conf_runs = max_conf_runs

    def analyze(
        self,
        test_name: str,
        history: list[tuple[datetime, TestResult]],
    ) -> PredictionResult:
        """
        Parameters
        ----------
        history : list of (timestamp, TestResult), any order.
        """
        if len(history) < self.min_runs:
            return PredictionResult(
                test_name=test_name,
                failure_probability=0.0,
                trend=Trend.STABLE,
                confidence=0.0,
                insufficient_data=True,
            )

        ordered = sorted(history, key=lambda x: x[0])
        outcomes = [1.0 if _is_failure(r.status) else 0.0 for _, r in ordered]

        slope = _ols_slope(outcomes)

        last_rate = sum(outcomes[-3:]) / min(3, len(outcomes))
        raw_prediction = last_rate + slope
        failure_prob = max(0.0, min(1.0, raw_prediction))

        if slope > self.slope_threshold:
            trend = Trend.DEGRADING
        elif slope < -self.slope_threshold:
            trend = Trend.IMPROVING
        else:
            trend = Trend.STABLE

        confidence = min(len(outcomes) / self.max_conf_runs, 1.0)

        return PredictionResult(
            test_name=test_name,
            failure_probability=round(failure_prob, 4),
            trend=trend,
            confidence=round(confidence, 4),
        )
