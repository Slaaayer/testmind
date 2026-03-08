from abc import ABC, abstractmethod
from datetime import datetime

from testmind.domain.models import TestReport, TestResult


class Store(ABC):
    @abstractmethod
    def save_report(self, report: TestReport) -> None:
        """Persist a test report. Silently skips if the report id already exists."""
        ...

    @abstractmethod
    def get_reports(self, project: str, limit: int = 50) -> list[TestReport]:
        """Return the most recent `limit` reports for a project, newest first."""
        ...

    @abstractmethod
    def get_test_history(
        self, project: str, test_name: str, limit: int = 50
    ) -> list[tuple[datetime, TestResult]]:
        """Return (report_timestamp, TestResult) pairs for a test name, newest first."""
        ...

    @abstractmethod
    def list_projects(self, include_deleted: bool = False) -> list[str]:
        """Return project names that have at least one stored report.

        When *include_deleted* is False (default) deleted projects are omitted.
        """
        ...

    @abstractmethod
    def delete_project(self, name: str) -> None:
        """Soft-delete a project (hides it from normal listings)."""
        ...

    @abstractmethod
    def restore_project(self, name: str) -> None:
        """Restore a soft-deleted project."""
        ...

    @abstractmethod
    def report_exists(self, report_id: str) -> bool:
        """Return True if a report with this id is already stored."""
        ...

    @abstractmethod
    def get_report_count(self, project: str) -> int:
        """Return the total number of stored reports for a project (including deleted)."""
        ...

    @abstractmethod
    def close(self) -> None: ...
