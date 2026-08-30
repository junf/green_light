# Contributing to green_light

Conventions for this repository. They exist because green_light is worked on from
several machines at once (Windows, macOS, Linux) and the same tool is often driven
by an assistant that only sees what the repository itself carries.

That is the point of this file: everything below used to live in a private,
gitignored note, so the one document stating the rules could never reach the
machine expected to follow them. If you change a convention, change it here.

## Language

- **Code, comments, docstrings and anything printed to the terminal: English.**
  This applies to warning and error text as much as to identifiers.
- **Commit messages: English.**
- **`README.md` (Japanese) is the canonical README; `README.en.md` is its
  translation.** Change the Japanese first, then mirror it. Doing it in that order
  is what keeps the translation honest.

## Commit messages

Write what changed and why the change is right, not just what was touched. A
reader six months out has the diff already; what they lack is the reasoning, the
measurement behind it, and what was ruled out.

- **No tool-generated trailers.** In particular, no `Co-Authored-By: Claude ...`
  and no `Claude-Session:` line. Assistants working here are commonly configured
  to append both by default; strip them before committing.
  - Five commits dated 2026-07-13 predate this rule and still carry the pair. They
    are already published and are deliberately left alone, so the log is
    inconsistent by decision rather than by accident.
- **Say which claims are measured.** If a commit fixes a failure you reproduced,
  name the machine and what you observed. If part of the reasoning is inference,
  label it as inference. Several fixes here have been made — and one unmade —
  precisely on that distinction.

## Branches

- `main` is the trunk. Work lands there as a fast-forward, never a merge commit.
- Assistant-authored work happens on the `claude` branch and is fast-forwarded
  into `main` only after the human has reviewed it. Nothing pushes to a remote
  without them saying so.
- Numbered feature branches (`feature/#123`) put `#123` at the end of the commit
  title. The `claude` branch is not numbered, so its commits carry no number.

## Code

- **No hardcoded paths, URLs or ports.** They belong in `config.json`
  (`config.example.json` is the template).
- **Fail loudly.** Capturing everything is the default, and anything skipped,
  filtered or unreachable is announced in the terminal. Silent data loss is the
  failure mode this tool exists to avoid, so a bug that drops output quietly is
  more serious here than one that crashes.
- **Do not weaken the security posture.** The debug port is bound to localhost
  only; `--no-sandbox`, `--disable-web-security` and `--remote-allow-origins=*`
  are never added; the tool never escalates privileges on the user's behalf, and
  prints the privileged step instead. Anything that reaches beyond localhost —
  `adb tcpip`, Wi-Fi `adb connect`, `--remote-debugging-address` — needs a fresh
  security review, not just a code review. The reasoning is in README's security
  section; read it before touching those areas.

## Testing

There is no test suite. Changes are verified by running the tool against real
hardware — a desktop Chrome, a USB-connected Android device, an iPhone over
usbmux — and reading the log it produces.

Two habits earn their keep:

- **Check that the failure you are fixing actually happens.** More than one
  plausible bug here dissolved when the code path was exercised: an earlier guard
  already closed it, or the process exited before the consequence could occur.
- **Confirm the syntax check.** `python -W error::SyntaxWarning -m py_compile *.py`
  for the Python sources, `bash -n` for the shell scripts.
