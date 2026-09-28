# Contributing

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate          # .venv\Scripts\activate on Windows
pip install -e ".[dev,gui]"   # gui: PySide6, for the GUI and its tests
pre-commit install            # optional: ruff on every commit
```

Before pushing:

```bash
ruff check .
ruff format --check .
mypy
coverage run -m pytest -q && coverage report
```

Everything runs without hardware: `FakeDwfApi` replaces the WaveForms library and `FakeTerminalDevice`
replaces the device terminal. Tests that need a real AD3 use the `ad3` marker or fixture and are skipped
when no device is found.

## Commit messages

Commits on `main` follow [Conventional Commits](https://www.conventionalcommits.org/): `feat: ...`,
`fix: ...`, `docs: ...`, `ci: ...`, `refactor: ...`, `test: ...`, `build: ...`, `chore: ...`, with `!` or a
`BREAKING CHANGE:` footer for breaking changes. They decide the next version and become the release notes.
When a pull request is squash-merged, its title is the commit message, so give the title the same form.

## Releasing

Releases are made by [release-please](https://github.com/googleapis/release-please), as in port-bridge:

1. Every push to `main` runs the `release-please` workflow, which opens (or updates) a release pull request
   `chore(main): release X.Y.Z`. It bumps `version` in `pyproject.toml` and `.release-please-manifest.json`
   and adds the release notes to `CHANGELOG.md`.
   Below 1.0, `feat` and `fix` bump the patch version and breaking changes the minor version. A
   `Release-As: X.Y.Z` footer in a commit forces a version.
2. Merging the release pull request creates the tag `vX.Y.Z` and the GitHub release, and the same workflow run:
   - builds the sdist and wheel and attaches them to the release (`release.yml`),
   - builds the Windows installer and the Linux AppImage of the GUI and attaches them (`build-installers.yml`),
   - publishes the sdist and wheel to PyPI.

`Release` and `Build Installers` can be run by hand (Actions > workflow > Run workflow) with an existing tag
to rebuild the files of a release.

To build the GUI bundle locally:

```bash
pip install ".[gui]" pyinstaller
python scripts/generate_icon.py      # QT_QPA_PLATFORM=offscreen without a display
pyinstaller ad3-bench-gui.spec       # dist/ad3-bench-gui.exe on Windows, dist/ad3-bench-gui/ on Linux
```

### One-time setup

- **Let release-please open pull requests:** *Settings > Actions > General > Workflow permissions*, enable
  *Allow GitHub Actions to create and approve pull requests*.
- **CI on the release pull request (optional):** pull requests opened with the default `GITHUB_TOKEN` do not
  trigger other workflows. To run CI on them, add a fine-grained personal access token (or a GitHub App token)
  with *Contents* and *Pull requests* read/write on this repository as the secret `RELEASE_PLEASE_TOKEN`.
- **PyPI:** publishing uses [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is
  stored. On pypi.org, under *Your account > Publishing*, add a pending publisher: project
  `ad3-waveforms-bench`, owner `embedded-pro`, repository `ad3-waveforms-bench`, workflow
  **`release-please.yml`**, environment `pypi`. Then create the environment `pypi` in the repository settings
  (*Settings > Environments*), optionally with required reviewers so each upload needs an approval.
