import { shortcut, handledShortcut, coordinate } from "/controls.mjs";

const $ = (id) => document.getElementById(id);
const headers = { "Content-Type": "application/json" };
const fields = ["model", "year", "os_version", "firmware"];
const fieldLabels = { model: "Model", year: "Year", os_version: "Tizen version", firmware: "Firmware" };
const urls = new Map(), cards = new Map();
let state = { sources: [], tvs: [], scan: { status: "never" }, sdk: "available" };
let busy = false, stopped = false, offline = false, selected = null;
const rows = new Map();

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "data") Object.assign(node.dataset, value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}
function error(message) { $("error").hidden = !message; $("error").textContent = message || ""; }
async function request(path, data) {
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(path, { method: data === undefined ? "GET" : "POST", headers,
      body: data === undefined ? undefined : JSON.stringify(data), signal: controller.signal });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || "Local request failed");
    return result;
  } finally { clearTimeout(timeout); }
}

// ---- naming ---------------------------------------------------------------
// A TV with no known name or model is called by its address, never an invented number.
function tvTitle(tv) {
  const address = tv.details?.address;
  return tv.unnamed && address ? `TV at ${address}` : tv.name;
}

function sourceName(source) {
  if (source.kind === "emulator") return "Emulator";
  const words = source.id.replace(/[-_]+/g, " ").trim();
  let name = words.charAt(0).toUpperCase() + words.slice(1);
  if (!/\btv\b/i.test(name)) name += " TV";
  const year = source.metadata?.year?.value;
  return year ? `${name} (${year})` : name;
}

// ---- dialogs ----------------------------------------------------------------
function renamePrompt(current) {
  const dialog = $("rename"); $("rename-name").value = current || "";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok" ? $("rename-name").value : null), { once: true });
    dialog.returnValue = ""; dialog.showModal();
  });
}

// ---- commands -----------------------------------------------------------------
async function tvCommand(op, alias, data = {}) {
  error("");
  let ok = false;
  try { state = { ...state, ...(await request(`/api/tv/${op}/${alias}`, data)) }; ok = true; }
  catch (problem) { error(problem.message); }
  render();
  return ok;
}
// An app ID belongs to one TV and is remembered only after it actually opened there.
// The old global key pre-filled every TV with whatever was typed last; drop it.
try { localStorage.removeItem("tizen-preview-app"); } catch { /* storage unavailable */ }
const previewKey = (alias) => `tizen-preview-app:${alias}`;
function rememberedApp(alias) {
  try { return localStorage.getItem(previewKey(alias)) || ""; } catch { return ""; }
}
async function scan() {
  error(""); $("scan").disabled = true;
  try { await request("/api/detect", {}); } catch (problem) { error(problem.message); }
  await refresh();
}
async function command(id, operation, data) {
  if (operation === "stream" && data.enabled === false) {
    try { await request(`/api/stream/${id}`, data); await refresh(); }
    catch (problem) { error(`${problem.message}. Pausing the live view was not confirmed.`); }
    return;
  }
  if (busy) return; // No queue, repeat dispatch or unattended input.
  const card = cards.get(`source:${id}`) || cards.get(`tv:${id}`);
  if (operation === "key" && document.activeElement?.closest(".remote")) card?.remote.focus();
  if (operation === "click") card?.view?.focus();
  busy = true; error(""); render();
  try {
    const completed = await request(`/api/${operation}/${id}`, data);
    const source = sourceById(id);
    if (["key", "click"].includes(operation) && source?.pixels_allowed) {
      await refresh(); // Acknowledge measured input before requesting costly pixels.
      if (card) card.reason.textContent = `Key sent in ${completed.command_ms} ms. Updating the picture…`;
      await request(`/api/capture/${id}`, {});
    }
  } catch (problem) { error(`${problem.message}. Nothing was retried automatically.`); }
  finally { busy = false; await refresh(); }
}

function sourceById(id) {
  return state.sources.find((s) => s.id === id) || (state.tvs || []).find((t) => t.alias === id)?.preview || null;
}
const tones = { ok: "connected", progress: "working", lost: "lost", warn: "warn", idle: "ready" };
// The server owns the one status per TV (same words as the CLI); the page only adds "Connection lost"
// when it cannot reach its own helper.
function tvStatus(tv) {
  if (offline) return { label: "Connection lost", tone: "lost", detail: "This page lost its local helper. Reopen the portal." };
  return { label: tv.label, tone: tones[tv.tone] || "", detail: tv.headline || "" };
}
function usablePreview(card, preview) {
  return card.previewReady && !offline && preview?.ready && !preview.stale &&
    preview.revision === card.revision && preview.frames?.length > 0;
}

