"""Tapo Web - a local web app to view, record and download from the camera.

Backend: FastAPI. All camera access stays on the LAN; credentials live only in
.env and are never exposed to the browser.
"""
from __future__ import annotations

import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles

from . import animals, camera, config, library, sd
from .ptz import ptz_available, ptz_move
from .recorder import recorder
from .streams import live_manager

STATIC = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    sd.startup()
    animals.startup()
    # Locate the camera at startup (best effort; UI copes if offline).
    try:
        camera.locate(force=True)
        camera.enrich_identity()
    except Exception:
        pass
    yield
    # Clean shutdown: stop every ffmpeg we started.
    live_manager.stop_all()
    recorder.stop_all()
    sd.fetcher.stop_all()
    animals.analyzer.stop()


app = FastAPI(title="Tapo Web", lifespan=lifespan)


def _require_host() -> str:
    st = camera.locate()
    if not st.online or not st.host:
        raise HTTPException(status_code=503,
                            detail="Caméra introuvable sur le LAN (hors ligne ou IP changée).")
    return st.host


# --------------------------------------------------------------------------- #
#  Status / discovery
# --------------------------------------------------------------------------- #
@app.get("/api/status")
def api_status():
    st = camera.locate()
    return {
        "online": st.online,
        "host": st.host,
        "model": st.model,
        "firmware": st.firmware,
        "streams": list(config.STREAMS.keys()),
        "ptz": ptz_available(st.host) if (st.online and st.host) else False,
        "continuous": recorder.continuous_running(),
        "recordings_dir": str(config.RECORDINGS_DIR),
    }


@app.post("/api/discover")
def api_discover():
    st = camera.locate(force=True)
    camera.enrich_identity()
    return {"online": st.online, "host": st.host, "model": st.model, "firmware": st.firmware}


# --------------------------------------------------------------------------- #
#  Settings: camera credentials are typed in the browser and stored in .env (600)
# --------------------------------------------------------------------------- #
def _is_local(request: Request) -> bool:
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1", "localhost")


@app.get("/api/setup")
def setup_state(request: Request):
    st = config.configured()
    st["needs_setup"] = not (st["camera_account"] and st["cloud_password"])
    st["can_edit"] = _is_local(request) or st["needs_setup"]
    return st


@app.post("/api/setup")
async def setup_save(request: Request):
    """Only from this machine (or when nothing is configured yet): a LAN visitor must
    not be able to re-point the app or overwrite the stored credentials."""
    st = config.configured()
    if not (_is_local(request) or not (st["camera_account"] and st["cloud_password"])):
        raise HTTPException(403, "Modifiable uniquement depuis la machine qui héberge l'application.")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "requête invalide")
    config.save_credentials({k: v for k, v in body.items() if isinstance(v, str)})
    sd._client = None
    camera.invalidate()
    return {"saved": True, **{k: v for k, v in config.configured().items() if k != "user"}}


# --------------------------------------------------------------------------- #
#  Snapshot
# --------------------------------------------------------------------------- #
@app.get("/api/snapshot")
def api_snapshot(stream: str = Query(config.DEFAULT_STREAM)):
    stream_path = config.STREAMS.get(stream)
    if not stream_path:
        raise HTTPException(400, "flux inconnu")
    host = _require_host()
    url = config.rtsp_url(host, stream_path)
    try:
        out = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp",
             "-i", url, "-frames:v", "1", "-q:v", "3",
             "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=18,
        )
    except subprocess.SubprocessError:
        camera.invalidate()  # camera may have moved/dropped -> re-discover next time
        raise HTTPException(502, "échec de la capture d'image")
    if not out.stdout:
        camera.invalidate()
        raise HTTPException(502, "pas d'image (caméra injoignable ou flux non prêt)")
    return Response(content=out.stdout, media_type="image/jpeg")


