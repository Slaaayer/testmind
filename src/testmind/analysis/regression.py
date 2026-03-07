"""
Regression and spike detection.

Regression (test level)
-----------------------
A test is a REGRESSION when it was stable (mostly passing) in an older
reference window but has started failing in a recent window.

Rules
-----
- Requires at least `min_runs` total observations (default 6).
- Reference window  : all runs except the last `recent_window` (default 3).
- Recent window     : the last `recent_window` runs.
- Is regression when:
    reference_pass_rate >= STABLE_THRESHOLD   (was stable)
    AND recent_fail_rate >= RECENT_FAIL_THRESHOLD  (now failing)

Spike (suite level)
-------------------
A SPIKE occurs when the most recent report's failure rate is significantly
higher than the rolling baseline of previous reports.

Rules
-----
- Requires at least `min_baseline` previous reports (default 3).
- Baseline = fail_rate of the N reports preceding the latest.
- z_score = (current_fail_rate - baseline_mean) / baseline_std
- Spike when z_score >= SPIKE_Z_THRESHOLD (default 2.0)
  AND current_fail_rate > baseline_mean  (one-tailed).
"""

import math
from datetime import datetime

from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.analysis.models import RegressionResult, SpikeResult

_STABLE_THRESHOLD = 0.90
_RECENT_FAIL_THRESHOLD = 0.60
_RECENT_WINDOW = 3
_MIN_RUNS = 6
_MIN_BASELINE = 3
_SPIKE_Z = 2.0


def _is_failure(status: TestStatus) -> bool:
    return status in (TestStatus.FAILED, TestStatus.ERROR)


class RegressionDetector:
    def __init__(
        self,
        recent_window: int = _RECENT_WINDOW,
        min_runs: int = _MIN_RUNS,
        stable_threshold: float = _STABLE_THRESHOLD,
        recent_fail_threshold: float = _RECENT_FAIL_THRESHOLD,
    ) -> None:
        self.recent_window = recent_window
        self.min_runs = min_runs
        self.stable_threshold = stable_threshold
        self.recent_fail_threshold = recent_fail_threshold

    def analyze(
        self,
        test_name: str,
        history: list[tuple[datetime, TestResult]],
    ) -> RegressionResult:
        """
        Parameters
        ----------
        history : list of (timestamp, TestResult), any order — will be sorted.
        """
        if len(history) < self.min_runs:
            return RegressionResult(
                test_name=test_name,
                is_regression=False,
                reference_pass_rate=0.0,
                recent_fail_rate=0.0,
                insufficient_data=True,
            )

        ordered = sorted(history, key=lambda x: x[0])
        recent = ordered[-self.recent_window :]
        reference = ordered[: -self.recent_window]

        ref_failures = sum(_is_failure(r.status) for _, r in reference)
        ref_pass_rate = 1.0 - ref_failures / len(reference)

        rec_failures = sum(_is_failure(r.status) for _, r in recent)
        rec_fail_rate = rec_failures / len(recent)

        is_regression = (
            ref_pass_rate >= self.stable_threshold
            and rec_fail_rate >= self.recent_fail_threshold
        )

        return RegressionResult(
            test_name=test_name,
            is_regression=is_regression,
            reference_pass_rate=ref_pass_rate,
            recent_fail_rate=rec_fail_rate,
        )


class SpikeDetector:
    def __init__(
        self,
        min_baseline: int = _MIN_BASELINE,
        z_threshold: float = _SPIKE_Z,
    ) -> None:
        self.min_baseline = min_baseline
        self.z_threshold = z_threshold

    def analyze(self, reports: list[TestReport]) -> SpikeResult:
        """
        Parameters
        ----------
        reports : ordered oldest → newest; the last entry is the current run.
        """
        if len(reports) < self.min_baseline + 1:
            return SpikeResult(
                is_spike=False,
                current_fail_rate=0.0,
                baseline_mean=0.0,
                baseline_std=0.0,
                z_score=0.0,
                insufficient_data=True,
            )

        ordered = sorted(reports, key=lambda r: r.timestamp)
        current = ordered[-1]
        baseline = ordered[:-1]

        current_fail_rate = current.fail_rate
        baseline_rates = [r.fail_rate for r in baseline]
        mean = sum(baseline_rates) / len(baseline_rates)
        variance = sum((x - mean) ** 2 for x in baseline_rates) / len(baseline_rates)
        std = math.sqrt(variance)

        if std == 0.0:
            z_score = 0.0 if current_fail_rate == mean else float("inf")
        else:
            z_score = (current_fail_rate - mean) / std

        is_spike = z_score >= self.z_threshold and current_fail_rate > mean

        return SpikeResult(
            is_spike=is_spike,
            current_fail_rate=current_fail_rate,
            baseline_mean=mean,
            baseline_std=std,
            z_score=z_score,
        )
