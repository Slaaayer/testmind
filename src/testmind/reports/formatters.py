"""
Output formatters for RunSummary.

TextFormatter  — human-readable plain text.
JsonFormatter  — machine-readable JSON.
"""

import json
from dataclasses import asdict
from datetime import datetime

from testmind.analysis.models import SpikeResult, StabilityResult, PredictionResult
from testmind.reports.summary import RunSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEP = "─" * 60


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------


class TextFormatter:
    """Produces a human-readable multi-section text report."""

    def format(self, summary: RunSummary) -> str:
        parts: list[str] = []
        r = summary.report

        # Header
        parts += [
            f"TestMind Report — project: {summary.project}",
            f"Run: {r.name}  |  {_ts(r.timestamp)}  |  Duration: {r.duration:.2f}s",
            _SEP,
        ]

        # Run overview
        parts += [
            "OVERVIEW",
            f"  Total: {r.total}   Passed: {r.passed}   Failed: {r.failed}"
            f"   Skipped: {r.skipped}   Errors: {r.errors}",
            f"  Pass rate: {_pct(r.pass_rate)}   Fail rate: {_pct(r.fail_rate)}",
            _SEP,
        ]

        # Spike
        if summary.spike:
            sp = summary.spike
            parts += [
                "⚠  FAILURE SPIKE DETECTED",
                f"  Current fail rate : {_pct(sp.current_fail_rate)}",
                f"  Baseline          : {_pct(sp.baseline_mean)} ± {_pct(sp.baseline_std)}",
                f"  Z-score           : {sp.z_score:.2f}",
                _SEP,
            ]

        # Flaky tests
        if summary.flaky:
            parts.append(f"FLAKY TESTS  ({len(summary.flaky)})")
            for f in summary.flaky:
                parts.append(
                    f"  {f.test_name:<50}  flip={_pct(f.flip_rate)}"
                    f"  fail={_pct(f.fail_rate)}  runs={f.run_count}"
                )
            parts.append(_SEP)

        # Regressions
        if summary.regressions:
            parts.append(f"REGRESSIONS  ({len(summary.regressions)})")
            for reg in summary.regressions:
                parts.append(
                    f"  {reg.test_name:<50}  ref_pass={_pct(reg.reference_pass_rate)}"
                    f"  recent_fail={_pct(reg.recent_fail_rate)}"
                )
            parts.append(_SEP)

        # Stability index (bottom 10 — worst first)
        stable = [s for s in summary.stability if not s.insufficient_data]
        if stable:
            show = stable[:10]
            parts.append(f"STABILITY INDEX  (worst {len(show)} of {len(stable)} tests)")
            parts.append(f"  {'Test':<50}  Score  Pass    Consist  Flips")
            for s in show:
                parts.append(
                    f"  {s.test_name:<50}  {s.score:5.1f}  "
                    f"{_pct(s.pass_rate):<7} {_pct(s.duration_consistency):<8} {_pct(s.flip_rate)}"
                )
            parts.append(_SEP)

        # Failure predictions (top 10 — highest risk first)
        risky = [p for p in summary.predictions if not p.insufficient_data]
        if risky:
            show_p = risky[:10]
            parts.append(f"FAILURE PREDICTIONS  (top {len(show_p)} by risk)")
            parts.append(f"  {'Test':<50}  Prob    Trend       Confidence")
            for p in show_p:
                parts.append(
                    f"  {p.test_name:<50}  {_pct(p.failure_probability):<7} "
                    f"{p.trend:<11} {_pct(p.confidence)}"
                )
            parts.append(_SEP)

        # Footer: counts
        flaky_n = len(summary.flaky)
        reg_n = len(summary.regressions)
        spike_n = 1 if summary.spike else 0
        parts.append(
            f"ISSUES: {flaky_n} flaky  |  {reg_n} regression(s)  |  {spike_n} spike(s)"
        )

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


class JsonFormatter:
    """Produces a machine-readable JSON string."""

    def format(self, summary: RunSummary, indent: int = 2) -> str:
        data = {
            "project": summary.project,
            "report": {
                "id": summary.report.id,
                "name": summary.report.name,
                "timestamp": summary.report.timestamp.isoformat(),
                "duration": summary.report.duration,
                "passed": summary.report.passed,
                "failed": summary.report.failed,
                "skipped": summary.report.skipped,
                "errors": summary.report.errors,
                "total": summary.report.total,
                "pass_rate": round(summary.report.pass_rate, 4),
                "fail_rate": round(summary.report.fail_rate, 4),
            },
            "issues": {
                "flaky_count": len(summary.flaky),
                "regression_count": len(summary.regressions),
                "spike_detected": summary.spike is not None,
            },
            "flaky": [asdict(f) for f in summary.flaky],
            "regressions": [asdict(r) for r in summary.regressions],
            "spike": asdict(summary.spike) if summary.spike else None,
            "stability": [asdict(s) for s in summary.stability],
            "predictions": [asdict(p) for p in summary.predictions],
        }
        return json.dumps(data, indent=indent, default=str)
