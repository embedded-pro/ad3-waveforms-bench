# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- `ad3-bench-server` shares the WaveForms library of the machine the AD3 is plugged into, for benches
  running in a container or on another machine.
- `RemoteDwfApi`, `AnalogDiscovery3(remote="host:port")`, the `AD3_REMOTE`/`AD3_REMOTE_TOKEN` variables and
  the `--ad3-remote` pytest option use such a server.
- `FirmwareTerminal` accepts pyserial URLs (`socket://`, `rfc2217://`, `loop://`) as the port, for
  example a serial port forwarded by port-bridge.
- Type information (`py.typed`), mypy and coverage in CI, tests on Linux, Windows and macOS with Python
  3.10-3.14, wheel build checks and a tag-driven PyPI release workflow.

### Changed

- The package version comes from the git tag (setuptools-scm).

## [0.1.0]

- First version, split out of the hal-ti hardware-in-the-loop validation.
