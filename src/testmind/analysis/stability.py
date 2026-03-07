"""
Stability Index  (0 – 100).

Formula
-------
score = pass_rate_score + consistency_score + non_flakiness_score

Where:
  pass_rate_score    = pass_rate  × 60
  consistency_score  = duration_consistency × 20
  non_flakiness_score = (1 - flip_rate) × 20

duration_consistency = 1 - min(CV, 1)
  CV (coefficient of variation) = std(durations) / mean(durations)
  → 0 when wildly variable, 1 when perfectly consistent.
  If all durations are zero or there is only one run, consistency = 1.

flip_rate = fraction of consecutive pairs with different outcomes.
"""

import math
from datetime import datetime

from testmind.domain.models import TestResult, TestStatus
from testmind.analysis.models import StabilityResult

_MIN_RUNS = 3


def _is_failure(status: TestStatus) -> bool:
    return status in (TestStatus.FAILED, TestStatus.ERROR)


def _flip_rate(outcomes: list[bool]) -> float:
    if len(outcomes) < 2:
        return 0.0
    flips = sum(a != b for a, b in zip(outcomes, outcomes[1:]))
    return flips / (len(outcomes) - 1)


def _duration_consistency(durations: list[float]) -> float:
    if len(durations) < 2:
        return 1.0
    mean = sum(durations) / len(durations)
    if mean == 0.0:
        return 1.0
    variance = sum((d - mean) ** 2 for d in durations) / len(durations)
    std = math.sqrt(variance)
    cv = std / mean
    return 1.0 - min(cv, 1.0)


class StabilityAnalyzer:
    def __init__(self, min_runs: int = _MIN_RUNS) -> None:
        self.min_runs = min_runs

    def analyze(
        self,
        test_name: str,
        history: list[tuple[datetime, TestResult]],
    ) -> StabilityResult:
        """
        Parameters
        ----------
        history : list of (timestamp, TestResult), any order.
        """
        if len(history) < self.min_runs:
            return StabilityResult(
                test_name=test_name,
                score=0.0,
                pass_rate=0.0,
                duration_consistency=0.0,
                flip_rate=0.0,
                run_count=len(history),
                insufficient_data=True,
            )

        ordered = sorted(history, key=lambda x: x[0])
        outcomes = [_is_failure(r.status) for _, r in ordered]
        durations = [r.duration for _, r in ordered]

        total = len(outcomes)
        failures = sum(outcomes)
        fail_rate = failures / total
        pass_rate = 1.0 - fail_rate

        consistency = _duration_consistency(durations)
        fr = _flip_rate(outcomes)

        score = pass_rate * 60.0 + consistency * 20.0 + (1.0 - fr) * 20.0

        return StabilityResult(
            test_name=test_name,
            score=round(score, 2),
            pass_rate=pass_rate,
            duration_consistency=consistency,
            flip_rate=fr,
            run_count=total,
        )
