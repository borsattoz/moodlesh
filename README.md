# moodlesh

[![CI](https://github.com/borsattoz/moodlesh/actions/workflows/ci.yml/badge.svg)](https://github.com/borsattoz/moodlesh/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Edit the structure of many Moodle courses at once, from the command line.

moodlesh lets you browse Moodle courses like folders, change them with short
commands, and apply the same change to one course or to a whole category. It
works through the Moodle website with your own account, single sign-on
included: no server access, plugins or web services are needed.

> **Status:** early alpha (0.1.0a1), tested on Moodle 4.5 and 5.1. Most of the
> code was written with an AI assistant and is still being reviewed: see
> [How moodlesh is built](#how-moodlesh-is-built). Use `--dry-run` or a test
> course before making real changes.

## What it looks like

Course templates and copies help when a course is created. moodlesh helps when
courses are already running. For example, to add an "Exam preparation" section,
with two subsections, to every course you manage in a category, write a short
script:

```text
mkdir "Exam preparation" --at 1
mkdir "Past papers" --into "Exam preparation"
mkdir "Mock exams" --into "Exam preparation"
```

Check what it would do, then apply it. Existing content is not touched.

```bash
moodlesh --dry-run run exam-prep.mdl --category Science
moodlesh run exam-prep.mdl --category Science
```

For everyday work there is an interactive shell, with Tab completion:

```text
moodle:/> cd PHY1
moodle:/PHY1> ls
#  section     contents
0  General     1 activity
1  Kinematics  1 subsection, 2 activities
2  Dynamics    1 subsection, 1 activity
moodle:/PHY1> ls 1
#  type        name                 contents
1  resource    Linear motion notes
2  subsection  Exercises/           2 activities
3  assign      Lab report
moodle:/PHY1> mkdir "Optics" --at 2
created section 2 'Optics'
```

## Try it in a minute

You need Python 3.11 or later. Install moodlesh and the browser it uses:

```bash
uv tool install git+https://github.com/borsattoz/moodlesh.git
"$(uv tool dir)/moodlesh/bin/playwright" install chromium
```

Then explore some sample courses, with no Moodle site involved:

```bash
moodlesh --demo
```

Try `ls`, `cd PHY1`, `tree`, `mkdir "Optics" --at 2` and `help`. When you are
ready, connect to your site:

```bash
moodlesh --url https://moodle.example.org
```

## Is moodlesh for you?

Use it if:

- you are a site administrator, category manager or Manager who looks after
  many courses;
- you repeat the same changes across courses, or want them written down as a
  script you can check and run again;
- you cannot get web services or server access, but can use Moodle in a
  browser.

Look elsewhere if:

- you edit one course now and then: the Moodle web interface is simpler;
- you never use a terminal: moodlesh has no graphical interface (yet);
- you need to manage activities, dates or users: these are planned, not
  available today;
- you have shell access to the Moodle server and need administration beyond
  course structure: [moosh](https://github.com/tmuras/moosh) covers far more.

## Features

Available now:

- Browse courses, sections and subsections with `ls`, `cd`, `pwd` and `tree`.
- Create and rename sections and subsections at a given position.
- Scripts, checked in full before anything is changed.
- One script on many courses, selected by name, category or text match.
- Dry runs that show what would change, and a local journal of every change.
- Login through the browser, including single sign-on.
- A typed Python library underneath the command line.

Planned: moving and deleting sections, undo from the journal, managing
activities and resources, bulk changes to dates and visibility, copying and
comparing course structures, export and import of course outlines. Tell us
what you need in the [issue tracker](https://github.com/borsattoz/moodlesh/issues):
it decides what comes next.

## Contents

- [Installation](#installation)
- [Getting started](#getting-started)
- [Commands](#commands)
- [Paths and positions](#paths-and-positions)
- [Scripts](#scripts)
- [Running a script on several courses](#running-a-script-on-several-courses)
- [Dry runs and the change journal](#dry-runs-and-the-change-journal)
- [Ways to connect](#ways-to-connect)
- [Options and environment variables](#options-and-environment-variables)
- [Using moodlesh from Python](#using-moodlesh-from-python)
- [Safety and privacy](#safety-and-privacy)
- [Compatibility](#compatibility)
- [Relation to moosh](#relation-to-moosh)
- [How moodlesh is built](#how-moodlesh-is-built)
- [Development](#development)

## Installation

With [uv](https://docs.astral.sh/uv/), as shown above, or with pip in a
virtual environment:

```bash
pip install git+https://github.com/borsattoz/moodlesh.git
playwright install chromium
```

## Getting started

```bash
export MOODLESH_URL=https://moodle.example.org
moodlesh
```

A browser window opens on your Moodle site. Log in as usual: moodlesh detects
when you are logged in, and the shell starts. Keep the window open while you
work, because requests to Moodle are sent from it. The session is saved, so
later starts usually need no login.

moodlesh does not bypass any login or security check. If the site shows one,
you complete it yourself in the window.

Other ways to start moodlesh:

```text
moodlesh [options] run FILE [targets]   run a script (- reads it from standard input)
moodlesh [options] exec "COMMANDS"      run commands given on the command line
moodlesh [options] login                log in and save the session
moodlesh [options] logout               end the session and delete it
```

## Commands

| Command | Description |
|---|---|
| `ls [PATH] [-f TEXT] [-d]` | List courses, the sections of a course, or the contents of a section. `-f` filters by name, `-d` shows only sections and subsections. |
| `cd [PATH]` | Change location. `cd` alone returns to the root. |
| `pwd` | Show the current location. |
| `tree [PATH] [-L N] [-d]` | Show the structure of a course or section. `-L` limits the depth, `-d` hides activities. |
| `mkdir NAME [--at N] [-i PATH]` | Create a section (inside a course) or a subsection (inside a section). `-i`/`--into` creates it somewhere else. |
| `rename PATH NAME` | Rename a section or subsection. Alias: `mv`. |
| `refresh` | Reload data from Moodle, for example after editing a course in the browser. |
| `help [COMMAND]` | List the commands, or describe one. |
| `exit` | Leave the shell. Alias: `quit`. |

In the shell, Tab completes commands, options and paths, and the command
history is kept between sessions. By default `ls` at the root lists only the
courses you can edit; `--all-courses` lists every course you can see.

## Paths and positions

Courses, sections and subsections are addressed like folders:

```text
/                  the root: your courses
/PHY1              a course, by short name, full name or numeric id
/PHY1/1            a section, by number (0 is the general section) or by name
/PHY1/1/2          a subsection, by its position in the section or by name
```

Each part of a path is either the number shown by `ls` in the `#` column or a
name. Names are case-insensitive and need quotes if they contain spaces.
Relative paths, `.` and `..` work as usual (`cd ../Dynamics`).

`--at N` is the position the new item will have, counted as `ls` counts. In a
course, `mkdir "Labs" --at 2` creates the section that becomes number 2
(section 0 never moves). In a section, `mkdir "Theory" --at 1` makes the new
subsection its first item. Without `--at`, the item is added at the end.
Moodle does not allow subsections inside a subsection; moodlesh reports this
before contacting Moodle.

## Scripts

A script is a text file with one command per line; commands can also be
separated by `;`. Lines starting with `#` are comments.

```bash
moodlesh run structure.mdl --check       # check the script without connecting to Moodle
moodlesh run structure.mdl               # run it
moodlesh exec 'cd /PHY1; tree -L 1'      # run commands given on the command line
```

The whole script is checked before the first command runs, and errors point
to the line and column:

```text
mkdir "Optics --at 2
      ^
syntax error: line 2, column 7: unclosed quotes
```

If a command fails while running (a missing permission, a course that does
not exist), execution stops at that line and the error is reported.

Exit codes: `0` success; `1` an error while running, or a cancelled run; `2` a
mistake in the script or in the options.

## Running a script on several courses

`run` and `exec` accept options that select courses. The script runs once per
course, starting from that course's root, so relative paths mean the same
thing in every course.

| Option | Selects |
|---|---|
| `-c`, `--course COURSE` | a course by id, short name or full name (repeatable) |
| `--category CATEGORY` | every course you can edit in the category, by id or name (repeatable) |
| `--match TEXT` | every course you can edit whose name contains TEXT |

`--category` and `--match` can be combined: a course must satisfy both.
Courses given with `--course` are always included.

moodlesh lists the selected courses and asks for confirmation. `-y`/`--yes`
skips the question, and is required when there is no terminal (for example in
a scheduled job). The run stops at the first course that fails and skips the
rest, unless `--keep-going` is given. A summary is printed at the end.

## Dry runs and the change journal

`--dry-run` (or `-n`) works with the shell, `run`, `exec` and runs on several
courses. moodlesh reads the real courses and checks your permissions as a real
run would, then simulates the changes, so later commands see the effect of
earlier ones. Nothing is written to Moodle.

```text
$ moodlesh -n exec 'cd PHY1; mkdir "Optics" --at 2; mkdir Lenses --into Optics'
dry run: changes are simulated, Moodle is not modified
would create section 2 'Optics'
would create subsection 'Lenses' in 'Optics'
dry run: nothing was changed
```

Every change sent to Moodle, successful or not, is appended to
`~/.local/state/moodlesh/changes.jsonl` (or to the file named by
`MOODLESH_JOURNAL`), one JSON object per line:

```json
{"time": "2026-10-20T15:45:31+02:00", "site": "https://moodle.example.org", "action": "add_section", "course": 3, "args": {"position": 1, "name": "Practice"}, "ok": true, "result": {"section_id": 14, "number": 1}}
```

## Ways to connect

| Transport | When to use it | How |
|---|---|---|
| `browser` (default) | Any site, including sites with single sign-on or security checks | `moodlesh` |
| `http` | Sites without security checks, if you prefer no browser window | `moodlesh login`, then `moodlesh --transport http` |
| REST | You have a web service token | `MOODLESH_TOKEN=... moodlesh` |

With the browser transport, requests are sent from a Chromium window, the same
way Moodle's own pages send them. `moodlesh login --password` logs in with a
Moodle username and password (manual accounts only), without a browser.
`moodlesh logout` ends the session on Moodle when the site allows it, and
always deletes the local copy.

## Options and environment variables

| Option | Variable | Meaning |
|---|---|---|
| `--url URL` | `MOODLESH_URL` | Moodle address |
| `--transport browser\|http` | `MOODLESH_TRANSPORT` | how to talk to Moodle |
| `--token TOKEN` | `MOODLESH_TOKEN` | web service token (prefer the variable: command-line arguments are visible to other local users) |
| `-n`, `--dry-run` | | simulate changes |
| `--all-courses` | | list every visible course, not only editable ones |
| `--demo` | | use sample data held in memory |
| `-v`, `-vv` | | more detailed logs |
| | `MOODLESH_BROWSER_CHANNEL` | `chrome` uses the installed Chrome instead of Chromium |
| | `MOODLESH_BROWSER_PATH` | path to a browser executable |
| | `MOODLESH_JOURNAL` | location of the change journal |

## Using moodlesh from Python

The command line is a thin layer over a typed library:

```python
from moodlesh.moodle import connect_with_browser

moodle, browser = connect_with_browser("https://moodle.example.org")
with browser:
    course = moodle.get_course(101)
    for section in course.sections:
        print(section.number, section.name, len(section.items))

    kinematics = course.section_by_number(1)
    if kinematics is not None:
        moodle.add_subsection(course.id, kinematics.id, position=1, name="Theory")
```

`connect_with_session` and `connect_with_token` give the same interface over
the `http` and REST transports. `moodlesh.dryrun.DryRunBackend` and
`moodlesh.journal.JournalingBackend` wrap any backend to simulate or record
changes.

## Safety and privacy

- Before writing anything, moodlesh checks that the whole operation can be
  completed, so a missing permission does not leave a partial change.
- The saved session (in `~/.local/state/moodlesh/`, permissions 600) gives the
  same access as your password while it is valid. `moodlesh logout` removes it.
- moodlesh talks only to the Moodle site you give it. There is no telemetry.

See [SECURITY.md](SECURITY.md) for details and for reporting vulnerabilities.

## Compatibility

| Moodle | Status |
|---|---|
| 4.5 | Tested (4.5.15) |
| 5.1 | Tested (5.1.8) |
| 5.0 | Expected to work, not tested |
| 5.2 and later | Not tested |
| 4.4 and older | Not supported (subsections were introduced in 4.5) |

Developed on Linux. macOS and Windows should work but have not been tested.

## Relation to moosh

[moosh](https://github.com/tmuras/moosh) is a command-line tool that runs on
the Moodle server and requires shell access to it. moodlesh runs on your own
computer and works through Moodle's web interface as a normal user. If you
have access to the server, moosh covers many more administrative tasks.

## How moodlesh is built

moodlesh is developed by [@borsattoz](https://github.com/borsattoz) with extensive help from an AI
assistant (Claude, by Anthropic). Most of the code, tests and documentation
were generated in conversation with it, and @borsattoz is reviewing them
progressively; this note will be updated as the review advances.

What has been checked so far:

- unit tests, run on every change by continuous integration on Python
  3.11 to 3.13, against a simulated Moodle that uses the real data formats;
- an integration suite run against real Moodle 4.5.15 and 5.1.8 sites;
- the browser login on a production Moodle 4.5 site with single sign-on.

Commits written with AI help say so in a `Co-Authored-By` line. If something
looks wrong, please open an issue: reports are the fastest way to make
moodlesh trustworthy.

## Development

```bash
uv sync
uv run pytest                                        # unit tests, no network
uv run ruff check . && uv run ruff format --check .
uv run mypy                                          # strict mode
```

```text
src/moodlesh/
├── core/        domain model and the CourseBackend protocol, independent of Moodle
├── moodle/      MoodleBackend and the browser, http and REST transports
├── memory.py    in-memory backend (tests and --demo)
├── dryrun.py    DryRunBackend: simulates writes on a copy
├── journal.py   JournalingBackend: records every write
└── cli/         command language (Lark grammar), interpreter, shell, runs on several courses
```

moodlesh uses only standard Moodle external functions, through the AJAX
endpoint Moodle's own pages use: `core_course_search_courses`,
`core_course_get_courses`, `core_courseformat_get_state`,
`core_courseformat_update_course`, `core_courseformat_new_module` and
`core_update_inplace_editable`.

The unit tests use a simulated Moodle and need no network. The integration
tests in `tests/integration` run against a real Moodle site and write to it,
so use a disposable one (for example from
[moodle-docker](https://github.com/moodlehq/moodle-docker)), seeded with
`php integration/seed_site.php /path/to/moodle`; the variables to set are
listed at the top of `tests/integration/test_real_moodle.py`.
[CLAUDE.md](CLAUDE.md) records the design decisions and conventions, and
[CHANGELOG.md](CHANGELOG.md) the changes.

## License

[MIT](LICENSE)
