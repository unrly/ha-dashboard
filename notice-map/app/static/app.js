"use strict";

const TYPES = {
  RZ: { label: "Rezoning", v: "--t-rz" }, SU: { label: "Special use", v: "--t-su" },
  SDP: { label: "Site plan", v: "--t-sdp" }, PF: { label: "Plat", v: "--t-pf" },
  BOA: { label: "Variance", v: "--t-boa" }, PA: { label: "Pre-application", v: "--t-pa" },
  OTHER: { label: "Other", v: "--muted" },
};
const DATE_LABELS = { filed: "Application filed", pc_hearing: "Planning Commission", bcc_hearing: "County Commissioners", boa_hearing: "Board of Adjustment" };

const $ = (s) => document.querySelector(s);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (s) => { if (!/^\d{4}-\d{2}-\d{2}/.test(s || "")) return s || ""; return new Date(s.slice(0, 10) + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }); };
const today = new Date().toISOString().slice(0, 10);

let cases = [];
let active = new Set(Object.keys(TYPES));
let selected = null;
let status = null;

// ---------- map ----------
const map = L.map("map", { zoomControl: true }).setView([39.5866, -105.085], 13);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);
const areaLayer = L.layerGroup().addTo(map);
const overlay = L.layerGroup().addTo(map);
const markers = L.layerGroup().addTo(map);

async function loadArea() {
  const r = await fetch("api/area");
  if (!r.ok) return;
  const feat = await r.json();
  areaLayer.clearLayers();
  const layer = L.geoJSON(feat, { style: { color: css("--zip"), weight: 2, dashArray: "8 6", fillOpacity: 0.04 }, interactive: false });
  layer.addTo(areaLayer);
  if (!cases.length) map.fitBounds(layer.getBounds(), { padding: [10, 10] });
}

function statusPill(c) {
  const cls = { hearing: "hear", closed: "ok", review: "rev" }[c.status_kind] || "rev";
  const next = nextHearing(c);
  const short = { pc_hearing: "PC", bcc_hearing: "BCC", boa_hearing: "BOA" };
  const text = c.status_kind === "hearing" && next ? `${short[next.k]} hearing ${fmtDate(next.date).replace(/, \d{4}$/, "")}` : (c.status || "In review");
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}
function nextHearing(c) {
  const hs = ["pc_hearing", "bcc_hearing", "boa_hearing"].map((k) => ({ k, date: (c.dates || {})[k], label: DATE_LABELS[k] })).filter((h) => h.date && h.date >= today).sort((a, b) => a.date.localeCompare(b.date));
  return hs[0];
}

function drawMarkers() {
  markers.clearLayers();
  overlay.clearLayers();
  for (const c of cases) {
    if (!active.has(c.type_code) || c.lat == null) continue;
    const t = TYPES[c.type_code] || TYPES.OTHER;
    const sel = c.id === selected;
    const html = `${c.status_kind === "hearing" ? '<span class="ring"></span>' : ""}<span class="post"></span>
      <span class="plac" style="background:var(${t.v})">${esc(c.type_code === "OTHER" ? "?" : c.type_code)}</span>${c.is_new ? '<span class="newdot"></span>' : ""}`;
    const icon = L.divIcon({ className: `pin${sel ? " sel" : ""}${c.status_kind === "closed" ? " closed" : ""}`, html, iconSize: [30, 40], iconAnchor: [15, 40] });
    const m = L.marker([c.lat, c.lon], { icon, title: c.name, keyboard: true, zIndexOffset: sel ? 1000 : 0 });
    m.on("click", () => select(c.id, true));
    if (sel) m.bindTooltip(esc(c.name), { permanent: true, direction: "right", offset: [12, -24] });
    m.addTo(markers);
  }
  const c = cases.find((x) => x.id === selected);
  if (c && active.has(c.type_code)) drawParcelAndFootprints(c);
}

