"use strict";

const t = TapoI18n.t;

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  cam: null,             // id of the selected camera (every camera route lives under /cams/<id>/)
  cams: [],
  setup: null,
  stream: "hd",
  hls: null,
  liveOn: false,
  manualRecId: null,
  online: false,
  ptz: false,
};

// ---------- helpers ----------
const P = () => (state.cam ? `/cams/${encodeURIComponent(state.cam)}` : "");

async function api(path, opts) {
  if (state.shuttingDown) throw new Error(t("Le serveur s'arrête."));
  const res = await fetch(path, opts);
  let body = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) body = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = (body && (body.detail || body.reason)) || res.statusText;
    throw new Error(TapoI18n.message(msg));
  }
  return body;
}

let toastTimer = null;
function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (kind ? " " + kind : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3600);
}

function fmtSize(b) {
  if (b == null) return "";
  const u = [t("o"), t("Ko"), t("Mo"), t("Go")];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  const digits = i > 0 && Math.round(b * 10) / 10 < 10 ? 1 : 0;
  return b.toLocaleString(TapoI18n.locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }) + " " + u[i];
}
function fmtDur(s) {
  if (s == null) return "";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(sec)}` : `${m}:${p(sec)}`;
}

// ---------- tabs ----------
function showTab(name) {
  const tab = $(`.tab[data-tab="${name}"]`);
  if (!tab) return;
  $$(".tab").forEach((t) => t.classList.remove("active"));
  $$(".panel").forEach((p) => p.classList.remove("active"));
  tab.classList.add("active");
  $("#tab-" + name).classList.add("active");
  if (name === "wall") startWall(); else stopWall();
  if (name === "live") loadCamSettings();
  if (name === "library") loadLibrary();
  if (name === "sd") loadSd();
  if (name === "animals") loadAnimals();
}
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    history.replaceState(null, "", "#" + tab.dataset.tab);
    showTab(tab.dataset.tab);
  });
});

// ---------- status ----------
async function refreshStatus() {
  if (state.shuttingDown) return;
  const cam = state.cam;
  try {
    const s = await api(P() + "/api/status");
    if (cam !== state.cam) return;           // another camera was picked meanwhile
    state.online = s.online;
    state.ptz = s.ptz;
    $("#statusdot").className = "dot " + (s.online ? "online" : "offline");
    $("#statustext").textContent = s.online ? t("Caméra en ligne") : t("Caméra hors ligne");
    const meta = [];
    if (s.host) meta.push(s.host);
    if (s.model) meta.push(s.model);
    if (s.firmware) meta.push("fw " + s.firmware);
    $("#statusmeta").textContent = meta.length ? "· " + meta.join(" · ") : "";
    $("#ptz-group").hidden = !s.ptz;
    $("#cont-toggle").checked = !!s.continuous;
    $("#cont-label").textContent = s.continuous ? t("Activé") : t("Désactivé");
  } catch (e) {
    if (cam !== state.cam) return;
    $("#statusdot").className = "dot offline";
    $("#statustext").textContent = state.cams.length ? t("Erreur de statut") : TapoI18n.message(e.message);
  }
}

$("#btn-discover").addEventListener("click", async () => {
  $("#statustext").textContent = t("Recherche…");
  try {
    await api(P() + "/api/discover", { method: "POST" });
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
  refreshStatus();
});

// ---------- stream selector ----------
$("#stream-seg").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  $$("#stream-seg button").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  state.stream = b.dataset.stream;
  if (state.liveOn) startLive(); // restart on the newly selected stream
});

// ---------- live ----------
function startLive() {
  const video = $("#live");
  const url = `${P()}/live/${state.stream}/index.m3u8`;
  stopHls();
  if (window.Hls && Hls.isSupported()) {
    const hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2, maxBufferLength: 6 });
    state.hls = hls;
    hls.loadSource(url);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (data.fatal) {
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
        else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
        else { toast(t("Erreur live: ") + (data.details || ""), "err"); stopLive(); }
      }
    });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = url; // Safari native HLS
    video.play().catch(() => {});
  } else {
    toast(t("HLS non supporté par ce navigateur"), "err");
    return;
  }
  state.liveOn = true;
  $("#live-overlay").hidden = true;
}
function stopHls() {
  if (state.hls) { try { state.hls.destroy(); } catch (e) {} state.hls = null; }
}
function stopLive() {
  stopHls();
  const video = $("#live");
  video.pause();
  video.removeAttribute("src");
  video.load();
  state.liveOn = false;
  $("#live-overlay").hidden = false;
  api(`${P()}/api/live/${state.stream}/stop`, { method: "POST" }).catch(() => {});
}
$("#btn-play").addEventListener("click", startLive);
$("#btn-live-start").addEventListener("click", startLive);
$("#btn-live-stop").addEventListener("click", stopLive);

// ---------- snapshot ----------
$("#btn-snap").addEventListener("click", async () => {
  toast(t("Capture…"));
  try {
    const res = await fetch(`${P()}/api/snapshot?stream=${state.stream}`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || t("échec"));
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `snapshot_${Date.now()}.jpg`;
    a.click();
    URL.revokeObjectURL(url);
    toast(t("Photo enregistrée"), "ok");
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
});

// ---------- manual recording ----------
$("#btn-rec-start").addEventListener("click", async () => {
  try {
    const r = await api(`${P()}/api/record/start?stream=${state.stream}`, { method: "POST" });
    state.manualRecId = r.id;
    $("#btn-rec-start").disabled = true;
    $("#btn-rec-stop").disabled = false;
    $("#rec-badge").hidden = false;
    $("#rec-hint").textContent = t("Enregistrement en cours → ") + r.target;
    toast(t("Enregistrement démarré"), "ok");
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
});
$("#btn-rec-stop").addEventListener("click", async () => {
  if (!state.manualRecId) return;
  try {
    await api(`${P()}/api/record/stop?id=${encodeURIComponent(state.manualRecId)}`, { method: "POST" });
    toast(t("Enregistrement arrêté"), "ok");
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
  state.manualRecId = null;
  $("#btn-rec-start").disabled = false;
  $("#btn-rec-stop").disabled = true;
  $("#rec-badge").hidden = true;
  $("#rec-hint").textContent = t("Capture le flux vers un MP4 local.");
});

// ---------- continuous DVR ----------
$("#cont-toggle").addEventListener("change", async (e) => {
  const on = e.target.checked;
  try {
    if (on) await api(`${P()}/api/continuous/start?stream=${state.stream}`, { method: "POST" });
    else await api(`${P()}/api/continuous/stop`, { method: "POST" });
    $("#cont-label").textContent = on ? t("Activé") : t("Désactivé");
    toast(on ? t("DVR continu activé") : t("DVR continu arrêté"), "ok");
  } catch (err) {
    e.target.checked = !on;
    toast(TapoI18n.message(err.message), "err");
  }
});

// ---------- PTZ ----------
$$(".ptzpad button[data-dir]").forEach((b) => {
  b.addEventListener("click", async () => {
    try { await api(`${P()}/api/ptz?direction=${b.dataset.dir}`, { method: "POST" }); }
    catch (e) { toast(TapoI18n.message(e.message), "err"); }
  });
});

// ---------- library ----------
async function loadLibrary() {
  const grid = $("#lib-grid");
  const cam = state.cam;
  try {
    const data = await api(P() + "/api/recordings");
    if (cam !== state.cam) return;
    const recs = data.recordings || [];
    $("#lib-empty").hidden = recs.length > 0;
    grid.innerHTML = "";
    for (const r of recs) grid.appendChild(card(r));
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
}

function card(r) {
  const el = document.createElement("div");
  el.className = "card";
  const res = r.width && r.height ? `${r.width}×${r.height}` : "";
  el.innerHTML = `
    <div class="thumb" style="background-image:url('${P()}/api/recordings/${encodeURIComponent(r.name)}/thumb')">
      <span class="badge ${r.kind}">${r.kind === "continuous" ? "DVR" : t("Manuel")}</span>
      ${r.duration != null ? `<span class="dur">${fmtDur(r.duration)}</span>` : ""}
    </div>
    <div class="body">
      <div class="fname">${r.name}</div>
      <div class="cmeta">
        <span>${r.created}</span><span>${fmtSize(r.size)}</span>${res ? `<span>${res}</span>` : ""}
      </div>
      <div class="actions">
        <button class="btn play">${t("▶ Lire")}</button>
        <a class="btn ghost dl" href="${P()}/api/recordings/${encodeURIComponent(r.name)}/download">⬇</a>
        <button class="btn danger del">🗑</button>
      </div>
    </div>`;
  el.querySelector(".thumb").addEventListener("click", () => openPlayer(r.name));
  el.querySelector(".play").addEventListener("click", () => openPlayer(r.name));
  el.querySelector(".del").addEventListener("click", async () => {
    if (!confirm(t`Supprimer ${r.name} ?`)) return;
    try {
      await api(`${P()}/api/recordings/${encodeURIComponent(r.name)}`, { method: "DELETE" });
      toast(t("Supprimé"), "ok");
      loadLibrary();
    } catch (e) { toast(TapoI18n.message(e.message), "err"); }
  });
  return el;
}

$("#btn-lib-refresh").addEventListener("click", loadLibrary);

// ---------- player modal ----------
function openPlayer(name) {
  $("#modal-title").textContent = name;
  const p = $("#player");
  p.src = `${P()}/api/recordings/${encodeURIComponent(name)}/play`;
  $("#modal-download").href = `${P()}/api/recordings/${encodeURIComponent(name)}/download`;
  $("#modal-download").hidden = false;
  $("#modal-sd-download").hidden = true;
  $("#modal-fetch").hidden = true;
  $("#modal").hidden = false;
  p.play().catch(() => {});
}
function closePlayer() {
  closeSdPlayer();
  const p = $("#player");
  p.pause();
  p.removeAttribute("src");
  p.load();
  $("#modal").hidden = true;
}
$("#modal-close").addEventListener("click", closePlayer);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closePlayer(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").hidden) closePlayer(); });

// ---------- SD card ----------
// Recordings are listed through the camera's control API and pulled from its
// media port (~10x realtime). "Lire" plays an HLS stream that grows while the
// clip is being fetched; the finished MP4 is kept as a local copy for instant
// replay and download. Thumbnails are the camera's own snapshots (lazy-loaded).
const sd = {
  days: [], date: null, month: null, clips: [],
  pollTimer: null,
  filter: "all", aniDays: {}, aniTimer: null,
  wantFile: new Set(),   // clip ids whose file the user asked to save once fetched
  player: null,          // { id, hls, timer, started }
};

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
}
const sdDate = (ymd) => new Date(+ymd.slice(0, 4), +ymd.slice(4, 6) - 1, +ymd.slice(6, 8));
const jobActive = (j) => !!j && ["queued", "connecting", "streaming", "finalizing"].includes(j.state);
const sdClip = (id) => sd.clips.find((c) => c.id === id);

async function loadSd(force = false) {
  const card = $("#sd-card");
  card.className = "sd-card";
  card.textContent = t("Connexion à la caméra…");
  const q = force ? "?force=true" : "";
  const cam = state.cam;
  try {
    const st = await api(P() + "/api/sd/status" + q);
    if (cam !== state.cam) return;           // another camera was picked meanwhile
    renderSdCard(st);
    if (!st.available) { $("#sd-layout").hidden = true; return; }
    const days = (await api(P() + "/api/sd/days" + q)).days || [];
    if (cam !== state.cam) return;
    sd.days = days;
    $("#sd-layout").hidden = false;
    if (!sd.days.length) {
      sd.date = null; sd.clips = [];
      renderCalendar(); renderDay();
      return;
    }
    if (!sd.date || !sd.days.includes(sd.date)) sd.date = sd.days[sd.days.length - 1];
    sd.month = sd.date.slice(0, 6);
    api(P() + "/api/sd/animals/days").then((d) => { sd.aniDays = d.days || {}; renderCalendar(); }).catch(() => {});
    renderCalendar();
    await loadDay(sd.date, force);
  } catch (e) {
    if (cam !== state.cam) return;
    $("#sd-layout").hidden = true;
    card.className = "sd-card err";
    card.replaceChildren(
      h("div", { text: t("Impossible de lire la carte SD : ") + TapoI18n.message(e.message) }),
      h("button", { class: "btn ghost retry", text: t("Réessayer"), onclick: () => loadSd(true) }));
  }
}

function renderSdCard(st) {
  const card = $("#sd-card");
  if (!st.available) {
    card.className = "sd-card err";
    card.replaceChildren(
      h("div", { class: "sd-card-line" }, h("span", { class: "pill bad", text: t("Indisponible") }),
        h("span", { text: TapoI18n.message(st.reason) || t("Carte SD inaccessible.") })),
      h("button", { class: "btn ghost retry", text: t("Réessayer"), onclick: () => loadSd(true) }));
    return;
  }
  const c = st.card || {}, local = st.cached || {};
  const used = c.total ? Math.min(100, Math.round(100 * (c.total - (c.free || 0)) / c.total)) : null;
  const line = h("div", { class: "sd-card-line" },
    h("span", { class: "pill", text: t("Carte OK") }),
    c.total != null && h("span", {}, t("Capacité "), h("b", { text: fmtSize(c.total) })),
    c.free != null && h("span", {}, t("Libre "), h("b", { text: fmtSize(c.free) })),
    c.loop && h("span", { text: t("Enregistrement en boucle") }),
    c.oldest && h("span", {}, t("Depuis le "), h("b", { text: new Date(c.oldest * 1000).toLocaleDateString(TapoI18n.locale) })),
    h("span", {}, t("Copies locales "), h("b", { text: local.count == null ? "…" : `${local.count}` }),
      local.bytes ? ` (${fmtSize(local.bytes)})` : ""));
  card.replaceChildren(line);
  if (used != null) card.append(h("div", { class: "meter", title: t`${used} % utilisé` }, h("div", { style: `width:${used}%` })));
}

// ---- calendar
function renderCalendar() {
  const grid = $("#cal-grid");
  const months = [...new Set(sd.days.map((d) => d.slice(0, 6)))];
  if (!sd.month) sd.month = months[months.length - 1] || null;
  if (!sd.month) { grid.replaceChildren(); $("#cal-title").textContent = "-"; return; }
  const y = +sd.month.slice(0, 4), m = +sd.month.slice(4, 6) - 1;
  $("#cal-title").textContent = new Date(y, m, 1).toLocaleDateString(TapoI18n.locale, { month: "long", year: "numeric" });
  $("#cal-prev").disabled = !months.some((x) => x < sd.month);
  $("#cal-next").disabled = !months.some((x) => x > sd.month);
  const lead = (new Date(y, m, 1).getDay() + 6) % 7;      // Monday-first
  const count = new Date(y, m + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(h("span", { class: "pad" }));
  for (let d = 1; d <= count; d++) {
    const ymd = sd.month + String(d).padStart(2, "0");
    const has = sd.days.includes(ymd);
    cells.push(h("button", {
      class: (has ? "has" : "") + (ymd === sd.date ? " sel" : "") + ((sd.aniDays[ymd] || {}).animals ? " ani" : ""), text: String(d),
      disabled: !has, "aria-pressed": ymd === sd.date ? "true" : "false",
      "aria-label": sdDate(ymd).toLocaleDateString(TapoI18n.locale, { day: "numeric", month: "long" }) + (has ? "" : t(" (aucun enregistrement)")),
      onclick: () => { sd.date = ymd; renderCalendar(); loadDay(ymd); },
    }));
  }
  grid.replaceChildren(...cells);
}
function shiftMonth(dir) {
  const months = [...new Set(sd.days.map((d) => d.slice(0, 6)))];
  const next = dir < 0 ? months.filter((x) => x < sd.month).pop() : months.find((x) => x > sd.month);
  if (next) { sd.month = next; renderCalendar(); }
}
$("#cal-prev").addEventListener("click", () => shiftMonth(-1));
$("#cal-next").addEventListener("click", () => shiftMonth(1));

// ---- day view
async function loadDay(date, force = false) {
  $("#sd-day-title").textContent = sdDate(date).toLocaleDateString(TapoI18n.locale,
    { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  $("#sd-day-meta").textContent = t("Chargement…");
  sd.clips = [];
  renderDay();
  $("#sd-empty").hidden = true;
  try {
    const data = await api(`${P()}/api/sd/recordings?date=${date}` + (force ? "&force=true" : ""));
    if (date !== sd.date) return;            // the user already picked another day
    sd.clips = data.clips || [];
    renderDay();
    if (sd.clips.some((c) => jobActive(c.job))) startSdPolling();
  } catch (e) {
    if (date !== sd.date) return;
    $("#sd-day-meta").textContent = "";
    $("#sd-clips").replaceChildren(h("div", { class: "empty" },
      h("div", { text: t("Impossible de lister ce jour : ") + TapoI18n.message(e.message) }),
      h("button", { class: "btn ghost retry", text: t("Réessayer"), onclick: () => loadDay(date, true) })));
  }
}

function renderDay() {
  const total = sd.clips.reduce((a, c) => a + c.duration, 0);
  $("#sd-day-meta").textContent = sd.clips.length
    ? t`${sd.clips.length} clip${sd.clips.length > 1 ? "s" : ""} · ${fmtDur(total)} de vidéo` : "";
  $("#sd-empty").hidden = sd.clips.length > 0;
  if (!sd.date) $("#sd-day-title").textContent = t("Aucun enregistrement sur la carte");

  renderSdFilters();
  $("#sd-timeline").replaceChildren(...sd.clips.map((c) => h("button", {
    class: "tl-seg" + (c.type === 1 ? " cont" : "") + (c.cached ? " local" : "") + (c.analysis && c.analysis.animal ? " animal" : ""),
    "data-id": c.id, title: `${c.time} · ${fmtDur(c.duration)}`, "aria-label": t`Clip de ${c.time}`,
    style: `left:${(100 * c.day_second / 86400).toFixed(3)}%;width:${(100 * c.duration / 86400).toFixed(3)}%`,
    onclick: () => openSdPlayer(c.id),
    onmouseenter: () => hotClip(c.id, true), onmouseleave: () => hotClip(c.id, false),
  })));

  const rows = [];
  let hour = null;
  for (const c of sd.clips.filter(sdMatchesFilter)) {
    const hh = c.time.slice(0, 2);
    if (hh !== hour) { hour = hh; rows.push(h("div", { class: "clip-hour", text: `${hh} h` })); }
    rows.push(clipRow(c));
  }
  $("#sd-clips").replaceChildren(...rows);
}

function hotClip(id, on) {
  document.querySelectorAll(`[data-id="${id}"]`).forEach((el) => el.classList.toggle("hot", on));
}

function clipRow(c) {
  const job = c.job, busy = jobActive(job);
  const tags = [h("span", { class: "tag " + (c.type === 1 ? "cont" : "det"), text: TapoI18n.label(c.type_label) })];
  const an = c.analysis;
  if (an && an.status === "done") {
    for (const d of an.detections) tags.push(h("button", {
      class: "tag " + (d.group === "animal" ? "animal" : "person"),
      title: t`${d.hits} images, vu de ${fmtDur(d.first)} à ${fmtDur(d.last)} - cliquer pour lire à cet instant`,
      text: `${d.emoji} ${TapoI18n.label(d.label)}` + (d.group === "animal" && d.key !== "animal" ? ` ${Math.round(d.score * 100)} %` : ""),
      onclick: () => openSdPlayer(c.id, Math.max(0, d.first - 2)) }),
      untagButton(c.id, d, () => { an.detections = an.detections.filter((x) => x !== d);
        an.animal = an.detections.some((x) => x.group === "animal"); renderDay(); }));
    if (!an.detections.length) tags.push(h("span", { class: "tag none", text: t("analysé · rien") }));
  } else if (an && an.status === "error") tags.push(h("span", { class: "tag err", text: t("analyse : ") + (TapoI18n.message(an.error) || t("échec")) }));
  if (c.analyzing) tags.push(h("span", { class: "tag", text: c.analyzing === "running" ? t("🔎 analyse en cours…") : t("analyse en attente") }));
  if (c.cached) tags.push(h("span", { class: "tag local", text: t("copie locale") + (c.size ? " · " + fmtSize(c.size) : "") }));
  if (job && job.state === "error") tags.push(h("span", { class: "tag err", text: TapoI18n.message(job.error) || t("échec") }));

  const actions = [h("button", { class: "btn", "data-act": "play", text: t("▶ Lire"), onclick: () => openSdPlayer(c.id) })];
  if (c.cached) {
    actions.push(h("a", { class: "btn ghost", "data-act": "dl", text: "⬇", title: t("Télécharger le fichier MP4"), "aria-label": t("Télécharger"),
      href: `${P()}/api/sd/clips/${c.id}/download`, download: true }));
    actions.push(h("button", { class: "btn ghost", "data-act": "del", text: "🗑", title: t("Supprimer la copie locale (la carte SD n'est pas modifiée)"),
      "aria-label": t("Supprimer la copie locale"), onclick: () => deleteSdLocal(c.id) }));
  } else if (busy) {
    actions.push(h("button", { class: "btn ghost", "data-act": "cancel", text: t("✕ Annuler"), onclick: () => cancelSdJob(c.id) }));
  } else {
    actions.push(h("button", { class: "btn ghost", "data-act": "dl", text: "⬇", title: t("Récupérer depuis la caméra puis télécharger"),
      "aria-label": t("Télécharger"), onclick: () => downloadSdClip(c.id) }));
  }

  const aniFrame = c.analysis && c.analysis.animal && c.analysis.frame;
  const img = h("img", { src: aniFrame ? `${P()}/api/sd/clips/${c.id}/animal` : `${P()}/api/sd/clips/${c.id}/thumb`, loading: "lazy", alt: "", width: 96, height: 54,
    onerror: (e) => retryThumb(e.target, c.id) });   // camera busy -> ask again; ▶ shows through meanwhile
  const thumb = h("button", { class: "clip-thumb", "aria-label": t`Lire le clip de ${c.time}`,
    onclick: () => openSdPlayer(c.id) }, h("span", { text: "▶" }), img);

  const row = h("div", { class: "clip" + (c.analysis && c.analysis.animal ? " ani" : ""), "data-id": c.id,
      onmouseenter: () => hotClip(c.id, true), onmouseleave: () => hotClip(c.id, false) },
    thumb,
    h("div", { class: "clip-main" },
      h("div", { class: "clip-time" }, `${c.time} → ${c.time_end}`, h("small", { text: fmtDur(c.duration) })),
      h("div", { class: "clip-tags" }, tags)),
    h("div", { class: "clip-actions" }, actions));
  if (busy) {
    const pct = job.total ? Math.min(100, 100 * job.progress / job.total) : 0;
    row.append(h("div", { class: "clip-progress" },
      h("div", { class: "fetchbar-track" }, h("div", { class: "fetchbar-fill", style: `width:${pct}%` })),
      h("span", { text: jobText(job) })));
  }
  return row;
}

function retryThumb(img, id) {
  const n = +(img.dataset.tries || 0) + 1;
  if (n > 4 || !img.isConnected) { img.remove(); return; }
  img.dataset.tries = n;
  img.style.visibility = "hidden";
  setTimeout(() => {
    if (!img.isConnected) return;
    img.onload = () => { img.style.visibility = ""; };
    img.src = `${P()}/api/sd/clips/${id}/thumb?r=${n}`;
  }, 3000 * n);
}

function jobText(job) {
  const speed = ((job.bytes_per_second || 0) / 1000000).toLocaleString(TapoI18n.locale, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }) + " " + t("Mo/s");
  const idle = job.idle_seconds >= 3
    ? " · " + t`Aucune donnée reçue depuis ${Math.floor(job.idle_seconds)} s` : "";
  switch (job.state) {
    case "queued": return t("En file d'attente…");
    case "connecting": return t("Connexion à la caméra…");
    case "streaming": return t`Récupération depuis la caméra… ${fmtDur(job.progress)} / ${fmtDur(job.total)}` + " · " + speed + idle;
    case "finalizing": return t("Finalisation du MP4…");
    default: return "";
  }
}

function refreshClip(id) {
  const c = sdClip(id);
  const old = document.querySelector(`.clip[data-id="${id}"]`);
  if (c && old) {
    // Patch the live row instead of replacing it: keyboard focus and the already
    // loaded thumbnail survive the 1-2 s progress ticks.
    const fresh = clipRow(c);
    const focused = old.contains(document.activeElement) ? document.activeElement.dataset.act : null;
    for (const sel of [".clip-tags", ".clip-actions", ".clip-progress"]) {
      const a = old.querySelector(sel), b = fresh.querySelector(sel);
      if (a && b) a.replaceWith(b); else if (a) a.remove(); else if (b) old.append(b);
    }
    if (focused) {
      const again = old.querySelector(`[data-act="${focused}"]`) || old.querySelector("[data-act]");
      if (again) again.focus();
    }
  }
  const seg = document.querySelector(`.tl-seg[data-id="${id}"]`);
  if (c && seg) seg.classList.toggle("local", !!c.cached);
}

function applyJob(id, job) {
  // Fold a job snapshot into the clip model; returns true when the fetch just completed.
  const c = sdClip(id);
  const finished = job.state === "done" || job.cached === true;
  if (c) {
    const was = c.cached;
    c.cached = finished || c.cached;
    c.job = finished || job.state === "none" || job.state === "cancelled" ? null : job;
    refreshClip(id);
    if (finished && !was) {
      // pick up the file size for the "copie locale" tag (sizes are read from the local
      // disk on every call: no need to force a new camera listing)
      api(`${P()}/api/sd/recordings?date=${sd.date}`).then((d) => {
        const fresh = (d.clips || []).find((x) => x.id === id);
        if (fresh && sdClip(id)) { sdClip(id).size = fresh.size; refreshClip(id); }
      }).catch(() => {});
    }
  }
  if (finished && sd.wantFile.delete(id)) saveSdFile(id);
  if (["error", "cancelled", "none"].includes(job.state)) sd.wantFile.delete(id);   // never poll forever
  if (finished) api(P() + "/api/sd/status").then(renderSdCard).catch(() => {});            // local-copies counter
  return finished;
}

function saveSdFile(id) {
  const a = h("a", { href: `${P()}/api/sd/clips/${id}/download`, download: true });
  document.body.append(a); a.click(); a.remove();
  toast(t("Téléchargement du fichier MP4 lancé"), "ok");
}

async function downloadSdClip(id) {
  try {
    const job = await api(`${P()}/api/sd/clips/${id}/fetch?kind=download`, { method: "POST" });
    sd.wantFile.add(id);
    if (!applyJob(id, job)) { toast(t("Récupération lancée : le fichier sera téléchargé à la fin.")); startSdPolling(); }
  } catch (e) { sd.wantFile.delete(id); toast(TapoI18n.message(e.message), "err"); }
}

async function cancelSdJob(id) {
  sd.wantFile.delete(id);
  try { await api(`${P()}/api/sd/clips/${id}/cancel`, { method: "POST" }); } catch (e) {}
  const c = sdClip(id);
  if (c) { c.job = null; refreshClip(id); }
}

async function deleteSdLocal(id) {
  if (!confirm(t("Supprimer la copie locale de ce clip ?\n(L'enregistrement reste sur la carte SD de la caméra.)"))) return;
  try {
    await api(`${P()}/api/sd/clips/${id}`, { method: "DELETE" });
    const c = sdClip(id);
    if (c) { c.cached = false; c.size = null; refreshClip(id); }
    toast(t("Copie locale supprimée"), "ok");
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
}

// ---- background polling of fetch jobs (list rows)
function startSdPolling() {
  if (sd.pollTimer) return;
  sd.pollTimer = setInterval(pollSdJobs, 2000);
}
async function pollSdJobs() {
  const tracked = sd.clips.filter((c) => jobActive(c.job)).map((c) => c.id);
  for (const id of sd.wantFile) if (!tracked.includes(id)) tracked.push(id);
  if (!tracked.length) { clearInterval(sd.pollTimer); sd.pollTimer = null; return; }
  for (const id of tracked) {
    if (sd.player && sd.player.id === id) continue;      // the player polls this one itself
    try { applyJob(id, await api(`${P()}/api/sd/clips/${id}/job`)); } catch (e) {}
  }
}

// ---- SD player (HLS while fetching, MP4 once local)
// Every open gets its own `pl` object: late answers of a previous open (even of
// the same clip) are recognised by identity and dropped.
async function openSdPlayer(id, startAt = 0) {
  const c = sdClip(id);
  if (!c) return;
  closeSdPlayer();
  sdSeekOnce(startAt);
  $("#modal-title").textContent = c.title ||
    t`Carte SD · ${sdDate(sd.date).toLocaleDateString(TapoI18n.locale)} ${c.time} (${fmtDur(c.duration)})`;
  $("#modal").hidden = false;
  const pl = sd.player = { id, hls: null, timer: null, started: false, noHls: false, dead: false };
  setSdModalState(c, c.job);
  if (c.cached) { playSdFile(id); return; }
  try {
    const job = await api(`${P()}/api/sd/clips/${id}/fetch?kind=play`, { method: "POST" });
    if (sd.player !== pl) {
      // closed (or replaced) before the answer: nobody watches this fetch any more
      if (jobActive(job) && job.kind === "play" && !sd.wantFile.has(id) && !(sd.player && sd.player.id === id)) cancelSdJob(id);
      return;
    }
    sd.clips.forEach((x) => { if (x.id !== id && x.job && x.job.kind === "play") { x.job = null; refreshClip(x.id); } });
    onSdPlayerJob(pl, job);
    pollSdPlayer(pl);
  } catch (e) {
    toast(TapoI18n.message(e.message), "err");
    if (sd.player === pl) closePlayer();                  // a late failure of A must not close B
  }
}

function untagButton(clipId, d, after) {
  return h("button", { class: "tag-x", title: t`Ce n'est pas « ${TapoI18n.label(d.label)} » : retirer cette étiquette`,
    "aria-label": t`Retirer l'étiquette ${TapoI18n.label(d.label)}`, text: "✕",
    onclick: async (e) => {
      e.stopPropagation();
      if (!confirm(t`Retirer l'étiquette « ${TapoI18n.label(d.label)} » de cette vidéo ?`)) return;
      try {
        await api(`${P()}/api/sd/clips/${clipId}/untag?key=${encodeURIComponent(d.key)}`, { method: "POST" });
        toast(t("Étiquette retirée"), "ok");
        after();
      } catch (err) { toast(TapoI18n.message(err.message), "err"); }
    } });
}

