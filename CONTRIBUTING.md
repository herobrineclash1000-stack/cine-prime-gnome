# Contributing to Cine Prime

Thank you for considering a contribution to Cine Prime. Contributions are welcome through bug reports, feature proposals, documentation improvements, translations, tests, and code changes.

## Before you start

- Search existing issues and pull requests to avoid duplicate work.
- For a substantial change, open an issue first so the design can be discussed before implementation.
- Be respectful and follow the project's Code of Conduct.
- Do not include personal media, databases, backups, API keys, tokens, credentials, or other private information in issues, commits, or pull requests.

## Development setup

Cine Prime targets Linux desktops with GTK4, libadwaita, and GStreamer. On Ubuntu, install the system dependencies listed in `README.md`, then run the application from the project directory:

```bash
python3 app.py
```

Run the non-GUI tests with:

```bash
python3 -m unittest discover -s tests -v
```

The GTK GUI tests and real GStreamer integration test require a graphical test environment. Their exact commands and environment variables are documented in `README.md`.

## Pull request guidelines

1. Create a focused branch from `main`.
2. Keep changes scoped to one bug fix or feature where practical.
3. Add or update tests for changed behaviour.
4. Run the relevant tests and `git diff --check` before submitting.
5. Update documentation when behaviour, dependencies, or user-facing workflows change.
6. Explain the problem, the solution, how it was tested, and any known limitations in the pull request.

Do not commit generated packages, build directories, caches, virtual environments, local databases, media files, or backups.

## Licensing contributions

By submitting a contribution intentionally for inclusion in Cine Prime, you agree that it is provided under the project's `GPL-3.0-or-later` license. You retain copyright in your contribution; no copyright assignment is requested.