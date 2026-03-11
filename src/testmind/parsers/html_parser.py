"""
pytest-html report parser.

Supports two formats:
- pytest-html v4+: test data stored as JSON in ``<div id="data-container" data-jsonblob="...">``
- pytest-html v2/v3: test rows in ``<table id="results-table">``

The parser tries v4 first, then falls back to the legacy table format.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from testmind.domain.models import TestReport, TestResult, TestStatus
from testmind.parsers.base import ReportParser

# pytest-html v4 result strings → TestStatus
_V4_STATUS_MAP: dict[str, TestStatus] = {
    "passed":  TestStatus.PASSED,
    "xpassed": TestStatus.PASSED,
    "failed":  TestStatus.FAILED,
    "error":   TestStatus.ERROR,
    "skipped": TestStatus.SKIPPED,
    "xfailed": TestStatus.SKIPPED,
    "rerun":   TestStatus.FAILED,   # intermediate rerun attempt
}

# pytest-html v2/v3 row CSS classes → TestStatus
_V2_STATUS_MAP: dict[str, TestStatus] = {
    "passed":  TestStatus.PASSED,
    "failed":  TestStatus.FAILED,
    "error":   TestStatus.ERROR,
    "skipped": TestStatus.SKIPPED,
    "xfailed": TestStatus.SKIPPED,
    "xpassed": TestStatus.PASSED,
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

        if soup.find("table", id="results-table") is None:
            raise ValueError(
                f"No <table id='results-table'> found in {path}. "
                "Is this a pytest-html report?"
            )

        suite_name = _extract_title(soup) or path.stem
        timestamp = _extract_timestamp(soup)

        # Try v4 JSON blob first; fall back to v2/v3 table rows
        data_div = soup.find("div", id="data-container")
        if data_div and data_div.get("data-jsonblob"):
            tests = _parse_v4_jsonblob(data_div["data-jsonblob"], suite_name)
        else:
            table = soup.find("table", id="results-table")
            tests = _parse_v2_rows(table, suite_name)

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
# pytest-html v4: JSON blob parser
# ---------------------------------------------------------------------------


# Pytest execution phases reported as separate entries in the JSON blob.
# These are not distinct tests; they represent phases of a single test run.
_PYTEST_PHASES = {"setup", "teardown", "call"}

# Status severity for deduplication: higher = worse outcome wins
_STATUS_SEVERITY: dict[TestStatus, int] = {
    TestStatus.ERROR:   4,
    TestStatus.FAILED:  3,
    TestStatus.UNKNOWN: 2,
    TestStatus.SKIPPED: 1,
    TestStatus.PASSED:  0,
}


def _parse_v4_jsonblob(raw: str, suite_name: str) -> list[TestResult]:
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError:
        return []

    # Accumulate by canonical test name; phase entries are merged by worst outcome.
    # key → (status, duration, classname, rerun_count)
    merged: dict[str, tuple[TestStatus, float, str | None, int]] = {}

    for entries in blob.get("tests", {}).values():
        if not entries:
            continue

        # Entries with result "Rerun" are intermediate retry attempts.
        # Count them, then take the last non-rerun entry as the final outcome.
        rerun_count = sum(1 for e in entries if e.get("result", "").lower() == "rerun")
        entry = entries[-1]
        result_str = entry.get("result", "").lower()
        status = _V4_STATUS_MAP.get(result_str, TestStatus.UNKNOWN)

        test_id = entry.get("testId", "")
        # Strip the @alias suffix added by pytest-html v4
        test_id = test_id.split("@")[0] if "@" in test_id else test_id
        # Strip pytest phase suffix (::setup, ::teardown, ::call)
        if test_id.split("::")[-1] in _PYTEST_PHASES:
            test_id = "::".join(test_id.split("::")[:-1])

        name, classname = _split_test_name(test_id)

        # Only track proper test functions
        base_name = name.split("[")[0]
        if not base_name.startswith("test_"):
            continue

        duration = _parse_hhmmss(entry.get("duration", ""))

        if name in merged:
            prev_status, prev_dur, prev_cls, prev_reruns = merged[name]
            # Keep worst status; sum durations and rerun counts across phases
            worst = prev_status if _STATUS_SEVERITY[prev_status] >= _STATUS_SEVERITY[status] else status
            merged[name] = (worst, prev_dur + duration, prev_cls or classname, prev_reruns + rerun_count)
        else:
            merged[name] = (status, duration, classname, rerun_count)

    return [
        TestResult(
            name=name,
            classname=classname,
            suite=suite_name,
            status=status,
            duration=duration,
            rerun_count=rerun_count,
        )
        for name, (status, duration, classname, rerun_count) in merged.items()
    ]


def _parse_hhmmss(value: str) -> float:
    """Convert 'HH:MM:SS' or 'MM:SS' or plain float string to seconds."""
    if not value:
        return 0.0
    parts = value.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(value)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# pytest-html v2/v3: table row parser
# ---------------------------------------------------------------------------


def _parse_v2_rows(table, suite_name: str) -> list[TestResult]:
    tests: list[TestResult] = []

    for tr in table.find_all("tr"):
        classes = set(tr.get("class") or [])

        status: TestStatus | None = None
        for cls, mapped in _V2_STATUS_MAP.items():
            if cls in classes:
                status = mapped
                break

        if status is None:
            continue

        result_td = tr.find("td", class_="col-result")
        name_td   = tr.find("td", class_="col-name")
        dur_td    = tr.find("td", class_="col-duration")

        if result_td is None or name_td is None:
            continue

        result_text = result_td.get_text(strip=True)
        if not result_text:
            log_div = name_td.find("div", class_="log")
            if log_div and tests:
                log_text = log_div.get_text(separator="\n", strip=True)
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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
    time_tag = soup.find("time")
    if time_tag and time_tag.get("datetime"):
        try:
            return _parse_dt(time_tag["datetime"])
        except ValueError:
            pass

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
    dt = datetime.fromisoformat(s.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
