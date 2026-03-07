import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from testmind.domain.models import TestStatus
from testmind.parsers.junit_parser import JUnitParser


@pytest.fixture
def parser() -> JUnitParser:
    return JUnitParser()


def write_xml(tmp_path: Path, content: str, filename: str = "report.xml") -> Path:
    p = tmp_path / filename
    p.write_text(textwrap.dedent(content).strip())
    return p


# ---------------------------------------------------------------------------
# Happy-path: basic structures
# ---------------------------------------------------------------------------


class TestBasicParsing:
    def test_single_testsuite_all_passed(self, parser, tmp_path):
        xml = """
            <?xml version="1.0"?>
            <testsuite name="MySuite" tests="2" time="0.5" timestamp="2024-01-15T10:00:00">
                <testcase name="test_a" classname="pkg.MyTest" time="0.2"/>
                <testcase name="test_b" classname="pkg.MyTest" time="0.3"/>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="myproject")

        assert report.name == "MySuite"
        assert report.project == "myproject"
        assert report.passed == 2
        assert report.failed == 0
        assert report.skipped == 0
        assert report.errors == 0
        assert len(report.tests) == 2
        assert abs(report.duration - 0.5) < 1e-9

    def test_testsuites_wrapper(self, parser, tmp_path):
        xml = """
            <?xml version="1.0"?>
            <testsuites name="AllSuites" timestamp="2024-02-01T08:00:00">
                <testsuite name="Suite1">
                    <testcase name="test_1" classname="A" time="0.1"/>
                </testsuite>
                <testsuite name="Suite2">
                    <testcase name="test_2" classname="B" time="0.2"/>
                </testsuite>
            </testsuites>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="proj")

        assert report.name == "AllSuites"
        assert report.passed == 2
        assert len(report.tests) == 2

    def test_report_id_is_deterministic(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="t" classname="C" time="0.1"/>
            </testsuite>
        """
        path = write_xml(tmp_path, xml)
        r1 = parser.parse(path, project="p")
        r2 = parser.parse(path, project="p")
        assert r1.id == r2.id

    def test_different_projects_produce_different_ids(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="t" classname="C" time="0.1"/>
            </testsuite>
        """
        path = write_xml(tmp_path, xml)
        r1 = parser.parse(path, project="proj_a")
        r2 = parser.parse(path, project="proj_b")
        assert r1.id != r2.id


# ---------------------------------------------------------------------------
# Test statuses
# ---------------------------------------------------------------------------


class TestStatusParsing:
    def test_failure_detected(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="test_fail" classname="C" time="0.1">
                    <failure message="AssertionError: 1 != 2">full stack trace here</failure>
                </testcase>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        t = report.tests[0]
        assert t.status == TestStatus.FAILED
        assert t.message == "AssertionError: 1 != 2"
        assert "stack trace" in (t.stack_trace or "")
        assert report.failed == 1

    def test_error_detected(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="test_err" classname="C" time="0.0">
                    <error message="NullPointerException">traceback</error>
                </testcase>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        t = report.tests[0]
        assert t.status == TestStatus.ERROR
        assert t.message == "NullPointerException"
        assert report.errors == 1

    def test_skipped_detected(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="test_skip" classname="C" time="0.0">
                    <skipped message="not implemented"/>
                </testcase>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        t = report.tests[0]
        assert t.status == TestStatus.SKIPPED
        assert t.message == "not implemented"
        assert report.skipped == 1

    def test_skipped_without_message(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="test_skip" classname="C" time="0.0">
                    <skipped/>
                </testcase>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.tests[0].status == TestStatus.SKIPPED
        assert report.tests[0].message is None

    def test_mixed_statuses(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="t_pass"  classname="C" time="0.1"/>
                <testcase name="t_fail"  classname="C" time="0.2">
                    <failure message="boom"/>
                </testcase>
                <testcase name="t_skip"  classname="C" time="0.0">
                    <skipped/>
                </testcase>
                <testcase name="t_error" classname="C" time="0.0">
                    <error message="oops"/>
                </testcase>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.passed == 1
        assert report.failed == 1
        assert report.skipped == 1
        assert report.errors == 1
        assert report.total == 4


# ---------------------------------------------------------------------------
# TestResult fields
# ---------------------------------------------------------------------------


class TestResultFields:
    def test_classname_and_suite_populated(self, parser, tmp_path):
        xml = """
            <testsuite name="SuiteAlpha" timestamp="2024-01-01T00:00:00">
                <testcase name="my_test" classname="com.example.FooTest" time="0.5"/>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        t = report.tests[0]
        assert t.classname == "com.example.FooTest"
        assert t.suite == "SuiteAlpha"
        assert t.name == "my_test"

    def test_missing_classname_is_none(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="t" time="0.1"/>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.tests[0].classname is None

    def test_duration_parsed_correctly(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="t1" time="1.234"/>
                <testcase name="t2" time="0.001"/>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.tests[0].duration == pytest.approx(1.234)
        assert report.tests[1].duration == pytest.approx(0.001)

    def test_missing_time_defaults_to_zero(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="t"/>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.tests[0].duration == 0.0


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def test_iso_timestamp_parsed(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-06-15T14:30:00">
                <testcase name="t" time="0.1"/>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.timestamp.year == 2024
        assert report.timestamp.month == 6
        assert report.timestamp.day == 15

    def test_missing_timestamp_defaults_to_now(self, parser, tmp_path):
        xml = """
            <testsuite name="S">
                <testcase name="t" time="0.1"/>
            </testsuite>
        """
        before = datetime.now(tz=timezone.utc)
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        after = datetime.now(tz=timezone.utc)
        assert before <= report.timestamp <= after

    def test_timestamp_on_testsuites_root_used(self, parser, tmp_path):
        xml = """
            <testsuites name="All" timestamp="2025-03-01T09:00:00">
                <testsuite name="S">
                    <testcase name="t" time="0.1"/>
                </testsuite>
            </testsuites>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.timestamp.year == 2025
        assert report.timestamp.month == 3

    def test_naive_timestamp_gets_utc(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T12:00:00">
                <testcase name="t" time="0.1"/>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.timestamp.tzinfo is not None


# ---------------------------------------------------------------------------
# Report aggregate properties
# ---------------------------------------------------------------------------


class TestReportProperties:
    def test_pass_rate(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="t1" time="0.1"/>
                <testcase name="t2" time="0.1"/>
                <testcase name="t3" time="0.1">
                    <failure message="x"/>
                </testcase>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.pass_rate == pytest.approx(2 / 3)

    def test_fail_rate_includes_errors(self, parser, tmp_path):
        xml = """
            <testsuite name="S" timestamp="2024-01-01T00:00:00">
                <testcase name="t1" time="0.1">
                    <failure message="f"/>
                </testcase>
                <testcase name="t2" time="0.1">
                    <error message="e"/>
                </testcase>
                <testcase name="t3" time="0.1"/>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.fail_rate == pytest.approx(2 / 3)

    def test_empty_suite_rates_are_zero(self, parser, tmp_path):
        xml = """<testsuite name="S" timestamp="2024-01-01T00:00:00"/>"""
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.pass_rate == 0.0
        assert report.fail_rate == 0.0
        assert report.total == 0

    def test_name_falls_back_to_filename(self, parser, tmp_path):
        xml = """
            <testsuite timestamp="2024-01-01T00:00:00">
                <testcase name="t" time="0.1"/>
            </testsuite>
        """
        report = parser.parse(write_xml(tmp_path, xml, "my_report.xml"), project="p")
        assert report.name == "my_report"


# ---------------------------------------------------------------------------
# Multi-suite: suite name propagated to each TestResult
# ---------------------------------------------------------------------------


class TestMultiSuite:
    def test_suite_name_on_each_test_result(self, parser, tmp_path):
        xml = """
            <testsuites timestamp="2024-01-01T00:00:00">
                <testsuite name="Alpha">
                    <testcase name="t1" time="0.1"/>
                </testsuite>
                <testsuite name="Beta">
                    <testcase name="t2" time="0.2"/>
                </testsuite>
            </testsuites>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        suite_names = {t.suite for t in report.tests}
        assert suite_names == {"Alpha", "Beta"}

    def test_duration_summed_across_suites(self, parser, tmp_path):
        xml = """
            <testsuites timestamp="2024-01-01T00:00:00">
                <testsuite name="A">
                    <testcase name="t1" time="1.0"/>
                </testsuite>
                <testsuite name="B">
                    <testcase name="t2" time="2.0"/>
                </testsuite>
            </testsuites>
        """
        report = parser.parse(write_xml(tmp_path, xml), project="p")
        assert report.duration == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_invalid_xml_raises_value_error(self, parser, tmp_path):
        path = write_xml(tmp_path, "<not valid xml<<>>")
        with pytest.raises(ValueError, match="Invalid XML"):
            parser.parse(path, project="p")

    def test_wrong_root_element_raises_value_error(self, parser, tmp_path):
        xml = """<root><foo/></root>"""
        with pytest.raises(ValueError, match="Unexpected root element"):
            parser.parse(write_xml(tmp_path, xml), project="p")

    def test_nonexistent_file_raises(self, parser, tmp_path):
        with pytest.raises(Exception):
            parser.parse(tmp_path / "missing.xml", project="p")
