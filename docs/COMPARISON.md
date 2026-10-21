# tapo-web and the other ways to use a Tapo camera

The short version is in the [README](../README.md#how-it-compares). This page has the details
behind each cell and where they come from. It was checked in October 2026 against the Tapo app,
[Tapo Control](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control) 7.2.8 for Home Assistant
and the [Frigate](https://frigate.video) documentation. If something here is wrong or out of
date, please open an issue, I'd rather fix the table than have it flatter this project.

| | tapo-web | Tapo app | Home Assistant + Tapo Control | Frigate |
|---|---|---|---|---|
| Live view in a browser, several cameras on one screen | Yes | No (1) | Yes | Yes |
| SD card by day, with a timeline and thumbnails | Yes | Yes, on the phone | Partly (2) | No (3) |
| SD clip playing within seconds | Yes | Yes | Partly (2) | No (3) |
| SD clips as MP4 files on your computer, kept after the card loops | Yes | Partly (4) | Partly (5) | No (3) |
| Animal species (fox, marten, badger, hedgehog, deer...) | Yes (6) | No (7) | No (8) | Partly (9) |
| Camera settings (privacy, LED, spotlight, tracking, detection, night vision, PTZ) | Yes | Yes | Yes, and more (10) | Partly (11) |
| Real-time alerts and automations | No | Yes | Yes | Yes |
| 24/7 recording to a disk | Basic (12) | No (13) | No (14) | Yes |
| Works away from home | No | Yes | Yes (15) | Yes (15) |
| Needs a TP-Link account or the internet once set up | No (16) | Yes (17) | No (16) | No (16) |
| What you need to run it | Python and ffmpeg, or Docker | A phone | Home Assistant | Docker, ideally a detection accelerator (18) |
| Price | Free | Free, Tapo Care is paid and optional | Free | Free, Frigate+ is paid and optional |

The new camera login (`-40211` with older tools) is not in the table: tapo-web, the Tapo app and
Tapo Control (since 7.2.0) all handle it, and Frigate only uses RTSP, which it does not change.

## Notes

1. The Tapo app runs on Android and iOS (and on Apple Silicon Macs as an iPad app). TP-Link: "The
   Tapo app is available for iOS and Android only." The tapo.tplinkcloud.com site is for the
   Tapo Care subscription, not for watching cameras.
2. The Media Browser lists the recordings by day, with event thumbnails since 7.2.0. A clip is
   downloaded first (at 8 to 10x since 7.2.0, it used to be 1x) and plays once that is done.
   There is no 24 h timeline.
3. Frigate records the RTSP stream itself and does not read the camera's SD card (a request for
   it was closed as not planned,
   [frigate#16924](https://github.com/blakeblackshear/frigate/issues/16924)).
4. One clip at a time, from 1 to 10 minutes, saved to the phone. Tapo Care keeps event clips in
   TP-Link's cloud, for 30 to 180 days depending on the plan.
5. Clips are copied into Home Assistant's storage, and a copy is deleted once the recording is
   gone from the card (`deleteFilesNoLongerPresentInCamera` in `utils.py`). So the copy never
   outlives the card.
6. With [DeepFaune](https://www.deepfaune.cnrs.fr), on the SD card clips, in the background (not
   live). It is an optional install of about 8 GB, Linux only for now, and not in the Docker
   image.
7. The camera's own detection is generic and depends on the model: person, pet, vehicle, and on some
   models sounds such as a baby crying. Tapo Care's filters sort by "people, vehicles, animals, or sounds".
8. Home Assistant gets the camera's own events over ONVIF, pet detection included, but nothing
   names a species.
9. Real-time detection on the live stream. The default model's animals include bird, cat, dog, horse,
   sheep, cow and bear. [Frigate+](https://docs.frigate.video/plus/) (paid) adds fox, deer, raccoon,
   squirrel, rabbit and a few more. Badger, wild boar, hedgehog and mustelids are only candidate
   labels so far. There is also a bird species classifier.
10. Tapo Control also has the siren and alarm, the record plan, SD card format, firmware updates
    and the sound detection settings.
11. Pan and tilt over ONVIF only. A Frigate maintainer: "Frigate intentionally doesn't implement
    camera-specific features".
12. tapo-web's continuous recording writes rolling 10-minute MP4 files while it is turned on. It
    has no retention policy yet and does not restart on its own after a dropped stream or a
    restart of the app.
13. 24/7 recording goes to the camera's SD card (or a Tapo hub for battery cameras), not to a
    computer. Tapo Care "does not support 24-hour continuous recording" (FAQ 101), although the
    US plans now list a 24/7 option.
14. Home Assistant can set the camera's own SD recording plan, and `camera.record` saves clips of
    up to an hour, but there is no DVR.
15. Through Home Assistant's own remote access, or your own VPN or reverse proxy for Frigate.
16. For every third-party tool, the camera is first set up in the Tapo app with a TP-Link
    account, and the camera account is created there. After that, tapo-web and Tapo Control use
    the TP-Link password with the camera only, on the LAN.
17. "Most Tapo cameras require the Tapo app and a TP-Link account for setup and everyday use."
    Live view and SD playback still work on the same Wi-Fi without internet, notifications and
    cloud services don't.
18. Frigate runs in Docker or as a Home Assistant app. "Windows is not officially supported", and
    a detector (Coral, a GPU, an Intel iGPU...) is "strongly recommended".

## Where the others are better

* **The Tapo app**: instant notifications, two-way audio, viewing from anywhere, sharing with
  family accounts, voice assistants, and the battery and hub cameras (the C410, C420, C425 and
  D230 have no RTSP, so tapo-web cannot show them live). And it is the official one.
* **Home Assistant with Tapo Control**: real-time events (person, pet, vehicle, line crossing,
  tamper...), automations, more settings, and a dashboard with a login. tapo-web has no login.
* **Frigate**: real-time detection with zones, a solid 24/7 recorder with retention and a review
  timeline, faces and plates, low-latency WebRTC live view (tapo-web uses HLS, which adds a few
  seconds), and any brand of RTSP camera.

go2rtc and Scrypted are left out: go2rtc is a streaming server (live view and two-way audio, no
recording, no SD card), and Scrypted's Tapo plugin is about two-way audio and HomeKit. Neither
reads the SD card.

They are not exclusive. Frigate or Home Assistant for alerts and tapo-web for the SD card and the
animals is a perfectly good setup. Keep in mind that a camera serves one SD video session at a
time, so two tools downloading from the same card at the same moment will get in each other's
way.

## Sources

* Tapo: FAQ [754](https://www.tapo.com/us/faq/754/), [312](https://www.tapo.com/us/faq/312/),
  [101](https://www.tapo.com/us/faq/101/), [166](https://www.tapo.com/us/faq/166/),
  [724](https://www.tapo.com/us/faq/724/), the [Tapo Care](https://www.tapo.com/us/tapocare/)
  page, TP-Link FAQ [2680](https://www.tp-link.com/us/support/faq/2680/) and
  [4416](https://www.tp-link.com/us/support/faq/4416/)
* Home Assistant: the [Tapo Control](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control)
  README, its releases 7.2.0, 7.2.2 and 7.2.8, and `utils.py` / `media_source.py`
* Frigate: [live](https://docs.frigate.video/configuration/live),
  [record](https://docs.frigate.video/configuration/record),
  [objects](https://docs.frigate.video/configuration/objects),
  [Frigate+](https://docs.frigate.video/plus/),
  [bird classification](https://docs.frigate.video/configuration/bird_classification),
  [cameras](https://docs.frigate.video/configuration/cameras),
  [installation](https://docs.frigate.video/frigate/installation)
* go2rtc: the [tapo source](https://github.com/AlexxIT/go2rtc/blob/master/internal/tapo/README.md)
