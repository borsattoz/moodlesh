# CLAUDE.md

Project instructions for moodlesh, shared with every contributor. Personal
preferences go in `CLAUDE.local.md` (git-ignored). Current work and open
problems live in GitHub issues, not here.

## What this is

A Python library plus an interactive CLI with a small DSL for scripting changes
to the structure of Moodle courses: browse courses like folders, `tree`, add
sections and subsections at a position, rename them; run scripts on one course
or many (`--course`, `--category`, `--match`), with `--dry-run` and a local
change journal. It runs on the user's computer and talks to Moodle over the web
as the user (unlike moosh, which runs on the server).

Audience: site administrators, category managers and Managers. Teachers are
out of scope: renaming needs `moodle/course:view` (to read the course format),
which by default only managers have.

This is the start of the project. Design for growth (move, delete, undo,
activities, dates, copy/diff, new backends): prefer extension points over
special cases.

## Rules that must not be broken

- **Preflight**: every check an operation needs (course editable, format
  readable for the rename after a creation, non-empty name) runs *before its
  first write* (`MoodleBackend.check_writable`). A permission problem must
  never leave a half-made change. (Learned on a real site: a teacher's `mkdir`
  created a section, then failed to rename it.)
- **Never retry an error returned by Moodle**: it may come from a write. Only
  transport problems are retried, once: invalid sesskey, a non-JSON response
  (login page or bot check), a Playwright error (`_Config.page_error`).
- **Never bypass bot protection** (no TLS spoofing, no copying `cf_clearance`
  into httpx, no headless tricks). The browser transport sends requests with
  `fetch` from a real, visible Chromium window; the user passes any check.
- **Every write goes through both safety layers**: `CourseBackend` is a
  Protocol whose methods are all `@abstractmethod`, and every backend
  (`MoodleBackend`, `InMemoryBackend`, `DryRunBackend`, `JournalingBackend`)
  inherits it explicitly, so a forgotten operation fails mypy and cannot be
  instantiated. Never add a catch-all `__getattr__` to a backend: it hides
  missing methods from mypy. External backends may still conform without
  inheriting.
- **No details of real Moodle instances** (URLs, identity providers,
  institutions) in the repository. Say "a Moodle 4.5 site with SSO and
  Cloudflare".
- Session files and the journal are created with mode 600; the session is as
  good as a password. Do not log cookies, tokens or sesskeys (`httpx` and
  `httpcore` loggers stay at WARNING because URLs contain the sesskey).

## Commands

```bash
uv sync
uv run pytest                                    # unit tests, no network
uv run ruff check . && uv run ruff format --check .
uv run mypy                                      # strict
uv run moodlesh --demo                           # shell on in-memory sample data
uv run moodlesh --demo -n                        # dry run on the sample data
```

All four checks must pass; CI runs them on Python 3.11–3.13.

`tests/integration` runs only with `MOODLESH_IT_URL` set and **writes** to the
site: use a disposable one (e.g. `moodlehq/moodle-docker`) seeded with
`php integration/seed_site.php /path/to/moodle` (users `catmanager` /
`Manager123!`, manager of category Science; `teacher` / `Teacher123!`, editing
teacher of PHY1). Moodle 5.2+ needs `composer install` before installing.

## Moodle versions

Supported: **4.5 and later** (subsections, `mod_subsection`, appeared in 4.5;
4.0–4.4 could be supported later for sections only).
Tested on real sites: 4.5.15 and 5.1.8. Expected: 5.0. Untested: 5.2+.

- `core_courseformat_new_module` first; fall back to
  `core_courseformat_create_module` (deprecated in 5.0) **only** when the
  function is missing (`invalidrecord` mentioning `external_functions`).
- `section_add` inserts **after** `targetsectionid`.
- From 5.1 Moodle's code lives under `public/` (relevant only when reading the
  Moodle source).

## Real-world constraints

- Web services and tokens are often unavailable to non-admins; the default
  transport uses the AJAX endpoint with the browser session.
- Sites may sit behind Cloudflare: any non-browser client gets `403` with
  `Cf-Mitigated: challenge`, even with valid cookies.
- SSO (e.g. Shibboleth) goes through pages on the Moodle host that do **not**
  mean the user is logged in: `/login/`, `/auth/`, `/Shibboleth.sso/`,
  `/shibboleth-ds/`, `/simplesaml/`, `/saml/` (`_AUTH_PATH_PREFIXES` in
  `moodle/auth.py`).
- A page is authenticated Moodle when `M.cfg.sesskey` exists and
  `M.cfg.userId` is not `0` (guest).
- `logout` ends the server session with a plain HTTP request: behind bot
  protection this fails silently and only the local session file is deleted.

Moodle error codes (verified on 4.5):

- `servicerequireslogin`, `invalidsesskey`: session not valid →
  `AuthenticationError`. Global errors carry their text in `error`;
  `invalidsesskey` arrives as a list.
