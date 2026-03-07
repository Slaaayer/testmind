from abc import ABC, abstractmethod
from pathlib import Path

from testmind.domain.models import TestReport


class ReportParser(ABC):
    @abstractmethod
    def parse(self, path: str | Path, project: str) -> TestReport: ...