# --------------------------------------------------------------------------- #
#  Live view (HLS)
# --------------------------------------------------------------------------- #
@app.get("/live/{stream}/index.m3u8")
def live_playlist(stream: str):
    if stream not in config.STREAMS:
        raise HTTPException(404, "flux inconnu")
    host = _require_host()
    try:
        s = live_manager.ensure(stream, host)
    except (KeyError, RuntimeError) as e:
        camera.invalidate()  # ffmpeg couldn't open the stream -> camera may have moved
        raise HTTPException(502, f"live indisponible: {e}")
    if not s.playlist.exists():
        raise HTTPException(503, "flux en cours de démarrage, réessayez")
    return FileResponse(s.playlist, media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-store"})


@app.get("/live/{stream}/{segment}")
def live_segment(stream: str, segment: str):
    if stream not in config.STREAMS:
        raise HTTPException(404, "flux inconnu")
    if not segment.endswith(".ts") or "/" in segment or ".." in segment:
        raise HTTPException(404, "segment invalide")
    live_manager.touch(stream)
    seg = config.HLS_DIR / stream / segment
    if not seg.is_file():
        raise HTTPException(404, "segment expiré")
    return FileResponse(seg, media_type="video/mp2t",
                        headers={"Cache-Control": "no-store"})


@app.post("/api/live/{stream}/stop")
def live_stop(stream: str):
    if stream not in config.STREAMS:
        raise HTTPException(404, "flux inconnu")
    try:
        live_manager._get(stream).stop()
    except KeyError:
        pass
    return {"stopped": True}


# --------------------------------------------------------------------------- #
#  Recording (local DVR)
# --------------------------------------------------------------------------- #
@app.post("/api/record/start")
def record_start(stream: str = Query(config.DEFAULT_STREAM)):
    if stream not in config.STREAMS:
        raise HTTPException(400, "flux inconnu")
    host = _require_host()
    rec = recorder.start_manual(host, stream)
    return {"id": rec.id, "target": rec.target, "stream": rec.stream}


@app.post("/api/record/stop")
def record_stop(id: str = Query(...)):
    ok = recorder.stop(id)
    if not ok:
        raise HTTPException(404, "enregistrement inconnu")
    return {"stopped": True, "id": id}


@app.get("/api/record/status")
def record_status():
    return {"active": recorder.status()}


@app.post("/api/continuous/start")
def continuous_start(stream: str = Query(config.DEFAULT_STREAM)):
    if stream not in config.STREAMS:
        raise HTTPException(400, "flux inconnu")
    host = _require_host()
    rec = recorder.start_continuous(host, stream)
    return {"id": rec.id, "kind": rec.kind, "stream": rec.stream,
            "segment_seconds": config.SEGMENT_SECONDS}


@app.post("/api/continuous/stop")
def continuous_stop():
    stopped = 0
    for r in recorder.status():
        if r["kind"] == "continuous":
            if recorder.stop(r["id"]):
                stopped += 1
    return {"stopped": stopped}


# --------------------------------------------------------------------------- #
#  Library
# --------------------------------------------------------------------------- #
@app.get("/api/recordings")
def recordings_list():
    return {"recordings": library.listing()}


@app.get("/api/recordings/{name}/thumb")
def recording_thumb(name: str):
    thumb = library.thumbnail(name)
    if not thumb:
        raise HTTPException(404, "vignette indisponible")
    return FileResponse(thumb, media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})


@app.get("/api/recordings/{name}/play")
def recording_play(name: str):
    p = library.resolve(name)
    if not p:
        raise HTTPException(404, "introuvable")
    # Starlette FileResponse honours Range requests -> in-browser seeking.
    return FileResponse(p, media_type="video/mp4")


@app.get("/api/recordings/{name}/download")
def recording_download(name: str):
    p = library.resolve(name)
    if not p:
        raise HTTPException(404, "introuvable")
    return FileResponse(p, media_type="video/mp4", filename=p.name)


@app.delete("/api/recordings/{name}")
def recording_delete(name: str):
    if not library.delete(name):
        raise HTTPException(404, "introuvable")
    return {"deleted": name}


# --------------------------------------------------------------------------- #
#  PTZ (pan/tilt models, via ONVIF)
# --------------------------------------------------------------------------- #
@app.post("/api/ptz")
def api_ptz(direction: str = Query(...)):
    host = _require_host()
    if not ptz_move(host, direction):
        raise HTTPException(400, "PTZ indisponible ou direction invalide")
    return {"moved": direction}


