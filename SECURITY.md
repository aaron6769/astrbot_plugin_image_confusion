# Security Policy

## Supported versions

Security fixes are applied to the latest version on the `main` branch.

## Reporting a vulnerability

Please use GitHub's private **Report a vulnerability** flow on this repository when it is available. If private reporting is unavailable, contact the maintainer privately before opening a public issue.

Do not include bot tokens, cookies, API keys, private images, or production configuration in a report. A useful report should contain the affected version, a minimal reproduction using non-sensitive test data, the expected impact, and any suggested mitigation.

## Security boundaries

This plugin performs reversible pixel shuffling; it is not encryption. Generated images must not be treated as cryptographically protected data.
