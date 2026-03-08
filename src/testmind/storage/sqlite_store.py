import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.storage.base import Store

_DDL = """
CREATE TABLE IF NOT EXISTS reports (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    project  TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    passed   INTEGER NOT NULL,
    failed   INTEGER NOT NULL,
    skipped  INTEGER NOT NULL,
    errors   INTEGER NOT NULL,
    duration REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS test_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    classname   TEXT,
    suite       TEXT,
    status      TEXT NOT NULL,
    duration    REAL NOT NULL,
    message     TEXT,
    stack_trace TEXT
);

CREATE TABLE IF NOT EXISTS deleted_projects (
    project TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_project   ON reports(project, timestamp);
CREATE INDEX IF NOT EXISTS idx_results_report    ON test_results(report_id);
CREATE INDEX IF NOT EXISTS idx_results_name      ON test_results(name);
"""


class SQLiteStore(Store):
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        path = str(db_path)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def save_report(self, report: TestReport) -> None:
        if self.report_exists(report.id):
            return
        with self._transaction():
            self._conn.execute(
                """
                INSERT INTO reports (id, name, project, timestamp, passed, failed, skipped, errors, duration)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.id,
                    report.name,
                    report.project,
                    report.timestamp.isoformat(),
                    report.passed,
                    report.failed,
                    report.skipped,
                    report.errors,
                    report.duration,
                ),
            )
            self._conn.executemany(
                """
                INSERT INTO test_results (report_id, name, classname, suite, status, duration, message, stack_trace)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        report.id,
                        t.name,
                        t.classname,
                        t.suite,
                        t.status.value,
                        t.duration,
                        t.message,
                        t.stack_trace,
                    )
                    for t in report.tests
                ],
            )

    def get_reports(self, project: str, limit: int = 50) -> list[TestReport]:
        rows = self._conn.execute(
            """
            SELECT r.* FROM reports r
            WHERE r.project = ?
              AND r.project NOT IN (SELECT project FROM deleted_projects)
            ORDER BY r.timestamp DESC LIMIT ?
            """,
            (project, limit),
        ).fetchall()

        reports = []
        for row in rows:
            tests = self._load_test_results(row["id"])
            reports.append(_row_to_report(row, tests))
        return reports

    def get_test_history(
        self, project: str, test_name: str, limit: int = 50
    ) -> list[tuple[datetime, TestResult]]:
        rows = self._conn.execute(
            """
            SELECT r.timestamp, tr.*
            FROM test_results tr
            JOIN reports r ON r.id = tr.report_id
            WHERE r.project = ? AND tr.name = ?
              AND r.project NOT IN (SELECT project FROM deleted_projects)
            ORDER BY r.timestamp DESC
            LIMIT ?
            """,
            (project, test_name, limit),
        ).fetchall()
        return [(_parse_ts(row["timestamp"]), _row_to_test_result(row)) for row in rows]

    def list_projects(self, include_deleted: bool = False) -> list[str]:
        if include_deleted:
            rows = self._conn.execute(
                "SELECT DISTINCT project FROM reports ORDER BY project"
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT DISTINCT project FROM reports
                WHERE project NOT IN (SELECT project FROM deleted_projects)
                ORDER BY project
                """
            ).fetchall()
        return [row["project"] for row in rows]

    def delete_project(self, name: str) -> None:
        with self._transaction():
            self._conn.execute(
                "INSERT OR IGNORE INTO deleted_projects (project, deleted_at) VALUES (?, ?)",
                (name, datetime.now(tz=timezone.utc).isoformat()),
            )

    def restore_project(self, name: str) -> None:
        with self._transaction():
            self._conn.execute(
                "DELETE FROM deleted_projects WHERE project = ?", (name,)
            )

    def report_exists(self, report_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        return row is not None

    def get_report_count(self, project: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM reports WHERE project = ?", (project,)
        ).fetchone()
        return row["c"]

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _migrate(self) -> None:
        self._conn.executescript(_DDL)
        self._conn.commit()

    @contextmanager
    def _transaction(self):
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _load_test_results(self, report_id: str) -> list[TestResult]:
        rows = self._conn.execute(
            "SELECT * FROM test_results WHERE report_id = ?", (report_id,)
        ).fetchall()
        return [_row_to_test_result(row) for row in rows]


# ------------------------------------------------------------------
# Row mappers
# ------------------------------------------------------------------


def _row_to_report(row: sqlite3.Row, tests: list[TestResult]) -> TestReport:
    return TestReport(
        name=row["name"],
        project=row["project"],
        timestamp=_parse_ts(row["timestamp"]),
        passed=row["passed"],
        failed=row["failed"],
        skipped=row["skipped"],
        errors=row["errors"],
        duration=row["duration"],
        tests=tests,
    )


def _row_to_test_result(row: sqlite3.Row) -> TestResult:
    return TestResult(
        name=row["name"],
        classname=row["classname"],
        suite=row["suite"],
        status=TestStatus(row["status"]),
        duration=row["duration"],
        message=row["message"],
        stack_trace=row["stack_trace"],
    )


def _parse_ts(ts_str: str) -> datetime:
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
