from dataclasses import dataclass
from enum import StrEnum


class Trend(StrEnum):
    IMPROVING = "improving"
    DEGRADING = "degrading"
    STABLE = "stable"


@dataclass(frozen=True)
class FlakyResult:
    test_name: str
    is_flaky: bool
    flip_rate: float      # fraction of consecutive pairs with different outcomes
    pass_rate: float
    fail_rate: float
    run_count: int
    # True when there are too few runs to decide
    insufficient_data: bool = False


@dataclass(frozen=True)
class RegressionResult:
    test_name: str
    is_regression: bool
    # pass rate in the reference (older) window
    reference_pass_rate: float
    # fail rate in the recent window
    recent_fail_rate: float
    insufficient_data: bool = False


@dataclass(frozen=True)
class SpikeResult:
    """Suite-level: was there a sudden failure-rate spike in the latest run?"""
    is_spike: bool
    current_fail_rate: float
    baseline_mean: float
    baseline_std: float
    z_score: float
    insufficient_data: bool = False


@dataclass(frozen=True)
class StabilityResult:
    test_name: str
    score: float            # 0–100
    pass_rate: float
    duration_consistency: float   # 0–1  (1 = perfectly consistent)
    flip_rate: float
    run_count: int
    insufficient_data: bool = False


@dataclass(frozen=True)
class PredictionResult:
    test_name: str
    failure_probability: float   # 0–1
    trend: Trend
    confidence: float            # 0–1  (grows with run count)
    insufficient_data: bool = False
