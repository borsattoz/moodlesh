# CLAUDE.md

Context for working on moodlesh. The README covers usage and architecture; this
file records the decisions behind the code and the conventions to keep.

## What this is

moodlesh is a Python library plus an interactive CLI with a small DSL for editing
the structure of Moodle courses: list courses, navigate them like folders, show a
`tree`, add sections and subsections at a given position, rename them. Scripts of
commands can be run with `moodlesh run`, on one course or many (`--course`,
`--category`, `--match`), with `--dry-run` and a local change journal.

Not to be confused with moosh (tmuras/moosh), the server-side Moodle shell:
moodlesh runs on the user's computer and talks to Moodle over the web as the
user. The README has a short comparison.

This is the **start** of the project: many more features are planned beyond
the current commands. Design for growth: new commands, new kinds of course items
(activities, resources), new operations (move, delete, copy, bulk changes across
courses) and possibly new backends should fit without reshaping what exists.
Prefer extension points over special cases.

### Who it is for

moodlesh targets **site administrators, category managers and users with the
Manager role** (at system, category or course level). It relies on their
capabilities, for example `moodle/course:view` for `core_course_get_courses`
(used to read the course format when renaming), which by default only managers
have. Teachers and students are out of scope for now; supporting them would
need changes to how the course format is read and to what `ls` lists.

Operations a user is not allowed to do (e.g. a category manager touching a
course in another category) must fail cleanly, never leaving a half-done change
behind. **Preflight rule**: every check an operation needs to complete (course
editable, course format readable for the rename that follows a creation, name
not empty) happens *before its first write* (`MoodleBackend.check_writable`).
This was learned the hard way: an editing teacher's `mkdir` created a section
and then failed to rename it.

The author uses a Moodle 4.5 instance where he is a category manager.

### Supported Moodle versions

**Moodle 4.5 and later** (checked against the source of 4.5, 5.0, 5.1, 5.2 and
6.0dev). 4.5 is the floor because subsections (`mod_subsection`, delegated
sections, `delegatesectionid` in the course state) and
`core_courseformat_create_module` first appear there. The other functions used
exist since 4.0, so a 4.0–4.4 site could be supported later for sections only,
by turning subsection features off.

- `core_courseformat_new_module` is used first; on early 4.5 releases that lack
  it, the code falls back to `core_courseformat_create_module`, which is
  deprecated since 5.0 and may be removed in 6.0. The fallback is harmless
  there because `new_module` always exists.
- From 5.1 Moodle keeps its code under `public/`: irrelevant to us (we only use
  the web API), but remember it when reading the Moodle source.
- `section_add` inserts after `targetsectionid` in every version checked.
- Verified on real sites with 4.5.15 and 5.1.8 (integration suite plus the
  shell with both browser and http transports). 5.0 is expected to work;
  5.2+ is untested (5.3 needs `composer install`, which the dev environment
  could not reach).

## Commands

```bash
uv sync
uv run pytest                                   # all tests, no network needed
uv run ruff check . && uv run ruff format --check .
uv run mypy                                     # strict mode
uv run moodlesh --demo                          # try the shell on in-memory sample data
uv run moodlesh --url https://moodle.example.org -vv    # real instance, debug logs
uv run moodlesh --demo -n                       # dry run on the demo data
```

All four checks (pytest, ruff check, ruff format, mypy strict) must pass before
a change is done; CI (`.github/workflows/ci.yml`) runs them on Python
3.11–3.13.

Integration tests (`tests/integration`) run only when `MOODLESH_IT_URL` is set
and they **write** to the site: use a disposable instance seeded with
`php integration/seed_site.php /path/to/moodle` (users `catmanager` /
`Manager123!`, manager of category Science; `teacher` / `Teacher123!`,
editing teacher of PHY1). Variables: `MOODLESH_IT_USER`,
`MOODLESH_IT_PASSWORD`, `MOODLESH_IT_TEACHER`, `MOODLESH_IT_TEACHER_PASSWORD`.

## Real-world constraints (learned the hard way on the test site)

These are typical of real deployments: assume any of them can occur.

- **Web services may be unavailable**: enabling them, the REST protocol,
  external services and tokens are site-admin settings, so a category manager
  usually has no token. The REST transport is for users who do.
