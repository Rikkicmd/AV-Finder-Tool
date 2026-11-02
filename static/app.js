/* AvigilonDash front end: plain JS, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = { devices: [], filter: "all", search: "", avOnly: false, view: "grid", selected: new Set(), cur: null, cfg: null, tab: "overview", liveTimer: null };

const ICON = {
  cam: '<svg viewBox="0 0 24 24"><path d="M3 7h12v10H3z"/><path d="m15 10 6-3v10l-6-3"/></svg>',
  rec: '<svg viewBox="0 0 24 24"><rect x="2" y="4" width="20" height="7" rx="2"/><rect x="2" y="13" width="20" height="7" rx="2"/><path d="M6 7.5h.01M6 16.5h.01"/></svg>',
  spark: '<svg viewBox="0 0 24 24"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/></svg>',
  lock: '<svg viewBox="0 0 24 24"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
  ok: '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  all: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>',
};

const STATE = {
  factory: ["Factory new", "No users yet. Run setup to secure it."],
  default_creds: ["Default password", "Still using a factory password."],
  ready: ["Configured", "Logged in"],
  locked: ["Needs login", "Password protected"],
  auth_failed: ["Login failed", "Saved password was rejected"],
  offline: ["Offline", "Not responding"],
};

const TIMEZONES = [
  ["UTC", "UTC0"], ["London / Dublin / Lisbon", "GMT0BST,M3.5.0/1,M10.5.0"],
  ["Paris / Berlin / Madrid / Rome", "CET-1CEST,M3.5.0,M10.5.0/3"], ["Athens / Helsinki / Kyiv", "EET-2EEST,M3.5.0/3,M10.5.0/4"],
  ["Johannesburg", "SAST-2"], ["Moscow", "MSK-3"], ["Dubai", "<+04>-4"], ["India", "IST-5:30"],
  ["Singapore / Perth / Hong Kong", "<+08>-8"], ["Tokyo / Seoul", "JST-9"], ["Brisbane", "AEST-10"],
  ["Sydney / Melbourne", "AEST-10AEDT,M10.1.0,M4.1.0/3"], ["Auckland", "NZST-12NZDT,M9.5.0,M4.1.0/3"],
  ["Honolulu", "HST10"], ["Anchorage", "AKST9AKDT,M3.2.0,M11.1.0"], ["Los Angeles / Vancouver", "PST8PDT,M3.2.0,M11.1.0"],
  ["Phoenix", "MST7"], ["Denver / Calgary", "MST7MDT,M3.2.0,M11.1.0"], ["Chicago / Winnipeg", "CST6CDT,M3.2.0,M11.1.0"],
  ["New York / Toronto", "EST5EDT,M3.2.0,M11.1.0"], ["Halifax", "AST4ADT,M3.2.0,M11.1.0"], ["São Paulo", "<-03>3"],
];
const BROWSER_TZ = {
  "Europe/London": 1, "Europe/Dublin": 1, "Europe/Lisbon": 1, "Europe/Paris": 2, "Europe/Berlin": 2, "Europe/Madrid": 2, "Europe/Rome": 2,
  "Europe/Amsterdam": 2, "Europe/Brussels": 2, "Europe/Stockholm": 2, "Europe/Athens": 3, "Europe/Helsinki": 3, "Africa/Johannesburg": 4,
  "Europe/Moscow": 5, "Asia/Dubai": 6, "Asia/Kolkata": 7, "Asia/Singapore": 8, "Asia/Hong_Kong": 8, "Australia/Perth": 8, "Asia/Tokyo": 9,
  "Asia/Seoul": 9, "Australia/Brisbane": 10, "Australia/Sydney": 11, "Australia/Melbourne": 11, "Pacific/Auckland": 12, "Pacific/Honolulu": 13,
  "America/Anchorage": 14, "America/Los_Angeles": 15, "America/Vancouver": 15, "America/Phoenix": 16, "America/Denver": 17,
  "America/Chicago": 18, "America/New_York": 19, "America/Toronto": 19, "America/Halifax": 20, "America/Sao_Paulo": 21,
};
const guessTZ = () => TIMEZONES[BROWSER_TZ[Intl.DateTimeFormat().resolvedOptions().timeZone] ?? 0][1];

/* ---------------------------------------------------------------- api */
async function api(method, path, body) {
  const r = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || `HTTP ${r.status}`);
  return data;
}
function toast(msg, kind = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.innerHTML = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 7000 : 4000);
}

/* ---------------------------------------------------------------- inventory */
async function load() {
  S.devices = await api("GET", "/api/devices");
  render();
}

async function scan() {
  const b = $("#btnScan");
  b.classList.add("busy"); b.disabled = true; $("span", b).textContent = "Scanning…";
  if (!S.devices.length) $("#devices").innerHTML = Array(4).fill('<div class="skeleton"></div>').join("");
  $("#empty").hidden = true;
  try {
    const r = await api("POST", "/api/discover", {});
    const fresh = r.devices.filter((d) => d.factory_fresh).length;
    toast(`Found <b>${r.found}</b> device${r.found === 1 ? "" : "s"}${fresh ? `, <b>${fresh}</b> factory-fresh` : ""}.`, "ok");
  } catch (e) { toast(`Scan failed: ${esc(e.message)}`, "bad"); }
  b.classList.remove("busy"); b.disabled = false; $("span", b).textContent = "Scan network";
  await load();
}

function visible() {
  const q = S.search.toLowerCase();
  return S.devices.filter((d) => {
    if (S.avOnly && !d.is_avigilon) return false;
    if (S.filter === "new" && !d.factory_fresh) return false;
    if (S.filter === "camera" && d.kind !== "camera") return false;
    if (S.filter === "recorder" && d.kind !== "recorder") return false;
    if (S.filter === "attention" && !["locked", "auth_failed"].includes(d.state)) return false;
    if (q && ![d.name, d.label, d.ip, d.model, d.serial, d.mac, d.hostname, d.location].some((v) => (v || "").toLowerCase().includes(q))) return false;
    return true;
  });
}

function render() {
  const D = S.devices;
  const c = { total: D.length, fresh: D.filter((d) => d.factory_fresh).length, cam: D.filter((d) => d.kind === "camera").length,
    rec: D.filter((d) => d.kind === "recorder").length, att: D.filter((d) => ["locked", "auth_failed", "offline"].includes(d.state)).length };
  const stat = (ico, color, n, label) => `<div class="stat"><div class="ico" style="background:${color}22;color:${color}">${ico}</div><div><b>${n}</b><span>${label}</span></div></div>`;
  $("#stats").innerHTML = stat(ICON.all, "#60a5fa", c.total, "Devices") + stat(ICON.spark, "#a855f7", c.fresh, "Factory-fresh")
    + stat(ICON.cam, "#22c55e", c.cam, "Cameras") + stat(ICON.rec, "#06b6d4", c.rec, "Recorders") + stat(ICON.lock, "#f59e0b", c.att, "Need attention");
  $("#llBanner").hidden = !D.some((d) => d.link_local);

  const counts = { all: D.length, new: c.fresh, camera: c.cam, recorder: c.rec, attention: D.filter((d) => ["locked", "auth_failed"].includes(d.state)).length };
  $$("#filters .chip").forEach((ch) => {
    ch.classList.toggle("active", ch.dataset.f === S.filter);
    ch.innerHTML = `${ch.textContent.replace(/\d+$/, "").trim()}<span class="n">${counts[ch.dataset.f]}</span>`;
  });

  const list = visible();
  const grid = $("#devices");
  grid.className = `grid ${S.view === "list" ? "list" : ""}`;
  grid.innerHTML = list.map(card).join("");
  $("#empty").hidden = D.length > 0;
  if (D.length && !list.length) grid.innerHTML = `<div class="note" style="grid-column:1/-1;padding:30px;text-align:center">No devices match this filter.</div>`;
  updateBatchbar();
}