// Draw the parcel and, when a summary exists, the estimated building footprints on the map.
function drawParcelAndFootprints(c) {
  const col = css((TYPES[c.type_code] || TYPES.OTHER).v);
  let north, west;
  if (c.parcel && c.parcel.length) {
    const rings = c.parcel.map((r) => r.map(([lon, lat]) => [lat, lon]));
    const poly = L.polygon(rings, { color: col, weight: 2, dashArray: "5 4", fillOpacity: 0.12, interactive: false }).addTo(overlay);
    const b = poly.getBounds();
    north = b.getNorth(); west = b.getWest();
  }
  const s = c.summary;
  if (!s || !s.buildings || !s.buildings.length) return;
  const mPerLat = 111320, mPerLon = 111320 * Math.cos((c.lat * Math.PI) / 180);
  if (north == null) { // no parcel shape: center the lot on the marker
    north = c.lat + (s.lot.depth_m / 2) / mPerLat;
    west = c.lon - (s.lot.width_m / 2) / mPerLon;
  }
  for (const bd of s.buildings) {
    const n = north - bd.y_m / mPerLat, w = west + bd.x_m / mPerLon;
    const so = n - bd.depth_m / mPerLat, e = w + bd.width_m / mPerLon;
    L.rectangle([[so, w], [n, e]], { color: css("--ink"), weight: 1, fillColor: col, fillOpacity: 0.65, interactive: false }).addTo(overlay);
  }
}

// ---------- list ----------
function renderFilters() {
  const present = new Set(cases.map((c) => c.type_code));
  $("#chips").innerHTML = Object.entries(TYPES).filter(([k]) => present.has(k) || k !== "OTHER")
    .map(([k, t]) => `<button type="button" class="chip" data-t="${k}" aria-pressed="${active.has(k)}"><span class="dot" style="background:var(${t.v})"></span>${t.label}</button>`).join("");
  $("#chips").querySelectorAll(".chip").forEach((b) => (b.onclick = () => { const k = b.dataset.t; active.has(k) ? active.delete(k) : active.add(k); renderAll(); }));
  $("#legend").innerHTML = Object.entries(TYPES).filter(([k]) => k !== "OTHER").map(([, t]) => `<div><span class="dot" style="background:var(${t.v})"></span>${t.label}</div>`).join("") +
    `<div><span class="dot" style="border:2px dashed var(--warn);background:transparent"></span>Hearing scheduled</div><div><span class="dot" style="background:var(--notice)"></span>New since last scan</div>`;
}

function renderList() {
  const vis = cases.filter((c) => active.has(c.type_code));
  const hearings = cases.filter((c) => c.status_kind === "hearing").length;
  $("#count").textContent = cases.length ? `${vis.length} of ${cases.length} notices · ${hearings} with hearings scheduled` : "";
  if (!cases.length) {
    $("#cases").innerHTML = `<li class="empty"><b>No notices yet</b><span>Press <em>Scan county now</em> to pull active land use cases in ZIP ${esc(status?.zip || "")}.</span></li>`;
    return;
  }
  $("#cases").innerHTML = vis.map((c) => `<li><button type="button" class="case" data-id="${esc(c.id)}" aria-current="${c.id === selected}">
    <span class="stripe" style="background:var(${(TYPES[c.type_code] || TYPES.OTHER).v})"></span>
    <span class="top"><span class="num">${esc(c.case_number)}</span>${c.is_new ? '<span class="pill new">New</span>' : ""}</span>
    <span class="name">${esc(c.name)}</span>
    <span class="sub"><span>${esc(c.type_label)}</span>${statusPill(c)}</span></button></li>`).join("");
  $("#cases").querySelectorAll(".case").forEach((b) => (b.onclick = () => select(b.dataset.id, true)));
}

