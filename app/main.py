"""Tapo Web - a local web app to view, record and download from Tapo cameras.

Backend: FastAPI. All camera access stays on the LAN; credentials live only in
cameras.json / .env and are never exposed to the browser.

Every camera route exists twice: under /cams/<id>/... for a given camera, and
without the prefix for the first camera (what older pages and scripts use).
"""
from __future__ import annotations

import re
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import animals, camera, checks, config, discovery, features, library, sd
from .cameras import Camera, CameraError, registry
from .ptz import ptz_available, ptz_move
from .recorder import recorder_for
from .streams import live_for

STATIC = Path(__file__).resolve().parent / "static"


def _locate_all():
    for cam in registry().list():
        try:
            camera.locate(cam, force=True)
            camera.enrich_identity(cam)
        except Exception:  # noqa: BLE001 - best effort, the UI copes if offline
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    sd.startup(registry().list())
    animals.startup()
    # Locate the cameras in the background (a subnet scan can take a few seconds).
    threading.Thread(target=_locate_all, daemon=True).start()
    yield
    # Clean shutdown: stop every ffmpeg we started.
    registry().stop_all()
    animals.analyzer.stop()


app = FastAPI(title="Tapo Web", lifespan=lifespan)
cam_routes = APIRouter()


def current_camera(request: Request) -> Camera:
    """The camera of /cams/<id>/..., or the first one."""
    cid = request.path_params.get("cid")
    reg = registry()
    cam = reg.get(cid) if cid else reg.default()
    if cam is None:
        if cid:
            raise HTTPException(404, "caméra inconnue")
        raise HTTPException(503, "Aucune caméra configurée.")
    return cam


def _require_host(cam: Camera) -> str:
    st = camera.locate(cam)
    if not st.online or not st.host:
        raise HTTPException(status_code=503,
                            detail="Caméra introuvable sur le LAN (hors ligne ou IP changée).")
    return st.host


# --------------------------------------------------------------------------- #
#  Cameras and account settings: typed in the browser, stored in cameras.json (600)
# --------------------------------------------------------------------------- #
# Addresses allowed to change the settings besides this machine: TAPO_ADMIN_HOSTS=192.168.1.20,172.17.0.1
# (a phone, or the Docker gateway when the port is published).
_ADMINS = {"127.0.0.1", "::1", "localhost",
           *(h.strip() for h in config._get("TAPO_ADMIN_HOSTS").split(",") if h.strip())}


def _is_local(request: Request) -> bool:
    return bool(request.client) and request.client.host in _ADMINS


def _shutdown_local(request: Request) -> bool:
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1", "localhost")


@app.post("/api/server/shutdown")
async def shutdown_server(request: Request):
    if not _shutdown_local(request) or request.headers.get("x-tapo-action") != "shutdown":
        raise HTTPException(403, "Arrêt disponible uniquement depuis cette machine.")
    callback = getattr(app.state, "request_shutdown", None)
    if not callable(callback):
        raise HTTPException(503, "Démarrez avec python -m app.server pour activer cette fonction.")
    callback()
    return {"stopping": True}


def _can_edit(request: Request) -> bool:
    """Only from this machine (or while nothing is configured yet): a LAN visitor
    must not be able to re-point the app or overwrite the stored credentials."""
    return _is_local(request) or registry().needs_setup()


def _check_edit(request: Request) -> None:
    if not _can_edit(request):
        raise HTTPException(403, "Modifiable uniquement depuis la machine qui héberge l'application.")


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "requête invalide")
    return {k: v for k, v in body.items() if isinstance(v, (str, bool))}


def _overview(request: Request) -> dict:
    reg = registry()
    return {"cameras": [c.public() for c in reg.list()], "needs_setup": reg.needs_setup(),
            "can_edit": _can_edit(request), "can_shutdown": _shutdown_local(request) and callable(getattr(app.state, "request_shutdown", None)), "subnet": reg.subnet or config.DEFAULT_SUBNET,
            "has_cloud_password": bool(reg.cloud_password)}


