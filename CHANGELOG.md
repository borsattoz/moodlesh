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

- The behaviour every transport must follow (which errors it raises, and
  that a call is never retried after an error returned by Moodle) is written
  in `Transport` and checked by the same tests for the AJAX, REST and browser
  transports. The REST transport now reports a page that does not come from
  Moodle (such as a bot check) as such, instead of failing with a generic
  error or calling it "web services disabled".
- REST's "Access control exception" is no longer presented as a login
  problem: Moodle uses it for many site settings (token expired, service or
  function not enabled, missing capability, IP restriction), so moodlesh lists
  the possible causes, suggests asking the site administrator, and shows
  Moodle's reason when the site sends it.

- `--transport rest` selects the web service transport explicitly. A token
  still selects it by itself, but a token given with `--transport browser` or
  `http` is now an error instead of being silently preferred, and `rest`
  without a token says what is missing.

- The browser transport runs Playwright's async API on its own asyncio loop
  (still in a dedicated thread, so nothing changes for the rest of moodlesh).

### Security

- `moodlesh login --password` (and `login_with_password` in the library)
  refuses to send a password over an unencrypted `http://` connection, except
  to local test sites (`localhost`, `127.0.0.1`, `::1`). The check happens
  before the password is asked for.
- The same applies to web service tokens: the REST transport refuses to send a
  token over `http://`, except to local test sites.
- `moodlesh login --password` checks that Moodle recognises the session
  before saving it, instead of guessing from where it was redirected. A
  session left half-way by multi-factor authentication is no longer saved,
  and failures say what happened: wrong credentials, a bot check, MFA, a
  required password change, or an unreachable site.

### Compatibility

- Tested on Moodle 4.5.15 and 5.1.8. Moodle 4.4 and older are not supported.
