"""Tests for TextFormatter and JsonFormatter."""
import json
from datetime import datetime, timezone

import pytest

from testmind.analysis.models import FlakyResult, PredictionResult, RegressionResult, SpikeResult, StabilityResult, Trend
from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.reports.formatters import JsonFormatter, TextFormatter
from testmind.reports.summary import RunSummary

_TS = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _report(passed: int = 8, failed: int = 1, skipped: int = 1) -> TestReport:
    tests = (
        [TestResult(name=f"t_pass_{i}", status=TestStatus.PASSED, duration=0.1) for i in range(passed)]
        + [TestResult(name=f"t_fail_{i}", status=TestStatus.FAILED, duration=0.2, message="boom") for i in range(failed)]
        + [TestResult(name=f"t_skip_{i}", status=TestStatus.SKIPPED, duration=0.0) for i in range(skipped)]
    )
    return TestReport(
        name="nightly",
        project="my-proj",
        tests=tests,
        timestamp=_TS,
        passed=passed,
        failed=failed,
        skipped=skipped,
        errors=0,
        duration=1.5,
    )


def _full_summary() -> RunSummary:
    return RunSummary(
        project="my-proj",
        report=_report(),
        flaky=[
            FlakyResult(test_name="test_flaky_one", is_flaky=True, flip_rate=0.8,
                        pass_rate=0.5, fail_rate=0.5, run_count=10),
        ],
        regressions=[
            RegressionResult(test_name="test_reg_one", is_regression=True,
                             reference_pass_rate=1.0, recent_fail_rate=0.67),
        ],
        spike=SpikeResult(is_spike=True, current_fail_rate=0.5, baseline_mean=0.05,
                          baseline_std=0.02, z_score=22.5),
        stability=[
            StabilityResult(test_name="test_bad",  score=30.0, pass_rate=0.5,
                            duration_consistency=0.3, flip_rate=0.8, run_count=10),
            StabilityResult(test_name="test_good", score=95.0, pass_rate=1.0,
                            duration_consistency=0.9, flip_rate=0.0, run_count=10),
        ],
        predictions=[
            PredictionResult(test_name="test_risky", failure_probability=0.85,
                             trend=Trend.DEGRADING, confidence=0.7),
            PredictionResult(test_name="test_safe",  failure_probability=0.05,
                             trend=Trend.STABLE,    confidence=0.9),
        ],
    )


def _minimal_summary() -> RunSummary:
    """No issues — clean run."""
    return RunSummary(project="clean", report=_report(passed=10, failed=0, skipped=0))


# ---------------------------------------------------------------------------
# TextFormatter
# ---------------------------------------------------------------------------


class TestTextFormatter:
    @pytest.fixture
    def fmt(self):
        return TextFormatter()

    def test_returns_string(self, fmt):
        assert isinstance(fmt.format(_full_summary()), str)

    def test_contains_project_name(self, fmt):
        out = fmt.format(_full_summary())
        assert "my-proj" in out

    def test_contains_report_name(self, fmt):
        out = fmt.format(_full_summary())
        assert "nightly" in out

    def test_contains_timestamp(self, fmt):
        out = fmt.format(_full_summary())
        assert "2024-06-15" in out

    def test_overview_counts(self, fmt):
        out = fmt.format(_full_summary())
        assert "Passed: 8" in out
        assert "Failed: 1" in out
        assert "Skipped: 1" in out

    def test_spike_section_present_when_spike(self, fmt):
        out = fmt.format(_full_summary())
        assert "SPIKE" in out

    def test_spike_section_absent_when_no_spike(self, fmt):
        out = fmt.format(_minimal_summary())
        assert "SPIKE" not in out

    def test_flaky_section_present_when_flaky(self, fmt):
        out = fmt.format(_full_summary())
        assert "FLAKY" in out
        assert "test_flaky_one" in out

    def test_flaky_section_absent_when_clean(self, fmt):
        out = fmt.format(_minimal_summary())
        assert "FLAKY" not in out

    def test_regression_section_present(self, fmt):
        out = fmt.format(_full_summary())
        assert "REGRESSION" in out
        assert "test_reg_one" in out

    def test_regression_section_absent_when_clean(self, fmt):
        out = fmt.format(_minimal_summary())
        assert "REGRESSION" not in out

    def test_stability_section_present(self, fmt):
        out = fmt.format(_full_summary())
        assert "STABILITY" in out
        assert "test_bad" in out
        assert "test_good" in out

    def test_predictions_section_present(self, fmt):
        out = fmt.format(_full_summary())
        assert "PREDICTION" in out
        assert "test_risky" in out

    def test_footer_issue_counts(self, fmt):
        out = fmt.format(_full_summary())
        assert "1 flaky" in out
        assert "1 regression" in out
        assert "1 spike" in out

    def test_footer_zero_issues_clean_run(self, fmt):
        out = fmt.format(_minimal_summary())
        assert "0 flaky" in out
        assert "0 regression" in out
        assert "0 spike" in out


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


class TestJsonFormatter:
    @pytest.fixture
    def fmt(self):
        return JsonFormatter()

    def test_returns_valid_json(self, fmt):
        out = fmt.format(_full_summary())
        parsed = json.loads(out)
        assert isinstance(parsed, dict)

    def test_project_field(self, fmt):
        out = json.loads(fmt.format(_full_summary()))
        assert out["project"] == "my-proj"

    def test_report_subkeys(self, fmt):
        out = json.loads(fmt.format(_full_summary()))
        report = out["report"]
        for key in ("id", "name", "timestamp", "duration", "passed", "failed",
                    "skipped", "errors", "total", "pass_rate", "fail_rate"):
            assert key in report, f"missing key: {key}"

    def test_issues_counts(self, fmt):
        out = json.loads(fmt.format(_full_summary()))
        issues = out["issues"]
        assert issues["flaky_count"] == 1
        assert issues["regression_count"] == 1
        assert issues["spike_detected"] is True

    def test_flaky_list(self, fmt):
        out = json.loads(fmt.format(_full_summary()))
        assert len(out["flaky"]) == 1
        assert out["flaky"][0]["test_name"] == "test_flaky_one"

    def test_regressions_list(self, fmt):
        out = json.loads(fmt.format(_full_summary()))
        assert len(out["regressions"]) == 1
        assert out["regressions"][0]["test_name"] == "test_reg_one"

    def test_spike_not_null(self, fmt):
        out = json.loads(fmt.format(_full_summary()))
        assert out["spike"] is not None
        assert out["spike"]["is_spike"] is True

    def test_spike_null_when_absent(self, fmt):
        out = json.loads(fmt.format(_minimal_summary()))
        assert out["spike"] is None

    def test_stability_list_present(self, fmt):
        out = json.loads(fmt.format(_full_summary()))
        assert len(out["stability"]) == 2
        names = {s["test_name"] for s in out["stability"]}
        assert names == {"test_bad", "test_good"}

    def test_predictions_list_present(self, fmt):
        out = json.loads(fmt.format(_full_summary()))
        assert len(out["predictions"]) == 2
        names = {p["test_name"] for p in out["predictions"]}
        assert names == {"test_risky", "test_safe"}

    def test_indented_output(self, fmt):
        out = fmt.format(_full_summary(), indent=4)
        assert "    " in out

    def test_pass_rate_rounded(self, fmt):
        out = json.loads(fmt.format(_full_summary()))
        assert isinstance(out["report"]["pass_rate"], float)
