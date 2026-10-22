# Changelog

All notable changes to moodlesh are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/) (while in 0.x, any release may
change behaviour).

## [Unreleased]

Nothing has been released yet. This section describes what the first release,
0.1.0, will contain.

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
- Robust handling of operations that stop half-way: a new section or
  subsection is recognised by where it was inserted, even if someone else
  changes the course at the same time; if an operation stops after it has
  already changed Moodle, moodlesh says so and the journal records it as
  `"partial": true` (`PartialChangeError` in the library).
- An enforceable backend contract: every method of `CourseBackend` is
  abstract and every backend inherits it explicitly, so a safety layer that
  forgets an operation fails type checking and cannot be created.

### Compatibility

- Tested on Moodle 4.5.15 and 5.1.8. Moodle 4.4 and older are not supported.
