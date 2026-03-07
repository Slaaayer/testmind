"""
Flaky test detection.

A test is FLAKY when it produces mixed pass/fail results over recent runs
without a clear directional trend — i.e. the failure rate is in the
"uncertain zone" (between FLAKY_LOW and FLAKY_HIGH thresholds) AND
consecutive results flip at a meaningful rate.

Rules
-----
- Requires at least `min_runs` observations (default 5).
- fail_rate  ∈ (FLAKY_LOW, FLAKY_HIGH)  → candidate
- flip_rate  > FLIP_THRESHOLD            → confirmed flaky
  where flip_rate = fraction of consecutive pairs whose outcome differs.
"""

from datetime import datetime

from testmind.domain.models import TestResult, TestStatus
from testmind.analysis.models import FlakyResult

# Thresholds (all tunable via constructor kwargs)
_FLAKY_LOW = 0.10    # below this → consistently passing
_FLAKY_HIGH = 0.90   # above this → consistently failing
_FLIP_THRESHOLD = 0.15
_MIN_RUNS = 5


def _is_failure(status: TestStatus) -> bool:
    return status in (TestStatus.FAILED, TestStatus.ERROR)


def _flip_rate(outcomes: list[bool]) -> float:
    if len(outcomes) < 2:
        return 0.0
    flips = sum(a != b for a, b in zip(outcomes, outcomes[1:]))
    return flips / (len(outcomes) - 1)


class FlakyDetector:
    def __init__(
        self,
        min_runs: int = _MIN_RUNS,
        flaky_low: float = _FLAKY_LOW,
        flaky_high: float = _FLAKY_HIGH,
        flip_threshold: float = _FLIP_THRESHOLD,
    ) -> None:
        self.min_runs = min_runs
        self.flaky_low = flaky_low
        self.flaky_high = flaky_high
        self.flip_threshold = flip_threshold

    def analyze(
        self,
        test_name: str,
        history: list[tuple[datetime, TestResult]],
    ) -> FlakyResult:
        """
        Parameters
        ----------
        history : list of (timestamp, TestResult), oldest → newest.
        """
        if len(history) < self.min_runs:
            return FlakyResult(
                test_name=test_name,
                is_flaky=False,
                flip_rate=0.0,
                pass_rate=0.0,
                fail_rate=0.0,
                run_count=len(history),
                insufficient_data=True,
            )

        # Oldest → newest order (history may arrive newest-first from store)
        ordered = sorted(history, key=lambda x: x[0])
        outcomes = [_is_failure(r.status) for _, r in ordered]

        total = len(outcomes)
        failures = sum(outcomes)
        fail_rate = failures / total
        pass_rate = 1.0 - fail_rate
        fr = _flip_rate(outcomes)

        is_flaky = (
            self.flaky_low < fail_rate < self.flaky_high
            and fr > self.flip_threshold
        )

        return FlakyResult(
            test_name=test_name,
            is_flaky=is_flaky,
            flip_rate=fr,
            pass_rate=pass_rate,
            fail_rate=fail_rate,
            run_count=total,
        )
