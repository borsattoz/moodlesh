<div align="center">

# 🐚 moodlesh

**The command line Moodle never had.**

Manage Moodle courses the way you manage files: navigate them, inspect them,
change them in one line, and script whatever you do more than once.
moodlesh is young and growing fast: course structure is here today, and much
more is on the way.

> **Status: alpha (0.1.0a1).** Tested on real Moodle 4.5 and 5.1 sites, but
> early software: try it with `--dry-run` or on a test course first.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Moodle 4.5+](https://img.shields.io/badge/Moodle-4.5%2B-F98012?logo=moodle&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Typed: mypy strict](https://img.shields.io/badge/typed-mypy%20strict-2A6DB2)
![Lint: ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)

</div>

```text
moodle:/> cd PHY1
moodle:/PHY1> ls
#  section     contents
0  General     1 activity
1  Kinematics  1 subsection, 2 activities
2  Dynamics    1 subsection, 1 activity
moodle:/PHY1> mkdir "Optics" --at 2
created section 2 'Optics'
moodle:/PHY1> cd Kinematics
moodle:/PHY1/Kinematics> mkdir "Theory" --at 1
created subsection 'Theory' in 'Kinematics'
```

---

## Why moodlesh?

Moodle is built for clicking, one course at a time. Managing dozens or hundreds
of courses that way means the same clicks over and over: open the course, turn
editing on, scroll, add, drag, rename… then the next course, and the next.

moodlesh gives you a shell instead. Things that took an afternoon become one
command, or one script you run on every course.

**✅ Available today**

- **🗂️ Navigate courses like a filesystem**: `ls`, `cd`, `pwd` and Tab completion.
- **🌳 See a whole course at once**: `tree` shows sections, subsections and
  activities on a single screen.
- **🎯 Shape the structure precisely**: create and rename sections and
  subsections exactly where you want them (`mkdir "Labs" --at 2`).
- **📜 Script it, repeat it**: save commands in a file and run them again. A
  script is validated in full first, so a typo never leaves a course half-built.
- **📚 One script, many courses**: run it on a list of courses, a whole category
  or every course matching a name, with a summary at the end.
- **🛟 Safe by default**: `--dry-run` shows exactly what would change without
  touching Moodle, and every real change is recorded in a local journal.
- **🔐 Uses your normal login**: SSO (Shibboleth, SAML, OIDC…) included, with no
  web services or tokens to ask your admin for.
- **🧩 A Python library too**: everything the shell does is a typed API.

**🚧 On the roadmap**

- **Move and delete**: reorder sections and subsections, remove empty ones.
- **Undo**: revert a run using the change journal.
- **Activities and resources**: create, move, hide, show, duplicate and delete
  them, not just sections.
- **Dates and visibility in bulk**: shift deadlines for a new term, open or
  close whole sections at once.
- **Copy and compare**: `cp` a structure from one course to another, `diff` two
  courses to see what changed.
- **Search across courses**: find every quiz without a time limit, every empty
  section, every hidden resource.
- **Export and import**: save a course outline as YAML or Markdown, edit it, and
  apply it back.

Have a use case that is not listed? [Open an issue](https://github.com/borsattoz/moodlesh/issues):
that is the best way to shape what comes next.

## Try it in 30 seconds, no Moodle needed

```bash
git clone https://github.com/borsattoz/moodlesh.git
cd moodlesh
uv run moodlesh --demo
```

`--demo` starts the shell on sample courses held in memory. Nothing is sent
anywhere, so play freely: `ls`, `cd PHY1`, `tree`, `mkdir "Optics" --at 2`,
`help`.

## moodlesh and moosh

[moosh](https://github.com/tmuras/moosh) ("MOOdle SHell") is the long-standing
command-line tool for Moodle *administrators with server access*: it runs on
the Moodle server and talks to the database and the PHP code directly.
moodlesh is a different tool for a different situation: it runs on **your own
computer**, talks to Moodle over the web **as you**, and needs no server access,
no plugins and no web services. If you can manage courses in the browser, you
can use moodlesh. If you have shell access to the server, moosh can do far more
today.

## Installation

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/) (or pip).

```bash
uv tool install git+https://github.com/borsattoz/moodlesh.git   # puts `moodlesh` on your PATH
"$(uv tool dir)/moodlesh/bin/playwright" install chromium    # the browser moodlesh drives
```

With pip, inside a virtualenv: `pip install git+https://github.com/borsattoz/moodlesh.git`,
then `playwright install chromium`. Working from a clone? `uv run playwright install chromium`.

## Connect to your Moodle

```bash
export MOODLESH_URL=https://moodle.example.org
moodlesh
```

A Chromium window opens. Log in as you always do, SSO included. The shell
notices on its own when you are in, and you are ready to go. Your session is
saved, so next time there is usually nothing to do. Keep the window open while
you work: moodlesh talks to Moodle through it.

> **Why a browser window?** Many Moodle sites use single sign-on, and some add
> a security check (for example Cloudflare's) for anything that is not a
> browser. moodlesh does not try to get around any of that: it works inside a
> real browser window, making the same requests Moodle's own pages make, and
> any login or check is completed by *you*, in the window.

<details>
<summary><b>Other ways to connect</b></summary>

| Transport | When to use it | How |
|---|---|---|
| `browser` (default) | Any site, especially with SSO or bot protection | `moodlesh` |
| `http` | Sites without bot protection, if you prefer no window | `moodlesh login`, then `moodlesh --transport http` |
| REST token | An admin gave you a web service token | `MOODLESH_TOKEN=... moodlesh` |

- `moodlesh login --password` logs in with a Moodle username and password
  (manual accounts only), without a browser.
- `MOODLESH_BROWSER_CHANNEL=chrome` uses your installed Chrome instead of
  Chromium.
- `moodlesh logout` ends the session on Moodle (when the site allows it) and
  deletes the saved copy.
- `moodlesh --all-courses` also lists courses you can see but not edit.

</details>

## Who is it for?

moodlesh is built for people who **shape courses at scale**: **site
administrators**, **category managers** and users with the **Manager** role.
It acts with your own Moodle permissions: it can do exactly what you could do
by clicking, no more.

## Documentation

### The shell

| Command | What it does |
|---|---|
| `ls [PATH] [-f TEXT] [-d]` | List courses, the sections of a course, or the contents of a section. `-f` filters by name, `-d` shows only sections and subsections. |
| `cd [PATH]` | Move around. `cd` alone goes back to the root. |
| `pwd` | Show where you are. |
| `tree [PATH] [-L N] [-d]` | Draw the structure of a course or section. `-L` limits the depth, `-d` hides activities. |
| `mkdir NAME [--at N] [-i PATH]` | Create a section (inside a course) or a subsection (inside a section). |
| `rename PATH NAME` | Rename a section or subsection. Alias: `mv`. |
| `refresh` | Reload from Moodle, e.g. after editing the course in the browser. |
| `help [COMMAND]` | List all commands, or show the details of one. |
| `exit` | Leave the shell. Alias: `quit`. |

**Tab** completes commands, options and paths, and your history is kept between
sessions.

### Paths

Every course is a folder, every section is a folder inside it, and every
subsection is a folder inside its section.

```text
/                  the root: your courses
/PHY1              a course: by short name, full name or numeric id
/PHY1/1            a section: by number (0 is the general section) or by name
/PHY1/1/2          a subsection: by its position in the section, or by name
```

Each part of a path can be **the number `ls` shows in the `#` column** or the
**name** (case-insensitive; quote it if it has spaces). Relative paths, `.` and
`..` work as you would expect:

```text
moodle:/PHY1/Kinematics> cd ../Dynamics
moodle:/PHY1/Dynamics> tree /PHY1 -d
```

### Positions

`--at N` is the position the new item **will have**, counted as `ls` counts:

- in a course, `mkdir "Labs" --at 2` creates the section that becomes number 2
  (section 0 never moves);
- in a section, `mkdir "Theory" --at 1` makes the new subsection its first item.

Leave `--at` out to add at the end. Moodle itself does not allow subsections
inside a subsection, and moodlesh tells you so before trying.

### Scripts

Put commands in a file, one per line (or separated by `;`). `#` starts a comment.

```text
# structure.mdl: add a practice area to the Physics course
cd /PHY1
mkdir "Practice" --at 1
mkdir "Guided exercises" --into Practice
mkdir "Past exams" --into Practice
tree -d
```

```bash
moodlesh run structure.mdl --check    # validate only, no connection to Moodle
moodlesh run structure.mdl            # run it
moodlesh exec 'cd /PHY1; tree -L 1'   # quick one-liners
```

Mistakes are caught **before** anything runs, and point at the exact spot:

```text
mkdir "Optics --at 2
      ^
syntax error: line 2, column 7: unclosed quotes
```

If something fails while running (a permission, a missing course), moodlesh
stops at that line and tells you which one. Exit codes are `0` for success, `2`
for mistakes in the script and `1` for anything that fails while running, so
moodlesh fits nicely into automation.

### Many courses at once

Add targets to `run` or `exec` and the script runs once per course, starting
from that course, so relative paths mean the same thing everywhere:

```bash
moodlesh run practice.mdl --category Science          # every course you can edit in Science
moodlesh run practice.mdl --match "2026"              # every course whose name contains 2026
moodlesh run practice.mdl -c PHY1 -c CALC1            # an explicit list (id, short or full name)
```

moodlesh lists the selected courses and asks for confirmation (`--yes` skips
the question, and is required without a terminal). It stops at the first
course that fails, unless you pass `--keep-going`, and ends with a summary:

```text
2 of 2 courses ok
course  result
CALC1   ok
PHY1    ok
```

### Dry runs and the change journal

`--dry-run` (`-n`) works with everything: the shell, `run`, `exec`, bulk runs.
moodlesh reads the real courses, checks your permissions as a real run would,
and simulates the changes, so later commands see the earlier ones:

```text
$ moodlesh -n exec 'cd PHY1; mkdir "Optics" --at 2; mkdir Lenses --into Optics'
dry run: changes are simulated, Moodle is not modified
would create section 2 'Optics'
would create subsection 'Lenses' in 'Optics'
dry run: nothing was changed
```

Every real change, successful or not, is appended to a journal,
`~/.local/state/moodlesh/changes.jsonl` (or `$MOODLESH_JOURNAL`), one JSON line
each: time, site, course, action, arguments and result.

Before writing anything, moodlesh checks that it can complete the whole
operation (for example that you may edit the course), so a permission problem
never leaves a half-made change behind.

### Use it from Python

The shell is a thin layer over a typed library:

```python
from moodlesh.moodle import connect_with_browser

moodle, browser = connect_with_browser("https://moodle.example.org")
with browser:
    course = moodle.get_course(101)
    for section in course.sections:
        print(section.number, section.name, len(section.items))

    kinematics = course.section_by_number(1)
    moodle.add_subsection(course.id, kinematics.id, position=1, name="Theory")
```

## Compatibility

| Moodle | Status |
|---|---|
| 4.5 | ✅ Tested (4.5.15) |
| 5.1 | ✅ Tested (5.1.8) |
| 5.0 | 🟡 Expected to work: every API used is present and unchanged |
| 5.2 and later | 🟡 Not tested yet; reports welcome |
| 4.4 and older | ❌ Not supported (subsections arrived in 4.5) |

"Tested" means the integration suite (`tests/integration`) and the shell, with
both the browser and the HTTP transport, pass against a real site of that
version.

moodlesh is developed and tested on Linux. It is plain Python plus Playwright,
so macOS and Windows should work too; reports are welcome.

## Under the hood

<details>
<summary><b>Architecture</b></summary>

```text
src/moodlesh/
├── core/              domain model, independent of Moodle and of the CLI
│   ├── models.py      Course, Section, Subsection, Activity (immutable)
│   ├── backend.py     the CourseBackend protocol + positioning rules
│   └── errors.py      exception hierarchy
├── moodle/            the real-Moodle implementation
│   ├── client.py      MoodleBackend: the logic, shared by every transport
│   ├── browser.py     BrowserTransport: calls from inside a Chromium window
│   ├── transport.py   AjaxSessionTransport (HTTP) and RestTransport (token)
│   ├── state.py       core_courseformat_get_state → model
│   └── auth.py        session storage and password login
├── memory.py          in-memory backend (tests and --demo)
├── dryrun.py          DryRunBackend: simulate writes on a copy
├── journal.py         JournalingBackend: record every write
└── cli/               the shell; depends only on core.CourseBackend
    ├── dsl/           Lark grammar, AST, parser
    ├── commands.py    typed commands, specs, validation
    ├── navigation.py  filesystem-like paths and caching
    ├── interpreter.py command execution
    ├── render.py      tables and trees (pure functions)
    ├── completion.py  Tab completion
    ├── bulk.py        one script, many courses
    ├── repl.py        interactive shell
    └── main.py        entry point
```

The DSL has three layers. The **grammar** only knows the shape of a command,
the **command specs** give arguments their meaning (and generate validation,
help and completion), and the **interpreter** runs them. Adding a command means
adding a dataclass and a spec, without touching the grammar.

</details>

<details>
<summary><b>Moodle functions used</b></summary>

All are standard Moodle external functions, callable through the same AJAX
endpoint Moodle's own pages use:

- `core_course_search_courses`, `core_course_get_courses`
- `core_courseformat_get_state`: the full course structure in one call
- `core_courseformat_update_course` (`section_add`)
- `core_courseformat_new_module` (falling back to `core_courseformat_create_module` on early 4.5)
- `core_update_inplace_editable`: renaming

</details>

<details>
<summary><b>Security and privacy</b></summary>

- moodlesh only ever acts as **you**, with your permissions, on the site you
  point it at. There is no server and no telemetry.
- Your session is saved in `~/.local/state/moodlesh/` with permissions `600`.
  While it is valid it is as good as your password: don't share it.
  `moodlesh logout` removes it.
- Pass a token with `MOODLESH_TOKEN` rather than `--token`: command-line
  arguments are visible to other users of the same computer.
- Security checks are never bypassed: if one appears, you pass it yourself in
  the browser window.
- Found a vulnerability? See [SECURITY.md](SECURITY.md).

</details>

## How moodlesh was built

moodlesh was written with substantial help from an AI assistant (Claude, by
Anthropic): most of the code, tests and docs were generated in conversation
with the maintainer, and have not yet been fully reviewed by a human. It is
tested with unit tests, a fake Moodle, and an integration suite run against
real Moodle 4.5 and 5.1 sites, but treat it as early software: use
`--dry-run`, try it on a test course first, and please report anything odd.

## Contributing

Contributions are very welcome!

```bash
uv sync
uv run pytest                                          # fast, no network needed
uv run ruff check . && uv run ruff format --check .
uv run mypy                                            # strict mode
```

The Moodle backend is tested against a fake Moodle (`tests/fakes.py`) that
speaks the same functions and data formats as Moodle 4.5, so you don't need a
Moodle site to hack on moodlesh. To run the integration suite against a
**disposable** site (it writes to it), seed it with
`php integration/seed_site.php /path/to/moodle` and see the docstring of
`tests/integration/test_real_moodle.py`. `CLAUDE.md` describes the design
decisions and conventions in more depth; changes are listed in
[CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE). Use it, fork it, build on it.

<div align="center">

**If moodlesh saves you a few hundred clicks, give it a ⭐. It helps others find it.**

</div>
