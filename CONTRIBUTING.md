# Contributing

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate          # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
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
3. The `Release` workflow builds the sdist and wheel, publishes them to PyPI and creates the GitHub release.

### One-time PyPI setup

Publishing uses PyPI [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is
stored in the repository:

1. On pypi.org, under *Your account > Publishing*, add a pending publisher: project `ad3-waveforms-bench`,
   owner `embedded-pro`, repository `ad3-waveforms-bench`, workflow `release.yml`, environment `pypi`.
2. In the GitHub repository settings, create the environment `pypi` (optionally with required reviewers so
   each release needs an approval).
