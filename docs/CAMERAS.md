# Camera compatibility

The app talks to a Tapo camera over the LAN in a few ways:

* **RTSP** for the live view, snapshots and local recordings, and **ONVIF** for the model name
  and pan/tilt. Both use the camera account you create in the Tapo app.
* The **local control API** for the SD card status and the list of recordings, and the
  **media port** (8800) for the SD clips and their thumbnails. Both use the TP-Link account
  password.

The control API comes in two flavours. Newer firmware uses the **V4** login (a SPAKE2+
handshake, the reason older pytapo and python-kasa releases get `-40211` on those cameras), in
`app/tapo_v4.py`. Other cameras use the older **type 3** login, reached through pytapo
(`app/control.py`). On "auto", the default, the app starts with what the camera's discovery
answer suggests and switches only when the camera says it speaks the other one, so a wrong
password never costs a second attempt. The protocol can also be set per camera in the settings,
or for all of them with `TAPO_CONTROL_PROTOCOL`.
The protocol notes are in [freeKC/tapo-v4-protocol](https://github.com/freeKC/tapo-v4-protocol).

## Tested models

✅ works, ❔ not tested yet, ❌ does not work

| Model | Firmware | Control protocol | Live | SD card | PTZ | Animals | Notes | Source |
|---|---|---|---|---|---|---|---|---|
| C510W (hw 2.0) | 1.3.4 Build 260523 | V4 | ✅ | ✅ | ✅ | ✅ | Everything in the app works, camera settings included. | Tested by the maintainer |
| C520WS | 1.3.5 Build 260522 Rel.14207n | Type 3 | ❔ | ✅ | ❔ | ❔ | SD status, recording dates, clip listing and download verified with `TAPO_CONTROL_PROTOCOL=3`. The camera answers `-40413` to the V4 login, which is what makes "auto" switch to type 3. Needs Third-Party Compatibility turned on in the Tapo app. | @uzior, #1 #2 |
| C200 (hw 5.0) | 1.4.6 | V4 with `extra_crypt` | ❔ | ❔ | ❔ | ❔ | Support added in 0.2.0 from a report: `extra_crypt` (SHA-256-crypt) and sha256 passcode in the V4 login, month by month day search. | Reported, not tested here |

"SD card" covers the status, the list of recordings, playback and download.

## Add your camera

Open an issue with the [camera report](https://github.com/freeKC/tapo-web/issues/new?template=camera-report.yml)
template: model, hardware and firmware versions, what works and the diagnostic report (gear
icon, "Edit" next to the camera, "Copy the diagnostics"). Reports of what does not work are just as useful.
