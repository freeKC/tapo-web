# Contributing

Thanks for your interest. This started as a tool for my own camera and garden, and it gets
better every time someone tries it on another model. Bug reports, camera reports, translations
and code are all welcome.

## Running it

You need Python 3.12 and ffmpeg (with ffprobe) on the PATH.

**Linux or WSL**

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh       # in the foreground, Ctrl+C to stop
./start.sh     # or in the background, output in logs/server.log
```

Then open http://localhost:8088. On the first start the settings open by themselves and look
for cameras on the network. Cameras are kept in `cameras.json` (chmod 600), the other settings in
`.env`, see `.env.example`. `run.sh` listens on all interfaces
by default, `start.sh` on 127.0.0.1 only; `TAPO_WEB_HOST` and `TAPO_WEB_PORT` change that.

**Windows**: create the venv with `requirements-windows.txt` as described in the README, then
double-click `start.bat`. Logs are in `logs/server.log` and `logs/server-error.log`.

**Docker**: see [docs/DOCKER.md](docs/DOCKER.md).

## Stopping a local server

Closing the browser leaves the server running. With `start.bat`, the local browser shows a
**Stop server** button. It asks for confirmation, then lets Uvicorn shut down normally so the
app's cleanup stops its ffmpeg processes. Live view and downloads are interrupted.

On Linux or WSL, the same launcher is available with
`.venv/bin/python -m app.server --host 127.0.0.1 --port 8088`.
The button is hidden when using plain Uvicorn, including the existing Docker entrypoint.
Only a loopback client can request shutdown; `TAPO_ADMIN_HOSTS` does not grant this permission.
Restart using the same command or launcher as before.

## Tests

```bash
.venv/bin/python -m pytest tests -q
node --test tests/test_i18n.cjs
```

pytest also runs the Node checks when `node` is installed and skips them otherwise. A few tests
need ffmpeg and are skipped without it, and `tests/test_windows_mux.py` only runs on Windows.
GitHub Actions runs the whole suite on Linux and Windows for every push and pull request.

## Testing against a real camera

Please read this before pointing anything at a camera, yours or someone else's.

* **Logins lock.** After a few wrong passwords a Tapo camera refuses logins for a while. Never
  loop over password guesses or credential variants, keep one login attempt at a time, and when
  a login fails, stop and check the password instead of retrying. The "auto" protocol choice
  follows the same rule: it makes a second login only when the camera answers that it speaks the
  other protocol (`-40413` or `-40211`), never after a wrong password or a lockout, and the
  connection test in the settings refuses to run twice within 20 seconds.
* **One client at a time.** The camera handles one media session at a time and does not like
  being polled. Do not run two instances of the app, or the app and a test script, against the
  same camera at once: the second one breaks the first one's playback.
* **Offline first.** Most tests use fakes (a fake camera control API, a fake media server, a
  synthetic MPEG-TS stream), so they run anywhere. New code should come with offline tests in
  the same spirit. In tests, use `camera.invalid` or 192.0.2.x addresses, never a real one.
* **Read only.** The app never writes to or deletes anything on the SD card. Changes that would
  need to are worth an issue first.

## Translations

The French strings in the code are the reference keys, so French has no locale file. Each other
language has its own catalog in `app/static/locales/`. To add one:

1. Copy `app/static/locales/en.js` to `xx.js`, change the code in `TapoI18n.register("xx", ...)`
   and translate the values. Keep the keys as they are.
2. Add the language to the `names` and `regions` maps in `app/static/i18n.js`.
3. Add `<script src="/static/locales/xx.js"></script>` next to the others in
   `app/static/index.html`.
4. Add the code to the `locales` list at the top of `tests/test_i18n.cjs`.

A catalog has two sections: `ui` for the interface text and `messages` for labels and the error
messages that come from the server. A string with values in it, like
``t`Supprimer ${name} ?` ``, has the key `Supprimer {{0}} ?`, and the translation must keep the
same `{{0}}` placeholders. `tests/test_i18n.cjs` checks that every catalog has the same keys and
placeholders (and no long dashes, U+2013 or U+2014, in the translations).

When you add a UI string to the code, add it to every file in `app/static/locales/`. If you
cannot translate it, put the English text in and say so in the pull request.

## Camera support

Where the camera protocols live:

* `app/tapo_v4.py`: the V4 control login (SPAKE2+ handshake, then an AES-CCM channel) and the
  SD card queries.
* `app/control.py`: type 3 control through pytapo, and the "auto" choice between V4 and type 3.
* `app/tapo_media.py`: the media port (8800), for SD clips and their thumbnails.
* `app/cameras.py` is the camera list (`cameras.json`), `app/discovery.py` finds cameras on the
  LAN (certificate scan and the UDP 20002 discovery answer), `app/camera.py` keeps track of where
  each one is (by MAC when DHCP moves it).
* `app/features.py`: camera settings (privacy mode, LED, night vision, detection, presets...),
  shown only when the camera lists the matching component.
* `app/checks.py`: the connection test and the diagnostic report.
* `app/ptz.py` does ONVIF (identity, pan and tilt).

The protocol itself is written up at
[freeKC/tapo-v4-protocol](https://github.com/freeKC/tapo-v4-protocol).

The best first contribution is often a camera report: open an issue with the "Camera report"
template and say what works on your model. That is how [docs/CAMERAS.md](docs/CAMERAS.md) grows.

## Pull requests

* Small and focused: one fix or one feature per pull request.
* With tests, offline when possible.
* Match the style of the surrounding code. Comments, commit messages and docs are in English.
* No passwords, tokens, IP or MAC addresses in the code, logs or fixtures. `.env` stays out of git.

The pull request template has a short checklist with the same points.