def _changed(cam: Camera) -> None:
    """Credentials, address or protocol changed: log in again, look again."""
    card = cam.peek("sd")
    if card is not None:
        card.reset()
    camera.invalidate(cam)


@app.get("/api/cameras")
def cameras_list(request: Request):
    return _overview(request)


@app.post("/api/cameras")
async def cameras_add(request: Request):
    _check_edit(request)
    values = await _body(request)
    try:
        cam = registry().add(values)
    except CameraError as e:
        raise HTTPException(400, str(e))
    camera.invalidate(cam)
    return cam.public()


@app.put("/api/cameras/{cid}")
async def cameras_update(cid: str, request: Request):
    _check_edit(request)
    values = await _body(request)
    try:
        cam = registry().update(cid, values)
    except CameraError as e:
        raise HTTPException(404 if "inconnue" in str(e) else 400, str(e))
    _changed(cam)
    return cam.public()


@app.delete("/api/cameras/{cid}")
def cameras_delete(cid: str, request: Request):
    _check_edit(request)
    if not registry().remove(cid):
        raise HTTPException(404, "caméra inconnue")
    return {"deleted": cid}


@app.get("/api/setup")
def setup_state(request: Request):
    return _overview(request)


@app.post("/api/setup")
async def setup_save(request: Request):
    """Account-wide settings (TP-Link password, subnet). The older single-camera
    form also sends host / user / password: they go to the first camera."""
    _check_edit(request)
    values = await _body(request)
    reg = registry()
    try:
        reg.set_account({k: values[k] for k in ("subnet", "cloud_password") if k in values})
        cam_values = {k: values[k] for k in ("host", "user", "password") if values.get(k)}
        if cam_values:
            cam = reg.default()
            cam = reg.update(cam.id, cam_values) if cam else reg.add(cam_values)
    except CameraError as e:
        raise HTTPException(400, str(e))
    for cam in reg.list():
        _changed(cam)
    return {"saved": True, **_overview(request)}


@app.post("/api/discovery")
async def discovery_scan(request: Request):
    """Tapo cameras on the subnet, marked with the configured camera they are."""
    _check_edit(request)
    try:
        values = await _body(request)
    except HTTPException:
        values = {}
    subnet = str(values.get("subnet") or registry().subnet or config.DEFAULT_SUBNET).strip().rstrip(".")
    if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}", subnet):
        raise HTTPException(400, "Sous-réseau invalide (attendu : 192.168.1).")
    found = await run_in_threadpool(discovery.scan, subnet)   # seconds of probing: not on the loop
    cams = registry().list()
    for dev in found:
        dev["camera"] = next((c.id for c in cams if (dev.get("mac") and dev["mac"] == c.cfg.mac)
                              or dev["ip"] in (c.state.host, c.cfg.host)), None)
    return {"subnet": subnet, "devices": found}


# --------------------------------------------------------------------------- #
#  Status / discovery of one camera
# --------------------------------------------------------------------------- #
@cam_routes.get("/api/status")
def api_status(cam: Camera = Depends(current_camera)):
    st = camera.locate(cam)
    return {
        "id": cam.id,
        "name": cam.name,
        "online": st.online,
        "host": st.host,
        "model": st.model,
        "firmware": st.firmware,
        "streams": list(config.STREAMS.keys()),
        # ONVIF answers on fixed cameras too: the component list, once read, says if it pans
        "ptz": (st.online and bool(st.host) and "ptz" in (features.known_components(cam) or {"ptz": 1})
                and ptz_available(cam, st.host)),
        "continuous": recorder_for(cam).continuous_running(),
        "recordings_dir": str(cam.recordings_dir),
    }


@cam_routes.post("/api/discover")
def api_discover(cam: Camera = Depends(current_camera)):
    st = camera.locate(cam, force=True)
    camera.enrich_identity(cam)
    return {"online": st.online, "host": st.host, "model": st.model, "firmware": st.firmware}


@cam_routes.post("/api/check")
def api_check(request: Request, cam: Camera = Depends(current_camera)):
    """Connection test (network, camera account, TP-Link account): logs in once."""
    _check_edit(request)
    try:
        return checks.run(cam)
    except checks.TooSoon as e:
        raise HTTPException(429, str(e))


