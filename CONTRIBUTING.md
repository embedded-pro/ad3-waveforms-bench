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

## Releasing

The version comes from the git tag (setuptools-scm); there is no version number to edit.

1. Move the `Unreleased` entries of `CHANGELOG.md` under a new version heading and merge that to `main`.
2. Tag and push: `git tag v0.2.0 && git push origin v0.2.0`.
3. The `Release` workflow builds the sdist and wheel, publishes them to PyPI, creates the GitHub release and
   then runs `Build Installers`, which attaches the Windows installer and the Linux AppImage of the GUI.
   `Build Installers` can also be run by hand (Actions > Build Installers > Run workflow) for an existing tag.

To build the GUI bundle locally:

```bash
pip install ".[gui]" pyinstaller
python scripts/generate_icon.py      # QT_QPA_PLATFORM=offscreen without a display
pyinstaller ad3-bench-gui.spec       # dist/ad3-bench-gui.exe on Windows, dist/ad3-bench-gui/ on Linux
```

### One-time PyPI setup

Publishing uses PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is
stored in the repository:

1. On pypi.org, under *Your account > Publishing*, add a pending publisher: project `ad3-waveforms-bench`,
   owner `embedded-pro`, repository `ad3-waveforms-bench`, workflow `release.yml`, environment `pypi`.
2. In the GitHub repository settings, create the environment `pypi` (optionally with required reviewers so
   each release needs an approval).