// ---------- case sheet ----------
function renderSheet() {
  const c = cases.find((x) => x.id === selected);
  if (!c) {
    $("#sheet").innerHTML = cases.length ? `<div class="empty"><b>Pick a notice</b><span>Tap a marker or a case in the list.</span></div>` :
      `<div class="empty"><b>How this works</b><span>Scan pulls the county's active land use cases, places them on the map, and keeps the ones inside your ZIP. Open a case to read its documents or ask Claude to summarize the plans.</span></div>`;
    return;
  }
  const t = TYPES[c.type_code] || TYPES.OTHER;
  const s = c.summary;
  const dates = Object.entries(c.dates || {}).sort((a, b) => a[1].localeCompare(b[1]));
  const next = nextHearing(c);
  const facts = [
    ["Request", s?.request || c.case_type_raw], ["Status", c.status], ["Site", c.acres ? `${c.acres} acres` : ""],
    ["Applicant", c.applicant], ["Case planner", c.planner], ["Parcel", c.pin],
  ].filter(([, v]) => v);
  const docs = c.docs || [];
  $("#sheet").innerHTML = `
   <div>
     <div class="num">${esc(c.case_number)}</div>
     <h2>${esc(c.name)}</h2>
     <div class="typeline"><span class="tag" style="background:var(${t.v})">${esc(t.label)}</span>${statusPill(c)}<span class="src">${esc(c.address)}</span></div>
   </div>
   <dl class="facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
   ${c.description ? `<section class="sec"><h3>County description</h3><p class="summary">${esc(c.description)}</p></section>` : ""}
   ${s ? `
     <section class="sec"><h3>What's proposed <span class="ai">AI summary · ${esc(s.confidence)} confidence</span></h3><p class="summary">${esc(s.summary)}</p></section>
     ${s.key_numbers?.length ? `<section class="sec"><h3>Key numbers <span class="ai">from plans</span></h3><div class="kv">${s.key_numbers.slice(0, 6).map((k) => `<div title="${esc(k.source)}"><b>${esc(k.value)}</b><span>${esc(k.label)}</span></div>`).join("")}</div></section>` : ""}
     <section class="sec"><h3>Massing sketch <span class="ai">estimated</span></h3><canvas id="massing" aria-label="3D massing sketch"></canvas>
       <p class="cap">${s.buildings?.length ? "Estimated from the site plan and elevations. Drag to rotate. Not exact." : "No buildings in this case. Lot outline only."}</p></section>
     ${s.watch?.length ? `<section class="sec"><h3>What to watch</h3><ul class="watch">${s.watch.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></section>` : ""}
     ${s.notes ? `<p class="cap">${esc(s.notes)}</p>` : ""}
     <div><button class="btn ghost" id="sumBtn" type="button">Summarize again</button></div>` : `
     <section class="sec"><h3>Plan summary</h3>
       <p class="summary">${status?.ai_enabled ? `Claude can read ${docs.length ? `the ${docs.length} case document${docs.length > 1 ? "s" : ""}` : "the case record"} and explain what's proposed, with key numbers and a 3D sketch.` : "Plan summaries are turned off in config.yaml."}</p>
       ${status?.ai_enabled ? `<p><button class="btn" id="sumBtn" type="button">Summarize plans</button></p>` : ""}
     </section>`}
   ${dates.length ? `<section class="sec"><h3>Timeline</h3><ul class="timeline">${dates.map(([k, d]) => `<li class="${d < today ? "done" : next && next.k === k ? "next" : ""}"><span class="tdot"></span><time>${esc(fmtDate(d))}</time><span>${esc(DATE_LABELS[k] || k)}</span></li>`).join("")}</ul></section>` : ""}
   <section class="sec"><h3>County documents</h3>${docs.length ? `<ul class="docs">${docs.map((d, i) => `<li><a href="${esc(d.url)}" target="_blank" rel="noopener"><span class="pdf">PDF</span><span>${esc(d.title)}</span><span class="pg">jeffco.us ↗</span></a></li>`).join("")}</ul>` :
     `<p class="cap">No document links were found for this case. Check the county's case page or call Planning &amp; Zoning at 303-271-8700.</p>`}</section>
   <details class="sec"><summary class="cap">All county fields</summary><dl class="raw" id="rawFields"></dl></details>`;
  const btn = $("#sumBtn");
  if (btn) btn.onclick = () => summarize(c.id, !!s);
  const det = $("#sheet details");
  det.addEventListener("toggle", async () => {
    if (!det.open || $("#rawFields").children.length) return;
    const r = await fetch(`api/cases/${encodeURIComponent(c.id)}`);
    const full = await r.json();
    $("#rawFields").innerHTML = Object.entries(full.raw || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  });
  if (s) drawMassing(c);
}

async function summarize(id, force) {
  const btn = $("#sumBtn");
  btn.disabled = true;
  btn.textContent = "Reading the plans… (about a minute)";
  try {
    const r = await fetch(`api/cases/${encodeURIComponent(id)}/summarize${force ? "?force=true" : ""}`, { method: "POST" });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || "Summary failed");
    const c = cases.find((x) => x.id === id);
    c.summary = body;
    if (selected === id) { renderSheet(); drawMarkers(); }
  } catch (e) {
    showError(e.message);
    btn.disabled = false;
    btn.textContent = force ? "Summarize again" : "Summarize plans";
  }
}

// ---------- isometric massing ----------
let rot = 0.6;
function drawMassing(c) {
  const cv = $("#massing");
  if (!cv || !c.summary) return;
  const s = c.summary;
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext("2d");
  ctx.scale(dpr, dpr);
  const col = css((TYPES[c.type_code] || TYPES.OTHER).v), ink = css("--ink"), muted = css("--muted");
  const bld = (s.buildings || []).map((b) => [b.x_m, b.y_m, b.width_m, b.depth_m, Math.max(0.2, b.floors)]);
  let lw = s.lot?.width_m || 0, lh = s.lot?.depth_m || 0;
  bld.forEach(([x, y, w, d]) => { lw = Math.max(lw, x + w); lh = Math.max(lh, y + d); });
  if (!lw || !lh) { lw = lh = 30; }
  const ccx = lw / 2, ccy = lh / 2, cos = Math.cos(rot), sin = Math.sin(rot);
  const R = (x, y) => { const dx = x - ccx, dy = y - ccy; return [dx * cos - dy * sin, dx * sin + dy * cos]; };
  const iso = (x, y, z) => { const [rx, ry] = R(x, y); return [(rx - ry) * 0.866, (rx + ry) * 0.5 - z]; };
  const corners = [[0, 0], [lw, 0], [lw, lh], [0, lh]];
  const maxH = Math.max(10, ...bld.map((b) => b[4] * 3.3));
  const xs = [], ys = [];
  corners.forEach(([x, y]) => { const a = iso(x, y, 0), b = iso(x, y, maxH); xs.push(a[0], b[0]); ys.push(a[1], b[1]); });
  const sc = Math.min((W - 30) / (Math.max(...xs) - Math.min(...xs)), (H - 30) / (Math.max(...ys) - Math.min(...ys)));
  const ox = W / 2 - ((Math.max(...xs) + Math.min(...xs)) / 2) * sc, oy = H / 2 - ((Math.max(...ys) + Math.min(...ys)) / 2) * sc;
  const S = (x, y, z) => { const [a, b] = iso(x, y, z); return [ox + a * sc, oy + b * sc]; };
  const poly = (p, fill, stroke) => { ctx.beginPath(); p.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); if (fill) { ctx.fillStyle = fill; ctx.fill(); } if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); } };
  const shade = (amt) => { ctx.fillStyle = col; const h = ctx.fillStyle; if (h[0] !== "#") return col; const [r, g, b] = h.slice(1).match(/\w\w/g).map((x) => parseInt(x, 16)); const f = (v) => Math.round(amt < 0 ? v * (1 + amt) : v + (255 - v) * amt); return `rgb(${f(r)},${f(g)},${f(b)})`; };
  ctx.setLineDash([5, 4]); poly(corners.map(([x, y]) => S(x, y, 0)), null, muted); ctx.setLineDash([]);
  const top = shade(0.25), light = col, dark = shade(-0.3);
  const boxes = bld.map(([x, y, w, d, f]) => { const [rx, ry] = R(x + w / 2, y + d / 2); return { x, y, w, d, f, h: f * 3.3, k: rx + ry }; }).sort((a, b) => a.k - b.k);
  for (const { x, y, w, d, f, h } of boxes) {
    const P4 = [[x, y], [x + w, y], [x + w, y + d], [x, y + d]];
    const faces = [[0, 1], [1, 2], [2, 3], [3, 0]].map(([i, j]) => { const a = R(...P4[i]), b = R(...P4[j]); return { i, j, k: (a[0] + b[0] + a[1] + b[1]) / 2, nx: b[1] - a[1], ny: -(b[0] - a[0]) }; });
    faces.sort((a, b) => a.k - b.k).forEach((fc) => {
      const a = P4[fc.i], b = P4[fc.j];
      poly([S(a[0], a[1], 0), S(b[0], b[1], 0), S(b[0], b[1], h), S(a[0], a[1], h)], fc.nx - fc.ny > 0 ? light : dark, ink);
    });
    poly(P4.map(([px, py]) => S(px, py, h)), top, ink);
    ctx.globalAlpha = 0.25;
    for (let i = 1; i < Math.round(f); i++) { const fl = P4.map(([px, py]) => S(px, py, i * 3.3)); ctx.beginPath(); fl.forEach(([px, py], k) => (k ? ctx.lineTo(px, py) : ctx.moveTo(px, py))); ctx.closePath(); ctx.strokeStyle = ink; ctx.stroke(); }
    ctx.globalAlpha = 1;
  }
  // north arrow
  const [ax, ay] = R(ccx, ccy - 1), [bx, by] = R(ccx, ccy);
  const dx = (ax - bx - (ay - by)) * 0.866, dy = (ax - bx + (ay - by)) * 0.5, L2 = Math.hypot(dx, dy) || 1;
  ctx.strokeStyle = muted; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(W - 22, 24); ctx.lineTo(W - 22 + (dx / L2) * 14, 24 + (dy / L2) * 14); ctx.stroke();
  ctx.fillStyle = muted; ctx.font = "600 10px 'JetBrains Mono', monospace"; ctx.textAlign = "center"; ctx.fillText("N", W - 22 + (dx / L2) * 22, 27 + (dy / L2) * 22);
}
let mdrag = null;
document.addEventListener("pointerdown", (e) => { if (e.target.id === "massing") { mdrag = { x: e.clientX, r: rot }; e.target.setPointerCapture(e.pointerId); } });
document.addEventListener("pointermove", (e) => { if (mdrag) { rot = mdrag.r + (e.clientX - mdrag.x) / 90; drawMassing(cases.find((c) => c.id === selected)); } });
document.addEventListener("pointerup", () => (mdrag = null));

// ---------- selection & data ----------
function select(id, fromUser) {
  selected = id;
  renderList(); drawMarkers(); renderSheet();
  const c = cases.find((x) => x.id === id);
  if (fromUser && c && c.lat != null) map.panTo([c.lat, c.lon]);
  if (fromUser && window.matchMedia("(max-width:720px)").matches) $(".sheet").scrollIntoView({ behavior: "smooth", block: "start" });
}
function renderAll() { renderFilters(); renderList(); drawMarkers(); renderSheet(); }
function showError(msg) { const e = $("#err"); e.textContent = msg; e.hidden = !msg; }

async function loadStatus() {
  status = await (await fetch("api/status")).json();
  $("#subtitle").textContent = `Land use notices · ZIP ${status.zip} · unincorporated Jefferson County`;
  const ls = status.last_scan;
  $("#lastScan").textContent = ls ? `Last scan: ${new Date(ls.finished).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}${ls.ok ? "" : " (failed)"}` : "Not scanned yet";
}
async function loadCases() {
  cases = await (await fetch("api/cases")).json();
  const rank = { hearing: 0, review: 1, closed: 2 };
  cases.sort((a, b) => (b.is_new - a.is_new) || (rank[a.status_kind] - rank[b.status_kind]) || a.case_number.localeCompare(b.case_number));
  if (!cases.find((c) => c.id === selected)) selected = cases[0]?.id ?? null;
  renderAll();
}

$("#scanBtn").onclick = async () => {
  const b = $("#scanBtn"), toast = $("#toast");
  b.disabled = true; $("#scanIcon").classList.add("spin"); toast.hidden = false; showError("");
  await fetch("api/scan", { method: "POST" });
  let st;
  do {
    await new Promise((r) => setTimeout(r, 900));
    st = await (await fetch("api/scan")).json();
    toast.textContent = st.step || "Working…";
  } while (st.running);
  b.disabled = false; $("#scanIcon").classList.remove("spin");
  if (st.error) showError(`Scan failed: ${st.error}`);
  await Promise.all([loadStatus(), loadCases(), loadArea()]);
  setTimeout(() => (toast.hidden = true), 3000);
};

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { loadArea(); renderAll(); });
window.addEventListener("resize", () => drawMassing(cases.find((c) => c.id === selected)));

(async () => {
  await loadStatus();
  if (status.center) map.setView(status.center, 13);
  await loadCases();
  await loadArea();
  const placed = cases.filter((c) => c.lat != null);
  if (placed.length) map.fitBounds(placed.map((c) => [c.lat, c.lon]), { padding: [40, 40], maxZoom: 15 });
  if (status.scan?.running) $("#scanBtn").click();
})();