@cam_routes.get("/api/diagnostics")
def api_diagnostics(cam: Camera = Depends(current_camera)):
    """What to paste in a camera report: no address, MAC or password in it."""
    return checks.report(cam)


# --------------------------------------------------------------------------- #
#  Snapshot
# --------------------------------------------------------------------------- #
@cam_routes.get("/api/snapshot")
def api_snapshot(stream: str = Query(config.DEFAULT_STREAM), cam: Camera = Depends(current_camera)):
    stream_path = config.STREAMS.get(stream)
    if not stream_path:
        raise HTTPException(400, "flux inconnu")
    host = _require_host(cam)
    url = cam.rtsp_url(host, stream_path)
    try:
        out = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp",
             "-i", url, "-frames:v", "1", "-q:v", "3",
             "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=18,
        )
    except subprocess.SubprocessError:
        camera.invalidate(cam)  # camera may have moved/dropped -> re-discover next time
        raise HTTPException(502, "échec de la capture d'image")
    if not out.stdout:
        camera.invalidate(cam)
        raise HTTPException(502, "pas d'image (caméra injoignable ou flux non prêt)")
    return Response(content=out.stdout, media_type="image/jpeg")


# --------------------------------------------------------------------------- #
#  Live view (HLS)
# --------------------------------------------------------------------------- #
@cam_routes.get("/live/{stream}/index.m3u8")
def live_playlist(stream: str, cam: Camera = Depends(current_camera)):
    if stream not in config.STREAMS:
        raise HTTPException(404, "flux inconnu")
    host = _require_host(cam)
    try:
        s = live_for(cam).ensure(stream, host)
    except (KeyError, RuntimeError) as e:
        camera.invalidate(cam)  # ffmpeg couldn't open the stream -> camera may have moved
        raise HTTPException(502, f"live indisponible: {e}")
    if not s.playlist.exists():
        raise HTTPException(503, "flux en cours de démarrage, réessayez")
    return FileResponse(s.playlist, media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-store"})


@cam_routes.get("/live/{stream}/{segment}")
def live_segment(stream: str, segment: str, cam: Camera = Depends(current_camera)):
    if stream not in config.STREAMS:
        raise HTTPException(404, "flux inconnu")
    if not segment.endswith(".ts") or "/" in segment or ".." in segment:
        raise HTTPException(404, "segment invalide")
    live = live_for(cam)
    live.touch(stream)
    seg = live._get(stream).dir / segment
    if not seg.is_file():
        raise HTTPException(404, "segment expiré")
    return FileResponse(seg, media_type="video/mp2t",
                        headers={"Cache-Control": "no-store"})


@cam_routes.post("/api/live/{stream}/stop")
def live_stop(stream: str, cam: Camera = Depends(current_camera)):
    if stream not in config.STREAMS:
        raise HTTPException(404, "flux inconnu")
    try:
        live_for(cam)._get(stream).stop()
    except KeyError:
        pass
    return {"stopped": True}


# --------------------------------------------------------------------------- #
#  Recording (local DVR)
# --------------------------------------------------------------------------- #
@cam_routes.post("/api/record/start")
def record_start(stream: str = Query(config.DEFAULT_STREAM), cam: Camera = Depends(current_camera)):
    if stream not in config.STREAMS:
        raise HTTPException(400, "flux inconnu")
    host = _require_host(cam)
    rec = recorder_for(cam).start_manual(host, stream)
    return {"id": rec.id, "target": rec.target, "stream": rec.stream}


@cam_routes.post("/api/record/stop")
def record_stop(id: str = Query(...), cam: Camera = Depends(current_camera)):
    ok = recorder_for(cam).stop(id)
    if not ok:
        raise HTTPException(404, "enregistrement inconnu")
    return {"stopped": True, "id": id}


@cam_routes.get("/api/record/status")
def record_status(cam: Camera = Depends(current_camera)):
    return {"active": recorder_for(cam).status()}


@cam_routes.post("/api/continuous/start")
def continuous_start(stream: str = Query(config.DEFAULT_STREAM), cam: Camera = Depends(current_camera)):
    if stream not in config.STREAMS:
        raise HTTPException(400, "flux inconnu")
    host = _require_host(cam)
    rec = recorder_for(cam).start_continuous(host, stream)
    return {"id": rec.id, "kind": rec.kind, "stream": rec.stream,
            "segment_seconds": config.SEGMENT_SECONDS}


@cam_routes.post("/api/continuous/stop")
def continuous_stop(cam: Camera = Depends(current_camera)):
    rec, stopped = recorder_for(cam), 0
    for r in rec.status():
        if r["kind"] == "continuous":
            if rec.stop(r["id"]):
                stopped += 1
    return {"stopped": stopped}


# --------------------------------------------------------------------------- #
#  Library
# --------------------------------------------------------------------------- #
@cam_routes.get("/api/recordings")
def recordings_list(cam: Camera = Depends(current_camera)):
    return {"recordings": library.listing(cam.recordings_dir)}


@cam_routes.get("/api/recordings/{name}/thumb")
def recording_thumb(name: str, cam: Camera = Depends(current_camera)):
    thumb = library.thumbnail(cam.recordings_dir, name)
    if not thumb:
        raise HTTPException(404, "vignette indisponible")
    return FileResponse(thumb, media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})


