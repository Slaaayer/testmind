from pathlib import Path
from typing import Annotated, Optional

import typer

from testmind.analysis.flaky import FlakyDetector
from testmind.analysis.predictor import FailurePredictor
from testmind.analysis.regression import RegressionDetector
from testmind.analysis.stability import StabilityAnalyzer
from testmind.parsers.html_parser import HtmlReportParser
from testmind.parsers.junit_parser import JUnitParser
from testmind.reports.dashboard import render_dashboard
from testmind.reports.formatters import JsonFormatter, TextFormatter
from testmind.reports.summary import Summarizer
from testmind.storage.base import Store
from testmind.storage.factory import open_store

app = typer.Typer(
    name="testmind",
    help="TestMind — ingest test reports, detect patterns, predict failures.",
    add_completion=False,
    no_args_is_help=True,

)

# ---------------------------------------------------------------------------
# Shared option types
# ---------------------------------------------------------------------------

_DbOpt = Annotated[
    Optional[str],
    typer.Option(
        "--db",
        envvar="TESTMIND_DB",
        help="SQLite file path or PostgreSQL URL (postgresql://...).",
        show_default=False,
    ),
]
_FmtOpt = Annotated[
    str,
    typer.Option("--format", "-f", help="Output format: text or json."),
]
_LimitOpt = Annotated[
    int,
    typer.Option("--limit", "-n", help="Max number of historical reports to load."),
]


def _open_store(db: Optional[str]) -> Store:
    return open_store(db)


def _detect_parser(path: Path) -> JUnitParser | HtmlReportParser:
    if path.suffix.lower() in (".html", ".htm"):
        return HtmlReportParser()
    return JUnitParser()


def _err(msg: str) -> None:
    typer.echo(msg, err=True)


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


@app.command()
def ingest(
    files: Annotated[list[Path], typer.Argument(help="One or more JUnit XML report files.")],
    project: Annotated[str, typer.Option("--project", "-p", help="Project name to track under.")],
    format: _FmtOpt = "text",
    db: _DbOpt = None,
    limit: _LimitOpt = 30,
) -> None:
    """Parse one or more JUnit XML reports, store them, and print the analysis summary.

    Pass multiple files to bulk-load history before the first analysis:

        testmind ingest reports/*.xml --project my-service
    """
    if not files:
        _err("No files provided.")
        raise typer.Exit(1)

    n = len(files)
    noun = "report" if n == 1 else "reports"
    typer.echo(f"Ingesting {n} {noun} for project '{project}'...")

    store = _open_store(db)
    try:
        stored = skipped = errors = 0

        for i, file in enumerate(files, start=1):
            prefix = f"  [{i}/{n}] {file.name:<40}"

            if not file.exists():
                typer.echo(f"{prefix}  ERROR: file not found")
                errors += 1
                continue

            try:
                report = _detect_parser(file).parse(file, project=project)
            except ValueError as exc:
                typer.echo(f"{prefix}  ERROR: {exc}")
                errors += 1
                continue

            if store.report_exists(report.id):
                typer.echo(f"{prefix}  already stored")
                skipped += 1
            else:
                store.save_report(report)
                typer.echo(
                    f"{prefix}  stored '{report.name}'"
                    f"  [{report.passed}✓  {report.failed}✗"
                    f"  {report.skipped}⊘  {report.errors}!]"
                )
                stored += 1

        # Summary line
        parts = [f"{stored} stored"]
        if skipped:
            parts.append(f"{skipped} duplicate(s) skipped")
        if errors:
            parts.append(f"{errors} error(s)")
        typer.echo(f"\n{', '.join(parts)}.")

        if stored + skipped == 0:
            # Every file failed — nothing useful in the store
            raise typer.Exit(1)

        # If the project is soft-deleted all stored reports are invisible to the
        # analyser.  Re-ingesting duplicates into a deleted project is a common
        # mistake after a "bad first run" — give an actionable error.
        active = store.list_projects(include_deleted=False)
        if project not in active:
            _err(
                f"Project '{project}' is soft-deleted and already has stored data.\n"
                f"To start fresh, permanently remove it first:\n"
                f"  testmind delete --hard {project}\n"
                f"Then re-run ingest."
            )
            raise typer.Exit(1)

        summarizer = Summarizer(history_limit=limit)
        summary = summarizer.summarize(project, store)

        typer.echo("")
        if format == "json":
            typer.echo(JsonFormatter().format(summary))
        else:
            typer.echo(TextFormatter().format(summary))
    finally:
        store.close()


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


