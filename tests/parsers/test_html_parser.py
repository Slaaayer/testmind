"""Tests for HtmlReportParser (pytest-html format)."""
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from testmind.domain.models import TestReport, TestStatus
from testmind.parsers.html_parser import HtmlReportParser


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------


def _minimal_html(rows: str = "", title: str = "Test Report") -> str:
    """Minimal valid pytest-html page."""
    return textwrap.dedent(f"""\
        <!DOCTYPE html>
        <html>
        <head><title>{title}</title></head>
        <body>
        <h1>test report</h1>
        <p id="environment">Report generated on 2024-06-15 at 10:30:00</p>
        <table id="results-table">
        <thead><tr><th>Result</th><th>Test</th><th>Duration</th></tr></thead>
        <tbody>
        {rows}
        </tbody>
        </table>
        </body>
        </html>
    """)


def _row(status: str, name: str, duration: str = "0.10") -> str:
    return (
        f'<tr class="{status} results-table-row">'
        f'<td class="col-result">{status.capitalize()}</td>'
        f'<td class="col-name">{name}</td>'
        f'<td class="col-duration">{duration}</td>'
        f"</tr>"
    )


def _expansion_row(log: str) -> str:
    """The extra detail row that pytest-html emits after each non-passed test."""
    return (
        '<tr class="failed results-table-row">'
        '<td class="col-result"></td>'
        f'<td class="col-name"><div class="log">{log}</div></td>'
        '<td class="col-duration"></td>'
        "</tr>"
    )


@pytest.fixture
def tmp_html(tmp_path: Path):
    """Factory: write HTML content to a temp file and return the path."""

    def _write(content: str, name: str = "report.html") -> Path:
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return p

    return _write


@pytest.fixture
def parser():
    return HtmlReportParser()


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


class TestBasicParsing:
    def test_returns_test_report(self, parser, tmp_html):
        html = _minimal_html(_row("passed", "tests/test_foo.py::test_a"))
        path = tmp_html(html)
        report = parser.parse(path, "myproject")
        assert isinstance(report, TestReport)

    def test_project_name(self, parser, tmp_html):
        html = _minimal_html(_row("passed", "tests/test_foo.py::test_a"))
        report = parser.parse(tmp_html(html), "myproject")
        assert report.project == "myproject"

    def test_passed_count(self, parser, tmp_html):
        rows = _row("passed", "t1") + _row("passed", "t2")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert report.passed == 2
        assert report.failed == 0

    def test_failed_count(self, parser, tmp_html):
        rows = _row("passed", "t1") + _row("failed", "t2")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert report.passed == 1
        assert report.failed == 1

    def test_skipped_count(self, parser, tmp_html):
        rows = _row("skipped", "t1") + _row("xfailed", "t2")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert report.skipped == 2

    def test_error_count(self, parser, tmp_html):
        rows = _row("error", "t1")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert report.errors == 1

    def test_xpassed_counts_as_passed(self, parser, tmp_html):
        rows = _row("xpassed", "t1")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert report.passed == 1

    def test_total_equals_sum(self, parser, tmp_html):
        rows = (
            _row("passed", "t1")
            + _row("failed", "t2")
            + _row("skipped", "t3")
            + _row("error", "t4")
        )
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert report.total == 4


# ---------------------------------------------------------------------------
# Test name / classname splitting
# ---------------------------------------------------------------------------


class TestTestNames:
    def test_full_path_splits_correctly(self, parser, tmp_html):
        rows = _row("passed", "tests/test_foo.py::MyClass::test_method")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        t = report.tests[0]
        assert t.name == "test_method"
        assert t.classname == "tests/test_foo.py::MyClass"

    def test_plain_name_no_classname(self, parser, tmp_html):
        rows = _row("passed", "test_simple")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        t = report.tests[0]
        assert t.name == "test_simple"
        assert t.classname is None

    def test_two_part_name(self, parser, tmp_html):
        rows = _row("passed", "module.py::test_fn")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        t = report.tests[0]
        assert t.name == "test_fn"
        assert t.classname == "module.py"


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------


class TestDuration:
    def test_duration_parsed(self, parser, tmp_html):
        rows = _row("passed", "t1", duration="1.23")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert report.tests[0].duration == pytest.approx(1.23)

    def test_total_duration_summed(self, parser, tmp_html):
        rows = _row("passed", "t1", duration="1.0") + _row("passed", "t2", duration="2.0")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert report.duration == pytest.approx(3.0)

    def test_missing_duration_defaults_zero(self, parser, tmp_html):
        # duration cell present but empty
        row = (
            '<tr class="passed results-table-row">'
            '<td class="col-result">Passed</td>'
            '<td class="col-name">t1</td>'
            '<td class="col-duration"></td>'
            "</tr>"
        )
        report = parser.parse(tmp_html(_minimal_html(row)), "p")
        assert report.tests[0].duration == 0.0


