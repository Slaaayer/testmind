from contextlib import contextmanager
from datetime import datetime, timezone

from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.storage.base import Store

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS reports (
        id        TEXT PRIMARY KEY,
        name      TEXT NOT NULL,
        project   TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        passed    INTEGER NOT NULL,
        failed    INTEGER NOT NULL,
        skipped   INTEGER NOT NULL,
        errors    INTEGER NOT NULL,
        duration  FLOAT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS test_results (
        id          SERIAL PRIMARY KEY,
        report_id   TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        classname   TEXT,
        suite       TEXT,
        status      TEXT NOT NULL,
        duration    FLOAT NOT NULL,
        message     TEXT,
        stack_trace TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS deleted_projects (
        project    TEXT PRIMARY KEY,
        deleted_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reports_project ON reports(project, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_results_report  ON test_results(report_id)",
    "CREATE INDEX IF NOT EXISTS idx_results_name    ON test_results(name)",
]


class PostgresStore(Store):
    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:
            raise ImportError(
                "psycopg is required for PostgreSQL support. "
                "Install it with: pip install testmind[postgres]"
            ) from e

        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        self._conn.autocommit = False
        self._migrate()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def save_report(self, report: TestReport) -> None:
        if self.report_exists(report.id):
            return
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO reports (id, name, project, timestamp, passed, failed, skipped, errors, duration)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            cur.executemany(
                """
                INSERT INTO test_results (report_id, name, classname, suite, status, duration, message, stack_trace)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.* FROM reports r
                WHERE r.project = %s
                  AND r.project NOT IN (SELECT project FROM deleted_projects)
                ORDER BY r.timestamp DESC LIMIT %s
                """,
                (project, limit),
            )
            rows = cur.fetchall()

        reports = []
        for row in rows:
            tests = self._load_test_results(row["id"])
            reports.append(_row_to_report(row, tests))
        return reports

    def get_test_history(
        self, project: str, test_name: str, limit: int = 50
    ) -> list[tuple[datetime, TestResult]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.timestamp, tr.*
                FROM test_results tr
                JOIN reports r ON r.id = tr.report_id
                WHERE r.project = %s AND tr.name = %s
                  AND r.project NOT IN (SELECT project FROM deleted_projects)
                ORDER BY r.timestamp DESC
                LIMIT %s
                """,
                (project, test_name, limit),
            )
            rows = cur.fetchall()
        return [(_parse_ts(row["timestamp"]), _row_to_test_result(row)) for row in rows]

    def list_projects(self, include_deleted: bool = False) -> list[str]:
        with self._conn.cursor() as cur:
            if include_deleted:
                cur.execute("SELECT DISTINCT project FROM reports ORDER BY project")
            else:
                cur.execute(
                    """
                    SELECT DISTINCT project FROM reports
                    WHERE project NOT IN (SELECT project FROM deleted_projects)
                    ORDER BY project
                    """
                )
            rows = cur.fetchall()
        return [row["project"] for row in rows]

    def delete_project(self, name: str) -> None:
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO deleted_projects (project, deleted_at)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (name, datetime.now(tz=timezone.utc).isoformat()),
            )

    def restore_project(self, name: str) -> None:
        with self._transaction() as cur:
            cur.execute("DELETE FROM deleted_projects WHERE project = %s", (name,))

    def report_exists(self, report_id: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1 FROM reports WHERE id = %s", (report_id,))
            return cur.fetchone() is not None

    def get_report_count(self, project: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c FROM reports WHERE project = %s", (project,)
            )
            row = cur.fetchone()
            return row["c"]

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _migrate(self) -> None:
        with self._transaction() as cur:
            for stmt in _DDL:
                cur.execute(stmt)

    @contextmanager
    def _transaction(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    def _load_test_results(self, report_id: str) -> list[TestResult]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM test_results WHERE report_id = %s", (report_id,)
            )
            rows = cur.fetchall()
        return [_row_to_test_result(row) for row in rows]


# ------------------------------------------------------------------
# Row mappers
# ------------------------------------------------------------------


def _row_to_report(row: dict, tests: list[TestResult]) -> TestReport:
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


def _row_to_test_result(row: dict) -> TestResult:
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