@app.command()
def analyze(
    project: Annotated[str, typer.Argument(help="Project name to analyse.")],
    format: _FmtOpt = "text",
    db: _DbOpt = None,
    limit: _LimitOpt = 30,
) -> None:
    """Run the full analysis pipeline on the latest stored run for a project."""
    store = _open_store(db)
    try:
        reports = store.get_reports(project, limit=1)
        if not reports:
            _err(f"No reports found for project '{project}'. Run 'ingest' first.")
            raise typer.Exit(1)

        summarizer = Summarizer(history_limit=limit)
        summary = summarizer.summarize(project, store)

        if format == "json":
            typer.echo(JsonFormatter().format(summary))
        else:
            typer.echo(TextFormatter().format(summary))
    finally:
        store.close()


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


@app.command()
def projects(
    db: _DbOpt = None,
    all: Annotated[bool, typer.Option("--all", "-a", help="Include soft-deleted projects.")] = False,
) -> None:
    """List all tracked projects."""
    store = _open_store(db)
    try:
        names = store.list_projects(include_deleted=all)
        deleted_set: set[str] = set()
        if all:
            # Build the set of deleted names by comparing with non-deleted list
            active = set(store.list_projects(include_deleted=False))
            deleted_set = set(names) - active

        if not names:
            typer.echo("No projects tracked yet. Use 'ingest' to add one.")
            return

        typer.echo(f"{'Project':<40}  {'Reports':>7}  {'Latest run':<20}  {'Status'}")
        typer.echo("─" * 80)
        for name in names:
            # For deleted projects get_reports returns [] so query directly
            if name in deleted_set:
                runs_count = store.get_report_count(name)
                typer.echo(f"{name:<40}  {runs_count:>7}  {'—':<20}  [deleted]")
            else:
                runs = store.get_reports(name, limit=9999)
                latest = runs[0].timestamp.strftime("%Y-%m-%d %H:%M") if runs else "—"
                typer.echo(f"{name:<40}  {len(runs):>7}  {latest:<20}  active")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# delete / restore
# ---------------------------------------------------------------------------


@app.command()
def delete(
    project: Annotated[str, typer.Argument(help="Project name to delete.")],
    hard: Annotated[bool, typer.Option("--hard", help="Permanently delete all data (cannot be undone).")] = False,
    db: _DbOpt = None,
) -> None:
    """Soft-delete a project (hidden from listings and analysis, data preserved).

    Use --hard to permanently remove all reports and test results for the project.
    """
    store = _open_store(db)
    try:
        all_projects = store.list_projects(include_deleted=True)
        if project not in all_projects:
            _err(f"Project '{project}' not found.")
            raise typer.Exit(1)

        if hard:
            store.hard_delete_project(project)
            typer.echo(f"Project '{project}' and all its data have been permanently deleted.")
        else:
            active = store.list_projects(include_deleted=False)
            if project not in active:
                _err(f"Project '{project}' is already deleted.")
                raise typer.Exit(1)
            store.delete_project(project)
            typer.echo(f"Project '{project}' has been soft-deleted. Use 'restore' to recover it.")
    finally:
        store.close()


@app.command()
def restore(
    project: Annotated[str, typer.Argument(help="Project name to restore.")],
    db: _DbOpt = None,
) -> None:
    """Restore a previously soft-deleted project."""
    store = _open_store(db)
    try:
        all_projects = store.list_projects(include_deleted=True)
        active = store.list_projects(include_deleted=False)
        if project not in all_projects:
            _err(f"Project '{project}' not found.")
            raise typer.Exit(1)
        if project in active:
            _err(f"Project '{project}' is not deleted.")
            raise typer.Exit(1)
        store.restore_project(project)
        typer.echo(f"Project '{project}' has been restored.")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# tests / test
# ---------------------------------------------------------------------------

_STATUS_ICON: dict[str, str] = {
    "PASSED":  "✓",
    "FAILED":  "✗",
    "ERROR":   "!",
    "SKIPPED": "⊘",
    "UNKNOWN": "?",
}


@app.command(name="tests")
def list_tests(
    project: Annotated[str, typer.Argument(help="Project name.")],
    db: _DbOpt = None,
) -> None:
    """List all tests in a project with their latest status and pass rate."""
    store = _open_store(db)
    try:
        rows = store.list_tests(project)
        if not rows:
            _err(f"No tests found for project '{project}'.")
            raise typer.Exit(1)

        typer.echo(f"Tests in '{project}'  ({len(rows)} test(s))\n")
        header = f"{'Test':<60}  {'St':>2}  {'Runs':>5}  {'Pass rate':>9}  {'Reruns':>6}"
        typer.echo(header)
        typer.echo("─" * len(header))
        for name, latest_status, run_count, pass_count, total_reruns in rows:
            icon = _STATUS_ICON.get(latest_status, "?")
            pass_rate = pass_count / run_count if run_count else 0.0
            rerun_marker = f"{total_reruns:>6}" if total_reruns else "      "
            typer.echo(
                f"{name[:60]:<60}  {icon:>2}  {run_count:>5}  {pass_rate:>8.0%}  {rerun_marker}"
            )
    finally:
        store.close()