function canTalk(d) { return ["ready", "factory", "default_creds"].includes(d.state); }

function card(d) {
  const [label, hint] = STATE[d.state] || [d.state || "Unknown", ""];
  const title = d.label || d.name || d.model || d.ip;
  const thumb = canTalk(d) && d.kind === "camera"
    ? `<img loading="lazy" src="/api/devices/${d.id}/snapshot?t=${Math.floor(Date.now() / 60000)}" onerror="this.replaceWith(thumbPh('${d.kind}','No snapshot'))">`
    : thumbPhHTML(d.kind, d.state === "locked" || d.state === "auth_failed" ? "Log in to preview" : d.kind === "recorder" ? "Recorder" : "Preview unavailable");
  let action;
  if (d.factory_fresh) action = `<button class="btn primary sm" onclick="event.stopPropagation();openWizard(['${d.id}'])">${ICON.spark} Set up</button>`;
  else if (d.state === "locked" || d.state === "auth_failed") action = `<button class="btn sm" onclick="event.stopPropagation();loginModal('${d.id}')">${ICON.lock} Log in</button>`;
  else if (d.state === "offline") action = `<button class="btn sm" onclick="event.stopPropagation();refreshDev('${d.id}')">Retry</button>`;
  else action = `<button class="btn sm" onclick="event.stopPropagation();openDrawer('${d.id}')">Configure</button>`;
  return `<article class="card ${d.factory_fresh ? "fresh" : ""} ${S.selected.has(d.id) ? "selected" : ""}" onclick="openDrawer('${d.id}')">
    <div class="thumb">${thumb}
      <div class="pill-row">
        <div class="sel" onclick="event.stopPropagation();toggleSel('${d.id}')" title="Select for batch setup">${ICON.ok}</div>
        <div style="display:flex;gap:6px">${d.factory_fresh ? '<span class="tag new">New</span>' : ""}${d.kind === "recorder" ? '<span class="tag rec">Recorder</span>' : ""}</div>
      </div>
    </div>
    <div class="card-body">
      <div class="card-title"><div style="min-width:0"><h4 title="${esc(title)}">${esc(title)}</h4><small>${esc(d.manufacturer || (d.is_avigilon ? "Avigilon" : "Unknown maker"))} · ${esc(d.model || d.hardware || "model unknown")}</small></div>
        <span class="badge ${esc(d.state)}" title="${esc(hint)}">${esc(label)}</span></div>
      <dl class="meta">
        <dt>IP</dt><dd class="mono">${esc(d.ip)}${d.link_local ? ' <span style="color:var(--warn)">link-local</span>' : ""}</dd>
        <dt>MAC</dt><dd class="mono">${esc(d.mac || "—")}</dd>
        <dt>Serial</dt><dd class="mono">${esc(d.serial || "—")}</dd>
        <dt>Firmware</dt><dd>${esc(d.firmware || "—")}</dd>
      </dl>
      <div class="card-actions">${action}</div>
    </div></article>`;
}
function thumbPhHTML(kind, text) { return `<div class="ph">${kind === "recorder" ? ICON.rec : ICON.cam}<span>${esc(text)}</span></div>`; }
function thumbPh(kind, text) { const d = document.createElement("div"); d.innerHTML = thumbPhHTML(kind, text); return d.firstChild; }

function toggleSel(id) { S.selected.has(id) ? S.selected.delete(id) : S.selected.add(id); render(); }
function selectedIds() { return [...S.selected]; }
function clearSelection() { S.selected.clear(); render(); }
function updateBatchbar() {
  const n = S.selected.size;
  $("#batchbar").hidden = n === 0;
  $("#batchCount").textContent = `${n} device${n === 1 ? "" : "s"} selected`;
}
async function refreshDev(id) {
  try { await api("POST", `/api/devices/${id}/refresh`); } catch (e) { toast(esc(e.message), "bad"); }
  await load();
}

/* ---------------------------------------------------------------- modals */
function modal(html, cls = "") {
  $("#modal").className = `modal ${cls}`;
  $("#modal").innerHTML = html;
  $("#modalWrap").hidden = false;
  setTimeout(() => { const f = $("#modal input:not([type=hidden]):not([type=radio]):not([type=checkbox])"); f && f.focus(); }, 30);
}
function closeModal() { $("#modalWrap").hidden = true; }
$("#modalWrap").addEventListener("mousedown", (e) => { if (e.target.id === "modalWrap") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { if (!$("#modalWrap").hidden) closeModal(); else closeDrawer(); } });

function loginModal(id) {
  const d = S.devices.find((x) => x.id === id);
  modal(`<form onsubmit="doLogin(event,'${id}')"><div class="modal-head"><h3>Log in to ${esc(d.label || d.name || d.ip)}</h3>
    <p>Enter an ONVIF administrator account for this device. On Avigilon cameras ONVIF users are managed in the camera web page
    under <i>Setup → Users</i> or in ACC.</p></div>
    <div class="modal-body form">
      <div class="field"><label>Username</label><input name="u" value="${esc(d.username || "admin")}" autocomplete="username"></div>
      <div class="field"><label>Password</label><input name="p" type="password" autocomplete="current-password"></div>
      <div class="full err-box" id="loginErr" hidden></div>
    </div>
    <div class="modal-foot"><span></span><div style="display:flex;gap:10px"><button type="button" class="btn ghost" onclick="closeModal()">Cancel</button>
    <button class="btn primary">Log in</button></div></div></form>`, "sm");
}
async function doLogin(e, id) {
  e.preventDefault();
  const f = e.target;
  try {
    await api("POST", `/api/devices/${id}/credentials`, { username: f.u.value, password: f.p.value });
    closeModal(); toast("Logged in.", "ok"); await load();
  } catch (err) { const b = $("#loginErr"); b.hidden = false; b.textContent = err.message; }
}

