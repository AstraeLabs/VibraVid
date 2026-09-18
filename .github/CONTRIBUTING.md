# Contributing to VibraVid

Thanks for taking the time to contribute! This document covers the basics for
getting a change into the project.

## Before you start

- For small fixes (typos, obvious bugs), just open a pull request.
- For anything larger — a new feature, a new supported site, a behavior
  change — please open an issue first, or start a discussion on the
  [Discord server](https://discord.com/invite/8vV68UGRc7), so we can align on
  the approach before you put time into it.
- Check [`.github/MkDoc/docs/`](MkDoc/docs/) (published at
  [astraelabs.github.io/VibraVid](https://astraelabs.github.io/VibraVid/))
  for the full user-facing documentation — configuration, CLI flags, Docker,
  the TUI, etc. It's the best starting point for understanding how a piece
  fits together before changing it.

## Setting up a local environment

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
pip install -r requirements.txt
```

See [`.github/MkDoc/docs/configuration.md`](MkDoc/docs/configuration.md)
for `config.json` / `login.json` setup, and
[`.github/MkDoc/docs/cli.md`](MkDoc/docs/cli.md) for running the CLI
(`python manual.py ...`), the TUI (`python tui.py`), or the GUI
(`GUI/manage.py runserver`).

## Adding a new site

Site modules live under `VibraVid/services/<site>/`, each with an `__init__.py`
declaring `indice` and `_useFor`. Follow the existing structure of a
comparable service (a movie/series site, an anime site, a music site, ...)
rather than starting from scratch — see
[`.github/MkDoc/docs/add-service.md`](MkDoc/docs/add-service.md) for
the full walkthrough.

## Before opening a pull request

- Run the linter: `ruff check .`
- Run the relevant tests: `pytest Test/` (or a subfolder, e.g. `pytest Test/tui`)
- Keep the change scoped to what the PR describes — unrelated cleanup makes
  review slower and harder to reason about.
- Write the PR description around the *why*: what problem this solves or
  what behavior changes, not just a restatement of the diff.

## Reporting bugs

Open a GitHub issue with: what you ran (command/steps), what you expected,
what happened instead, and relevant log output (redact any tokens/cookies
first). For a security vulnerability, see [`SECURITY.md`](SECURITY.md)
instead of opening a public issue.