@cam_routes.get("/api/recordings/{name}/play")
def recording_play(name: str, cam: Camera = Depends(current_camera)):
    p = library.resolve(cam.recordings_dir, name)
    if not p:
        raise HTTPException(404, "introuvable")
    # Starlette FileResponse honours Range requests -> in-browser seeking.
    return FileResponse(p, media_type="video/mp4")


@cam_routes.get("/api/recordings/{name}/download")
def recording_download(name: str, cam: Camera = Depends(current_camera)):
    p = library.resolve(cam.recordings_dir, name)
    if not p:
        raise HTTPException(404, "introuvable")
    return FileResponse(p, media_type="video/mp4", filename=p.name)


@cam_routes.delete("/api/recordings/{name}")
def recording_delete(name: str, cam: Camera = Depends(current_camera)):
    if not library.delete(cam.recordings_dir, name):
        raise HTTPException(404, "introuvable")
    return {"deleted": name}


# --------------------------------------------------------------------------- #
#  PTZ (pan/tilt models, via ONVIF)
# --------------------------------------------------------------------------- #
@cam_routes.post("/api/ptz")
def api_ptz(direction: str = Query(...), cam: Camera = Depends(current_camera)):
    host = _require_host(cam)
    if not ptz_move(cam, host, direction):
        raise HTTPException(400, "PTZ indisponible ou direction invalide")
    return {"moved": direction}


# --------------------------------------------------------------------------- #
#  Camera settings (control API, see features.py)
# --------------------------------------------------------------------------- #
def _feature(fn, *args):
    try:
        return fn(*args)
    except features.SettingsError as e:
        raise HTTPException(400, str(e))
    except sd.SdError as e:
        raise HTTPException(502, str(e))


@cam_routes.get("/api/settings")
def camera_settings(force: bool = Query(False), cam: Camera = Depends(current_camera)):
    return _feature(features.read, sd.card_for(cam), force)


@cam_routes.put("/api/settings")
async def camera_settings_set(request: Request, cam: Camera = Depends(current_camera)):
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not body:
        raise HTTPException(400, "requête invalide")
    card = sd.card_for(cam)
    def apply_settings():
        for key, value in body.items():
            _feature(features.write, card, key, value)
        return _feature(features.read, card, True)
    return await run_in_threadpool(apply_settings)


@cam_routes.post("/api/presets")
async def preset_save(request: Request, cam: Camera = Depends(current_camera)):
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("name"), str):
        raise HTTPException(400, "requête invalide")
    card = sd.card_for(cam)
    await run_in_threadpool(_feature, features.save_preset, card, body["name"])
    return await run_in_threadpool(_feature, features.read, card, True)


@cam_routes.post("/api/presets/{preset_id}/goto")
def preset_goto(preset_id: str, cam: Camera = Depends(current_camera)):
    _feature(features.goto_preset, sd.card_for(cam), preset_id)
    return {"moved": preset_id}


