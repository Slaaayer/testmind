# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-03-08

### Changed
- Updated README with PyPI installation instructions (`uv tool install`, `pip install`)
- Added Contributing section for development setup

## [0.1.0] - 2026-03-08

### Added
- JUnit XML parser with support for bulk ingestion of historical reports
- SQLite-backed storage with SHA-256 deduplication
- Flaky test detection (flip rate + fail rate thresholds)
- Regression detection (stable reference window vs recent failures)
- Suite-wide failure spike detection (z-score baseline comparison)
- Stability index (0–100 composite score per test)
- Failure prediction with trend classification (degrading / stable / improving)
- CLI commands: `ingest`, `analyze`, `projects`, `history`
- Text and JSON output formats
- Python library API — all components importable and composable independently

[Unreleased]: https://github.com/Slaaayer/testmind/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/Slaaayer/testmind/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Slaaayer/testmind/releases/tag/v0.1.0
