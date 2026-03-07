import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.parsers.base import ReportParser


class JUnitParser(ReportParser):
    def parse(self, path: str | Path, project: str) -> TestReport:
        path = Path(path)
        try:
            tree = ET.parse(path)
        except ET.ParseError as e:
            raise ValueError(f"Invalid XML in {path}: {e}") from e

        root = tree.getroot()

        if root.tag == "testsuites":
            suites = list(root.findall("testsuite"))
            report_name = root.get("name") or path.stem
            ts_str = root.get("timestamp")
        elif root.tag == "testsuite":
            suites = [root]
            report_name = root.get("name") or path.stem
            ts_str = root.get("timestamp")
        else:
            raise ValueError(f"Unexpected root element <{root.tag}>; expected <testsuite> or <testsuites>")

        if not ts_str and suites:
            ts_str = suites[0].get("timestamp")

        timestamp = _parse_timestamp(ts_str)

        tests: list[TestResult] = []
        total_duration = 0.0

        for suite in suites:
            suite_name = suite.get("name", "") or None
            for tc in suite.findall("testcase"):
                result = _parse_testcase(tc, suite_name)
                tests.append(result)
                total_duration += result.duration

        passed = sum(1 for t in tests if t.status == TestStatus.PASSED)
        failed = sum(1 for t in tests if t.status == TestStatus.FAILED)
        skipped = sum(1 for t in tests if t.status == TestStatus.SKIPPED)
        errors = sum(1 for t in tests if t.status == TestStatus.ERROR)

        return TestReport(
            name=report_name,
            project=project,
            tests=tests,
            timestamp=timestamp,
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            duration=total_duration,
        )


def _parse_testcase(tc: ET.Element, suite_name: str | None) -> TestResult:
    name = tc.get("name") or "unknown"
    classname = tc.get("classname") or None
    duration = float(tc.get("time") or 0)

    failure = tc.find("failure")
    error = tc.find("error")
    skipped = tc.find("skipped")

    if failure is not None:
        status = TestStatus.FAILED
        message = failure.get("message") or (failure.text or "").strip() or None
        stack_trace = failure.text
    elif error is not None:
        status = TestStatus.ERROR
        message = error.get("message") or (error.text or "").strip() or None
        stack_trace = error.text
    elif skipped is not None:
        status = TestStatus.SKIPPED
        message = skipped.get("message") or None
        stack_trace = None
    else:
        status = TestStatus.PASSED
        message = None
        stack_trace = None

    return TestResult(
        name=name,
        classname=classname,
        suite=suite_name,
        status=status,
        duration=duration,
        message=message,
        stack_trace=stack_trace,
    )


def _parse_timestamp(ts_str: str | None) -> datetime:
    if ts_str:
        try:
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(tz=timezone.utc)
