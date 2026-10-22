# Changelog

All notable changes to moodlesh are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/) (while in 0.x, any release may
change behaviour).

## [Unreleased]

### Fixed

- Creating a section or subsection while someone else changes the same course
  no longer fails after the creation with an unclear error: the new item is
  recognised by where it was inserted, and if that is not possible moodlesh
  stops with a message saying that the item was created but not named.
- The change journal no longer reports a failed operation when part of it was
  written (for example a section created but not named): such entries now have
  `"partial": true` and describe what exists in Moodle. Library users get a
  `PartialChangeError` carrying the created section.

## [0.1.0a1] - first public alpha

### Added

- Interactive shell with a filesystem-like DSL: `ls`, `cd`, `pwd`, `tree`,
  `mkdir` (sections and subsections, `--at` positions), `rename`/`mv`,
  `refresh`, `help`, `exit`; Tab completion and persistent history.
- Scripts: `moodlesh run FILE` (or `-` for stdin), `moodlesh exec "..."`,
  `run --check` to validate without connecting. Scripts are validated in full
  before the first command runs.
- Bulk runs: `--course`, `--category`, `--match` run a script once per selected
  course, with confirmation (`--yes`), `--keep-going` and a summary.
- `--dry-run` / `-n`: simulate every change on a copy of the real courses,
  with the same permission checks as a real run.
- Change journal: every write, successful or not, is appended to
  `changes.jsonl` in the state directory (or `$MOODLESH_JOURNAL`).
- Transports: browser (default; Playwright, works with SSO and security
  checks passed by the user), HTTP with a saved session, REST with a token.
- `moodlesh login` (browser, or `--password` for manual accounts) and
  `moodlesh logout` (ends the session on Moodle when possible).
- Only courses the user can edit are listed by default (`--all-courses` to
  list every visible course).
- Preflight checks before any write, so permission problems never leave a
  half-made change.
- Python library: `CourseBackend` protocol, `MoodleBackend`,
  `InMemoryBackend`, `DryRunBackend`, `JournalingBackend`.
- Integration test suite for real, disposable Moodle sites
  (`tests/integration`, `integration/seed_site.php`).

### Compatibility

- Tested on Moodle 4.5.15 and 5.1.8. Moodle 4.4 and older are not supported.
