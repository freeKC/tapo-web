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
| C520WS (hw 1.0) | 1.3.5 Build 260522 Rel.14207n | Type 3 (auto) | ✅ | ✅ | ✅ | ❔ | Live view, SD card and ONVIF movement verified. Auto selects and remembers type 3. Needs Third-Party Compatibility turned on in the Tapo app. See the settings caveat below. | @uzior, [#16](https://github.com/freeKC/tapo-web/issues/16), #1 #2 |
| C200 (hw 5.0) | 1.4.6 | V4 with `extra_crypt` | ❔ | ❔ | ❔ | ❔ | Support added in 0.2.0 from a report: `extra_crypt` (SHA-256-crypt) and sha256 passcode in the V4 login, month by month day search. | Reported, not tested here |

"SD card" covers the status, the list of recordings, playback and download.

### C520WS validation notes

The [hardware 1.0 report](https://github.com/freeKC/tapo-web/issues/16) also confirms LED and
spotlight changes and PTZ preset save, return and removal, tested with the thread-pool fix in
[#13](https://github.com/freeKC/tapo-web/pull/13). Settings writes and preset saving are not
confirmed to work on main without that fix. Privacy, tracking, detection and night-mode settings
were read only; changing them was not tested. Snapshots and animal detection were not separately
tested.

## Every Tapo camera

The tables below list every Tapo camera model on tapo.com and in TP-Link's download centres as of
October 2026, with what TP-Link's own spec pages and RTSP FAQs
([724](https://www.tapo.com/us/faq/724/), [2680](https://www.tp-link.com/us/support/faq/2680/))
say about them. Apart from the tested models above, nothing here was tried with tapo-web. It is
what the hardware suggests:

* 🟢 **should work**: mains-powered, with RTSP and a microSD slot. The live view, recordings, SD
  card, animal detection and camera settings should all work, and the pan and tilt pad on the
  pan/tilt models.
* 🟡 **partly**: works only in some setups, see the note.
* ❔ **unknown**: TP-Link does not say whether it has RTSP.
* ❌ **no live view**: no RTSP. TP-Link gives battery and solar cameras no RTSP, they sleep to
  save power. Their SD card is another story, see the battery section.

The settings only show what each camera reports, so a model without a spotlight gets no spotlight
switch. "PT" means pan and tilt, "light" a spotlight or floodlight.

### Indoor

| Model | Kind | PT | Light | | Notes |
|---|---|:-:|:-:|:-:|---|
| C100 | fixed | | | 🟢 | Some firmware uses the new login (`-40211` with older tools) |
| C101 | fixed | | | 🟢 | hw 5.0 advertises the new login |
| C110 | fixed | | | 🟢 | |
| C111 | fixed | | | 🟢 | |
| C125 | fixed | | | 🟢 | |
| C840 | fixed, two lenses | | | 🟢 | The wide lens only (the other one is on stream6/7) |
| TC60 | fixed | | | 🟢 | |
| C200 | pan/tilt | ✓ | | 🟢 | hw 5.0 on 1.4.6 uses the new login with `extra_crypt`, handled since 0.2.0 |
| C201 | pan/tilt | ✓ | | 🟢 | |
| C202 | pan/tilt, Ethernet | ✓ | | 🟢 | ONVIF not stated on the spec page, pan and tilt may not work |
| C210 | pan/tilt | ✓ | | 🟢 | |
| C211 | pan/tilt | ✓ | | 🟢 | |
| C212 | pan/tilt, Ethernet | ✓ | | 🟢 | |
| C21A | pan/tilt | ✓ | | 🟢 | |
| C220 | pan/tilt | ✓ | | 🟢 | `-40211` reported on 1.4.4 |
| C222 | pan/tilt, Ethernet | ✓ | | 🟢 | |
| C225 | pan/tilt | ✓ | | 🟢 | |
| C230 | pan/tilt | ✓ | | 🟢 | |
| C232 | pan/tilt, Ethernet | ✓ | | 🟢 | |
| C250 | pan/tilt, wall mount | ✓ | | 🟢 | |
| C260 | pan/tilt, 4K | ✓ | | 🟢 | |
| C825 | pan/tilt, video calls | ✓ | | 🟢 | |
| TC70, TC71, TC72, TCA72, TC73, TC74, TCB72 | pan/tilt | ✓ | | 🟢 | |

### Indoor and outdoor (IP65/66)

| Model | Kind | PT | Light | | Notes |
|---|---|:-:|:-:|:-:|---|
| C120 | fixed | | ✓ | 🟢 | The US spec lists RTSP and ONVIF, some regional pages leave it blank |
| TCW30 | fixed | | | 🟢 | |
| C103, C104, C113, C121, TCW61 | fixed | | ✓ | ❔ | Their spec pages do not mention RTSP |
| C206, C207, C216, C217, C236 | pan/tilt | ✓ | | 🟢 | |
| C245D, TC34, TCW34 | pan/tilt, two lenses | ✓ | | 🟢 | The wide lens only |
| C246D | pan/tilt, two lenses | ✓ | ✓ | 🟢 | The wide lens only |

### Outdoor

| Model | Kind | PT | Light | | Notes |
|---|---|:-:|:-:|:-:|---|
| C310 | fixed, Ethernet | | | 🟢 | |
| C320WS | fixed, Ethernet | | ✓ | 🟢 | |
| C325WB | fixed, Ethernet or PoE | | ✓ | 🟢 | |
| TC65 | fixed, Ethernet | | | 🟢 | |
| C500 | pan/tilt | ✓ | | 🟢 | |
| C510W | pan/tilt | ✓ | ✓ | ✅ | Tested, see above |
| C51A | pan/tilt | ✓ | ✓ | 🟢 | `-40211` reported on 1.3.4 |
| C520WS | pan/tilt, Ethernet | ✓ | ✓ | ✅ | Live view, SD card and ONVIF movement tested, see above |
| C52A, C530WS, C550WS, C560WS | pan/tilt, Ethernet | ✓ | ✓ | 🟢 | |
| C545D, C575D, TC47 | pan/tilt, two lenses | ✓ | | 🟢 | The wide lens only |
| TC40 | pan/tilt | ✓ | | 🟢 | |
| TC41, TCA41, TC42, TC43, TC46 | pan/tilt | ✓ | ✓ | 🟢 | |
| C501GW, TC40GW | pan/tilt, 4G | ✓ | | 🟡 | ONVIF (pan and tilt here) only in Ethernet mode |
| C566WB | pan/tilt, 4K | ✓ | | ❔ | No spec page found |

### Floodlights (hardwired)

| Model | Kind | PT | Light | | Notes |
|---|---|:-:|:-:|:-:|---|
| C710 | floodlight | ✓ | ✓ | 🟢 | The floodlight switch itself has not been tried |
| C720, TC53, TC55 | floodlight | | ✓ | 🟢 | Same |

### Doorbells

| Model | Kind | | Notes |
|---|---|:-:|---|
| D130 | wired | 🟢 | |
| D225, D235, TD25 | battery or wired | 🟡 | RTSP only when wired with the jumper cable and "Always-on" turned on (firmware 1.1.14 and later) |
| D235L, TD25L, D260, D260 Pro | battery or wired | ❔ | Not in TP-Link's RTSP list |
| D205, TD20, D210, TD21, TDB21 | battery | ❌ | |
| D230, TD23 | battery, through the H200 hub | ❌ | |
| D100C | chime, no camera | | Not a camera |

### Battery and solar

All ❌ for the live view. The SD card may still work on the ones that are on Wi-Fi themselves
(not through a hub): on a C410 (firmware 1.2.1), the SD card status and the fast clip download
worked with the same requests tapo-web uses, tested with pytapo while the camera was awake. Not
tried in the app itself yet, a report would be very welcome.

| Models | Kind |
|---|---|
| C400, C401, C402, C403, C410, C411, C425, C425K, C460, C465 | fixed |
| C610, C615F, C615G, C630, C660, C665G, TC50, TCH50, TC90, TCW90, TC90G | pan/tilt |
| C645D, C675D, TC93D | pan/tilt, two lenses |
| TC80, TC82, TCB82, TC85, TC92 | fixed |
| C420 (and its S1, S2, S4 kits), C400S2 | through the H200 hub |

The H200 and H500 hubs have no RTSP either.

### Good to know

* TP-Link says a camera runs only two of these at once: Tapo Care, local recording (SD card or
  hub) and RTSP/ONVIF. With all three turned on, RTSP is the one that goes, and the live view
  stops working.
* ONVIF has no two-way audio on Tapo cameras.
* On the two-lens models the second lens is on `stream6` and `stream7`, which the app does not
  use yet.

### The newer login

Cameras on recent firmware refuse the older local login with `-40211` and want the newer one
(SPAKE2+). The app speaks both and picks the right one. Seen so far:

* C510W hw 2.0 on 1.3.4 Build 260523 and C200 hw 5.0 on 1.4.6 Build 260709 (checked end to end)
* C101 hw 5.0 on 1.4.3 advertises it
* `-40211` reports on C100 (1.4.3, 1.4.4, 1.5.1), TC60 (1.4.3), C51A (1.3.4), C220 (1.4.4) and
  C125 (1.4.2). Some of them went away after turning Third-Party Compatibility off and on again
  in the Tapo app, so those cameras still accept the older login too.

The C520WS on 1.3.5 is still on the older login (it answers `-40413` to the new one).

## Add your camera

Open an issue with the [camera report](https://github.com/freeKC/tapo-web/issues/new?template=camera-report.yml)
template: model, hardware and firmware versions, what works and the diagnostic report (gear
icon, "Edit" next to the camera, "Copy the diagnostics"). Reports of what does not work are just as useful.
