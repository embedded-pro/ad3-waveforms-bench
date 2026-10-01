# Changelog

## [0.2.1](https://github.com/embedded-pro/ad3-waveforms-bench/compare/v0.2.0...v0.2.1) (2026-10-01)


### Bug Fixes

* reset digital-in before arming the logic analyzer ([f8cc4ae](https://github.com/embedded-pro/ad3-waveforms-bench/commit/f8cc4ae525bdfa636406c5f4692329459d239cda))
* send the UART parity code the WaveForms runtime expects ([0e2f644](https://github.com/embedded-pro/ad3-waveforms-bench/commit/0e2f644e9376ac8d10d56cb58c5a3e2df14f64d0))
* UART parity mapping and digital-in reset before logic capture ([dcb3754](https://github.com/embedded-pro/ad3-waveforms-bench/commit/dcb3754fd4a4997e4e78cd824f48bc13b0c2435a))

## [0.2.0](https://github.com/embedded-pro/ad3-waveforms-bench/compare/v0.1.0...v0.2.0) (2026-09-28)


### Features

* ad3-bench-gui, a PySide6 GUI for the server, with a Windows installer and a Linux AppImage ([d9cedb7](https://github.com/embedded-pro/ad3-waveforms-bench/commit/d9cedb71afb82eb998f19a3cc9ef39071ae8c970))
* ad3-bench-server shares the WaveForms library over TCP; RemoteDwfApi, AnalogDiscovery3(remote=...), AD3_REMOTE and --ad3-remote use it ([d9cedb7](https://github.com/embedded-pro/ad3-waveforms-bench/commit/d9cedb71afb82eb998f19a3cc9ef39071ae8c970))
* add remote AD3 server/client, GUI with installers, and release tooling ([ceb8a38](https://github.com/embedded-pro/ad3-waveforms-bench/commit/ceb8a3868f7ddca52e37c04b9a996377f6c0edb6))
* FirmwareTerminal accepts pyserial URLs such as socket:// for serial ports forwarded by port-bridge ([d9cedb7](https://github.com/embedded-pro/ad3-waveforms-bench/commit/d9cedb71afb82eb998f19a3cc9ef39071ae8c970))
* initial import of the AD3 WaveForms bench toolkit ([67d50a1](https://github.com/embedded-pro/ad3-waveforms-bench/commit/67d50a1c5cbaa18eef36e1f5228d12170d48fa72))


### Continuous Integration

* release with release-please, as in port-bridge ([7ddd7af](https://github.com/embedded-pro/ad3-waveforms-bench/commit/7ddd7af2400cb8400f9168039398ddea0df5a270))
* release with release-please, as in port-bridge ([d9cedb7](https://github.com/embedded-pro/ad3-waveforms-bench/commit/d9cedb71afb82eb998f19a3cc9ef39071ae8c970))


### Build System

* **deps:** Bump the actions group with 4 updates ([ec58c95](https://github.com/embedded-pro/ad3-waveforms-bench/commit/ec58c95344607e55e48fdd7c5ffa0115805c88c4))
* **deps:** Bump the actions group with 4 updates ([ab34243](https://github.com/embedded-pro/ad3-waveforms-bench/commit/ab34243beafb8f0863afacbbd26bc128030a9cf2))
* type information (py.typed), mypy and coverage in CI, tests on Linux, Windows and macOS with Python 3.10-3.14 ([d9cedb7](https://github.com/embedded-pro/ad3-waveforms-bench/commit/d9cedb71afb82eb998f19a3cc9ef39071ae8c970))

## [0.1.0]

* First version, split out of the hal-ti hardware-in-the-loop validation.
