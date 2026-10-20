# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/borsattoz/moodlesh/security/advisories/new)
instead. You should get an answer within a week.

moodlesh is maintained by volunteers and is alpha software: only the latest
release receives fixes.

## What moodlesh stores and sends

- **Session**: the Moodle session cookies are saved in
  `~/.local/state/moodlesh/session-<host>.json` (or under `$XDG_STATE_HOME`),
  with permissions `600`. While valid, they give the same access as your
  password. `moodlesh logout` ends the session on Moodle (when the site allows
  it) and deletes the file.
- **Change journal**: `changes.jsonl` in the same directory (permissions `600`)
  records course ids, section names and results of every change. It contains
  no credentials.
- **Tokens and passwords** are never written to disk by moodlesh. Prefer
  `MOODLESH_TOKEN` to `--token`, since command-line arguments are visible to
  other local users.
- **Network**: moodlesh talks only to the Moodle URL you give it. There is no
  telemetry.
- **Logs**: `-v`/`-vv` print function names and arguments (course ids, names),
  not cookies, tokens or session keys. Check before sharing logs anyway.

## Out of scope

moodlesh never tries to bypass bot protection, rate limits or access controls.
It acts with the user's own Moodle permissions; anything it can do, the user
could do in the browser.