function sdSeekOnce(t) {
  const video = $("#player");
  if (!(t > 0)) return;
  const go = () => { try { if (video.duration > t || !isFinite(video.duration)) video.currentTime = t; } catch (e) {} };
  video.addEventListener("loadedmetadata", go, { once: true });
}

// ---- animal detection (DeepFaune, local): filters, day analysis, live refresh
function sdMatchesFilter(c) {
  const f = sd.filter, dets = (c.analysis && c.analysis.detections) || [];
  if (f === "all") return true;
  if (f === "animals") return dets.some((d) => d.group === "animal");
  return dets.some((d) => d.key === f);
}
function renderSdFilters() {
  const counts = new Map();
  let animals = 0;
  for (const c of sd.clips) {
    const dets = (c.analysis && c.analysis.detections) || [];
    if (dets.some((d) => d.group === "animal")) animals++;
    for (const d of dets) counts.set(d.key, { n: ((counts.get(d.key) || {}).n || 0) + 1, d });
  }
  const chip = (key, text) => h("button", { class: "chip" + (sd.filter === key ? " on" : ""), text,
    "aria-pressed": sd.filter === key ? "true" : "false", onclick: () => { sd.filter = key; renderDay(); } });
  const chips = [chip("all", t`Tous ${sd.clips.length}`)];
  if (counts.size) chips.push(chip("animals", t`🐾 Animaux ${animals}`));
  for (const [key, { n, d }] of counts) chips.push(chip(key, `${d.emoji} ${TapoI18n.label(d.label)} ${n}`));
  if (sd.filter !== "all" && sd.filter !== "animals" && !counts.has(sd.filter)) sd.filter = "all";
  $("#sd-filters").replaceChildren(...chips);
  const done = sd.clips.filter((c) => c.analysis && c.analysis.status === "done").length;
  const pending = sd.clips.filter((c) => c.analyzing).length;
  $("#sd-analyze-status").textContent = pending ? t`Analyse : ${done} / ${sd.clips.length} (reste ${pending})…`
    : done ? t`${done} / ${sd.clips.length} clips analysés` : "";
  $("#btn-analyze").hidden = !sd.clips.length || (done === sd.clips.length && !pending);
  if (pending && !sd.aniTimer) sd.aniTimer = setInterval(refreshAnalysis, 6000);
  if (!pending && sd.aniTimer) { clearInterval(sd.aniTimer); sd.aniTimer = null; }
}
async function refreshAnalysis() {
  if (!sd.date || !$("#tab-sd").classList.contains("active")) return;
  try {
    const date = sd.date, data = await api(`${P()}/api/sd/recordings?date=${date}`);
    if (date !== sd.date) return;
    const fresh = new Map((data.clips || []).map((c) => [c.id, c]));
    let changed = false;
    for (const c of sd.clips) {
      const f = fresh.get(c.id);
      if (!f) continue;
      if (JSON.stringify([c.analysis, c.analyzing, c.cached]) !== JSON.stringify([f.analysis, f.analyzing, f.cached])) {
        Object.assign(c, { analysis: f.analysis, analyzing: f.analyzing, cached: f.cached, size: f.size });
        changed = true;
      }
    }
    if (changed && !sd.player) renderDay(); else renderSdFilters();
    if (changed) api(P() + "/api/sd/animals/days").then((d) => { sd.aniDays = d.days || {}; renderCalendar(); }).catch(() => {});
  } catch (e) {}
}
$("#btn-analyze").addEventListener("click", async () => {
  try {
    const st = await api(P() + "/api/sd/animals/status");
    if (!st.available) { toast(t("Moteur d'analyse non installé : lancer ~/tapo-web/ml/setup.sh"), "err"); return; }
    const r = await api(`${P()}/api/sd/animals/analyze?date=${sd.date}`, { method: "POST" });
    toast(r.queued ? t`${r.queued} clips en file d'analyse (≈ ${Math.ceil(r.queued * 25 / 60)} min, en arrière-plan)` : t("Tout est déjà analysé"), "ok");
    refreshAnalysis();
    if (!sd.aniTimer) sd.aniTimer = setInterval(refreshAnalysis, 6000);
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
});

function pollSdPlayer(pl) {
  if (pl.timer || sd.player !== pl) return;
  const c = sdClip(pl.id);
  if (!c || !jobActive(c.job)) return;
  pl.timer = setInterval(async () => {
    try {
      const job = await api(`${P()}/api/sd/clips/${pl.id}/job`);
      if (sd.player === pl) onSdPlayerJob(pl, job);       // drop answers that outlived their player
    } catch (e) {}
  }, 1000);
}

function playSdFile(id) {
  const video = $("#player");
  video.src = `${P()}/api/sd/clips/${id}/play`;
  video.play().catch(() => {});
  if (sd.player) sd.player.started = true;
}

function onSdPlayerJob(pl, job) {
  if (sd.player !== pl || !job || job.id !== pl.id) return;
  const id = pl.id;
  const finished = applyJob(id, job);
  setSdModalState(sdClip(id), job);
  if (["error", "cancelled", "none"].includes(job.state)) {
    if (job.state === "error") toast(TapoI18n.message(job.error) || t("Échec de la récupération"), "err");
    clearInterval(pl.timer); pl.timer = null;
    pl.dead = true;                                       // the server deleted the HLS output:
    if (pl.hls) { try { pl.hls.stopLoad(); } catch (e) {} }   // play out the buffer, never reload
    if (!pl.started) closePlayer();
    return;
  }
  if (!pl.started && !pl.noHls && job.hls_ready) startSdHls(pl);
  if (finished) {
    clearInterval(pl.timer); pl.timer = null;
    if (!pl.started) playSdFile(id);                      // short clip, or HLS gave up: play the MP4
    else if (pl.hls) pl.timer = setInterval(() => {       // keep the HLS copy alive while paused
      if (sd.player === pl) api(`${P()}/api/sd/clips/${id}/job`).catch(() => {});
    }, 120000);
  }
}

function startSdHls(pl) {
  pl.started = true;
  const video = $("#player");
  const url = `${P()}/sdplay/${pl.id}/index.m3u8`;
  if (!(window.Hls && Hls.isSupported())) {               // Safari: native HLS
    video.src = url;
    video.play().catch(() => {});
    return;
  }
  const hls = pl.hls = new Hls({ startPosition: 0, maxBufferLength: 30, liveDurationInfinity: false });
  let recoveries = 0;
  hls.loadSource(url);
  hls.attachMedia(video);
  hls.on(Hls.Events.MANIFEST_PARSED, () => video.play().catch(() => {}));
  hls.on(Hls.Events.ERROR, (_e, data) => {
    if (!data.fatal || sd.player !== pl) return;
    if (pl.dead) { hls.stopLoad(); return; }
    const D = Hls.ErrorDetails;
    const manifest = [D.MANIFEST_LOAD_ERROR, D.MANIFEST_LOAD_TIMEOUT, D.MANIFEST_PARSING_ERROR].includes(data.details);
    if (!manifest && ++recoveries <= 2 && data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
    else if (!manifest && recoveries <= 2 && data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
    else sdHlsGaveUp(pl);                                 // never loop: play the MP4 once complete
  });
}

function sdHlsGaveUp(pl) {
  if (sd.player !== pl) return;
  if (pl.hls) { try { pl.hls.destroy(); } catch (e) {} pl.hls = null; }
  pl.noHls = true;
  const c = sdClip(pl.id);
  if (c && c.cached) { playSdFile(pl.id); return; }
  pl.started = false;                                     // onSdPlayerJob() plays the MP4 when done
  $("#modal-fetch-text").textContent = t("Lecture dès la fin de la récupération…");
}

function setSdModalState(c, job) {
  const cached = !!(c && c.cached);
  const link = $("#modal-download"), btn = $("#modal-sd-download"), bar = $("#modal-fetch");
  link.hidden = !cached;
  if (cached && c) link.href = `${P()}/api/sd/clips/${c.id}/download`;
  btn.hidden = cached;
  const waiting = c && sd.wantFile.has(c.id);
  btn.disabled = !!waiting;
  btn.textContent = waiting ? t("⬇ Téléchargement à la fin de la récupération…") : t("⬇ Télécharger");
  const busy = jobActive(job);
  bar.hidden = !busy;
  if (busy) {
    $("#modal-fetch-fill").style.width = (job.total ? Math.min(100, 100 * job.progress / job.total) : 0) + "%";
    $("#modal-fetch-text").textContent = sd.player && sd.player.noHls
      ? t("Lecture dès la fin de la récupération… ") + jobText(job) : jobText(job);
  }
}

$("#modal-sd-download").addEventListener("click", async () => {
  const pl = sd.player;
  if (!pl) return;
  try {
    const job = await api(`${P()}/api/sd/clips/${pl.id}/fetch?kind=download`, { method: "POST" });
    sd.wantFile.add(pl.id);
    if (sd.player === pl) { pl.dead = false; onSdPlayerJob(pl, job); pollSdPlayer(pl); }   // (re)start this player's poll
    else { applyJob(pl.id, job); startSdPolling(); }
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
});

function closeSdPlayer() {
  const pl = sd.player;
  if (!pl) return;
  sd.player = null;
  if (pl.timer) clearInterval(pl.timer);
  if (pl.hls) { try { pl.hls.destroy(); } catch (e) {} }
  const c = sdClip(pl.id);
  // A play-only fetch has no audience any more: free the camera. A requested
  // download keeps going in the background (and the row shows its progress).
  if (c && jobActive(c.job) && c.job.kind === "play" && !sd.wantFile.has(pl.id)) cancelSdJob(pl.id);
  else if (c && jobActive(c.job)) startSdPolling();
  $("#modal-fetch").hidden = true;
  $("#modal-sd-download").hidden = true;
  $("#modal-download").hidden = false;
}

// ---------- Animaux: every detection, across all days (clips kept locally for good) ----------
const ani = { key: null, species: [], clips: [] };

async function loadAnimals() {
  const cam = state.cam;
  try {
    const d = await api(P() + "/api/animals/species");
    if (cam !== state.cam) return;
    ani.species = d.species || [];
    const st = d.status || {};
    $("#ani-status").textContent = !st.available ? t("Moteur d'analyse non installé (ml/setup.sh)")
      : (st.current || st.queued) ? t`Analyse automatique en cours - ${st.queued + (st.current ? 1 : 0)} clips en attente`
      : d.auto ? t("Analyse automatique active - tout est à jour") : "";
    const total = ani.species.reduce((a, x) => a + x.count, 0);
    const item = (key, emoji, label, count, last) => h("button", {
      class: "ani-sp" + (ani.key === key ? " sel" : ""), "aria-pressed": ani.key === key ? "true" : "false",
      onclick: () => { ani.key = key; loadAnimals(); } },
      h("span", { class: "emo", text: emoji }),
      h("span", {}, label, last ? h("small", { text: t("vu le ") + new Date(last * 1000).toLocaleDateString(TapoI18n.locale) }) : null),
      h("span", { class: "cnt", text: String(count) }));
    if (!ani.key) ani.key = (location.hash.split("/")[1] || "all");   // deep link: /#animals/mustelid
    $("#ani-species").replaceChildren(item("all", "🐾", t("Tous les animaux"), total, 0),
      ...ani.species.map((x) => item(x.key, x.emoji, TapoI18n.label(x.label), x.count, x.last)));
    $("#ani-empty").hidden = ani.species.length > 0;
    await loadAnimalClips();
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
}

async function loadAnimalClips() {
  const key = ani.key;
  const d = await api(`${P()}/api/animals/clips?key=${encodeURIComponent(key)}`);
  if (key !== ani.key) return;
  ani.clips = d.clips || [];
  const sp = ani.species.find((x) => x.key === key);
  $("#ani-title").textContent = sp ? `${sp.emoji} ${TapoI18n.label(sp.label)}` : t("🐾 Tous les animaux");
  $("#ani-meta").textContent = ani.clips.length ? t`${ani.clips.length} vidéo${ani.clips.length > 1 ? "s" : ""}` : "";
  const rows = [];
  let day = null;
  for (const c of ani.clips) {
    if (c.day !== day) { day = c.day; rows.push(h("div", { class: "clip-hour",
      text: sdDate(c.day).toLocaleDateString(TapoI18n.locale, { weekday: "long", day: "numeric", month: "long", year: "numeric" }) })); }
    rows.push(animalRow(c));
  }
  $("#ani-clips").replaceChildren(...rows);
}

function animalRow(c) {
  const play = (t) => playAnimalClip(c, t);
  const tags = c.detections.flatMap((d) => [h("button", {
    class: "tag " + (d.group === "animal" ? "animal" : "person"),
    title: t`${d.hits} images, vu de ${fmtDur(d.first)} à ${fmtDur(d.last)}`,
    text: `${d.emoji} ${TapoI18n.label(d.label)}` + (d.group === "animal" && d.key !== "animal" ? ` ${Math.round(d.score * 100)} %` : ""),
    onclick: () => play(Math.max(0, d.first - 2)) }), untagButton(c.id, d, loadAnimals)]);
  tags.push(h("span", { class: "tag " + (c.cached ? "local" : "none"), text: c.cached ? t("copie locale") : t("sur la carte SD") }));
  const img = h("img", { src: c.frame ? `${P()}/api/sd/clips/${c.id}/animal` : `${P()}/api/sd/clips/${c.id}/thumb`, loading: "lazy", alt: "",
    onerror: (e) => e.target.remove() });
  const actions = [h("button", { class: "btn", text: t("▶ Lire"), onclick: () => play(Math.max(0, c.first - 2)) })];
  if (c.cached) actions.push(h("a", { class: "btn ghost", text: "⬇", "aria-label": t("Télécharger"), title: t("Télécharger le MP4"),
    href: `${P()}/api/sd/clips/${c.id}/download`, download: true }));
  return h("div", { class: "clip ani", "data-id": c.id },
    h("button", { class: "clip-thumb", "aria-label": t`Lire la vidéo du ${c.date} ${c.time}`, onclick: () => play(Math.max(0, c.first - 2)) },
      h("span", { text: "▶" }), img),
    h("div", { class: "clip-main" },
      h("div", { class: "clip-time" }, `${c.date} · ${c.time}`, h("small", { text: fmtDur(c.duration) })),
      h("div", { class: "clip-tags" }, tags)),
    h("div", { class: "clip-actions" }, actions));
}

function playAnimalClip(c, startAt) {
  // reuse the SD player: it plays the local MP4, or fetches the clip if it is still on the card
  let model = sdClip(c.id);
  if (!model) { model = { ...c, job: null, type: 2, type_label: "", time_end: "" }; sd.clips.push(model); }
  model.cached = c.cached;
  model.title = `${c.detections.map((d) => d.emoji).join(" ")} ${c.date} ${c.time} (${fmtDur(c.duration)})`;
  openSdPlayer(c.id, startAt);
}
$("#btn-ani-refresh").addEventListener("click", loadAnimals);

$("#btn-sd-refresh").addEventListener("click", () => loadSd(true));

// ---------- poll active recordings so the UI recovers state on reload ----------
async function syncRecordings() {
  try {
    const s = await api(P() + "/api/record/status");
    const manual = (s.active || []).find((r) => r.kind === "manual");
    if (manual && !state.manualRecId) {
      state.manualRecId = manual.id;
      $("#btn-rec-start").disabled = true;
      $("#btn-rec-stop").disabled = false;
      $("#rec-badge").hidden = false;
    }
  } catch (e) {}
}

// ---------- camera settings (privacy, LED, night vision, detection, presets) ----------
const SETTING_SWITCHES = [
  ["privacy", "Mode privé (image masquée)"],
  ["led", "Voyant lumineux"],
  ["white_lamp", "Projecteur"],
  ["auto_track", "Suivi automatique"],
];
const SETTING_DETECTIONS = [["motion", "Détection de mouvement"], ["person", "Détection de personne"]];
const SENSITIVITIES = [["low", "Faible"], ["normal", "Normale"], ["high", "Élevée"]];
const NIGHT_MODES = {
  night_vision: ["Vision nocturne", [["md_night_vision", "Intelligente"], ["inf_night_vision", "Infrarouge (noir et blanc)"],
                                     ["wtl_night_vision", "Couleur (projecteur)"]]],
  day_night: ["Jour / nuit", [["auto", "Automatique"], ["on", "Nuit forcée"], ["off", "Jour forcé"]]],
};
const JSON_HEADERS = { "Content-Type": "application/json" };

async function loadCamSettings(force = false) {
  const cam = state.cam;
  try {
    const d = await api(P() + "/api/settings" + (force ? "?force=true" : ""));
    if (cam === state.cam) renderCamSettings(d.values || {});
  } catch (e) {
    if (cam !== state.cam) return;
    $("#cam-settings").hidden = true;
    renderPresets(null);
  }
}

function switchInput(checked, onchange) {
  const input = h("input", { type: "checkbox" });
  input.checked = !!checked;
  input.addEventListener("change", () => onchange(input.checked, input));
  return h("label", { class: "switch" }, input, h("span", { class: "slider" }));
}

function choice(options, value, onchange) {
  const sel = h("select", {}, ...options.map(([v, label]) => h("option", { value: v, selected: v === value }, t(label))));
  sel.addEventListener("change", () => onchange(sel.value, sel));
  return sel;
}

async function setCamSetting(body, control) {
  if (control) control.disabled = true;
  try {
    const d = await api(P() + "/api/settings", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify(body) });
    renderCamSettings(d.values || {});
  } catch (e) {
    toast(TapoI18n.message(e.message), "err");
    loadCamSettings(true);
  }
}

function renderCamSettings(v) {
  const row = (label, ...controls) => h("div", { class: "cam-setting" }, h("span", { text: t(label) }), h("span", { class: "row" }, ...controls));
  const wide = (label, control) => h("label", { class: "cam-setting wide" }, h("span", { text: t(label) }), control);
  const rows = [];
  for (const [key, label] of SETTING_SWITCHES) {
    if (key in v) rows.push(row(label, switchInput(v[key], (on, el) => setCamSetting({ [key]: on }, el))));
  }
  for (const [key, label] of SETTING_DETECTIONS) {
    const d = v[key];
    if (!d) continue;
    rows.push(row(label,
      d.sensitivity ? choice(SENSITIVITIES, d.sensitivity, (s, el) => setCamSetting({ [key]: { sensitivity: s } }, el)) : null,
      switchInput(d.enabled, (on, el) => setCamSetting({ [key]: { enabled: on } }, el))));
  }
  for (const [key, [label, options]] of Object.entries(NIGHT_MODES)) {
    if (v[key]) rows.push(wide(label, choice(options, v[key], (m, el) => setCamSetting({ [key]: m }, el))));
  }
  $("#cam-settings-body").replaceChildren(...rows);
  $("#cam-settings").hidden = !rows.length;
  renderPresets("presets" in v ? v.presets : null);
}

function renderPresets(list) {
  const box = $("#ptz-presets");
  if (!list) { box.replaceChildren(); return; }
  box.replaceChildren(...list.map((p) => h("span", { class: "preset" },
    h("button", { type: "button", text: p.name, title: t("Aller à cette position"), onclick: () => gotoPreset(p) }),
    h("button", { type: "button", class: "x", text: "✕", title: t("Oublier cette position"), "aria-label": t`Oublier ${p.name}`,
      onclick: () => deletePreset(p) }))),
  h("button", { type: "button", class: "btn ghost", text: t("+ Mémoriser la position"), onclick: savePreset }));
}

async function gotoPreset(p) {
  try { await api(`${P()}/api/presets/${encodeURIComponent(p.id)}/goto`, { method: "POST" }); }
  catch (e) { toast(TapoI18n.message(e.message), "err"); }
}

async function savePreset() {
  const name = prompt(t("Nom de cette position :"));
  if (!name) return;
  try {
    const d = await api(P() + "/api/presets", { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ name }) });
    renderCamSettings(d.values || {});
    toast(t("Position mémorisée"), "ok");
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
}

async function deletePreset(p) {
  if (!confirm(t`Oublier la position ${p.name} ?`)) return;
  try {
    const d = await api(`${P()}/api/presets/${encodeURIComponent(p.id)}`, { method: "DELETE" });
    renderCamSettings(d.values || {});
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
}

// ---------- cameras ----------
const CAM_KEY = "tapo-web.camera";
function storedCam() { try { return localStorage.getItem(CAM_KEY); } catch (e) { return null; } }
function storeCam(id) { try { localStorage.setItem(CAM_KEY, id); } catch (e) { /* per-tab only */ } }

async function loadCameras() {
  const d = await api("/api/cameras");
  state.cams = d.cameras || [];
  state.setup = d;
  $("#btn-shutdown").hidden = !d.can_shutdown;
  const ids = state.cams.map((c) => c.id);
  if (!ids.includes(state.cam)) state.cam = ids.includes(storedCam()) ? storedCam() : (ids[0] || null);
  renderCamPicker();
  return d;
}

function renderCamPicker() {
  const sel = $("#cam-select");
  sel.replaceChildren(...state.cams.map((c) => h("option", { value: c.id, selected: c.id === state.cam }, c.name)));
  sel.hidden = state.cams.length < 2;
  $('.tab[data-tab="wall"]').hidden = state.cams.length < 2;
}
$("#cam-select").addEventListener("change", (e) => selectCamera(e.target.value));

function selectCamera(id, tab) {
  if (id !== state.cam) {
    stopLive();
    closePlayer();
    clearInterval(sd.pollTimer); sd.pollTimer = null;
    clearInterval(sd.aniTimer); sd.aniTimer = null;
    Object.assign(sd, { days: [], date: null, month: null, clips: [], filter: "all", aniDays: {} });
    sd.wantFile.clear();
    ani.key = null;
    state.manualRecId = null;
    $("#btn-rec-start").disabled = false;
    $("#btn-rec-stop").disabled = true;
    $("#rec-badge").hidden = true;
    state.cam = id;
    storeCam(id);
    renderCamPicker();
    refreshStatus();
    syncRecordings();
  }
  const target = tab || $(".tab.active")?.dataset.tab || "live";
  history.replaceState(null, "", "#" + target);
  showTab(target === "wall" ? "live" : target);
}

// ---------- all cameras at once ----------
const wall = { players: [] };

function stopWall() {
  for (const p of wall.players) { try { p.hls && p.hls.destroy(); } catch (e) {} p.video.removeAttribute("src"); }
  wall.players = [];
}

async function startWall() {
  stopWall();
  try { await loadCameras(); } catch (e) { /* keep the last known list */ }
  if (!$("#tab-wall").classList.contains("active")) return;
  const grid = $("#wall");
  grid.replaceChildren(...state.cams.map((c) => {
    const video = h("video", { muted: true, playsinline: true, autoplay: true });
    video.muted = true;
    const url = `/cams/${encodeURIComponent(c.id)}/live/sd/index.m3u8`;
    const p = { video, hls: null };
    if (!c.online) { /* nothing to play */ } else if (window.Hls && Hls.isSupported()) {
      p.hls = new Hls({ liveSyncDurationCount: 2, maxBufferLength: 6 });
      p.hls.loadSource(url);
      p.hls.attachMedia(video);
      p.hls.on(Hls.Events.ERROR, (_e, data) => { if (data.fatal && data.type === Hls.ErrorTypes.NETWORK_ERROR) setTimeout(() => p.hls && p.hls.startLoad(), 3000); });
    } else {
      video.src = url;
    }
    wall.players.push(p);
    return h("button", { class: "wall-tile", "aria-label": t`Ouvrir ${c.name}`, onclick: () => selectCamera(c.id, "live") },
      video,
      c.online ? null : h("span", { class: "wall-off", text: t("hors ligne") }),
      h("span", { class: "wall-name" }, h("span", { class: "dot " + (c.online ? "online" : "offline") }), c.name));
  }));
}

// ---------- settings: cameras, TP-Link account, first-run assistant ----------
const setup = { editing: null, scan: [] };

function closeSetup() {
  $("#setup").hidden = true;
}
$("#setup-close").addEventListener("click", closeSetup);

async function openSetup() {
  try {
    const d = await loadCameras();
    if (!d.can_edit) { toast(t("Les identifiants ne se modifient que depuis la machine qui héberge l'application."), "err"); return; }
    showSetupList();
    $("#setup").hidden = false;
    if (d.needs_setup && !setup.scan.length) scanNetwork();
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
}
$("#btn-settings").addEventListener("click", openSetup);

function showSetupList() {
  const d = state.setup || {};
  $("#setup-title").textContent = t("Réglages");
  $("#setup-list").hidden = false;
  $("#cam-form").hidden = true;
  $("#setup-welcome").hidden = !d.needs_setup;
  $("#account-form").subnet.value = d.subnet || "";
  $("#account-form").cloud_password.value = "";
  $("#account-form").cloud_password.placeholder = d.has_cloud_password ? t("laisser vide pour conserver") : "";
  $("#account-state").textContent = d.has_cloud_password ? t("enregistré") : t("pas encore enregistré");
  $("#cam-list").replaceChildren(...(state.cams.length ? state.cams.map((c) => h("div", { class: "cam-row" },
    h("span", { class: "dot " + (c.online ? "online" : "offline") }),
    h("span", { class: "cam-row-name" }, c.name, h("small", { text: [c.model, c.host].filter(Boolean).join(" · ") })),
    h("button", { class: "btn ghost", type: "button", text: t("Modifier"), onclick: () => editCamera(c) }),
    h("button", { class: "btn ghost", type: "button", text: "🗑", title: t("Retirer cette caméra"), "aria-label": t("Retirer cette caméra"),
      onclick: () => removeCamera(c) }))) : [h("div", { class: "hint", text: t("Aucune caméra pour l'instant.") })]));
  renderScan();
}

async function removeCamera(c) {
  if (!confirm(t`Retirer ${c.name} ? Ses enregistrements restent sur le disque.`)) return;
  try {
    await api(`/api/cameras/${encodeURIComponent(c.id)}`, { method: "DELETE" });
    await loadCameras();
    if (!state.cams.find((x) => x.id === state.cam) && state.cams[0]) selectCamera(state.cams[0].id);
    showSetupList();
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
}

async function scanNetwork() {
  const btn = $("#btn-cam-scan");
  btn.disabled = true;
  $("#scan-results").replaceChildren(h("div", { class: "hint", text: t("Recherche des caméras sur le réseau… (jusqu'à une minute)") }));
  try {
    const subnet = $("#account-form").subnet.value.trim();
    const d = await api("/api/discovery", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(subnet ? { subnet } : {}) });
    setup.scan = d.devices || [];
    renderScan(true);
  } catch (e) {
    $("#scan-results").replaceChildren(h("div", { class: "hint err", text: TapoI18n.message(e.message) }));
  } finally { btn.disabled = false; }
}
$("#btn-cam-scan").addEventListener("click", scanNetwork);

function renderScan(fresh) {
  const box = $("#scan-results");
  if (!setup.scan.length) {
    if (fresh) box.replaceChildren(h("div", { class: "hint", text: t("Aucune caméra trouvée. Vérifiez le sous-réseau ci-dessous, ou ajoutez la caméra à la main.") }));
    else box.replaceChildren();
    return;
  }
  box.replaceChildren(h("div", { class: "hint", text: t("Caméras trouvées sur le réseau :") }),
    ...setup.scan.map((dev) => h("div", { class: "cam-row" },
      h("span", { class: "emo", text: "📷" }),
      h("span", { class: "cam-row-name" }, dev.model || t("Caméra Tapo"),
        h("small", { text: [dev.name, dev.ip, dev.firmware && "fw " + dev.firmware].filter(Boolean).join(" · ") })),
      dev.camera ? h("span", { class: "pill", text: t("déjà ajoutée") })
        : h("button", { class: "btn", type: "button", text: t("Ajouter"), onclick: () => editCamera(null, dev) }))));
}

$("#btn-cam-add").addEventListener("click", () => editCamera(null, null));

function editCamera(cam, found) {
  setup.editing = cam ? cam.id : null;
  const f = $("#cam-form");
  f.reset();
  $("#setup-title").textContent = cam ? cam.name : t("Ajouter une caméra");
  f.name.value = cam ? cam.custom_name || "" : (found && found.model) || "";
  f.host.value = cam ? cam.host || "" : (found && found.ip) || "";
  f.mac.value = (found && found.mac) || "";
  f.model.value = (found && found.model) || "";
  f.user.value = cam ? cam.user || "" : (state.cams[0] && state.cams[0].user) || "";
  f.protocol.value = cam ? cam.protocol || "auto" : "auto";
  f.other_account.checked = !!(cam && cam.own_cloud_password);
  const shared = state.setup && state.setup.has_cloud_password;
  f.cloud_password.placeholder = (cam && cam.own_cloud_password) || shared ? t("laisser vide pour conserver") : "";
  f.password.placeholder = cam && cam.has_password ? t("laisser vide pour conserver") : "";
  $("#cam-form-intro").textContent = found
    ? t`${found.model || t("Caméra")} trouvée sur ${found.ip}. Il reste les deux comptes ci-dessous.`
    : cam ? "" : t("Saisissez l'adresse IP de la caméra si vous la connaissez, sinon laissez vide : elle sera cherchée sur le réseau.");
  $("#cam-form-test").hidden = !cam;
  $("#cam-form-diag").hidden = !cam;
  $("#check-results").replaceChildren();
  $("#setup-list").hidden = true;
  f.hidden = false;
  (f.user.value ? f.password : f.user).focus();
}
$("#cam-form-back").addEventListener("click", showSetupList);

$("#cam-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = Object.fromEntries(["name", "host", "user", "password", "protocol", "mac", "model"].map((k) => [k, f[k].value.trim()]));
  const cloud = f.cloud_password.value;
  const json = (method, payload) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  try {
    if (cloud && !f.other_account.checked) await api("/api/setup", json("POST", { cloud_password: cloud }));
    if (f.other_account.checked) { if (cloud) body.cloud_password = cloud; }
    else if (setup.editing) body.clear_cloud_password = true;
    const cam = setup.editing
      ? await api(`/api/cameras/${encodeURIComponent(setup.editing)}`, json("PUT", body))
      : await api("/api/cameras", json("POST", body));
    toast(t("Identifiants enregistrés"), "ok");
    await loadCameras();
    const fresh = !setup.editing;
    setup.editing = cam.id;
    setup.scan = setup.scan.map((d) => (d.mac && d.mac === body.mac) || d.ip === body.host ? { ...d, camera: cam.id } : d);
    $("#cam-form-test").hidden = false;
    $("#cam-form-diag").hidden = false;
    if (state.cam !== cam.id && (fresh || !state.cam)) selectCamera(cam.id);
    else refreshStatus();
    if (fresh) runCheck();
  } catch (err) { toast(TapoI18n.message(err.message), "err"); }
});

const CHECK_LABELS = { network: "Réseau", camera_account: "Compte de la caméra (live)", tplink_account: "Compte TP-Link (carte SD)" };
const CHECK_HELP = {
  camera_account: "Vérifiez l'utilisateur et le mot de passe du compte de la caméra (appli Tapo → la caméra → Paramètres avancés → Compte de la caméra).",
  tplink_account: "Vérifiez le mot de passe du compte TP-Link et activez « Compatibilité tierce » dans l'appli Tapo (Moi → Tapo Lab). Évitez d'essayer plusieurs mots de passe de suite : la caméra bloque les connexions pendant un moment.",
  network: "La caméra n'a pas été trouvée. Vérifiez qu'elle est allumée et sur le même réseau, ou indiquez son adresse IP.",
};

async function runCheck() {
  const box = $("#check-results");
  const btn = $("#cam-form-test");
  btn.disabled = true;
  box.replaceChildren(h("div", { class: "hint", text: t("Test de la connexion…") }));
  try {
    const r = await api(`/cams/${encodeURIComponent(setup.editing)}/api/check`, { method: "POST" });
    box.replaceChildren(...r.checks.map((c) => h("div", { class: "check " + (c.ok ? "ok" : "bad") },
      h("span", { class: "check-mark", text: c.ok ? "✓" : "✕" }),
      h("span", {}, h("b", { text: t(CHECK_LABELS[c.key] || c.key) }), " ", TapoI18n.message(c.detail || ""),
        !c.ok && CHECK_HELP[c.key] ? h("small", { text: t(CHECK_HELP[c.key]) }) : null))));
    if (r.checks.every((c) => c.ok)) box.append(h("div", { class: "hint ok", text: t("Tout fonctionne. Vous pouvez fermer cette fenêtre.") }));
    loadCameras().then(() => { if (!$("#setup-list").hidden) showSetupList(); }).catch(() => {});
    refreshStatus();
  } catch (e) {
    box.replaceChildren(h("div", { class: "hint err", text: TapoI18n.message(e.message) }));
  } finally { btn.disabled = false; }
}
$("#cam-form-test").addEventListener("click", runCheck);

async function copyDiagnostics() {
  try {
    const d = await api(`/cams/${encodeURIComponent(setup.editing)}/api/diagnostics`);
    const text = "```json\n" + JSON.stringify(d, null, 2) + "\n```";
    try { await navigator.clipboard.writeText(text); toast(t("Rapport copié : collez-le dans un ticket GitHub."), "ok"); }
    catch (e) { prompt(t("Rapport de diagnostic (aucune adresse ni mot de passe) :"), text); }
  } catch (e) { toast(TapoI18n.message(e.message), "err"); }
}
$("#cam-form-diag").addEventListener("click", copyDiagnostics);

$("#account-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = { subnet: f.subnet.value.trim() };
  if (f.cloud_password.value) body.cloud_password = f.cloud_password.value;
  try {
    await api("/api/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast(t("Identifiants enregistrés"), "ok");
    await loadCameras();
    showSetupList();
  } catch (err) { toast(TapoI18n.message(err.message), "err"); }
});

// ---------- init ----------
loadCameras().then((d) => {
  if (d.needs_setup) openSetup();
  const tab = location.hash.slice(1).split("/")[0];       // deep link: /#sd, /#library, /#wall
  if (tab) showTab(tab);
  else loadCamSettings();
  refreshStatus();
  syncRecordings();
}).catch(() => refreshStatus());
const statusTimer = setInterval(refreshStatus, 15000);

$("#btn-shutdown").addEventListener("click", async () => {
  if (!confirm(t("Arrêter le serveur ? Le direct et les téléchargements seront interrompus, les enregistrements seront finalisés. Relancez votre commande de démarrage pour revenir."))) return;
  const button = $("#btn-shutdown");
  button.disabled = true;
  try {
    await api("/api/server/shutdown", { method: "POST", headers: { "X-Tapo-Action": "shutdown" } });
    state.shuttingDown = true;
    clearInterval(statusTimer);
    clearInterval(sd.pollTimer);
    clearInterval(sd.aniTimer);
    stopWall();
    closePlayer();
    stopLive();
    document.title = t("Tapo Web - arrêt du serveur");
    document.body.replaceChildren();
    document.body.append(h("main", { text: t("Le serveur s'arrête. Vous pouvez fermer cet onglet. Pour revenir, relancez votre commande de démarrage.") }));
  } catch (error) { button.disabled = false; toast(TapoI18n.message(error.message), "err"); }
});