function addModal() {
  modal(`<form onsubmit="doAdd(event)"><div class="modal-head"><h3>Add device by IP</h3>
    <p>Use this for devices on another subnet or ones that don't answer discovery. You can also enter a full ONVIF URL.</p></div>
    <div class="modal-body form">
      <div class="field full"><label>IP address or ONVIF URL</label><input name="a" placeholder="192.168.1.50 or http://10.0.0.5:8080/onvif/device_service" required></div>
      <div class="field"><label>Username (optional)</label><input name="u" placeholder="admin"></div>
      <div class="field"><label>Password (optional)</label><input name="p" type="password"></div>
      <div class="full err-box" id="addErr" hidden></div>
    </div>
    <div class="modal-foot"><span></span><div style="display:flex;gap:10px"><button type="button" class="btn ghost" onclick="closeModal()">Cancel</button>
    <button class="btn primary">Add device</button></div></div></form>`, "sm");
}
async function doAdd(e) {
  e.preventDefault();
  const f = e.target;
  try {
    const d = await api("POST", "/api/devices/manual", { address: f.a.value, username: f.u.value || null, password: f.p.value || null });
    closeModal();
    toast(d.state === "offline" ? "Added, but the device isn't answering ONVIF yet." : "Device added.", d.state === "offline" ? "bad" : "ok");
    await load();
  } catch (err) { const b = $("#addErr"); b.hidden = false; b.textContent = err.message; }
}

async function settingsModal() {
  const s = await api("GET", "/api/settings");
  modal(`<form onsubmit="saveSettings(event)"><div class="modal-head"><h3>Settings</h3><p>Stored locally in the <code>data/</code> folder.</p></div>
    <div class="modal-body form">
      <div class="field full"><label>Extra subnets to sweep during a scan</label>
        <input name="targets" value="${esc((s.scan_targets || []).join(", "))}" placeholder="e.g. 192.168.10.0/24, 10.0.5.20">
        <div class="hint">Multicast discovery covers this computer's own networks. Add subnets here for routed networks where multicast doesn't reach (max /20 each).</div></div>
      <div class="field"><label>Discovery wait (seconds)</label><input name="timeout" type="number" min="1" max="15" value="${esc(s.discovery_timeout)}"></div>
      <div class="field"><label>&nbsp;</label><label class="switch" style="height:38px"><input type="checkbox" name="av" ${s.avigilon_only ? "checked" : ""}><span></span>Show Avigilon devices only</label></div>
      <div class="field full"><label>Default credentials to try on new devices</label>
        <input name="creds" value="${esc(s.default_credentials.map((c) => `${c.username}:${c.password}`).join(", "))}">
        <div class="hint">Comma separated <code>user:password</code> pairs, tried in order. Leave the password empty for <code>admin:</code>.</div></div>
    </div>
    <div class="modal-foot"><span></span><div style="display:flex;gap:10px"><button type="button" class="btn ghost" onclick="closeModal()">Cancel</button>
    <button class="btn primary">Save</button></div></div></form>`);
}
async function saveSettings(e) {
  e.preventDefault();
  const f = e.target;
  const creds = f.creds.value.split(",").map((s) => s.trim()).filter(Boolean).map((s) => { const i = s.indexOf(":"); return i < 0 ? { username: s, password: "" } : { username: s.slice(0, i), password: s.slice(i + 1) }; });
  await api("PUT", "/api/settings", { scan_targets: f.targets.value.split(",").map((s) => s.trim()).filter(Boolean), discovery_timeout: +f.timeout.value || 3, default_credentials: creds, avigilon_only: f.av.checked });
  S.avOnly = f.av.checked; $("#avOnly").checked = S.avOnly;
  closeModal(); toast("Settings saved.", "ok"); render();
}

/* ---------------------------------------------------------------- drawer */
async function openDrawer(id) {
  const d = S.devices.find((x) => x.id === id);
  if (d && (d.state === "locked" || d.state === "auth_failed")) return loginModal(id);
  if (d && d.state === "offline") return refreshDev(id);
  S.cur = id; S.tab = "overview"; S.cfg = null;
  $("#drawer").classList.add("open"); $("#scrim").classList.add("show");
  headDrawer(d);
  $("#dBody").innerHTML = '<div style="display:grid;place-items:center;padding:60px"><span class="spinner"></span></div>';
  $$("#dTabs button").forEach((b) => b.classList.toggle("active", b.dataset.t === "overview"));
  await reloadCfg();
}
function headDrawer(d) {
  const [label] = STATE[d.state] || [d.state];
  $("#dTitle").innerHTML = `${d.kind === "recorder" ? ICON.rec : ICON.cam} ${esc(d.label || d.name || d.ip)} <span class="badge ${d.state}">${esc(label)}</span>`;
  $("#dSub").innerHTML = `${esc(d.manufacturer || "")} ${esc(d.model || "")} · <span class="mono">${esc(d.ip)}</span>`;
}
async function reloadCfg() {
  try {
    S.cfg = await api("GET", `/api/devices/${S.cur}/config`);
    headDrawer(S.cfg.device);
    renderTab();
  } catch (e) { $("#dBody").innerHTML = `<div class="err-box">${esc(e.message)}</div>`; }
}
function closeDrawer() {
  $("#drawer").classList.remove("open"); $("#scrim").classList.remove("show");
  clearInterval(S.liveTimer); S.cur = null;
}
$("#dTabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.tab = b.dataset.t;
  $$("#dTabs button").forEach((x) => x.classList.toggle("active", x === b));
  renderTab();
});

function sec(cfgKey) { return S.cfg[cfgKey] && S.cfg[cfgKey].ok ? S.cfg[cfgKey].data : null; }
function secErr(cfgKey) { const s = S.cfg[cfgKey]; return s && !s.ok ? `<div class="err-box">Couldn't read this from the device: ${esc(s.error)}</div>` : ""; }

function renderTab() {
  clearInterval(S.liveTimer);
  const fn = { overview: tabOverview, identity: tabIdentity, network: tabNetwork, time: tabTime, video: tabVideo, users: tabUsers, maint: tabMaint }[S.tab];
  $("#dBody").innerHTML = fn();
  if (S.tab === "video") $$(".profile-card[data-vec]").forEach(loadEncOptions);
  if (S.tab === "overview") startLive();
  if (S.tab === "network") netMode($("#dBody form"));
}