// ---- remote ---------------------------------------------------------------------
function remotePanel() {
  const button = (key, label, text) => el("button", { type: "button", data: { key }, "aria-label": label }, text);
  const why = el("p", { class: "why" });
  // Keyboard hints only while a picture is showing: before that, no key reaches anything.
  const hint = el("p", { class: "hint", hidden: true }, "Keyboard: arrows, Enter = OK, Backspace = Back");
  const remote = el("div", { class: "remote", tabIndex: 0, role: "group", "aria-label": "Remote" },
    el("h3", {}, "Remote"),
    el("div", { class: "dpad" }, button("up", "Up", "▲"), button("left", "Left", "◀"), button("ok", "OK", "OK"),
      button("right", "Right", "▶"), button("down", "Down", "▼")),
    el("div", { class: "remote-row" }, button("back", "Back", "Back")),
    why, hint);
  return { remote, why, hint, keys: [...remote.querySelectorAll("[data-key]")] };
}

// ---- scanned TV cards -------------------------------------------------------------
const certText = (c) => c?.profile ? `Your certificate ${c.profile} covers: ${(c.covers || []).join(", ") || "no TVs"}.` : "";
function tvCard(alias) {
  const current = () => (state.tvs || []).find((t) => t.alias === alias);
  const name = el("h2"), pill = el("span", { class: "pill" }), reason = el("p", { class: "reason" });
  const rename = el("button", { type: "button", class: "link", "aria-label": "Rename", title: "Rename" }, "✎ Rename");
  const connect = el("button", { type: "button", class: "primary" }, "Connect");
  const disconnect = el("button", { type: "button", class: "secondary" }, "Disconnect");
  const rescan = el("button", { type: "button", class: "primary", hidden: true }, "Scan again");
  const guide = el("div", { class: "guide", hidden: true });
  // Screen
  const screenText = el("p", { class: "placeholder" }), frames = el("div", { class: "frames" }), age = el("p", { class: "note" });
  const refreshPicture = el("button", { type: "button", class: "secondary" }, "Refresh picture");
  const save = el("button", { type: "button", class: "secondary" }, "Save screenshot");
  const screen = el("section", { class: "picture", "aria-label": "Screen" },
    el("div", { class: "row" }, el("h3", {}, "Screen"), refreshPicture, save), screenText, age, frames);
  // Apps
  const appsNote = el("p", { class: "subtle" }), appList = el("ul", { class: "apps", "aria-label": "Apps on this TV" });
  const reloadApps = el("button", { type: "button", class: "link" }, "Reload list");
  const apps = el("section", { class: "apps-section", "aria-label": "Apps on this TV" },
    el("div", { class: "row" }, el("h3", {}, "Apps on this TV"), reloadApps), appsNote, appList);
  // Install
  const pkg = el("input", { maxLength: 1024, placeholder: "/path/to/app.wgt", "aria-label": "Package file (.wgt)",
    autocomplete: "off", spellcheck: "false" });
  const choose = el("button", { type: "button", class: "secondary" }, "Choose .wgt…");
  const install = el("button", { type: "button", class: "primary" }, "Install");
  const installSection = el("section", { class: "install", "aria-label": "Install app" },
    el("h3", {}, "Install app"), el("div", { class: "row" }, pkg, choose, install));
  // Certificate: appears only when this TV is not in the active certificate.
  const certLine = el("p"), certButton = el("button", { type: "button", class: "primary" }, "Add this TV to my certificate");
  const copyDuid = el("button", { type: "button", class: "secondary" }, "Copy device ID");
  const certCommand = el("p", { class: "subtle" });
  const cert = el("section", { class: "guide cert", "aria-label": "Certificate", hidden: true },
    certLine, el("div", { class: "row" }, certButton, copyDuid), certCommand);
  const job = el("div", { class: "job", role: "status", "aria-live": "polite" });
  const list = el("dl");
  const details = el("details", { class: "tv-details" }, el("summary", {}, "TV details"), list);
  const { remote, why, hint, keys } = remotePanel();
  const connected = el("div", { class: "connected-body" }, screen, apps, installSection);
  const root = el("article", { class: "device", "aria-label": alias },
    el("div", { class: "device-main" },
      el("div", { class: "title-row" }, name, pill, rename), reason,
      el("div", { class: "row" }, connect, rescan, disconnect), guide, cert, job, connected, details),
    remote);
  const card = { root, name, pill, reason, connect, rescan, disconnect, guide, screenText, frames, age, refreshPicture, save,
    appsNote, appList, reloadApps, apps, pkg, choose, install, installSection, cert, certLine, certButton, copyDuid,
    certCommand, job, list, details, connected, remote, why, hint, keys, summary: "", revision: null, appRows: new Map() };

  connect.addEventListener("click", () => tvCommand("connect", alias, {}));
  disconnect.addEventListener("click", () => tvCommand("disconnect", alias));
  rescan.addEventListener("click", scan);
  rename.addEventListener("click", async () => {
    const tv = current(); if (!tv) return;
    const value = await renamePrompt(tv.renamed ? tv.name : "");
    if (value !== null) tvCommand("rename", alias, { name: value });
  });
  refreshPicture.addEventListener("click", () => command(alias, "capture", {}));
  save.addEventListener("click", () => tvCommand("screenshot", alias, {}));
  reloadApps.addEventListener("click", () => tvCommand("apps", alias, {}));
  choose.addEventListener("click", async () => {
    error("");
    try { const picked = await request("/api/choose-package", {}); if (picked.path) pkg.value = picked.path; }
    catch (problem) { error(`${problem.message}. Paste the .wgt path instead.`); }
  });
  install.addEventListener("click", () => {
    const path = pkg.value.trim();
    if (!path) { error("Choose a .wgt file or paste its path first."); pkg.focus(); return; }
    tvCommand("install", alias, { package: path });
  });
  certButton.addEventListener("click", () => tvCommand("cert", alias, {}));
  copyDuid.addEventListener("click", async () => {
    const duid = current()?.details?.duid; if (!duid) return;
    try { await navigator.clipboard.writeText(duid); copyDuid.textContent = "Copied"; setTimeout(() => { copyDuid.textContent = "Copy device ID"; }, 1500); }
    catch { error(`Copy failed. The device ID is ${duid}.`); }
  });
  const send = (key) => { if (usablePreview(card, sourceById(alias))) command(alias, "key", { key }); };
  for (const key of keys) key.addEventListener("click", () => send(key.dataset.key));
  remote.addEventListener("keydown", (event) => {
    if (!handledShortcut(event)) return;
    event.preventDefault();
    const p = sourceById(alias), key = shortcut(event, usablePreview(card, p) && p.input_allowed, busy);
    if (key) command(alias, "key", { key });
  });
  return card;
}
function openApp(alias, appId) { return tvCommand("preview", alias, { app: appId, restart: true }); }
function appRow(card, alias, appId) {
  const id = el("code", { class: "app-id" }, appId), tag = el("span", { class: "app-tag" });
  const open = el("button", { type: "button", class: "secondary" }, "Open screen (restarts app)");
  const shot = el("button", { type: "button", class: "secondary" }, "Screenshot");
  const remove = el("button", { type: "button", class: "secondary danger" }, "Uninstall");
  open.addEventListener("click", () => openApp(alias, appId));
  shot.addEventListener("click", () => tvCommand("screenshot", alias, { app: appId }));
  remove.addEventListener("click", () => tvCommand("remove", alias, { app: appId }));
  const item = el("li", { class: "app" }, el("div", { class: "app-name" }, id, tag), el("div", { class: "row" }, open, shot, remove));
  return { item, tag, open, shot, remove };
}
function renderApps(card, tv) {
  const a = tv.apps || { items: [], store: [], installed_here: [] };
  const store = new Set(a.store || []), mine = new Set(a.installed_here || []);
  const showing = tv.preview?.app, last = rememberedApp(tv.alias), working = Boolean(tv.pending) || offline;
  card.appsNote.textContent = a.state === "listed" ? `${a.items.length} app${a.items.length === 1 ? "" : "s"}. Store apps may refuse; the TV gives the answer.`
    : a.state === "unreadable" ? "The TV did not return its app list. Choose Reload list." : "Reading the app list…";
  const wanted = a.state === "listed" ? a.items : [];
  for (const [appId, row] of card.appRows) if (!wanted.includes(appId)) { row.item.remove(); card.appRows.delete(appId); }
  wanted.forEach((appId, index) => {
    if (!card.appRows.has(appId)) card.appRows.set(appId, appRow(card, tv.alias, appId));
    const row = card.appRows.get(appId);
    row.tag.textContent = appId === showing ? "On screen" : store.has(appId) ? "Store app — can't be viewed"
      : mine.has(appId) ? "Installed from here" : appId === last ? "Last opened" : "";
    row.tag.className = `app-tag${store.has(appId) ? " muted" : appId === showing ? " live" : ""}`;
    row.open.className = mine.has(appId) || appId === last ? "primary" : "secondary";
    row.open.textContent = appId === showing ? "Reopen (restarts app)" : "Open screen (restarts app)";
    for (const b of [row.open, row.shot, row.remove]) b.disabled = working;
    if (card.appList.children[index] !== row.item) card.appList.insertBefore(row.item, card.appList.children[index] || null);
  });
}
function renderJob(card, tv) {
  const j = tv.job;
  if (!j) { card.job.replaceChildren(); return; }
  const parts = [el("p", { class: j.ok ? "" : "problem" }, j.message || (j.ok ? "Done" : "Failed"))];
  for (const path of j.paths || []) parts.push(el("p", { class: "subtle" }, "Saved to ", el("code", {}, path)));
  if (j.backup) parts.push(el("p", { class: "subtle" }, "Old certificate files backed up to ", el("code", {}, j.backup)));
  if (j.command) parts.push(el("p", { class: "subtle" }, "Command: ", el("code", {}, j.command)));
  if (j.next) parts.push(el("p", { class: "subtle" }, `Next: ${j.next}`));
  if (j.output) parts.push(el("pre", { class: "output" }, j.output));
  card.job.replaceChildren(...parts);
}
async function updateTv(card, tv, keep) {
  const p = tv.preview;
  card.previewReady = false;
  let live = tv.status === "connected" && Boolean(p?.ready) && (p.frames || []).length > 0 && !p.stale && !offline;
  let previewError = "";
  if (live && (card.revision !== p.revision || !card.frames.childElementCount)) {
    const figures = [];
    try {
      for (let i = 0; i < p.frames.length; i++) {
        const frame = p.frames[i];
        const img = el("img", { src: await image({ id: tv.alias, revision: p.revision }, i),
          alt: `${tvTitle(tv)}: ${p.app} screen, layer ${i + 1}`, width: frame.width, height: frame.height });
        await img.decode();
        figures.push(el("figure", {}, img));
      }
      card.frames.replaceChildren(...figures);
      card.revision = p.revision;
    } catch (problem) {
      live = false;
      previewError = `Could not show the picture: ${problem.message}`;
    }
  }
  card.name.textContent = tvTitle(tv);
  const shown = tvStatus(tv), working = Boolean(tv.pending);
  card.pill.textContent = shown.label;
  card.pill.className = `pill ${shown.tone}`;
  card.reason.textContent = shown.detail;
  card.reason.className = `reason${shown.tone === "lost" ? " problem" : ""}`;
  // Not reachable: one line ("Turn the TV on, then scan again") and a Scan button; nothing to connect to.
  const unreachable = !tv.held && tv.label === "Not reachable";
  const canConnect = !tv.held && ["Ready to connect", "Connection problem"].includes(tv.label);
  card.connect.hidden = !canConnect;
  card.connect.textContent = tv.label === "Connection problem" ? "Try again" : "Connect";
  card.connect.disabled = working || offline || state.sdk === "missing";
  card.rescan.hidden = !unreachable;
  card.rescan.disabled = offline || state.scan?.status === "scanning";
  card.disconnect.hidden = !tv.held;
  card.disconnect.disabled = working || offline;
  const meta = tv.metadata || {}, d = tv.details || {};
  const seen = unreachable && tv.last_seen ? `last seen ${new Date(tv.last_seen * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "";
  card.summary = [meta.model, meta.os?.text, unreachable ? "" : d.address, seen].filter(Boolean).join(" · ");
  // Setup guidance appears only when it explains why this TV can't be used yet.
  const guidance = (tv.held && tv.status === "connected") || unreachable ? null : tv.guidance;
  const next = tv.dev_mode?.next && !tv.held && !unreachable && tv.label !== "Ready to connect" ? tv.dev_mode.next : "";
  card.guide.hidden = !guidance && !next;
  if (guidance) {
    const host = guidance.host_ip ? el("p", {}, "This computer's address: ", el("code", {}, guidance.host_ip)) : null;
    card.guide.replaceChildren(...[el("h3", {}, guidance.title), host,
      el("ol", {}, ...(guidance.steps || []).map((step) => el("li", {}, step.replace("{host}", guidance.host_ip || "this computer's address"))))].filter(Boolean));
  } else if (next) card.guide.replaceChildren(el("p", {}, `Next: ${next}`));
  // Certificate: one action, only when this connected TV is not in the active certificate.
  const c = tv.certificate || {}, fix = tv.held && ["not-covered", "no-profile", "default"].includes(c.state) && d.duid;
  card.cert.hidden = !fix;
  if (fix) {
    card.certLine.textContent = c.state === "not-covered" ? `${certText(c)} This TV is ${d.duid}.`
      : `You have no Samsung distributor certificate yet. This TV is ${d.duid}.`;
    card.certButton.textContent = tv.cert_tool === "installed"
      ? (c.state === "not-covered" ? "Add this TV to my certificate" : "Create a certificate for this TV")
      : "Install tizen-cert and add this TV";
    card.certCommand.textContent = tv.cert_tool === "installed" ? "Opens tizen-cert in a terminal; Samsung sign-in happens in your browser."
      : "Shows and runs the tizen-cert installer in a terminal, then opens tizen-cert. Samsung sign-in happens in your browser.";
    card.certButton.disabled = card.copyDuid.disabled = working || offline;
  }
  renderJob(card, tv);
  card.connected.hidden = !tv.held;
  if (tv.held) renderApps(card, tv);
  card.install.disabled = card.choose.disabled = working || offline;
  card.refreshPicture.hidden = card.save.hidden = !live;
  card.refreshPicture.disabled = card.save.disabled = busy || working || !live;
  card.screenText.hidden = live;
  card.screenText.textContent = previewError || (tv.pending === "preview" ? "Opening the app's screen…" : "Pick an app to see its screen.");
  card.previewReady = live;
  // Remembered per TV, and only once its picture actually showed.
  if (live && rememberedApp(tv.alias) !== p.app) {
    try { localStorage.setItem(previewKey(tv.alias), p.app); } catch { /* storage unavailable */ }
  }
  for (const key of card.keys) key.disabled = busy || !live || !p.input_allowed;
  card.hint.hidden = !live;
  card.why.textContent = offline ? "Connection lost. Keys are off until the page reconnects."
    : !tv.held ? "Connect to the TV, then open an app."
    : !live ? "Open an app's screen to use the remote." : busy ? "Sending…" : `Keys go to ${p.app}.`;
  card.age.textContent = live && p.captured_at ? `Picture from ${new Date(p.captured_at * 1000).toLocaleTimeString()} · the app's own drawing; video and the TV menu don't appear` : "";
  const rows = [["Network address", d.address], ["Device ID (DUID)", d.duid], ["Model", d.model || meta.model],
    ["OS", d.os], ["Firmware", d.firmware], ["Model year", meta.model_year?.value],
    ["Developer port", d.developer_port], ["Developer link", d.sdk_link],
    ["Certificate", c.profile ? `${c.profile} · covers ${(c.covers || []).join(", ") || "no TVs"}` : c.state === "no-profile" ? "None yet" : null]];
  card.list.replaceChildren(...rows.filter(([, v]) => v !== undefined && v !== null && v !== "")
    .flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, String(v))]));
  if (!live) { card.frames.replaceChildren(); card.revision = null; return; }
  for (let i = 0; i < p.frames.length; i++) keep.add(`${tv.alias}/${i}/${p.revision}`);
}

// ---- configured TVs/emulator with live view --------------------------------------
function sourceCard(id) {
  const name = el("h2"), kind = el("span", { class: "kind" }), pill = el("span", { class: "pill" });
  const reason = el("p", { class: "reason" });
  const connect = el("button", { type: "button", class: "primary" }, "Connect");
  const disconnect = el("button", { type: "button", class: "secondary" }, "Disconnect");
  const capture = el("button", { type: "button", class: "secondary" }, "Refresh picture");
  const stream = el("button", { type: "button", class: "secondary" }, "Start live view");
  const duration = el("input", { type: "number", min: 10, max: 120, value: 120, "aria-label": "Stay connected for (seconds)" });
  const interval = el("select", { "aria-label": "Live view rate" });
  for (const [v, t] of [["1", "Every second"], ["2", "Every 2 seconds"], ["5", "Every 5 seconds"]]) interval.add(new Option(t, v));
  interval.value = "2";
  const frames = el("div", { class: "frames" }), age = el("p", { class: "note" });
  const view = el("div", { class: "view", tabIndex: 0, "aria-label": "Picture. Arrow keys, Enter and Backspace act as the remote." }, age, frames);
  const picture = el("section", { class: "picture", "aria-label": "Screen" }, el("div", { class: "row" }, el("h3", {}, "Screen"), capture, stream, interval), view);
  const list = el("dl"), limits = el("p", { class: "note" });
  const meta = el("form", { class: "fields" });
  const inputs = {};
  for (const f of fields) {
    const input = el("input", { type: "text", maxLength: 100, "aria-label": fieldLabels[f] });
    const from = el("select", { "aria-label": `${fieldLabels[f]} source` });
    for (const [v, t] of [["user-provided", "I checked this"], ["unknown", "Not sure"]]) from.add(new Option(t, v));
    inputs[f] = { input, from };
    meta.append(el("label", {}, fieldLabels[f], input, from));
  }
  meta.append(el("div", { class: "row" }, el("button", { type: "submit", class: "secondary" }, "Save TV info")));
  const details = el("details", {}, el("summary", {}, "Details"), list,
    el("label", {}, "Stay connected for (seconds)", duration), meta, limits);
  const { remote, why, hint, keys } = remotePanel();
  const root = el("article", { class: "device", "aria-label": id },
    el("div", { class: "device-main" }, el("div", { class: "title-row" }, name, kind, pill),
      reason, el("div", { class: "row" }, connect, disconnect), details),
    picture, remote);
  const card = { root, name, kind, pill, reason, connect, disconnect, capture, stream, duration, interval, frames, age, view, list, limits, inputs, remote, why, hint, keys, filled: false, revision: null };
  const current = () => state.sources.find((s) => s.id === id);
  connect.addEventListener("click", () => {
    if (!current()) return;
    command(id, "connect", { authorized: true, pixels: true, input: true, run_seconds: Number(duration.value) });
  });
  disconnect.addEventListener("click", () => command(id, "disconnect", {}));
  capture.addEventListener("click", () => command(id, "capture", {}));
  stream.addEventListener("click", () => command(id, "stream", { enabled: !current()?.streaming, interval: Number(interval.value) }));
  for (const key of keys) key.addEventListener("click", () => command(id, "key", { key: key.dataset.key }));
  const keyboard = (event) => {
    if (!handledShortcut(event)) return;
    event.preventDefault(); // Suppress native Space/Enter, repeats and browser Back even while busy/disconnected.
    const s = current(), ready = s?.status === "connected" && s.expires_in > 0 && s.input_allowed;
    const key = shortcut(event, ready, busy);
    if (!key) return;
    command(id, "key", { key });
  };
  remote.addEventListener("keydown", keyboard);
  view.addEventListener("keydown", keyboard);
  meta.addEventListener("submit", (event) => {
    event.preventDefault(); const values = {};
    for (const f of fields) values[f] = { value: inputs[f].input.value, provenance: inputs[f].from.value };
    command(id, "metadata", values);
  });
  return card;
}
async function image(source, index) {
  const key = `${source.id}/${index}/${source.revision}`;
  let url = urls.get(key);
  if (!url) {
    const response = await fetch(`/api/frame/${key}`, { headers, signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("The picture changed while loading. Refresh it.");
    if (Number(response.headers.get("Content-Length")) > 8 * 1024 * 1024) throw new Error("Picture is too large.");
    const blob = await response.blob();
    if (blob.size > 8 * 1024 * 1024) throw new Error("Picture is too large.");
    url = URL.createObjectURL(blob); urls.set(key, url);
  }
  return url;
}
async function updateSource(card, s, keep) {
  card.name.textContent = sourceName(s);
  card.kind.textContent = s.kind === "emulator" ? "Emulator, not a real TV" : "";
  const ready = ["connected", "in-flight"].includes(s.status) && s.expires_in > 0 && !offline;
  card.pill.textContent = offline ? "Connection lost" : ready ? "Connected" : s.status === "in-flight" ? "Working…" : "Not connected";
  card.pill.className = `pill ${offline ? "lost" : ready ? "connected" : ""}`;
  card.reason.className = `reason${s.error ? " problem" : ""}`;
  card.reason.textContent = s.error || (ready ? `Disconnects automatically in ${s.expires_in}s.` : "Choose Connect to see the screen and use the remote.");
  card.connect.disabled = busy || ready || offline;
  card.disconnect.disabled = busy || !ready;
  card.capture.disabled = busy || !ready || !s.pixels_allowed;
  card.stream.disabled = !ready || !s.pixels_allowed || (busy && !s.streaming);
  card.stream.textContent = s.streaming ? "Pause live view" : "Start live view";
  card.duration.max = s.run_limit_seconds || 120;
  for (const key of card.keys) {
    key.disabled = busy || !ready || !s.input_allowed || (key.dataset.key === "ok" && !s.activation_allowed);
  }
  card.hint.textContent = ready && s.activation_allowed ? "Keyboard: arrows, Enter = OK, Backspace = Back" : "Keyboard: arrows, Backspace = Back";
  card.hint.hidden = !ready;
  card.why.textContent = offline ? "Connection lost. Keys are off until the page reconnects."
    : !ready ? "Choose Connect on this card to turn the remote on."
    : busy ? "Sending…" : "Ready.";
  if (!card.filled) {
    for (const f of fields) {
      card.inputs[f].input.value = s.metadata?.[f]?.value || "";
      card.inputs[f].from.value = s.metadata?.[f]?.provenance === "unknown" ? "unknown" : "user-provided";
    }
    card.filled = true;
  }
  const rows = [["Kind", s.kind === "emulator" ? "Emulator" : "TV"], ["Connection ID", s.id],
    ["What you see", s.provenance], ["Key round trip", s.command_ms == null ? "—" : `${s.command_ms} ms`],
    ["Picture time", s.capture_ms == null ? "—" : `${s.capture_ms} ms`],
    ...fields.map((f) => [fieldLabels[f], `${s.metadata?.[f]?.value || "unknown"} (${s.metadata?.[f]?.provenance || "unknown"})`])];
  card.list.replaceChildren(...rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, String(v ?? "—"))]));
  card.limits.textContent = s.limitations || "";
  card.age.textContent = s.captured_at ? `${s.stale ? "Out of date — " : ""}Picture from ${new Date(s.captured_at * 1000).toLocaleTimeString()}` : "";
  for (let i = 0; i < s.frames.length; i++) keep.add(`${s.id}/${i}/${s.revision}`);
  if (card.revision === s.revision && card.frames.childElementCount) return;
  if (!s.frames.length) {
    card.frames.replaceChildren(el("div", { class: "placeholder" }, "No picture yet. Connect, then choose Refresh picture. This page never shows a made-up picture."));
    card.revision = s.revision; return;
  }
  const figures = [];
  for (let i = 0; i < s.frames.length; i++) {
    const frame = s.frames[i];
    const img = el("img", { src: await image(s, i), alt: `${sourceName(s)} app screen, layer ${i + 1}; may be incomplete`, width: frame.width, height: frame.height });
    img.addEventListener("click", (event) => {
      const live = state.sources.find((x) => x.id === s.id);
      if (busy || !live?.input_allowed || live.stale || live.revision !== s.revision || !frame.clickable || !live.activation_allowed) return;
      const point = coordinate(img.getBoundingClientRect(), frame.width, frame.height, event.clientX, event.clientY);
      if (point) command(s.id, "click", { frame: i, revision: s.revision, ...point });
    });
    figures.push(el("figure", {}, img, el("figcaption", {}, `Layer ${i + 1} · ${frame.width} × ${frame.height}` +
      (frame.clickable && !s.stale && s.activation_allowed ? " · click safe buttons" : ""))));
  }
  card.frames.replaceChildren(...figures);
  card.revision = s.revision;
}

// ---- left rail: compact rows; choosing one only changes what this page shows -------
function select(key) {
  selected = key; render(); // Local only: no request, connection or key is sent.
  rows.get(key)?.button.focus();
}
function renderRail(wanted) {
  for (const key of [...rows.keys()]) if (!wanted.includes(key)) { rows.get(key).item.remove(); rows.delete(key); }
  const list = $("tv-list");
  wanted.forEach((key, index) => {
    if (!rows.has(key)) {
      const label = el("span", { class: "row-name" }), status = el("span", { class: "row-status" });
      const sub = el("span", { class: "row-meta" });
      const button = el("button", { type: "button", class: "tv-row", data: { key } }, label, status, sub);
      button.addEventListener("click", () => select(key));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
        event.preventDefault();
        const keys = [...rows.keys()], next = keys[keys.indexOf(key) + (event.key === "ArrowDown" ? 1 : -1)];
        if (next) select(next);
      });
      rows.set(key, { item: el("li", {}, button), button, label, status, sub });
    }
    const row = rows.get(key), card = cards.get(key);
    row.label.textContent = card.name.textContent;
    row.status.textContent = card.pill.textContent;
    row.sub.textContent = card.summary || "";
    row.status.className = `row-status ${card.pill.className.replace("pill", "").trim()}`;
    row.button.setAttribute("aria-current", key === selected ? "true" : "false");
    row.button.classList.toggle("selected", key === selected);
    if (list.children[index] !== row.item) list.insertBefore(row.item, list.children[index] || null);
  });
}

// ---- page ---------------------------------------------------------------------------
let rendering = false, renderAgain = false;
async function render() {
  if (rendering) { renderAgain = true; return; }
  rendering = true;
  try {
    const scanInfo = state.scan || {};
    const scanning = scanInfo.status === "scanning";
    $("scan").disabled = scanning || offline || state.sdk === "missing";
    $("scan").textContent = scanning ? "Scanning…" : "Scan network for TVs";
    $("scan-status").textContent = offline ? "Connection lost to this page's helper. Reopen the portal."
      : state.sdk === "missing" ? "Samsung's sdb tool was not found. Install Tizen Studio, then reopen."
      : scanning ? "Looking for TVs with Developer Mode on your network…"
      : scanInfo.status === "failed" ? `Scan failed: ${scanInfo.error || "unknown error"}`
      : scanInfo.status === "done" ? `Found ${scanInfo.found} TV${scanInfo.found === 1 ? "" : "s"}. Scanning only looks; it changes nothing.`
      : "Not scanned yet. Scanning only looks; it changes nothing on any TV.";
    const wanted = [], keep = new Set();
    for (const tv of state.tvs || []) {
      const key = `tv:${tv.alias}`;
      if (!cards.has(key)) cards.set(key, tvCard(tv.alias));
      await updateTv(cards.get(key), tv, keep); wanted.push(key);
    }
    for (const s of state.sources || []) {
      const key = `source:${s.id}`;
      if (!cards.has(key)) cards.set(key, sourceCard(s.id));
      await updateSource(cards.get(key), s, keep); wanted.push(key);
    }
    for (const key of [...cards.keys()]) if (!wanted.includes(key)) { cards.get(key).root.remove(); cards.delete(key); }
    if (!wanted.includes(selected)) selected = wanted[0] || null;
    const list = $("tvs");
    wanted.forEach((key, index) => { const node = cards.get(key).root; if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null); });
    for (const key of wanted) cards.get(key).root.hidden = key !== selected;
    renderRail(wanted);
    $("empty").hidden = wanted.length > 0;
    $("pick").hidden = Boolean(selected) || !wanted.length;
    for (const [key, url] of urls) if (!keep.has(key)) { URL.revokeObjectURL(url); urls.delete(key); }
  } catch (problem) { error(problem.message); }
  finally {
    rendering = false;
    if (renderAgain) { renderAgain = false; queueMicrotask(render); }
  }
}
let polling = false;
async function refresh() {
  if (polling || stopped) return;
  polling = true;
  try {
    state = { tvs: [], scan: { status: "never" }, sdk: "available", ...(await request("/api/state")) };
    offline = false;
  } catch (problem) {
    offline = true;
    error(`Connection lost: ${problem.message}. Controls are off; the last picture is kept and marked out of date.`);
    state.sources.forEach((s) => { s.stale = true; s.input_allowed = false; });
  } finally {
    polling = false;
  }
  await render();
}
$("scan").addEventListener("click", scan);
window.addEventListener("pagehide", () => {
  stopped = true; for (const url of urls.values()) URL.revokeObjectURL(url);
  // Intentional teardown only; no device input in unload.
  for (const source of state.sources) fetch(`/api/disconnect/${source.id}`, { method: "POST", headers,
    body: "{}", keepalive: true }).catch(() => {});
});
refresh(); setInterval(refresh, 1000);