@cam_routes.delete("/api/presets/{preset_id}")
def preset_delete(preset_id: str, cam: Camera = Depends(current_camera)):
    card = sd.card_for(cam)
    _feature(features.delete_preset, card, preset_id)
    return _feature(features.read, card, True)


# --------------------------------------------------------------------------- #
#  SD-card recordings (camera control API + media port, see sd.py)
# --------------------------------------------------------------------------- #
def _sd(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except sd.SdError as e:
        raise HTTPException(status_code=502, detail=str(e))


def _card(cam: Camera = Depends(current_camera)) -> sd.SdCard:
    return sd.card_for(cam)


@cam_routes.get("/api/sd/status")
def sd_status(force: bool = Query(False), card: sd.SdCard = Depends(_card)):
    return _sd(card.status, force)


@cam_routes.get("/api/sd/days")
def sd_days(force: bool = Query(False), card: sd.SdCard = Depends(_card)):
    return _sd(card.days, force)


@cam_routes.get("/api/sd/recordings")
def sd_recordings(date: str = Query(...), force: bool = Query(False), cam: Camera = Depends(current_camera)):
    out = _sd(sd.card_for(cam).recordings, date, force)
    found = animals.store_for(cam).results_for_day(date)
    queued = set(animals.analyzer.status(cam)["queued_ids"])
    current = animals.analyzer.current_for(cam)
    for c in out["clips"]:
        c["analysis"] = found.get(c["id"])
        c["analyzing"] = "running" if c["id"] == current else "queued" if c["id"] in queued else None
    return out


@cam_routes.get("/api/animals/species")
def animals_species(cam: Camera = Depends(current_camera)):
    status = {k: v for k, v in animals.analyzer.status(cam).items() if k != "queued_ids"}
    return {"species": animals.store_for(cam).species(), "status": status, "auto": animals.AUTO}


@cam_routes.get("/api/animals/clips")
def animals_clips(key: str = Query("all"), cam: Camera = Depends(current_camera)):
    return {"key": key, "clips": animals.store_for(cam).clips_with(key)}


@cam_routes.get("/api/sd/animals/status")
def sd_animals_status(cam: Camera = Depends(current_camera)):
    st = animals.analyzer.status(cam)
    st.pop("queued_ids", None)
    return st


@cam_routes.get("/api/sd/animals/days")
def sd_animals_days(cam: Camera = Depends(current_camera)):
    return {"days": animals.store_for(cam).days_with_animals()}


@cam_routes.post("/api/sd/animals/analyze")
def sd_animals_analyze(date: str = Query(...), redo: bool = Query(False), cam: Camera = Depends(current_camera)):
    """Queue every clip of a day for animal detection (lowest priority, in the background)."""
    clips = _sd(sd.card_for(cam).recordings, date, False)["clips"]
    n = animals.analyzer.enqueue(cam, [(c["id"], date) for c in clips], redo=redo)
    return {"queued": n, "total": len(clips)}


@cam_routes.post("/api/sd/animals/stop")
def sd_animals_stop(cam: Camera = Depends(current_camera)):
    return {"cleared": animals.analyzer.clear(cam)}


@cam_routes.post("/api/sd/clips/{clip_id}/untag")
def sd_untag(clip_id: str, key: str = Query(...), undo: bool = Query(False), cam: Camera = Depends(current_camera)):
    """Manual correction: this label is wrong for this clip. Survives re-analysis."""
    _sd(animals.store_for(cam).reject, clip_id, key, undo)
    return {"clip": clip_id, "removed": key, "undo": undo}


@cam_routes.get("/api/sd/clips/{clip_id}/animal")
def sd_animal_frame(clip_id: str, cam: Camera = Depends(current_camera)):
    p = _sd(animals.store_for(cam).frame_path, clip_id)
    if not p:
        raise HTTPException(404, "pas d'image")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@cam_routes.get("/api/sd/jobs")
def sd_jobs(card: sd.SdCard = Depends(_card)):
    return {"jobs": card.fetcher.jobs()}


@cam_routes.post("/api/sd/clips/{clip_id}/fetch")
def sd_fetch(clip_id: str, kind: str = Query("play"), card: sd.SdCard = Depends(_card)):
    return _sd(card.fetcher.submit, clip_id, kind)


@cam_routes.get("/api/sd/clips/{clip_id}/job")
def sd_job(clip_id: str, card: sd.SdCard = Depends(_card)):
    _sd(sd.parse_id, clip_id)
    job = card.fetcher.get(clip_id)
    if job is None:
        cached = card.local_file(clip_id) is not None
        return {"id": clip_id, "state": "done" if cached else "none", "cached": cached}
    card.fetcher.touch(clip_id)
    return {**job.snapshot(), "cached": card.local_file(clip_id) is not None}


@cam_routes.post("/api/sd/clips/{clip_id}/cancel")
def sd_cancel(clip_id: str, card: sd.SdCard = Depends(_card)):
    return {"cancelled": card.fetcher.cancel(clip_id)}


def _sd_file(card: sd.SdCard, clip_id: str):
    p = _sd(card.local_file, clip_id)
    if not p:
        raise HTTPException(404, "clip non téléchargé")
    return p


@cam_routes.get("/api/sd/clips/{clip_id}/play")
def sd_play(clip_id: str, card: sd.SdCard = Depends(_card)):
    return FileResponse(_sd_file(card, clip_id), media_type="video/mp4")


@cam_routes.get("/api/sd/clips/{clip_id}/download")
def sd_download(clip_id: str, card: sd.SdCard = Depends(_card)):
    return FileResponse(_sd_file(card, clip_id), media_type="video/mp4",
                        filename=card.download_name(clip_id))


@cam_routes.get("/api/sd/clips/{clip_id}/thumb")
def sd_thumb(clip_id: str, card: sd.SdCard = Depends(_card)):
    thumb = _sd(card.thumbnail, clip_id)
    if not thumb:                           # camera busy / offline: the browser retries later
        raise HTTPException(503, "vignette pas encore disponible", headers={"Retry-After": "5"})
    return FileResponse(thumb, media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})


