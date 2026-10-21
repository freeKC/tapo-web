# tapo-web

[![tests](https://github.com/freeKC/tapo-web/actions/workflows/tests.yml/badge.svg)](https://github.com/freeKC/tapo-web/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/freeKC/tapo-web)](https://github.com/freeKC/tapo-web/releases)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
![UI in English, French, German, Spanish, Polish](https://img.shields.io/badge/UI-EN%20%C2%B7%20FR%20%C2%B7%20DE%20%C2%B7%20ES%20%C2%B7%20PL-informational)

[Version française](README.fr.md)

**Your TP-Link Tapo cameras in a browser: live view, the videos on their SD card, and automatic
animal detection. Everything stays on your network. No cloud, no account, no subscription.**

It started because I wanted to know who empties the cat's bowl at 3 am.

It's a stone marten.

![stone marten on the roof at night](docs/img/marten.jpg)

## What you get

- **Every camera live**, one at a time or all of them on one screen, in HD or SD, with snapshots,
  recording on demand and a continuous DVR.
- **The SD card as a video library.** A calendar of the days with recordings, a 24 h timeline,
  the camera's own thumbnail for each clip. A clip starts playing after two seconds and downloads
  at about 10x realtime, as a normal MP4 with the original H.264 (no re-encode).
- **Animal detection** on every clip, in the background, with [DeepFaune](https://www.deepfaune.cnrs.fr).
  Pick a species and get all its videos across all days, with a frame where the animal is boxed.
  Clips are kept on disk, so they are still there long after the card has looped over them.
- **Camera settings** without the Tapo app: privacy mode, status LED, spotlight, auto tracking,
  motion and person detection with their sensitivity, night vision mode, pan and tilt with
  saved positions. Each camera only shows what it has.
- **A setup that finds your cameras.** It scans the network, says which model and firmware it
  found, walks you through the two accounts the camera wants, and tests the connection.
- **Works with recent firmware** that answers `-40211` to pytapo, python-kasa and Home Assistant.
  This app speaks the newer login (V4, SPAKE2+) and the older one, and picks the right one by
  itself.
- Runs on **Linux, WSL, Windows or Docker**. The UI is in English, French, German, Spanish and
  Polish.

## How it compares

| | tapo-web | Tapo app | Home Assistant + Tapo Control | Frigate |
|---|:-:|:-:|:-:|:-:|
| Live view in a browser, all cameras on one screen | ✅ | ❌ phone only | ✅ | ✅ |
| SD card by day, with a timeline and thumbnails | ✅ | ✅ on the phone | ➖ no timeline | ❌ |
| SD clip playing within seconds | ✅ | ✅ | ➖ once downloaded | ❌ |
| SD clips kept as MP4 files on your computer after the card loops | ✅ | ➖ one at a time, to the phone | ➖ deleted when the card drops them | ❌ |
| Animal species: fox, marten, badger, hedgehog, deer... | ✅ | ❌ | ❌ | ➖ a few, with the paid Frigate+ |
| Camera settings (privacy, LED, spotlight, detection, PTZ...) | ✅ | ✅ | ✅ even more | ➖ pan and tilt |
| Real-time alerts and automations | ❌ | ✅ | ✅ | ✅ |
| 24/7 recording to a disk | ➖ basic | ❌ | ❌ | ✅ |
| Works away from home | ❌ | ✅ | ✅ | ✅ |
| Runs on | Linux, Windows, Docker | Android, iOS | Home Assistant | Docker |

So tapo-web is the one for the SD card and for finding out which animals came by at night. For a
notification on your phone when someone walks up the drive, Home Assistant or Frigate do it
better, and you can run them side by side. Each cell is explained, with its source, in
[docs/COMPARISON.md](docs/COMPARISON.md).

## Quick start

You need Python 3.12 and ffmpeg. Then pick one:

**Linux or WSL** (with [uv](https://github.com/astral-sh/uv), or plain pip)

```bash
git clone https://github.com/freeKC/tapo-web && cd tapo-web
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
./start.sh            # then open http://localhost:8088
```

**Windows**

```powershell
git clone https://github.com/freeKC/tapo-web; cd tapo-web
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
.\start.bat           # starts the server and opens the browser
```

**Docker**

```bash
git clone https://github.com/freeKC/tapo-web && cd tapo-web
mkdir -p config data && docker compose up -d --build
```

Details, Docker Desktop included, in [docs/DOCKER.md](docs/DOCKER.md).

On the first start the settings open by themselves and look for cameras on the network. From the
Tapo app you need three things:

1. the **camera account**: in the Tapo app, the camera, then ⚙, Advanced settings, Camera
   account. It is used for the live view, recordings and pan and tilt;
2. your **TP-Link account password** (the one you log into the Tapo app with). The camera wants
   it for its SD card;
3. **Third-Party Compatibility** turned on: Me, Tapo Lab, Third-Party Compatibility. Without it
   recent firmware refuses local logins.

Everything is kept in `cameras.json` on your machine, readable by you only, and only ever sent to
your cameras. Settings can only be changed from the machine that runs the app (or the addresses
you list in `TAPO_ADMIN_HOSTS`).

<p align="center">
  <img src="docs/img/setup.png" width="420" alt="The settings on a first start: a welcome note, the cameras found on the network with their model, address and firmware, an Add button for each, and the TP-Link account password">
  <img src="docs/img/settings.png" width="270" alt="The pan and tilt pad with Save this position, then the camera settings: privacy mode, status light, spotlight, auto tracking, motion and person detection with a sensitivity, night vision mode">
</p>

## The SD card

A calendar of the days that have recordings, a 24 h timeline and one row per clip with the
camera's own thumbnail. Filters by what the camera or the detection saw (animals, a species,
people, vehicles). Play starts while the rest of the clip is still coming in. Download gives you a
plain MP4.

![SD card tab](docs/img/sdcard.png)

The app never writes to or deletes anything on the card.

## Animal detection

![animals tab](docs/img/animals.png)

It uses [DeepFaune](https://www.deepfaune.cnrs.fr) (CNRS), a model trained on European wildlife
camera traps: fox, marten and other mustelids, badger, hedgehog, cat, dog, roe deer, wild boar,
birds and so on. A YOLO detector finds the animal, a ViT classifier names it. It copes well with
infrared night footage, which is when all the interesting visitors come by.

A few things I had to add to make it usable on a garden camera:

* frames are sampled densely in the first seconds of a clip, because the animal that triggered
  the recording is often gone after three seconds
* anything that sits in exactly the same spot frame after frame gets dropped (a dark corner of
  my driveway was a "bird" every single night), unless the classifier is very sure, so a cat
  sitting still on the wall is still a cat
* species that make no sense here (the model once saw a cow on my terrace) fall back to a
  plain "animal" tag
* a wrong tag can be removed by hand, and that stays true after a new analysis

On an old GTX 1050 a one minute clip takes about 16 seconds, so a full day of recordings is done
in under 20 minutes. It also runs on CPU, just slower. Out of my first 697 clips, 418 were
nothing at all, 180 were our dog, and 5 were the marten. That is the point of the whole thing.

It is optional (about 8 GB with PyTorch and the model weights), and Linux only for now:

```bash
./ml/setup.sh
```

The model runs in its own process and virtualenv, and stops when there is nothing to do. Viewing
always has priority over background work. The whole chain and the numbers are in
[docs/DETECTION.md](docs/DETECTION.md).

## Which cameras?

| | Models |
|---|---|
| ✅ **Tested** | **C510W** (firmware 1.3.4, newer login): everything works. **C520WS** (1.3.5, older login): SD card checked by a contributor. **C200** hw 5.0: its login quirks are handled, from a report. |
| 🟢 **Should work**, indoor | C100, C101, C110, C111, C125, C840, TC60. With pan and tilt: C200, C201, C202, C210, C211, C212, C21A, C220, C222, C225, C230, C232, C250, C260, C825, TC70, TC71, TC72, TCA72, TC73, TC74, TCB72 |
| 🟢 **Should work**, outdoor | C120, C310, C320WS, C325WB, TC65, TCW30. With pan and tilt: C206, C207, C216, C217, C236, C245D, C246D, C500, C51A, C52A, C530WS, C545D, C550WS, C560WS, C575D, TC34, TCW34, TC40, TC41, TCA41, TC42, TC43, TC46, TC47. Floodlights: C710, C720, TC53, TC55. Doorbell: D130 |
| 🟡 **Partly** | D225, D235, TD25 doorbells (only when wired, with Always-on). C501GW, TC40GW (pan and tilt only over Ethernet) |
| ❔ **Unknown** | C103, C104, C113, C121, TCW61, C566WB, D235L, TD25L, D260: TP-Link doesn't say whether they have RTSP |
| ❌ **No live view** | Battery and solar cameras: C400, C401, C402, C403, C410, C411, C425, C425K, C460, C465, C610, C615F, C615G, C630, C645D, C660, C665G, C675D, TC50, TCH50, TC80, TC82, TCB82, TC85, TC90, TCW90, TC90G, TC92, TC93D, and the D205, TD20, D210, TD21, TDB21 doorbells. Behind the H200 hub: C420, C400S2, D230, TD23. The SD card may still work on the ones on Wi-Fi |

"Should work" means mains-powered, with RTSP and a microSD slot, and the same local API as the
tested ones. They have not been tried here. The details for each model (pan and tilt, lights,
two-lens models, firmware with the newer login) are in [docs/CAMERAS.md](docs/CAMERAS.md).

If you try it on a camera, please [tell me how it went](https://github.com/freeKC/tapo-web/issues/new?template=camera-report.yml),
good or bad: the settings have a "Copy the diagnostics" button that gives everything I need,
without any address or password. Each report moves a model up to ✅.

## On a phone, a TV or a headset

My other app, **[Immuch360](https://github.com/freeKC/Immuch360)**, connects to Tapo cameras
too. It shows them live on Android, Android TV, the Meta Quest and Windows, and lists the
recordings of the SD card day by day on every platform. A clip plays like any other video and can
be sent to your Immich server. Same idea: it talks to the camera on your network only, never to
TP-Link's servers.

## When something goes wrong

* **"TP-Link account password refused"** in the connection test: check the password, and turn
  on Third-Party Compatibility in the Tapo app. Don't try several passwords in a row: after a few
  wrong ones the camera blocks logins for about 30 minutes.
* **The camera is not found**: check the subnet in the settings (the first three parts of your
  network addresses, for example 192.168.1), or type the camera's IP. Under WSL the app scans the
  real LAN, not WSL's own network, which is why the subnet matters.
* **No live view, but the SD card works**: TP-Link says a camera runs only two of Tapo Care, local
  recording and RTSP at once. With a Tapo Care subscription and SD recording both on, the camera
  turns RTSP off. Battery cameras have no RTSP at all.
* **"Camera busy"** when playing a clip: the camera serves one video session at a time, and the
  Tapo app was probably open on it. Close it and try again.
* **`-40211` with other tools**: that is the newer V4 login. How it works is written up in
  [tapo-v4-protocol](https://github.com/freeKC/tapo-v4-protocol), and pytapo supports it since
  3.4.20.

## Why this exists

After a firmware update my C510W stopped answering pytapo, python-kasa and the Home Assistant
integration: `error_code -40211` on every login. The camera had moved to a new local protocol
(SPAKE2+ handshake, then an AES-CCM channel) that nobody had documented. I worked it out and wrote
it all down in **[tapo-v4-protocol](https://github.com/freeKC/tapo-v4-protocol)**. This app is
built on top of that, and the findings went into pytapo, python-kasa and the Tapo Control
integration for Home Assistant.

Clips come off the SD card at about 10x realtime (a 66 s clip in 7 s on my Wi-Fi). Tools built on
the older `playback` request get the same clip at 1x, so one minute of video takes one minute.
The difference comes from the `download` request of the camera's media port, which pytapo and
Home Assistant's Tapo Control now use too.

## Contributing

Bug reports, camera reports, translations and code are all welcome. Start with
[CONTRIBUTING.md](CONTRIBUTING.md): how to run the tests (they need no camera), the rules for
testing against a real one, and how to add a language. Thanks to
[@uzior](https://github.com/uzior) for the Windows support, the older login and the Polish UI.

Useful settings in `.env` (see `.env.example`): `TAPO_DATA_DIR` to put the videos on another
drive, `TAPO_ANALYZE_KEEP` (`all`, `animals` or `none`), `TAPO_ANALYZE_AUTO=0` to analyse only on
demand.

## Notes

* Runs fine under WSL2. If WSL goes to sleep when you close the terminal, start the app from a
  Windows scheduled task instead.
* There is no login on the web UI: anyone who can reach the port can watch the cameras.
  `start.sh` and `start.bat` listen on 127.0.0.1 only for that reason.
* Not affiliated with TP-Link.

Keywords, for people searching: Tapo C510W, C500, C520WS, C200, C210, C220, C225, C100, C110,
C120, C310, C320WS, TP-Link Tapo local API, error -40211, "Invalid authentication data",
encrypt_type 4, SPAKE2+, download Tapo SD card recordings to PC, Tapo without cloud, several Tapo
cameras in a browser, Tapo Home Assistant, pytapo alternative, wildlife camera, trail camera
software, fouine, marten, fox, hedgehog, DeepFaune, MegaDetector, PyTorch GTX 1050, Docker.

MIT license. DeepFaune has its own license (CeCILL / CC BY-SA for the weights) and is downloaded
by the setup script, not included here.