function tabOverview() {
  const d = S.cfg.device, info = sec("info") || {}, host = sec("hostname") || {}, net = sec("network"), t = sec("time") || {};
  const profiles = sec("profiles") || [];
  const iface = net && net.interfaces[0];
  let html = "";
  if (d.factory_fresh) html += `<div class="banner" style="background:rgba(168,85,247,.08);border-color:rgba(168,85,247,.4);color:#e9d5ff">${ICON.spark}
    <div style="flex:1"><b>This device is factory-fresh.</b> ${d.state === "factory" ? "It has no ONVIF users, so anyone on the network can change it." : "It still uses a default password."}
    Run first-time setup to secure it and give it a proper address.</div><button class="btn primary sm" onclick="openWizard(['${d.id}'])">Set up</button></div>`;
  if (profiles.length) html += `<div class="live"><img id="liveImg" alt=""><span class="rec-dot">LIVE</span>
      <div class="profile-pick"><select id="liveProfile" class="btn sm" onchange="startLive()">${profiles.map((p) => `<option value="${esc(p.token)}">${esc(p.name)}${p.encoder ? ` · ${p.encoder.width}×${p.encoder.height}` : ""}</option>`).join("")}</select></div></div>`;
  html += `<div class="section"><h5>Device</h5><dl class="kv">
    <dt>Manufacturer</dt><dd>${esc(info.Manufacturer || "—")}</dd><dt>Model</dt><dd>${esc(info.Model || "—")}</dd>
    <dt>Serial</dt><dd class="mono">${esc(info.SerialNumber || "—")}</dd><dt>Firmware</dt><dd>${esc(info.FirmwareVersion || "—")}</dd>
    <dt>Hostname</dt><dd>${esc(host.name || "—")}</dd><dt>Device clock (UTC)</dt><dd>${esc(t.utc ? new Date(t.utc).toLocaleString() : "—")} ${t.type ? `<span class="note">(${esc(t.type)})</span>` : ""}</dd>
    <dt>IP address</dt><dd class="mono">${esc(iface ? `${iface.address}/${iface.prefix}` : d.ip)} ${iface ? `<span class="note">${iface.dhcp ? "DHCP" : "Static"}</span>` : ""}</dd>
    <dt>MAC</dt><dd class="mono">${esc(iface ? iface.mac : d.mac || "—")}</dd>
    <dt>ONVIF endpoint</dt><dd class="mono">${esc(d.xaddr)}</dd>
    <dt>Logged in as</dt><dd>${d.state === "factory" ? "<i>no authentication (factory state)</i>" : esc(d.username || "—")}</dd>
  </dl></div>`;
  if (profiles.length) html += `<div class="section"><h5>Stream addresses</h5>${profiles.map((p) => `<div style="margin-bottom:10px"><b>${esc(p.name)}</b>
      <span class="note">${p.encoder ? `${esc(p.encoder.encoding)} ${p.encoder.width}×${p.encoder.height} @ ${p.encoder.fps} fps, ${p.encoder.bitrate} kbps` : ""}</span>
      <div class="uri"><code data-uri="${esc(p.token)}">loading…</code><button class="btn sm ghost" onclick="copyUri(this)">${ICON.copy}</button></div></div>`).join("")}</div>`;
  setTimeout(() => profiles.forEach(async (p) => {
    const el = $(`code[data-uri="${CSS.escape(p.token)}"]`);
    try { const r = await api("GET", `/api/devices/${S.cur}/stream-uri?profile=${encodeURIComponent(p.token)}`); el && (el.textContent = r.uri || "—"); }
    catch (e) { el && (el.textContent = e.message); }
  }));
  return html;
}
function copyUri(btn) { navigator.clipboard.writeText($("code", btn.parentElement).textContent); toast("Copied to clipboard."); }
function startLive() {
  clearInterval(S.liveTimer);
  const img = $("#liveImg"); if (!img) return;
  const prof = $("#liveProfile").value, id = S.cur;
  const tick = () => {
    if (S.cur !== id || S.tab !== "overview") return clearInterval(S.liveTimer);
    const n = new Image();
    n.onload = () => { img.src = n.src; };
    n.src = `/api/devices/${id}/snapshot?profile=${encodeURIComponent(prof)}&t=${Date.now()}`;
  };
  tick(); S.liveTimer = setInterval(tick, 2000);
}

function tabIdentity() {
  const d = S.cfg.device, host = sec("hostname") || {};
  return `<form class="section" onsubmit="saveIdentity(event)"><h5>Identity</h5><div class="form">
    <div class="field"><label>Display name (ONVIF name scope)</label><input name="name" value="${esc(d.name || "")}"><div class="hint">What VMS software such as ACC shows when it discovers the device.</div></div>
    <div class="field"><label>Hostname</label><input name="hostname" value="${esc(host.name || "")}" pattern="[A-Za-z0-9\\-]{1,63}"><div class="hint">Letters, numbers and dashes only.</div></div>
    <div class="field"><label>Location</label><input name="location" value="${esc(d.location || "")}" placeholder="e.g. Building A / Entrance"></div>
    <div class="field"><label>Label in this dashboard</label><input name="label" value="${esc(d.label || "")}" placeholder="optional, only stored here"></div>
  </div><div class="actions"><button class="btn primary">Save identity</button></div></form>${secErr("hostname")}`;
}
async function saveIdentity(e) {
  e.preventDefault(); const f = e.target;
  await act(() => api("PUT", `/api/devices/${S.cur}/identity`, { name: f.name.value, hostname: f.hostname.value || null, location: f.location.value, label: f.label.value }), "Identity saved.");
}

function tabNetwork() {
  const net = sec("network");
  if (!net) return secErr("network");
  const i = net.interfaces[0] || {};
  const dns = net.dns || { from_dhcp: true, servers: [] };
  return `<form class="section" onsubmit="saveNetwork(event)"><h5>IPv4 <span class="note mono" style="text-transform:none">${esc(i.name || "")} · ${esc(i.mac || "")}</span></h5>
    <input type="hidden" name="token" value="${esc(i.token)}">
    <div class="radio-cards" style="margin-bottom:16px">
      <label><input type="radio" name="mode" value="dhcp" ${i.dhcp ? "checked" : ""} onchange="netMode(this.form)"><div><b>DHCP</b><small>Get an address automatically</small></div></label>
      <label><input type="radio" name="mode" value="static" ${!i.dhcp ? "checked" : ""} onchange="netMode(this.form)"><div><b>Static</b><small>Fixed address (recommended for cameras)</small></div></label>
    </div>
    <div class="form" id="staticFields">
      <div class="field"><label>IP address</label><input name="address" value="${esc(i.address || "")}" placeholder="192.168.1.100"></div>
      <div class="field"><label>Subnet</label><select name="prefix">${[8, 16, 20, 21, 22, 23, 24, 25, 26, 27, 28].map((p) => `<option value="${p}" ${p === (i.prefix || 24) ? "selected" : ""}>/${p} (${prefixToMask(p)})</option>`).join("")}</select></div>
      <div class="field"><label>Default gateway</label><input name="gateway" value="${esc(net.gateway && net.gateway !== "0.0.0.0" ? net.gateway : "")}" placeholder="192.168.1.1"></div>
      <div class="field"><label>DNS servers</label><input name="dns" value="${esc(dns.from_dhcp ? "" : dns.servers.join(", "))}" placeholder="e.g. 192.168.1.1, 1.1.1.1"></div>
    </div>
    ${i.link_local ? `<p class="note">Link-local address: <span class="mono">${esc(i.link_local)}</span></p>` : ""}
    <p class="note">Changing the address takes effect immediately. The dashboard follows the device to its new IP, but this computer must be able to reach that subnet.</p>
    <div class="actions"><button class="btn primary">Apply network settings</button></div></form>`;
}
function prefixToMask(p) { const m = p ? (0xffffffff << (32 - p)) >>> 0 : 0; return [24, 16, 8, 0].map((s) => (m >>> s) & 255).join("."); }
function netMode(f) { const st = $("#staticFields"); if (!st || !f) return; const on = f.mode.value === "static"; st.style.opacity = on ? 1 : 0.45; $$("input,select", st).forEach((x) => (x.disabled = !on)); }
const IP_RE = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
async function saveNetwork(e) {
  e.preventDefault(); const f = e.target;
  const dhcp = f.mode.value === "dhcp";
  if (!dhcp && !IP_RE.test(f.address.value)) return toast("Enter a valid IP address.", "bad");
  if (!dhcp && f.gateway.value && !IP_RE.test(f.gateway.value)) return toast("Gateway isn't a valid IP address.", "bad");
  const dns = f.dns.value.split(/[ ,]+/).filter(Boolean);
  if (!confirm(dhcp ? "Switch this device to DHCP? Its address may change and you'll need to rescan to find it." : `Set this device to ${f.address.value}/${f.prefix.value}?`)) return;
  const r = await act(() => api("PUT", `/api/devices/${S.cur}/network`, { token: f.token.value, dhcp, address: f.address.value, prefix: +f.prefix.value, gateway: f.gateway.value || null, dns_dhcp: !dns.length, dns }), "Network settings applied.");
  if (r && r.reboot_needed) toast("The device says it needs a reboot for this to take effect.");
}