# ---------------------------------------------------------------------------
# Expansion rows (failure log)
# ---------------------------------------------------------------------------


class TestExpansionRows:
    def test_log_attached_as_message(self, parser, tmp_html):
        rows = _row("failed", "t1") + _expansion_row("AssertionError: boom")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        t = report.tests[0]
        assert t.message is not None
        assert "AssertionError" in t.message

    def test_log_attached_as_stack_trace(self, parser, tmp_html):
        rows = _row("failed", "t1") + _expansion_row("long traceback here")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert "long traceback" in report.tests[0].stack_trace

    def test_no_expansion_row_leaves_message_none(self, parser, tmp_html):
        rows = _row("failed", "t1")
        report = parser.parse(tmp_html(_minimal_html(rows)), "p")
        assert report.tests[0].message is None


# ---------------------------------------------------------------------------
# Title / suite name
# ---------------------------------------------------------------------------


class TestSuiteName:
    def test_h1_used_as_name(self, parser, tmp_html):
        html = textwrap.dedent("""\
            <html><head><title>ignored title</title></head>
            <body>
            <h1>My Suite</h1>
            <p>Report generated on 2024-01-01 at 00:00:00</p>
            <table id="results-table"><tbody>
            """ + _row("passed", "t") + """
            </tbody></table></body></html>
        """)
        report = parser.parse(tmp_html(html), "p")
        assert report.name == "My Suite"

    def test_title_tag_fallback(self, parser, tmp_html):
        html = textwrap.dedent("""\
            <html><head><title>Fallback Title</title></head>
            <body>
            <p>Report generated on 2024-01-01 at 00:00:00</p>
            <table id="results-table"><tbody>
            """ + _row("passed", "t") + """
            </tbody></table></body></html>
        """)
        report = parser.parse(tmp_html(html), "p")
        assert report.name == "Fallback Title"

    def test_falls_back_to_file_stem(self, parser, tmp_html):
        html = textwrap.dedent("""\
            <html><head><title>test report</title></head>
            <body>
            <p>Report generated on 2024-01-01 at 00:00:00</p>
            <table id="results-table"><tbody>
            """ + _row("passed", "t") + """
            </tbody></table></body></html>
        """)
        path = tmp_html(html, name="my_report.html")
        report = parser.parse(path, "p")
        assert report.name == "my_report"


# ---------------------------------------------------------------------------
# Timestamp extraction
# ---------------------------------------------------------------------------


class TestTimestamp:
    def test_timestamp_from_generated_on_paragraph(self, parser, tmp_html):
        html = _minimal_html(_row("passed", "t"))
        report = parser.parse(tmp_html(html), "p")
        assert report.timestamp == datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_timestamp_from_time_element(self, parser, tmp_html):
        html = textwrap.dedent("""\
            <html><head><title>R</title></head>
            <body>
            <time datetime="2024-03-20T09:00:00">20 Mar 2024</time>
            <table id="results-table"><tbody>
            """ + _row("passed", "t") + """
            </tbody></table></body></html>
        """)
        report = parser.parse(tmp_html(html), "p")
        assert report.timestamp.year == 2024
        assert report.timestamp.month == 3
        assert report.timestamp.day == 20

    def test_missing_timestamp_defaults_to_now(self, parser, tmp_html):
        html = textwrap.dedent("""\
            <html><head><title>R</title></head>
            <body>
            <table id="results-table"><tbody>
            """ + _row("passed", "t") + """
            </tbody></table></body></html>
        """)
        before = datetime.now(tz=timezone.utc)
        report = parser.parse(tmp_html(html), "p")
        after = datetime.now(tz=timezone.utc)
        assert before <= report.timestamp <= after


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_missing_file_raises_value_error(self, parser, tmp_path):
        with pytest.raises(ValueError, match="Cannot read"):
            parser.parse(tmp_path / "nonexistent.html", "p")

    def test_no_results_table_raises_value_error(self, parser, tmp_html):
        html = "<html><body><p>No table here</p></body></html>"
        with pytest.raises(ValueError, match="results-table"):
            parser.parse(tmp_html(html), "p")

    def test_empty_table_returns_zero_tests(self, parser, tmp_html):
        html = _minimal_html(rows="")
        report = parser.parse(tmp_html(html), "p")
        assert report.tests == []
        assert report.passed == report.failed == 0
