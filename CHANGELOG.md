# Changelog

All notable changes to this project are listed here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-10-20

### Added

- Several cameras. They are kept in `cameras.json` (chmod 600), and an existing `.env` becomes
  the first camera, which keeps its files where they are. Every camera route is under
  `/cams/<id>/`, and the old routes still answer for the first camera (#5).
- An "All" tab with every camera live, and a camera picker in the top bar (#5).
- A setup assistant: the settings scan the network, show each camera found with its model and
  firmware, and explain the two accounts and Third-Party Compatibility (#5).
- A connection test (network, camera account, TP-Link account) and a diagnostic report to paste
  in a camera report, without any address, MAC or password (#5).
- Camera settings in the Live tab, shown only when the camera has them: privacy mode, status
  LED, spotlight, auto tracking, motion and person detection with their sensitivity, night
  vision mode, and PTZ presets (#6).
- `TAPO_ADMIN_HOSTS` to allow settings changes from other addresses (a phone, the Docker
  gateway).
- Continuous integration: the test suite on Linux and Windows, and a Docker image build.
- A Dockerfile and a docker-compose.yml, see [docs/DOCKER.md](docs/DOCKER.md).
- Issue templates (bug report, feature request, camera report) and a pull request template.
- A contributing guide, [CONTRIBUTING.md](CONTRIBUTING.md), and a camera compatibility list,
  [docs/CAMERAS.md](docs/CAMERAS.md).
- A test that fails when a UI string has no translation.

### Changed

- The login protocol is "auto" by default: the app uses what the camera's discovery answer
  suggests and switches only when the camera says it speaks the other one (`-40413`,
  `-40211`). A wrong password or a lockout never triggers a second login, and the protocol
  that worked is remembered (#5).
- A camera is found again by its MAC address when DHCP gives it another address (#5).
- The TP-Link password is stored once for the account; a camera on another account can have
  its own (#5).
- The PTZ pad only shows on cameras that list pan and tilt (#6).
- Tests run in temporary folders and never touch a running install (`TAPO_CONFIG_DIR`,
  `TAPO_DATA_DIR`, `TAPO_HLS_DIR`).
- The SD card tab opens at once even with thousands of local copies: they are counted once in
  the background instead of on every request (#8).
- New README in English and French, with fresh screenshots (#9).

## [0.2.0] - 2026-10-20

### Added

- Type 3 local control through pytapo, for cameras that do not use the V4 login, selected with
  `TAPO_CONTROL_PROTOCOL=3`. Verified on a C520WS (#2, @uzior).
- Native Windows support: `start.bat` and `start.ps1` launchers, `requirements-windows.txt`,
  and SD clip audio muxed over a loopback TCP connection, since Windows cannot pass the extra
  pipe used on Linux (#3, @uzior).
- The UI follows the browser language preferences, French or Polish (#4, @uzior).
- English, German and Spanish UI, and a language picker in the top bar. The choice is kept in
  the browser, `?lang=xx` in the URL works too, and English is the fallback.
- Tests that check every translation has the same keys and placeholders.
- V4 login for cameras that send `extra_crypt` (seen on a C200): the passcode goes through
  SHA-256-crypt before the handshake.
- The V4 client can also hash the credential with sha256 (local access token mode).
- Recordings that are damaged on the SD card itself are detected after the download, noted,
  and never retried.
- A wrong tag can be removed from a clip by hand, and the correction survives a new analysis.

### Changed

- Days with recordings are asked month by month: a well filled card refuses a wide date range.
- A failed V4 login reports how many tries are left before a lockout, when the camera says so.
- Stricter rule for secondary labels in the animal detection.
- Static files are versioned, so browsers pick up UI updates.
- The top bar wraps on phones.

### Fixed

- A failed or incomplete PAKE registration reply gives an error with the camera's error code
  instead of a crash (#1, @uzior).
- One Polish string was still in French.

## [0.1.0] - 2026-09-19

First public version, tested with a Tapo C510W on firmware 1.3.4.

### Added

- Live view in the browser (RTSP to HLS with ffmpeg), HD and SD streams, snapshots.
- Recording on demand and continuous recording in 10 minute segments, with a local library
  and thumbnails.
- Pan and tilt over ONVIF on motorised models.
- SD card browser: calendar of the days with recordings, 24 h timeline, the camera's own
  thumbnail for each clip, playback that starts while the clip is still downloading, and MP4
  download with the original H.264 (no re-encode), at about 10x realtime.
- Client for the camera's V4 local login (SPAKE2+ handshake, then an AES-CCM channel) and for
  its media port.
- Animal detection with DeepFaune in its own virtualenv (`ml/setup.sh`), run in the background
  on every clip, and an Animals tab to browse clips by species.
- Camera credentials entered in the browser and kept in a local `.env`; the camera is found on
  the LAN even when its IP changes.
- Offline tests with a fake camera and a fake media server.
- A write-up of the detection pipeline ([docs/DETECTION.md](docs/DETECTION.md)) and a French README.

[Unreleased]: https://github.com/freeKC/tapo-web/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/freeKC/tapo-web/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/freeKC/tapo-web/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/freeKC/tapo-web/releases/tag/v0.1.0