function tabTime() {
  const t = sec("time") || {}, ntp = sec("ntp") || { servers: [] };
  const tzKnown = TIMEZONES.some(([, v]) => v === t.timezone);
  return `<form class="section" onsubmit="saveTime(event)"><h5>Date &amp; time</h5>
    <p class="note" style="margin-top:-6px">Device clock: <b>${esc(t.utc ? new Date(t.utc).toLocaleString() : "unknown")}</b> · your computer: <b>${new Date().toLocaleString()}</b></p>
    <div class="radio-cards" style="margin-bottom:16px">
      <label><input type="radio" name="mode" value="NTP" ${t.type !== "Manual" ? "checked" : ""}><div><b>NTP</b><small>Keep time from a time server</small></div></label>
      <label><input type="radio" name="mode" value="Manual" ${t.type === "Manual" ? "checked" : ""}><div><b>Sync to this computer</b><small>Set the clock once, now</small></div></label>
    </div>
    <div class="form">
      <div class="field full"><label>NTP servers</label><input name="ntp" value="${esc(ntp.servers.join(", "))}" placeholder="pool.ntp.org, 192.168.1.1"><div class="hint">Often your recorder or domain controller. Leave empty to use NTP from DHCP.</div></div>
      <div class="field"><label>Time zone</label><select name="tz">${TIMEZONES.map(([n, v]) => `<option value="${esc(v)}" ${v === t.timezone ? "selected" : ""}>${esc(n)}</option>`).join("")}
        ${!tzKnown && t.timezone ? `<option value="${esc(t.timezone)}" selected>Current: ${esc(t.timezone)}</option>` : ""}</select></div>
      <div class="field"><label>Custom POSIX TZ (optional)</label><input name="tzc" placeholder="overrides the list, e.g. CST6CDT,M3.2.0,M11.1.0"></div>
    </div>
    <div class="actions"><button class="btn primary">Save time settings</button></div></form>${secErr("time")}`;
}
async function saveTime(e) {
  e.preventDefault(); const f = e.target;
  const servers = f.ntp.value.split(/[ ,]+/).filter(Boolean);
  await act(() => api("PUT", `/api/devices/${S.cur}/time`, { mode: f.mode.value, ntp_dhcp: !servers.length, ntp_servers: servers, timezone: f.tzc.value || f.tz.value }), "Time settings saved.");
}

function tabVideo() {
  const profiles = sec("profiles");
  if (!profiles) return secErr("profiles");
  if (!profiles.length) return `<div class="section note">This device has no media profiles${S.cfg.device.kind === "recorder" ? ". Recorders are configured through ACC; ONVIF here covers identity, network, time and users." : "."}</div>`;
  return profiles.filter((p) => p.encoder).map((p) => {
    const e = p.encoder;
    return `<form class="profile-card" data-vec="${esc(e.token)}" onsubmit="saveEncoder(event)">
      <h6>${esc(p.name)} <span>${esc(e.name)} · token ${esc(e.token)}</span></h6>
      <input type="hidden" name="token" value="${esc(e.token)}">
      <div class="form">
        <div class="field"><label>Codec</label><select name="encoding"><option>${esc(e.encoding)}</option></select></div>
        <div class="field"><label>Resolution</label><select name="res"><option value="${e.width}x${e.height}">${e.width} × ${e.height}</option></select></div>
        <div class="field full"><label>Frame rate</label><div class="range-row"><input type="range" name="fps" min="1" max="30" value="${e.fps}" oninput="this.nextElementSibling.value=this.value+' fps'"><output>${e.fps} fps</output></div></div>
        <div class="field full"><label>Max bitrate</label><div class="range-row"><input type="range" name="bitrate" min="128" max="20000" step="128" value="${e.bitrate}" oninput="this.nextElementSibling.value=fmtKbps(this.value)"><output>${fmtKbps(e.bitrate)}</output></div></div>
        <div class="field"><label>Keyframe interval (GOP)</label><input type="number" name="gov" min="1" max="300" value="${e.gov || e.fps}"></div>
        <div class="field"><label>Quality</label><input type="number" name="quality" min="0" max="100" step="1" value="${e.quality}"></div>
      </div>
      <div class="actions"><button class="btn primary">Apply to ${esc(p.name)}</button></div></form>`;
  }).join("");
}
function fmtKbps(v) { v = +v; return v >= 1000 ? `${(v / 1000).toFixed(1)} Mbps` : `${v} kbps`; }
async function loadEncOptions(form) {
  try {
    const o = await api("GET", `/api/devices/${S.cur}/encoder-options/${encodeURIComponent(form.dataset.vec)}`);
    const curEnc = form.encoding.value, curRes = form.res.value;
    if (o.encodings.length) {
      form.encoding.innerHTML = o.encodings.map((x) => `<option ${x.encoding === curEnc ? "selected" : ""}>${x.encoding}</option>`).join("");
      const fill = () => {
        const opt = o.encodings.find((x) => x.encoding === form.encoding.value) || o.encodings[0];
        const keep = form.res.value || curRes;
        form.res.innerHTML = opt.resolutions.map((r) => `<option value="${r.w}x${r.h}" ${`${r.w}x${r.h}` === keep ? "selected" : ""}>${r.w} × ${r.h}${r.w * r.h >= 3e6 ? " (max)" : ""}</option>`).join("");
        form.fps.min = opt.fps[0]; form.fps.max = opt.fps[1];
        if (+form.fps.value > opt.fps[1]) { form.fps.value = opt.fps[1]; form.fps.dispatchEvent(new Event("input")); }
        form.gov.min = opt.gov[0]; form.gov.max = opt.gov[1];
      };
      form.encoding.onchange = fill; fill();
    }
    if (o.quality) { form.quality.min = o.quality[0]; form.quality.max = o.quality[1]; }
  } catch (e) { /* keep current values only */ }
}
async function saveEncoder(e) {
  e.preventDefault(); const f = e.target;
  const [w, h] = f.res.value.split("x").map(Number);
  await act(() => api("PUT", `/api/devices/${S.cur}/encoder`, { token: f.token.value, encoding: f.encoding.value, width: w, height: h, fps: +f.fps.value, bitrate: +f.bitrate.value, gov: +f.gov.value || null, quality: +f.quality.value }), "Video settings applied.");
}