- **Cloudflare bot protection** sits in front of the site (on the author's
  instance). Any non-browser HTTP
  client (httpx, even Playwright's `context.request`) gets `403` with
  `Cf-Mitigated: challenge` and a "Security Check" page, even with
  valid session cookies. Do **not** try to bypass it (TLS fingerprint spoofing,
  copying `cf_clearance` into httpx, headless tricks). The supported approach is
  the browser transport: calls are made with `fetch` from inside a real,
  visible Chromium window, and the human passes any check in that window.
- **Login is Shibboleth SSO** with an external identity provider. The flow
  goes `/login/index.php` → `/shibboleth-ds/...` → `/Shibboleth.sso/Login` →
  external IdP → back to Moodle. Pages under `/login/`, `/auth/`,
  `/Shibboleth.sso/`, `/shibboleth-ds/` are on the Moodle host but do **not**
  mean the user is logged in (see `_AUTH_PATH_PREFIXES` in `moodle/auth.py`).
- A page is "authenticated Moodle" when `M.cfg.sesskey` exists and
  `M.cfg.userId` is not `0` (0 = guest).

### Moodle error codes (verified on 4.5)

- `servicerequireslogin`, `invalidsesskey`: the session is not valid →
  `AuthenticationError` (the CLI suggests `moodlesh login`). Global errors
  carry their text in the `error` key; `invalidsesskey` arrives as a list.
- `requireloginerror` *inside* a function: the course is not accessible to the
  user. It is **not** an authentication error.
- `invalidrecord` with "external_functions" in the message: the function does
  not exist on this Moodle version. Only this triggers the
  `new_module` → `create_module` fallback.
- `nopermissions`: also raised by moodlesh itself in `check_writable`.

## Architecture decisions

- `core/` is the domain (immutable dataclasses, `CourseBackend` protocol,
  positioning rules, exceptions) and depends on nothing else in the package.
- `moodle/` implements `CourseBackend` once (`MoodleBackend`), on top of an
  interchangeable `Transport`:
  - `BrowserTransport` (default): Playwright in a **dedicated thread**, because
    sync Playwright cannot share the main thread with prompt_toolkit's asyncio
    loop. `call()` queues a job and waits on a `Future`.
  - `AjaxSessionTransport`: same AJAX endpoint via httpx (sites without bot
    protection).
  - `RestTransport`: web services with a token.
- AJAX request/response handling is shared (`ajax_request_body`,
  `decode_ajax_response`) so all AJAX transports behave identically.
- **Never retry an error returned by Moodle** (it may come from a write). Only
  retry transport-level problems: invalid sesskey, a non-JSON response (login
  page or bot check), or a Playwright error (`_Config.page_error`, Playwright's
  `Error` class) — once. Any other exception is a bug and propagates.
- Safety layers are `CourseBackend` decorators, applied in `cli/main.py`:
  `DryRunBackend` (reads from the real backend, copies each course into an
  `InMemoryBackend` with negative ids, simulates writes there, and calls the
  real `check_writable` as preflight) or `JournalingBackend` (appends one JSONL
  entry per write, success or failure, file mode 600; forwards other
  attributes such as `check_writable`). `--demo` gets dry-run only, no journal.
- Bulk runs (`cli/bulk.py`): the script runs once per course with an
  `Interpreter` whose start location is that course; stop at the first failing
  course unless `--keep-going`; confirmation needed unless `--dry-run` or
  `--yes`, and refused (exit 2) without a TTY.
- `MoodleBackend(editable_only=True)` lists courses via
  `core_course_search_courses` with `requiredcapabilities=["moodle/course:update"]`;
  `--all-courses` turns it off.
- Course structure comes from one call, `core_courseformat_get_state`, which
  includes delegated sections (subsections). Subsections are `mod_subsection`
  modules whose `delegatesectionid` points to a section with
  `component == "mod_subsection"`.
- Moodle functions used (all AJAX-enabled in 4.5, verified against the
  MOODLE_405_STABLE source): `core_course_search_courses`,
  `core_course_get_courses`, `core_courseformat_get_state`,
  `core_courseformat_update_course` (action `section_add`, inserts **after**
  `targetsectionid`), `core_courseformat_new_module` (falls back to
  `core_courseformat_create_module`, which takes a section *number*),
  `core_update_inplace_editable` (`component=format_<format>`,
  `itemtype=sectionname`; renaming the delegated section also renames the
  subsection module).
- Moodle does not allow subsections inside subsections; this is enforced before
  calling Moodle (`item_insertion_before`).
- Positions are 1-based and match what `ls` shows in the `#` column. Section 0
  never moves. `None` means "append".
- The session (Playwright cookie format) is stored in
  `~/.local/state/moodlesh/session-<host>.json`, created with mode 600.

## DSL design

Three layers; keep them separate:

1. `cli/dsl/grammar.lark` only recognises shape: commands, values (words or
   quoted strings), options (`-x`, `--name`, `--name=value`), `;`/newlines,
   `#` comments. Adding a command must not require touching the grammar.
2. `cli/commands.py`: each command is a frozen dataclass with a `CommandSpec`.
   Validation (`bind`), `help` and tab completion are all derived from the spec.
3. `cli/interpreter.py` executes; it never prints, it returns `rich` renderables.

To add a command: dataclass + `CommandSpec` in `commands.py`, add it to
`COMMANDS`, add a `case` in `Interpreter.execute`, add tests.

Scripts are fully validated (`compile_script`) before the first command runs;
execution stops at the first runtime error and reports the line.

## Conventions

- Everything is in English: code, comments, messages, docs, tests, demo data.
- Python 3.11+, `from __future__ import annotations`, full type hints, mypy strict.
- Line length 110 (ruff). Quote names in messages with single quotes:
  `created section 2 'Optics'`.
- User-facing errors are `MoodleshError` subclasses; the CLI maps them to exit
  codes (`EXIT_USAGE = 2` for syntax/usage errors, `EXIT_ERROR = 1` otherwise).
- Tests never touch the network. `tests/fakes.py` is a fake Moodle that answers
  the real function names with the real 4.5 data formats; `tests/test_browser.py`
  tests the browser logic with a fake page. Demo data (`memory.demo_backend`) is
  used by the tests: changing it means updating the tests.

## Status and open points

- Version 0.1.0a1 (alpha). See `CHANGELOG.md`.
- Verified end to end on local Moodle 4.5.15 and 5.1.8 sites (browser and http
  transports, dry run, journal, bulk, logout, teacher without manager rights).
  On the author's production instance the SSO login in the browser works and
  plain httpx requests are blocked by Cloudflare; the full browser flow there
  is still to be confirmed by the author.
- `logout` ends the server session with a plain HTTP request: on sites behind
  bot protection this fails silently and only the local file is deleted.
- Roadmap (README): move and delete, undo from the journal, activities and
  resources, dates and visibility in bulk, copy/diff between courses, search
  across courses, export/import of outlines. Keep the README's "Available
  today" / "On the roadmap" lists and `CHANGELOG.md` (`[Unreleased]`) in sync
  with what is actually implemented: never advertise a feature that is not there.
- Known limitation: `rename` and the rename that follows `mkdir` need
  `moodle/course:view` (to read the course format), so editing teachers
  without the Manager role are refused up front.

## Working with the maintainer

- The maintainer is Italian: talk to him in Italian. Everything in the
  repository (code, docs, commit messages, issues) stays in English.
- Most of the code was generated with an AI assistant and has **not yet been
  fully reviewed** by the maintainer; the README says so ("How moodlesh was
  built"). Keep that note accurate as the review progresses. When he asks,
  walk him through a module explaining the *why*, not just the *what*.
- He commits under his own name. AI-assisted commits carry a
  `Co-Authored-By:` trailer.
- Never put details of his real Moodle instance (URL, identity provider,
  institution) in the repository: say only "a Moodle 4.5 site with SSO and
  Cloudflare".
- Before changing code that writes to Moodle, prefer proving it with the fake
  (`tests/fakes.py`) first, then on a disposable site; suggest `--dry-run`
  before any real run on his instance.

## Handoff: where things stand (October 2026)

- The git history was deliberately reset: the next steps are `git init`, one
  initial commit ("Initial public alpha (0.1.0a1)"), creating the public
  repository `borsattoz/moodlesh` with `gh`, setting it as `origin`, pushing.
- `.github/workflows/ci.yml` may still need to be put in place by hand (the
  previous tooling could not write to `.github/`): check it exists before the
  first push.
- Proposed workflow: `main` always green and protected (pull requests only,
  CI required); one branch and one pull request per change
  (`feat/...`, `fix/...`, `docs/...`); the maintainer reviews and merges.
- The Moodle 4.5.15 and 5.1.8 test sites used so far no longer exist. To run
  `tests/integration` again, set up a disposable site (e.g. `moodlehq/moodle-docker`),
  then `php integration/seed_site.php /path/to/moodle`. Moodle 5.2+ needs
  `composer install` before installing; that would also let 5.2/5.3 move to
  "tested" in the README.
- Still to confirm on the maintainer's real site (browser transport): `ls`,
  `tree`, then `-n` (dry run), then real writes on a test course, then a bulk
  dry run.
- Candidate next features, in a sensible order: move sections/subsections,
  delete empty ones, undo from the journal; then activities.
