# Changelog

All notable changes to this project are documented in this file.

## 0.1.4 - 2026-08-12

- Bound input files to 25 MiB and decoded images to 12 million pixels before allocating the Gilbert curve and output buffer.
- Reject unsupported transformation modes and remove partial output files after failed writes.
- Separate the image transformation core from the AstrBot integration for isolated testing.
- Add round-trip, input-limit, mode-validation, and Gilbert-curve regression tests.
- Add GitHub Actions CI, Dependabot configuration, minimum AstrBot version metadata, and a security policy.

## 0.1.3 - 2026-06-10

- Initial public release with reversible Gilbert Curve image shuffling and timed output cleanup.