function tabUsers() {
  const users = sec("users");
  if (!users) return secErr("users");
  const me = S.cfg.device.username;
  return `<div class="section"><h5>ONVIF users <button class="btn sm" onclick="userModal()">+ Add user</button></h5>
    ${users.length ? `<table class="users"><tr><th>Username</th><th>Level</th><th></th></tr>${users.map((u) => `<tr><td>${esc(u.username)} ${u.username === me ? '<span class="note">(this dashboard)</span>' : ""}</td><td>${esc(u.level)}</td>
      <td style="text-align:right"><button class="btn sm ghost" onclick="userModal('${esc(u.username)}','${esc(u.level)}')">Change password</button>
      ${u.username !== me ? `<button class="btn sm danger" onclick="delUser('${esc(u.username)}')">Delete</button>` : ""}</td></tr>`).join("")}</table>`
      : `<p class="note">No users. The device is in its factory state and accepts commands from anyone. Use <b>first-time setup</b> or add an Administrator now.</p>`}
  </div>`;
}
function pwScore(p) { let s = 0; if (p.length >= 8) s++; if (p.length >= 12) s++; if (/[A-Z]/.test(p) && /[a-z]/.test(p)) s++; if (/\d/.test(p)) s++; if (/[^A-Za-z0-9]/.test(p)) s++; return s; }
function pwMeter(inp) { const s = pwScore(inp.value), m = inp.parentElement.querySelector(".pw-meter i"); if (!m) return; m.style.width = `${s * 20}%`; m.style.background = ["#ef4444", "#ef4444", "#f59e0b", "#f59e0b", "#22c55e", "#22c55e"][s]; }
function userModal(name, level) {
  modal(`<form onsubmit="saveUser(event,${name ? "true" : "false"})"><div class="modal-head"><h3>${name ? `Change password for ${esc(name)}` : "Add ONVIF user"}</h3></div>
    <div class="modal-body form">
      <div class="field"><label>Username</label><input name="u" value="${esc(name || "")}" ${name ? "readonly" : ""} required></div>
      <div class="field"><label>Level</label><select name="lv">${["Administrator", "Operator", "User"].map((l) => `<option ${l === (level || "Administrator") ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      <div class="field full"><label>Password</label><input name="p" type="password" required minlength="8" oninput="pwMeter(this)" autocomplete="new-password"><div class="pw-meter"><i></i></div>
        <div class="hint">Avigilon cameras need at least 8 characters; mix letters, numbers and symbols.</div></div>
    </div>
    <div class="modal-foot"><span></span><div style="display:flex;gap:10px"><button type="button" class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn primary">Save</button></div></div></form>`, "sm");
}
async function saveUser(e, existing) {
  e.preventDefault(); const f = e.target;
  const ok = await act(() => api(existing ? "PUT" : "POST", `/api/devices/${S.cur}/users`, { username: f.u.value, password: f.p.value, level: f.lv.value }), existing ? "Password changed." : "User added.");
  if (ok) closeModal();
}
async function delUser(u) {
  if (!confirm(`Delete user ${u}?`)) return;
  await act(() => api("DELETE", `/api/devices/${S.cur}/users/${encodeURIComponent(u)}`), "User deleted.");
}

function tabMaint() {
  return `<div class="section"><h5>Status</h5><div class="actions" style="justify-content:flex-start;margin:0">
      <button class="btn" onclick="act(()=>api('POST','/api/devices/${S.cur}/refresh'),'Status refreshed.')">Re-check status</button>
      <button class="btn" onclick="loginModal('${S.cur}')">Change saved login</button></div></div>
    <div class="section danger-zone"><h5>Danger zone</h5>
      <div class="row"><div><b>Reboot</b><div class="note">The device will be offline for about a minute.</div></div><button class="btn danger" onclick="doReboot()">Reboot</button></div>
      <div class="row"><div><b>Factory reset (keep network)</b><div class="note">Restores defaults but keeps the IP settings, so you can find it again.</div></div><button class="btn danger" onclick="doReset(false)">Soft reset</button></div>
      <div class="row"><div><b>Full factory reset</b><div class="note">Everything back to factory, including network. The device returns to DHCP or link-local.</div></div><button class="btn danger" onclick="doReset(true)">Hard reset</button></div>
      <div class="row"><div><b>Forget device</b><div class="note">Remove it from this dashboard only. It'll come back on the next scan.</div></div><button class="btn danger" onclick="doForget()">Forget</button></div>
    </div>`;
}
async function doReboot() { if (confirm("Reboot this device now?")) { await act(() => api("POST", `/api/devices/${S.cur}/reboot`), "Reboot started."); closeDrawer(); } }
async function doReset(hard) {
  const d = S.cfg.device;
  const typed = prompt(`${hard ? "FULL" : "Soft"} factory reset of ${d.label || d.name || d.ip}.\nThis cannot be undone. Type RESET to confirm.`);
  if (typed !== "RESET") return;
  await act(() => api("POST", `/api/devices/${S.cur}/factory-reset`, { hard }), "Factory reset started. Rescan in a couple of minutes.");
  closeDrawer();
}
async function doForget() { await api("DELETE", `/api/devices/${S.cur}`); closeDrawer(); load(); }

/* run an action, toast, refresh */
async function act(fn, okMsg) {
  try {
    const r = await fn();
    toast(okMsg, "ok");
    await load();
    if (S.cur) { const d = S.devices.find((x) => x.id === S.cur); if (d && canTalk(d)) await reloadCfg(); }
    return r || true;
  } catch (e) { toast(esc(e.message), "bad"); return null; }
}

/* ---------------------------------------------------------------- setup wizard */
const W = { ids: [], step: 0, data: {} };
const WSTEPS = ["Security", "Identity", "Network", "Time", "Video", "Review"];

function openWizard(ids) {
  if (!ids.length) return;
  const devs = ids.map((id) => S.devices.find((d) => d.id === id)).filter(Boolean);
  const bad = devs.filter((d) => !canTalk(d));
  if (bad.length) return toast(`${bad.length} selected device(s) need a login or are offline. Log in first, or deselect them.`, "bad");
  W.ids = ids; W.step = 0;
  const one = devs.length === 1 ? devs[0] : null;
  W.data = {
    admin_username: "admin", admin_password: "", admin_password2: "",
    name: one ? (one.name || "") : "", hostname: one ? (one.hostname || "") : "", prefix: "cam-", location: "",
    netmode: "keep", address: one && !one.link_local ? one.ip : "", netprefix: 24, gateway: "", dns: "",
    timemode: "NTP", ntp: "pool.ntp.org", tz: guessTZ(),
    vres: "keep", vfps: 25, vbitrate: 4000, reboot: false,
  };
  closeDrawer();
  drawWizard();
}
function devNames() { return W.ids.map((id) => S.devices.find((d) => d.id === id)).map((d) => d.label || d.name || d.ip); }

function drawWizard() {
  const multi = W.ids.length > 1, D = W.data;
  const steps = WSTEPS.map((s, i) => `<div class="${i < W.step ? "done" : i === W.step ? "cur" : ""}"><i></i><span>${s}</span></div>`).join("");
  let body = "";
  switch (W.step) {
    case 0: body = `<div class="form">
        <div class="field full"><p class="note" style="margin:0 0 4px">Create the administrator account used by ONVIF clients such as ACC. ${multi ? "Every selected device gets the same account." : ""}</p></div>
        <div class="field"><label>Admin username</label><input data-k="admin_username" value="${esc(D.admin_username)}"></div><div></div>
        <div class="field"><label>New password</label><input type="password" data-k="admin_password" value="${esc(D.admin_password)}" oninput="pwMeter(this)" autocomplete="new-password"><div class="pw-meter"><i></i></div></div>
        <div class="field"><label>Confirm password</label><input type="password" data-k="admin_password2" value="${esc(D.admin_password2)}" autocomplete="new-password"></div>
        <div class="field full hint">At least 8 characters with letters and numbers. Store it in your password manager: you'll need it to add the device to ACC.</div></div>`; break;
    case 1: body = `<div class="form">
        ${multi ? `<div class="field"><label>Hostname prefix</label><input data-k="prefix" value="${esc(D.prefix)}"><div class="hint">Devices become ${esc(D.prefix)}01, ${esc(D.prefix)}02…</div></div>`
               : `<div class="field"><label>Display name</label><input data-k="name" value="${esc(D.name)}" placeholder="e.g. Front Entrance"></div>
                  <div class="field"><label>Hostname</label><input data-k="hostname" value="${esc(D.hostname)}" placeholder="cam-front-entrance"></div>`}
        <div class="field ${multi ? "" : "full"}"><label>Location (optional)</label><input data-k="location" value="${esc(D.location)}" placeholder="Building A"></div></div>`; break;
    case 2: body = `<div class="radio-cards" style="margin-bottom:16px">
        ${["keep:Keep current:Leave the address alone", "dhcp:DHCP:Address from your DHCP server", `static:Static:${multi ? "Sequential fixed addresses" : "Fixed address"}`].map((s) => { const [v, t, h] = s.split(":"); return `<label><input type="radio" name="netmode" value="${v}" ${D.netmode === v ? "checked" : ""} onchange="wset('netmode',this.value)"><div><b>${t}</b><small>${h}</small></div></label>`; }).join("")}</div>
        ${D.netmode === "static" ? `<div class="form">
          <div class="field"><label>${multi ? "First IP address" : "IP address"}</label><input data-k="address" value="${esc(D.address)}" placeholder="192.168.1.100">${multi ? `<div class="hint">Next devices get the following addresses (+1 each).</div>` : ""}</div>
          <div class="field"><label>Subnet</label><select data-k="netprefix">${[16, 20, 22, 23, 24, 25, 26].map((p) => `<option value="${p}" ${p == D.netprefix ? "selected" : ""}>/${p} (${prefixToMask(p)})</option>`).join("")}</select></div>
          <div class="field"><label>Gateway</label><input data-k="gateway" value="${esc(D.gateway)}" placeholder="192.168.1.1"></div>
          <div class="field"><label>DNS servers</label><input data-k="dns" value="${esc(D.dns)}" placeholder="optional"></div></div>` : ""}
        <p class="note">The network change is applied last, after everything else, so the device stays reachable during setup.</p>`; break;
    case 3: body = `<div class="radio-cards" style="margin-bottom:16px">
          <label><input type="radio" name="tm" value="NTP" ${D.timemode === "NTP" ? "checked" : ""} onchange="wset('timemode',this.value)"><div><b>NTP</b><small>Recommended. Use your recorder or a time server.</small></div></label>
          <label><input type="radio" name="tm" value="Manual" ${D.timemode === "Manual" ? "checked" : ""} onchange="wset('timemode',this.value)"><div><b>Sync to this computer</b><small>One-off set, for isolated networks</small></div></label>
          <label><input type="radio" name="tm" value="skip" ${D.timemode === "skip" ? "checked" : ""} onchange="wset('timemode',this.value)"><div><b>Skip</b><small>Don't touch the clock</small></div></label></div>
        ${D.timemode !== "skip" ? `<div class="form">
          ${D.timemode === "NTP" ? `<div class="field full"><label>NTP servers</label><input data-k="ntp" value="${esc(D.ntp)}"><div class="hint">Comma separated. Your Avigilon NVR or domain controller is a good choice on isolated camera networks.</div></div>` : ""}
          <div class="field full"><label>Time zone</label><select data-k="tz">${TIMEZONES.map(([n, v]) => `<option value="${esc(v)}" ${v === D.tz ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></div></div>` : ""}`; break;
    case 4: body = `<div class="form">
        <div class="field full"><label>Primary stream</label><select data-k="vres" onchange="wset('vres',this.value)">
          ${[["keep", "Keep camera defaults"], ["3840x2160", "4K · 3840 × 2160"], ["2592x1944", "5 MP · 2592 × 1944"], ["2560x1440", "4 MP · 2560 × 1440"], ["1920x1080", "1080p · 1920 × 1080"], ["1280x720", "720p · 1280 × 720"]].map(([v, n]) => `<option value="${v}" ${v === D.vres ? "selected" : ""}>${n}</option>`).join("")}
        </select><div class="hint">Applied to each camera's highest-resolution profile. If a camera doesn't support the size, that step is reported and the rest continues.</div></div>
        ${D.vres !== "keep" ? `<div class="field full"><label>Frame rate</label><div class="range-row"><input type="range" min="1" max="30" data-k="vfps" value="${D.vfps}" oninput="this.nextElementSibling.value=this.value+' fps'"><output>${D.vfps} fps</output></div></div>
          <div class="field full"><label>Max bitrate</label><div class="range-row"><input type="range" min="512" max="16384" step="256" data-k="vbitrate" value="${D.vbitrate}" oninput="this.nextElementSibling.value=fmtKbps(this.value)"><output>${fmtKbps(D.vbitrate)}</output></div></div>` : ""}
        <div class="field full"><label class="switch"><input type="checkbox" data-k="reboot" ${D.reboot ? "checked" : ""}><span></span>Reboot when finished</label></div></div>`; break;
    case 5: body = reviewHTML(); break;
    case 6: body = `<div id="wizResults"><div style="display:flex;gap:12px;align-items:center"><span class="spinner"></span> Applying settings…</div></div>`; break;
  }
  const last = W.step === 5, running = W.step === 6;
  modal(`<div class="modal-head"><h3>${ICON.spark} First-time setup</h3><p>${multi ? `${W.ids.length} devices` : esc(devNames()[0])}</p>
      ${multi ? `<div class="dev-pills">${devNames().map((n) => `<span>${esc(n)}</span>`).join("")}</div>` : ""}</div>
    ${running ? "" : `<div class="steps">${steps}</div>`}
    <div class="modal-body">${body}<div class="err-box" id="wizErr" hidden style="margin-top:14px"></div></div>
    <div class="modal-foot" id="wizFoot">${running ? "" : `<button class="btn ghost" onclick="${W.step ? "wizBack()" : "closeModal()"}">${W.step ? "Back" : "Cancel"}</button>
      <button class="btn primary" onclick="${last ? "wizApply()" : "wizNext()"}">${last ? `Apply to ${W.ids.length} device${W.ids.length > 1 ? "s" : ""}` : "Continue"}</button>`}</div>`);
  const pw = $('#modal input[data-k="admin_password"]'); pw && pwMeter(pw);
}
function wcollect() { $$("#modal [data-k]").forEach((el) => { W.data[el.dataset.k] = el.type === "checkbox" ? el.checked : el.value; }); }
function wset(k, v) { wcollect(); W.data[k] = v; drawWizard(); }
function wizBack() { wcollect(); W.step--; drawWizard(); }
function wizNext() {
  wcollect();
  const D = W.data, err = (m) => { const b = $("#wizErr"); b.hidden = false; b.textContent = m; };
  if (W.step === 0) {
    if (!D.admin_username) return err("Enter a username.");
    if (D.admin_password.length < 8) return err("Use at least 8 characters for the password.");
    if (!/[A-Za-z]/.test(D.admin_password) || !/\d/.test(D.admin_password)) return err("Mix letters and numbers in the password.");
    if (D.admin_password !== D.admin_password2) return err("The passwords don't match.");
  }
  if (W.step === 1 && W.ids.length === 1 && D.hostname && !/^[A-Za-z0-9-]{1,63}$/.test(D.hostname)) return err("Hostnames can only contain letters, numbers and dashes.");
  if (W.step === 2 && D.netmode === "static") {
    if (!IP_RE.test(D.address)) return err("Enter a valid IP address.");
    if (D.gateway && !IP_RE.test(D.gateway)) return err("The gateway isn't a valid IP address.");
  }
  if (W.step === 3 && D.timemode === "NTP" && !D.ntp.trim()) return err("Enter at least one NTP server, or choose another option.");
  W.step++; drawWizard();
}
function ipAdd(ip, n) { const p = ip.split(".").map(Number); let v = ((p[0] << 24) >>> 0) + (p[1] << 16) + (p[2] << 8) + p[3] + n; return [24, 16, 8, 0].map((s) => (v >>> s) & 255).join("."); }
function reviewHTML() {
  const D = W.data, multi = W.ids.length > 1;
  const tzName = (TIMEZONES.find(([, v]) => v === D.tz) || [D.tz])[0];
  const net = D.netmode === "keep" ? "Unchanged" : D.netmode === "dhcp" ? "DHCP" :
    multi ? `Static ${D.address} → ${ipAdd(D.address, W.ids.length - 1)} /${D.netprefix}` : `Static ${D.address}/${D.netprefix}`;
  return `<dl class="review">
    <dt>Administrator</dt><dd>${esc(D.admin_username)} · ${"•".repeat(Math.min(D.admin_password.length, 12))}</dd>
    <dt>${multi ? "Hostnames" : "Name / hostname"}</dt><dd>${multi ? `${esc(D.prefix)}01 … ${esc(D.prefix)}${String(W.ids.length).padStart(2, "0")}` : `${esc(D.name || "—")} / ${esc(D.hostname || "unchanged")}`}</dd>
    <dt>Location</dt><dd>${esc(D.location || "—")}</dd>
    <dt>Network</dt><dd>${esc(net)}${D.netmode === "static" && D.gateway ? `, gateway ${esc(D.gateway)}` : ""}</dd>
    <dt>Time</dt><dd>${D.timemode === "skip" ? "Unchanged" : D.timemode === "NTP" ? `NTP ${esc(D.ntp)} · ${esc(tzName)}` : `Sync to this computer · ${esc(tzName)}`}</dd>
    <dt>Video</dt><dd>${D.vres === "keep" ? "Camera defaults" : `${D.vres.replace("x", " × ")} @ ${D.vfps} fps, ${fmtKbps(D.vbitrate)}`}</dd>
    <dt>Reboot</dt><dd>${D.reboot ? "Yes" : "No"}</dd></dl>`;
}
async function wizApply() {
  const D = W.data, multi = W.ids.length > 1;
  W.step = 6; drawWizard();
  const ntp = D.ntp.split(/[ ,]+/).filter(Boolean);
  const tpl = {
    admin_username: D.admin_username, admin_password: D.admin_password,
    hostname: multi ? null : D.hostname || null, name: multi ? null : D.name || null, location: D.location || null,
    time: D.timemode === "skip" ? null : { mode: D.timemode, ntp_dhcp: false, ntp_servers: ntp, timezone: D.tz },
    video_preset: D.vres === "keep" ? null : { width: +D.vres.split("x")[0], height: +D.vres.split("x")[1], fps: +D.vfps, bitrate: +D.vbitrate, gov: +D.vfps },
    network: D.netmode === "keep" ? null : { token: "", dhcp: D.netmode === "dhcp", address: D.address || null, prefix: +D.netprefix, gateway: D.gateway || null,
      dns_dhcp: !D.dns.trim(), dns: D.dns.split(/[ ,]+/).filter(Boolean) },
    reboot: !!D.reboot,
  };
  let results;
  try {
    const r = await api("POST", "/api/setup/batch", { ids: W.ids, template: tpl, ip_start: D.netmode === "static" ? D.address : null, hostname_prefix: multi ? D.prefix : null });
    results = r.results;
  } catch (e) { results = [{ ok: false, steps: [{ step: "Request", ok: false, error: e.message }] }]; }
  const okAll = results.every((r) => r.ok);
  $("#wizResults").innerHTML = results.map((r) => {
    const dev = S.devices.find((d) => d.id === r.id);
    return `${multi ? `<h4 style="margin:14px 0 4px">${esc(dev ? dev.label || dev.name || dev.ip : r.id)} ${r.ok ? '<span class="badge ready">Done</span>' : '<span class="badge offline">Issues</span>'}</h4>` : ""}
      <ul class="result-list">${r.steps.map((s) => `<li><span class="${s.ok ? "ok" : "bad"}">${s.ok ? ICON.ok : ICON.x}</span>${esc(s.step)}<small>${esc(s.ok ? s.detail || (s.new_ip ? `now at ${s.new_ip}` : "") : s.error)}</small></li>`).join("")}</ul>`;
  }).join("") + `<p class="note" style="margin-top:14px">${okAll ? "All done. Credentials are saved in this dashboard, and the device is ready to add to ACC." : "Some steps failed. Open the device to retry individual settings."}</p>`;
  $("#wizFoot").innerHTML = `<span></span><button class="btn primary" onclick="closeModal()">Close</button>`;
  S.selected.clear();
  await load();
}

/* ---------------------------------------------------------------- wire up */
$("#btnScan").onclick = scan;
$("#btnAdd").onclick = addModal;
$("#btnSettings").onclick = settingsModal;
$("#filters").addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) { S.filter = c.dataset.f; render(); } });
$("#search").addEventListener("input", (e) => { S.search = e.target.value; render(); });
$("#avOnly").addEventListener("change", (e) => { S.avOnly = e.target.checked; api("PUT", "/api/settings", { avigilon_only: S.avOnly }); render(); });
$("#viewToggle").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; S.view = b.dataset.v; $$("#viewToggle button").forEach((x) => x.classList.toggle("active", x === b)); render(); });

(async () => {
  try { const s = await api("GET", "/api/settings"); S.avOnly = !!s.avigilon_only; $("#avOnly").checked = S.avOnly; } catch (e) { /* ignore */ }
  await load();
  if (!S.devices.length) scan();
})();
