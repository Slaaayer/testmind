"""
RunSummary — aggregates all analysis results for a single project run.

Flow (called after the report is already saved in the store):
  1. Pull the most recent TestReport as the "current" run.
  2. Pull all reports in the window for spike detection.
  3. For each test in the current run, pull its history and run all analysers.
  4. Return a RunSummary.
"""

from dataclasses import dataclass, field

from testmind.analysis.flaky import FlakyDetector
from testmind.analysis.models import (
    FlakyResult,
    PredictionResult,
    RegressionResult,
    SpikeResult,
    StabilityResult,
)
from testmind.analysis.predictor import FailurePredictor
from testmind.analysis.regression import RegressionDetector, SpikeDetector
from testmind.analysis.stability import StabilityAnalyzer
from testmind.domain.models import TestReport
from testmind.storage.base import Store


@dataclass
class RunSummary:
    project: str
    report: TestReport
    # only entries where the flag is True
    flaky: list[FlakyResult] = field(default_factory=list)
    regressions: list[RegressionResult] = field(default_factory=list)
    spike: SpikeResult | None = None
    # all tests, sorted by score ascending (worst first)
    stability: list[StabilityResult] = field(default_factory=list)
    # all tests, sorted by failure_probability descending (highest risk first)
    predictions: list[PredictionResult] = field(default_factory=list)


class Summarizer:
    """
    Orchestrates all analysers against a project's stored history.

    Assumes the current report has *already* been saved to the store so that
    its results appear in the test history queries.
    """

    def __init__(
        self,
        flaky_detector: FlakyDetector | None = None,
        regression_detector: RegressionDetector | None = None,
        spike_detector: SpikeDetector | None = None,
        stability_analyzer: StabilityAnalyzer | None = None,
        predictor: FailurePredictor | None = None,
        history_limit: int = 30,
    ) -> None:
        self._flaky = flaky_detector or FlakyDetector()
        self._regression = regression_detector or RegressionDetector()
        self._spike = spike_detector or SpikeDetector()
        self._stability = stability_analyzer or StabilityAnalyzer()
        self._predictor = predictor or FailurePredictor()
        self._history_limit = history_limit

    def summarize(self, project: str, store: Store) -> RunSummary:
        reports = store.get_reports(project, limit=self._history_limit)
        if not reports:
            raise ValueError(f"No reports found for project '{project}'")

        current_report = reports[0]  # newest first from store

        # Suite-level spike: pass oldest→newest
        spike_result = self._spike.analyze(list(reversed(reports)))

        # Per-test analysis
        flaky_results: list[FlakyResult] = []
        regression_results: list[RegressionResult] = []
        stability_results: list[StabilityResult] = []
        prediction_results: list[PredictionResult] = []

        for test in current_report.tests:
            history = store.get_test_history(
                project, test.name, limit=self._history_limit
            )

            fr = self._flaky.analyze(test.name, history)
            rr = self._regression.analyze(test.name, history)
            sr = self._stability.analyze(test.name, history)
            pr = self._predictor.analyze(test.name, history)

            if fr.is_flaky:
                flaky_results.append(fr)
            if rr.is_regression:
                regression_results.append(rr)
            stability_results.append(sr)
            prediction_results.append(pr)

        return RunSummary(
            project=project,
            report=current_report,
            flaky=flaky_results,
            regressions=regression_results,
            spike=spike_result if spike_result.is_spike else None,
            stability=sorted(stability_results, key=lambda s: s.score),
            predictions=sorted(
                prediction_results, key=lambda p: -p.failure_probability
            ),
        )
