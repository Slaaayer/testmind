"""
pytest-html report parser.

Targets the table-based HTML format produced by pytest-html (v2 / v3 / v4).
The parser is intentionally lenient: it extracts what it can and fills in
sensible defaults for anything missing.

Supported elements
------------------
- Results table: ``<table id="results-table">``
- Row classes: ``passed``, ``failed``, ``error``, ``skipped``, ``xfailed``,
  ``xpassed``
- Failure/error log: ``<div class="log">`` inside an expanded detail row
- Timestamp: ``<p>Report generated on …</p>`` or ``<time>`` element
- Suite name: ``<title>`` or ``<h1>``
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.parsers.base import ReportParser

# Maps pytest-html row CSS classes → TestStatus
_STATUS_MAP: dict[str, TestStatus] = {
    "passed":  TestStatus.PASSED,
    "failed":  TestStatus.FAILED,
    "error":   TestStatus.ERROR,
    "skipped": TestStatus.SKIPPED,
    "xfailed": TestStatus.SKIPPED,   # expected failure → treat as skipped
    "xpassed": TestStatus.PASSED,    # unexpected pass  → treat as passed
}

# Patterns to extract a datetime from the "generated on" paragraph
_TS_PATTERNS = [
    # "Report generated on 2024-01-15 at 10:30:00"
    r"(\d{4}-\d{2}-\d{2})\s+at\s+(\d{2}:\d{2}:\d{2})",
    # "Report generated on 15-Jan-2024 at 10:30:00"
    r"(\d{2}-\w{3}-\d{4})\s+at\s+(\d{2}:\d{2}:\d{2})",
    # ISO-like: "2024-01-15T10:30:00"
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})",
]


class HtmlReportParser(ReportParser):
    """Parse a pytest-html test report into a ``TestReport``."""

    def parse(self, path: str | Path, project: str) -> TestReport:
        path = Path(path)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError(f"Cannot read {path}: {exc}") from exc

        soup = BeautifulSoup(content, "html.parser")

        table = soup.find("table", id="results-table")
        if table is None:
            raise ValueError(
                f"No <table id='results-table'> found in {path}. "
                "Is this a pytest-html report?"
            )

        suite_name = _extract_title(soup) or path.stem
        timestamp = _extract_timestamp(soup)
        tests = _parse_rows(table, suite_name)

        passed  = sum(1 for t in tests if t.status == TestStatus.PASSED)
        failed  = sum(1 for t in tests if t.status == TestStatus.FAILED)
        skipped = sum(1 for t in tests if t.status == TestStatus.SKIPPED)
        errors  = sum(1 for t in tests if t.status == TestStatus.ERROR)
        duration = sum(t.duration for t in tests)

        return TestReport(
            name=suite_name,
            project=project,
            tests=tests,
            timestamp=timestamp,
            passed=passed,
            failed=failed,
            skipped=skipped,
            errors=errors,
            duration=duration,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_rows(table, suite_name: str) -> list[TestResult]:
    tests: list[TestResult] = []
    # Collect main result rows; skip log-expansion rows (col-result is empty)
    pending_message: str | None = None

    for tr in table.find_all("tr"):
        classes = set(tr.get("class") or [])

        # Determine status from row class
        status: TestStatus | None = None
        for cls, mapped in _STATUS_MAP.items():
            if cls in classes:
                status = mapped
                break

        if status is None:
            continue  # header or unknown row

        result_td = tr.find("td", class_="col-result")
        name_td   = tr.find("td", class_="col-name")
        dur_td    = tr.find("td", class_="col-duration")

        if result_td is None or name_td is None:
            continue

        result_text = result_td.get_text(strip=True)

        if not result_text:
            # This is an expansion row — grab the log as the message for the
            # most recently appended test
            log_div = name_td.find("div", class_="log")
            if log_div and tests:
                log_text = log_div.get_text(separator="\n", strip=True)
                # Replace the last test with one that has the message attached
                last = tests[-1]
                tests[-1] = TestResult(
                    name=last.name,
                    classname=last.classname,
                    suite=last.suite,
                    status=last.status,
                    duration=last.duration,
                    message=log_text[:1000],
                    stack_trace=log_text,
                )
            continue

        # Extract name and optional classname from "path::Class::method"
        full_name = name_td.get_text(strip=True).split("\n")[0].strip()
        name, classname = _split_test_name(full_name)

        duration = 0.0
        if dur_td:
            try:
                duration = float(dur_td.get_text(strip=True) or 0)
            except ValueError:
                pass

        tests.append(TestResult(
            name=name,
            classname=classname,
            suite=suite_name,
            status=status,
            duration=duration,
        ))

    return tests


def _split_test_name(full_name: str) -> tuple[str, str | None]:
    """
    Split "path/to/test_file.py::ClassName::test_method" into
    (test_method, "path/to/test_file.py::ClassName").
    Falls back to (full_name, None) for plain names.
    """
    parts = full_name.split("::")
    if len(parts) >= 2:
        return parts[-1], "::".join(parts[:-1])
    return full_name, None


def _extract_title(soup: BeautifulSoup) -> str | None:
    for selector in (("h1", {}), ("title", {})):
        tag = soup.find(selector[0])
        if tag:
            text = tag.get_text(strip=True)
            if text and text.lower() not in ("", "test report"):
                return text
    return None


def _extract_timestamp(soup: BeautifulSoup) -> datetime:
    # 1. Look for a <time> element with a datetime attribute
    time_tag = soup.find("time")
    if time_tag and time_tag.get("datetime"):
        try:
            return _parse_dt(time_tag["datetime"])
        except ValueError:
            pass

    # 2. Scan paragraph text for "generated on" patterns
    for p in soup.find_all(["p", "span", "div"]):
        text = p.get_text(" ", strip=True)
        if "generated" not in text.lower():
            continue
        dt = _try_parse_ts_text(text)
        if dt:
            return dt

    return datetime.now(tz=timezone.utc)


def _try_parse_ts_text(text: str) -> datetime | None:
    for pattern in _TS_PATTERNS:
        m = re.search(pattern, text)
        if m:
            try:
                combined = " ".join(m.groups())
                return _parse_dt(combined)
            except ValueError:
                continue
    return None


def _parse_dt(s: str) -> datetime:
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d-%b-%Y %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(s.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Last resort: fromisoformat
    dt = datetime.fromisoformat(s.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