# --------------------------------------------------------------------------- #
#  SD-card recordings (camera control API "V4" + media port, see sd.py)
# --------------------------------------------------------------------------- #
def _sd(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except sd.SdError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/sd/status")
def sd_status(force: bool = Query(False)):
    return _sd(sd.status, force)


@app.get("/api/sd/days")
def sd_days(force: bool = Query(False)):
    return _sd(sd.days, force)


@app.get("/api/sd/recordings")
def sd_recordings(date: str = Query(...), force: bool = Query(False)):
    out = _sd(sd.recordings, date, force)
    found = animals.results_for_day(date)
    queued = set(animals.analyzer.status()["queued_ids"])
    current = animals.analyzer.current
    for c in out["clips"]:
        c["analysis"] = found.get(c["id"])
        c["analyzing"] = "running" if c["id"] == current else "queued" if c["id"] in queued else None
    return out


@app.get("/api/animals/species")
def animals_species():
    return {"species": animals.species(), "status": {k: v for k, v in animals.analyzer.status().items() if k != "queued_ids"},
            "auto": animals.AUTO}


@app.get("/api/animals/clips")
def animals_clips(key: str = Query("all")):
    return {"key": key, "clips": animals.clips_with(key)}


@app.get("/api/sd/animals/status")
def sd_animals_status():
    st = animals.analyzer.status()
    st.pop("queued_ids", None)
    return st


@app.get("/api/sd/animals/days")
def sd_animals_days():
    return {"days": animals.days_with_animals()}


@app.post("/api/sd/animals/analyze")
def sd_animals_analyze(date: str = Query(...), redo: bool = Query(False)):
    """Queue every clip of a day for animal detection (lowest priority, in the background)."""
    clips = _sd(sd.recordings, date, False)["clips"]
    n = animals.analyzer.enqueue([(c["id"], date) for c in clips], redo=redo)
    return {"queued": n, "total": len(clips)}


@app.post("/api/sd/animals/stop")
def sd_animals_stop():
    return {"cleared": animals.analyzer.clear()}


@app.get("/api/sd/clips/{clip_id}/animal")
def sd_animal_frame(clip_id: str):
    p = _sd(animals.frame_path, clip_id)
    if not p:
        raise HTTPException(404, "pas d'image")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.get("/api/sd/jobs")
def sd_jobs():
    return {"jobs": sd.fetcher.jobs()}


@app.post("/api/sd/clips/{clip_id}/fetch")
def sd_fetch(clip_id: str, kind: str = Query("play")):
    return _sd(sd.fetcher.submit, clip_id, kind)


@app.get("/api/sd/clips/{clip_id}/job")
def sd_job(clip_id: str):
    _sd(sd.parse_id, clip_id)
    job = sd.fetcher.get(clip_id)
    if job is None:
        cached = sd.local_file(clip_id) is not None
        return {"id": clip_id, "state": "done" if cached else "none", "cached": cached}
    sd.fetcher.touch(clip_id)
    return {**job.snapshot(), "cached": sd.local_file(clip_id) is not None}


@app.post("/api/sd/clips/{clip_id}/cancel")
def sd_cancel(clip_id: str):
    return {"cancelled": sd.fetcher.cancel(clip_id)}


def _sd_file(clip_id: str):
    p = _sd(sd.local_file, clip_id)
    if not p:
        raise HTTPException(404, "clip non téléchargé")
    return p


@app.get("/api/sd/clips/{clip_id}/play")
def sd_play(clip_id: str):
    return FileResponse(_sd_file(clip_id), media_type="video/mp4")


@app.get("/api/sd/clips/{clip_id}/download")
def sd_download(clip_id: str):
    return FileResponse(_sd_file(clip_id), media_type="video/mp4",
                        filename=sd.download_name(clip_id))


@app.get("/api/sd/clips/{clip_id}/thumb")
def sd_thumb(clip_id: str):
    thumb = _sd(sd.thumbnail, clip_id)
    if not thumb:                           # camera busy / offline: the browser retries later
        raise HTTPException(503, "vignette pas encore disponible", headers={"Retry-After": "5"})
    return FileResponse(thumb, media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})


@app.delete("/api/sd/clips/{clip_id}")
def sd_delete_local(clip_id: str):
    """Deletes the LOCAL copy only; the SD card is never modified."""
    if not _sd(sd.delete_local, clip_id):
        raise HTTPException(404, "aucune copie locale")
    return {"deleted": clip_id}


@app.get("/sdplay/{clip_id}/index.m3u8")
def sd_hls_playlist(clip_id: str):
    _sd(sd.parse_id, clip_id)
    job = sd.fetcher.get(clip_id)
    if job is None or not job.playlist.is_file():
        raise HTTPException(404, "flux non prêt")
    sd.fetcher.touch(clip_id)
    return FileResponse(job.playlist, media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-store"})


@app.get("/sdplay/{clip_id}/{segment}")
def sd_hls_segment(clip_id: str, segment: str):
    _sd(sd.parse_id, clip_id)
    if not segment.endswith(".ts") or "/" in segment or ".." in segment:
        raise HTTPException(404, "segment invalide")
    sd.fetcher.touch(clip_id)
    seg = sd.SD_HLS / clip_id / segment
    if not seg.is_file():
        raise HTTPException(404, "segment introuvable")
    return FileResponse(seg, media_type="video/mp2t")


# --------------------------------------------------------------------------- #
#  Frontend
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"


app.mount("/static", StaticFiles(directory=STATIC), name="static")