@app.command(name="test")
def show_test(
    project: Annotated[str, typer.Argument(help="Project name.")],
    test_name: Annotated[str, typer.Argument(help="Full test name (as shown by 'tests' command).")],
    db: _DbOpt = None,
    limit: _LimitOpt = 30,
) -> None:
    """Show analysis and recent history for a single test."""
    store = _open_store(db)
    try:
        history = store.get_test_history(project, test_name, limit=limit)
        if not history:
            _err(f"No history found for test '{test_name}' in project '{project}'.")
            raise typer.Exit(1)

        flaky  = FlakyDetector().analyze(test_name, history)
        regr   = RegressionDetector().analyze(test_name, history)
        stab   = StabilityAnalyzer().analyze(test_name, history)
        pred   = FailurePredictor().analyze(test_name, history)

        typer.echo(f"Test:    {test_name}")
        typer.echo(f"Project: {project}\n")

        typer.echo("Analysis")
        typer.echo("─" * 40)

        # Stability
        if stab.insufficient_data:
            typer.echo(f"  Stability:    — (need ≥3 runs, have {stab.run_count})")
        else:
            typer.echo(
                f"  Stability:    {stab.score:.1f}/100"
                f"  (pass rate {stab.pass_rate:.0%}, flip rate {stab.flip_rate:.0%})"
            )

        # Flaky
        if flaky.insufficient_data:
            typer.echo("  Flaky:        — (need ≥5 runs)")
        else:
            flag = "YES" if flaky.is_flaky else "No"
            typer.echo(f"  Flaky:        {flag}")

        # Regression
        if regr.insufficient_data:
            typer.echo("  Regression:   — (need ≥6 runs)")
        else:
            flag = "YES" if regr.is_regression else "No"
            typer.echo(f"  Regression:   {flag}")

        # Prediction
        if pred.insufficient_data:
            typer.echo("  Prediction:   — (need ≥3 runs)")
        else:
            typer.echo(
                f"  Prediction:   {pred.failure_probability:.0%} failure probability"
                f"  (trend: {pred.trend}, confidence: {pred.confidence:.0%})"
            )

        total_reruns = sum(r.rerun_count for _, r in history)
        if total_reruns:
            typer.echo(f"  Reruns:       {total_reruns} retry attempt(s) across all runs")

        typer.echo(f"\nRecent runs  (last {len(history)})")
        typer.echo("─" * 40)
        hdr = f"  {'Timestamp':<22}  {'Status':<8}  {'Duration':>9}  {'Reruns':>6}"
        typer.echo(hdr)
        for ts, result in history:
            icon = _STATUS_ICON.get(result.status.value, "?")
            rerun_str = f"{result.rerun_count:>6}" if result.rerun_count else "      "
            typer.echo(
                f"  {ts.strftime('%Y-%m-%d %H:%M:%S'):<22}"
                f"  {icon} {result.status.value:<6}"
                f"  {result.duration:>8.2f}s"
                f"  {rerun_str}"
            )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


@app.command()
def dashboard(
    projects: Annotated[
        Optional[list[str]],
        typer.Option("--project", "-p", help="Project(s) to include. Defaults to all active projects."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Output HTML file path."),
    ] = Path("testmind-dashboard.html"),
    db: _DbOpt = None,
) -> None:
    """Generate a self-contained HTML dashboard for one or more projects."""
    store = _open_store(db)
    try:
        selected = list(projects) if projects else store.list_projects(include_deleted=False)
        if not selected:
            _err("No active projects found.")
            raise typer.Exit(1)

        html = render_dashboard(store, selected)
        output.write_text(html, encoding="utf-8")
        typer.echo(f"Dashboard written to {output}  ({len(selected)} project(s))")
    finally:
        store.close()


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


@app.command()
def history(
    project: Annotated[str, typer.Argument(help="Project name.")],
    db: _DbOpt = None,
    limit: _LimitOpt = 10,
) -> None:
    """Show recent runs for a project."""
    store = _open_store(db)
    try:
        reports = store.get_reports(project, limit=limit)
        if not reports:
            _err(f"No reports found for project '{project}'.")
            raise typer.Exit(1)

        typer.echo(f"History for '{project}'  (showing {len(reports)} run(s))\n")
        header = (
            f"{'Run':<35}  {'Timestamp':<22}  "
            f"{'Pass':>5}  {'Fail':>5}  {'Skip':>5}  {'Err':>5}  {'Duration':>9}"
        )
        typer.echo(header)
        typer.echo("─" * len(header))
        for r in reports:
            ts = r.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            typer.echo(
                f"{r.name:<35}  {ts:<22}  {r.passed:>5}  {r.failed:>5}"
                f"  {r.skipped:>5}  {r.errors:>5}  {r.duration:>8.2f}s"
            )
    finally:
        store.close()