@cam_routes.delete("/api/sd/clips/{clip_id}")
def sd_delete_local(clip_id: str, card: sd.SdCard = Depends(_card)):
    """Deletes the LOCAL copy only; the SD card is never modified."""
    if not _sd(card.delete_local, clip_id):
        raise HTTPException(404, "aucune copie locale")
    return {"deleted": clip_id}


@cam_routes.get("/sdplay/{clip_id}/index.m3u8")
def sd_hls_playlist(clip_id: str, card: sd.SdCard = Depends(_card)):
    _sd(sd.parse_id, clip_id)
    job = card.fetcher.get(clip_id)
    if job is None or not job.playlist.is_file():
        raise HTTPException(404, "flux non prêt")
    card.fetcher.touch(clip_id)
    return FileResponse(job.playlist, media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-store"})


@cam_routes.get("/sdplay/{clip_id}/{segment}")
def sd_hls_segment(clip_id: str, segment: str, card: sd.SdCard = Depends(_card)):
    _sd(sd.parse_id, clip_id)
    if not segment.endswith(".ts") or "/" in segment or ".." in segment:
        raise HTTPException(404, "segment invalide")
    card.fetcher.touch(clip_id)
    seg = card.hls_dir / clip_id / segment
    if not seg.is_file():
        raise HTTPException(404, "segment introuvable")
    return FileResponse(seg, media_type="video/mp2t")


app.include_router(cam_routes, prefix="/cams/{cid}")
app.include_router(cam_routes)


# --------------------------------------------------------------------------- #
#  Frontend
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    # version the assets by modification time: a browser never keeps a stale script
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assets = [*STATIC.glob("*.js"), *STATIC.glob("*.css"), *(STATIC / "locales").glob("*.js")]
    for p in assets:
        name = p.relative_to(STATIC).as_posix()
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={int(p.stat().st_mtime)}"')
    return Response(content=html, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"


app.mount("/static", StaticFiles(directory=STATIC), name="static")
