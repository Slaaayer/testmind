import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from testmind.parsers.html_parser import HtmlReportParser
from testmind.parsers.junit_parser import JUnitParser
from testmind.reports.formatters import JsonFormatter, TextFormatter
from testmind.reports.summary import Summarizer
from testmind.storage.base import Store
from testmind.storage.factory import open_store

app = typer.Typer(
    name="testmind",
    help="TestMind — ingest test reports, detect patterns, predict failures.",
    add_completion=False,
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
    project: Annotated[str, typer.Argument(help="Project name to soft-delete.")],
    db: _DbOpt = None,
) -> None:
    """Soft-delete a project (hidden from listings and analysis, data preserved)."""
    store = _open_store(db)
    try:
        active = store.list_projects(include_deleted=False)
        if project not in active:
            all_projects = store.list_projects(include_deleted=True)
            if project in all_projects:
                _err(f"Project '{project}' is already deleted.")
            else:
                _err(f"Project '{project}' not found.")
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