- `requireloginerror` inside a function: course not accessible. **Not** an
  authentication error.
- `nopermissions`: also raised by moodlesh in `check_writable`.

## Architecture

- `core/`: immutable dataclasses, the `CourseBackend` protocol, positioning
  rules, exceptions. Depends on nothing else in the package.
- `moodle/`: one `MoodleBackend` over interchangeable transports.
  `BrowserTransport` (default) runs Playwright in a **dedicated thread**, since
  sync Playwright cannot share the main thread with prompt_toolkit's asyncio
  loop; `call()` queues a job and waits on a `Future`. `AjaxSessionTransport`
  (httpx) and `RestTransport` (token) share request/response handling
  (`ajax_request_body`, `decode_ajax_response`).
- Structure comes from one call, `core_courseformat_get_state`. Subsections
  are `mod_subsection` modules whose `delegatesectionid` points to a section
  with `component == "mod_subsection"`. Renaming uses
  `core_update_inplace_editable` (`component=format_<format>`,
  `itemtype=sectionname`).
- `MoodleBackend(editable_only=True)` lists courses with
  `core_course_search_courses` and `requiredcapabilities=["moodle/course:update"]`;
  `--all-courses` turns it off.
- Safety layers are `CourseBackend` decorators applied in `cli/main.py`:
  `DryRunBackend` (copies each course into an `InMemoryBackend` with negative
  ids, simulates writes, calls the real `check_writable`) or
  `JournalingBackend` (one JSONL line per write, success or failure). `--demo`
  gets dry-run only. An operation that stops after its first write raises
  `PartialChangeError` (with the created section, if known), which the journal
  records as `"partial": true`.
- `cli/bulk.py`: one `Interpreter` per course, starting at that course; stop
  at the first failing course unless `--keep-going`; confirmation unless
  `--dry-run` or `--yes`; refused without a TTY (exit 2).
- Positions are 1-based, as in the `#` column of `ls`. Section 0 never moves.
  `None` means "append". No subsections inside subsections (checked before
  calling Moodle, `item_insertion_before`).

## DSL

Three layers, kept separate:

1. `cli/dsl/grammar.lark` recognises only shape (commands, words or quoted
   strings, options, `;`/newlines, `#` comments). New commands never touch it.
2. `cli/commands.py`: each command is a frozen dataclass with a `CommandSpec`,
   from which validation, `help` and Tab completion are derived.
3. `cli/interpreter.py` executes and returns `rich` renderables; it never
   prints.

To add a command: dataclass + `CommandSpec`, add it to `COMMANDS`, add a `case`
in `Interpreter.execute`, add tests. Scripts are validated in full
(`compile_script`) before the first command runs.

## Conventions

- Everything in the repository is in English.
- Python 3.11+, `from __future__ import annotations`, full type hints, mypy
  strict, ruff with line length 110.
- Messages quote names with single quotes: `created section 2 'Optics'`.
- User-facing errors are `MoodleshError` subclasses; exit codes: 0 ok,
  1 runtime error, 2 usage or syntax error.
- Tests never touch the network. `tests/fakes.py` answers the real function
  names with the real 4.5 formats; `tests/test_browser.py` uses a fake page.
  Demo data (`memory.demo_backend`) is used by tests: changing it means
  updating them.

## Documentation

All user documentation is in `README.md`, by the maintainer's choice. Style:
plain, friendly, factual, second person. No emoji, no marketing phrasing,
only functional badges (CI, license). Engage with a concrete problem and its
solution; be candid about limits. Examples must be real output from
`moodlesh --demo`. One GitHub alert at most (the "Use at your own risk"
warning).

README order: title, badges, one-line definition, short paragraph, status line,
warning → "What it looks like" (the "Exam preparation" script: templates and
course copies help when a course is created, moodlesh when courses are already
running) → "Try it in a minute" → "Is moodlesh for you?" → "Features" →
"Contents" → reference → "How moodlesh is built" → "Development" → "License".

Positioning: emphasise **scripting and automation** rather than bulk editing.
Keep the repository description, the README opening line and the topics
consistent.

Keep "Features" (available now / planned), the reference sections and
`CHANGELOG.md` (`[Unreleased]`) in sync with the code: never advertise a
feature that does not exist.

## Transparency and review

Most of the code was generated with an AI assistant and is being reviewed by
the maintainer, file by file, in public: issue #3 tracks it (each file ticked
with the commit reviewed), problems found are issues labelled
`review-finding`, and the stable 0.1.0 (milestone `0.1.0`) comes out when the
review is complete. The README states this in "How moodlesh is built": keep
that section accurate.

- Changes reach `main` only through pull requests the maintainer reviews
  (branches `feat/...`, `fix/...`, `docs/...`; squash merge).
- Commits written with AI help end with a `Co-Authored-By:` trailer; say in
  the pull request description what was asked of the assistant.
- Prefer proving changes that write to Moodle with the fake first, then on a
  disposable site; suggest `--dry-run` before any real run.
