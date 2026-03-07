from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, computed_field

from testmind.utils.tools import generate_unique_id


class TestStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class TestResult(BaseModel):
    name: str
    classname: str | None = None
    suite: str | None = None
    status: TestStatus
    duration: float
    message: str | None = None
    stack_trace: str | None = None


class TestReport(BaseModel):
    name: str
    project: str
    tests: list[TestResult]
    timestamp: datetime
    passed: int
    failed: int
    skipped: int
    errors: int
    duration: float

    @computed_field
    @property
    def id(self) -> str:
        return generate_unique_id(
            project=self.project,
            duration=self.duration,
            timestamp=self.timestamp.isoformat(),
            tests=len(self.tests),
        )

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.skipped + self.errors

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0

    @property
    def fail_rate(self) -> float:
        return (self.failed + self.errors) / self.total if self.total > 0 else 0.0


