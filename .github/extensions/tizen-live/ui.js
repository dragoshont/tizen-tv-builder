import { shortcut, handledShortcut, coordinate } from "/controls.mjs";

const $ = (id) => document.getElementById(id);
const headers = { "Content-Type": "application/json" };
const fields = ["model", "year", "os_version", "firmware"];
const fieldLabels = { model: "Model", year: "Year", os_version: "Tizen version", firmware: "Firmware" };
const urls = new Map(), cards = new Map(), confirmed = new Set();
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
function sourceName(source) {
  if (source.kind === "emulator") return "Emulator";
  const words = source.id.replace(/[-_]+/g, " ").trim();
  let name = words.charAt(0).toUpperCase() + words.slice(1);
  if (!/\btv\b/i.test(name)) name += " TV";
  const year = source.metadata?.year?.value;
  return year ? `${name} (${year})` : name;
}

// ---- dialogs ----------------------------------------------------------------
function ask(name, mustType, text, title = `Connect to ${name}?`, action = "Connect") {
  const dialog = $("confirm");
  $("confirm-title").textContent = title;
  $("confirm-ok").textContent = action;
  $("confirm-text").textContent = text;
  $("confirm-type").hidden = !mustType;
  $("confirm-type-text").textContent = mustType ? `Type ${mustType} to continue.` : "";
  $("confirm-alias").value = "";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      if (dialog.returnValue !== "ok") return resolve(null);
      const typed = $("confirm-alias").value.trim();
      resolve(mustType && typed !== mustType ? null : (mustType ? typed : ""));
    }, { once: true });
    dialog.returnValue = ""; dialog.showModal();
  });
}
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
// The one status a TV card, its list row and its explanation show. Nothing else may restate it.
// "Connected" needs a decoded, current app picture; a developer link alone is "No app open".
function tvStatus(tv, live, previewError) {
  const dev = tv.dev_mode || {};
  const problem = previewError || tv.error || "";
  const pending = { connect: "Connecting…", identify: "Checking…", disconnect: "Disconnecting…",
    run: "Starting app…", preview: "Opening app…" };
  if (tv.pending) return { label: pending[tv.pending] || "Working…", tone: "working", detail: "", next: "", problem: false };
  if (tv.missing && !tv.held) return { label: "Not found", tone: "", problem: false, next: "",
    detail: "Not found in the last scan. Turn the TV on, then scan again." };
  if (live) return { label: "Connected", tone: "connected", next: "", problem: Boolean(problem),
    detail: problem || `Showing ${tv.preview?.app || "the app"}: its own drawing, not the whole TV.` };
  // A held link that just failed is not a working connection: never "No app open" over an error.
  if (tv.held && tv.error) return { label: "Connection problem", tone: "lost", problem: true, detail: tv.error,
    next: tv.guidance?.title || (dev.next && dev.next !== "None" ? dev.next : "") };
  if (tv.held) return { label: "No app open", tone: "ready", next: "", problem: Boolean(problem),
    detail: problem || "Connected to the TV. Open an app to see its screen." };
  const blocked = {
    "not-enabled": ["Developer Mode off", "lost"],
    "needs-config": ["Needs setup", "lost"],
    closed: ["Can't connect", "lost"],
    unknown: ["Not reachable", ""],
  }[dev.state];
  if (blocked) return { label: blocked[0], tone: blocked[1], problem: Boolean(problem), canConnect: dev.state !== "not-enabled",
    detail: problem || (dev.state === "not-enabled" ? "" : dev.explanation || ""), next: dev.next && dev.next !== "None" ? dev.next : "" };
  return { label: problem ? "Couldn't connect" : "Ready to connect", tone: problem ? "lost" : "", problem: Boolean(problem), canConnect: true,
    detail: problem || "Developer Mode is on. Connect to use this TV.", next: "" };
}

function usablePreview(card, preview) {
  return card.previewReady && !offline && preview?.ready && !preview.stale &&
    preview.revision === card.revision && preview.frames?.length > 0;
}

// ---- remote ---------------------------------------------------------------------
function remotePanel() {
  const button = (key, label, text) => el("button", { type: "button", data: { key }, "aria-label": label }, text);
  const why = el("p", { class: "why" });
  const hint = el("p", { class: "hint" }, "Keyboard: arrows, Backspace = Back");
  const remote = el("div", { class: "remote", tabIndex: 0, role: "group", "aria-label": "Remote" },
    el("h3", {}, "Remote"),
    el("div", { class: "dpad" }, button("up", "Up", "▲"), button("left", "Left", "◀"), button("ok", "OK", "OK"),
      button("right", "Right", "▶"), button("down", "Down", "▼")),
    el("div", { class: "remote-row" }, button("back", "Back", "Back")),
    why, hint);
  return { remote, why, hint, keys: [...remote.querySelectorAll("[data-key]")] };
}

// ---- scanned TV cards -------------------------------------------------------------
function tvCard(alias) {
  const name = el("h2"), pill = el("span", { class: "pill" }), reason = el("p", { class: "reason" });
  const rename = el("button", { type: "button", class: "link", "aria-label": "Rename", title: "Rename" }, "✎ Rename");
  const connect = el("button", { type: "button", class: "primary" }, "Connect");
  const identify = el("button", { type: "button", class: "secondary" }, "Identify");
  const disconnect = el("button", { type: "button", class: "secondary" }, "Disconnect");
  const info = el("p", { class: "subtle" }), devLabel = el("strong"), devNext = el("span", { class: "subtle" });
  const dev = el("p", { class: "dev" }, devLabel, " ", devNext), install = el("p", { class: "subtle" }), guide = el("div", { class: "guide", hidden: true }), certGuide = el("div", { class: "guide", hidden: true });
  const list = el("dl"), job = el("p", { class: "subtle" });
  const app = el("input", { maxLength: 40, placeholder: "Package.AppName", "aria-label": "App ID", autocomplete: "off" });
  const pkg = el("input", { maxLength: 1024, placeholder: "/path/to/app.wgt (optional)", "aria-label": "Package file", autocomplete: "off" });
  const run = el("button", { type: "button", class: "secondary" }, "Install and run…");
  const details = el("details", {}, el("summary", {}, "Advanced"), list, install, certGuide,
    el("div", { class: "row" }, identify),
    el("div", { class: "fields" }, el("label", {}, "App ID", app), el("label", {}, "Package", pkg), el("div", { class: "row" }, run, job)));
  const { remote, why, hint, keys } = remotePanel();
  const screenText = el("p"), frames = el("div", { class: "frames" }), age = el("p", { class: "note" });
  const previewApp = el("input", { maxLength: 40, placeholder: "Package.AppName", "aria-label": "App to preview",
    autocomplete: "off", spellcheck: "false", value: rememberedApp(alias) });
  const open = el("button", { type: "button", class: "primary" }, "Open app for preview");
  const refreshPicture = el("button", { type: "button", class: "secondary", hidden: true }, "Refresh picture");
  const opener = el("div", { class: "row" }, previewApp, open);
  const screen = el("section", { class: "picture", "aria-label": "Screen" },
    el("div", { class: "row" }, el("h3", {}, "Screen"), refreshPicture),
    el("div", { class: "placeholder" }, screenText, opener), age, frames);
  const root = el("article", { class: "device", "aria-label": alias },
    el("div", { class: "device-main" },
      el("div", { class: "title-row" }, name, pill, rename),
      info, dev, reason, el("div", { class: "row" }, connect, disconnect), guide, details),
    screen, remote);
  const card = { root, name, pill, reason, connect, identify, disconnect, info, guide, certGuide, list, job, app, pkg, run, remote, why, hint, keys, screenText, devLabel, devNext, install, summary: "",
    previewApp, open, opener, refreshPicture, frames, age, revision: null };
  open.addEventListener("click", async () => {
    const tv = state.tvs.find((t) => t.alias === alias), appId = previewApp.value.trim(); if (!tv) return;
    if (!appId) { error("Enter the app ID to preview, for example Package.AppName."); previewApp.focus(); return; }
    const answer = await ask(tv.name, "", `This restarts ${appId} on the TV. Only the app's own drawing is shown, not the whole TV screen or video.`,
      `Open ${appId} for preview?`, "Restart and preview");
    if (answer === null) return;
    const opened = await tvCommand("preview", alias, { app: appId, restart: true });
    const tvNow = state.tvs.find((t) => t.alias === alias);
    if (opened && tvNow && tvNow.preview && tvNow.preview.ready) {
      try { localStorage.setItem(previewKey(alias), appId); } catch { /* storage unavailable */ }
    }
  });
  refreshPicture.addEventListener("click", () => command(alias, "capture", {}));
  for (const key of keys) key.addEventListener("click", () => {
    if (usablePreview(card, sourceById(alias))) command(alias, "key", { key: key.dataset.key });
  });
  remote.addEventListener("keydown", (event) => {
    if (!handledShortcut(event)) return;
    event.preventDefault();
    const p = sourceById(alias), ready = usablePreview(card, p) && p.input_allowed;
    const key = shortcut(event, ready, busy);
    if (!key) return;
    if (key === "ok" && !p.activation_allowed) { error("OK is limited to safe, audited buttons on this app."); return; }
    command(alias, "key", { key });
  });
  connect.addEventListener("click", async () => {
    const tv = state.tvs.find((t) => t.alias === alias); if (!tv) return;
    // Connect needs no dialog: it reserves nothing and launches no app.
    tvCommand("connect", alias, { confirm: "" });
  });
  identify.addEventListener("click", () => tvCommand("identify", alias));
  disconnect.addEventListener("click", () => tvCommand("disconnect", alias));
  rename.addEventListener("click", async () => {
    const tv = state.tvs.find((t) => t.alias === alias); if (!tv) return;
    const value = await renamePrompt(tv.renamed ? tv.name : "");
    if (value !== null) tvCommand("rename", alias, { name: value });
  });
  run.addEventListener("click", () => {
    const typed = prompt(`Installing changes this TV. Type ${alias} to confirm.`);
    if (typed !== null) tvCommand("run", alias, { confirm: typed.trim(), app: app.value.trim(), package: pkg.value.trim() });
  });
  return card;
}
async function updateTv(card, tv, keep) {
  // Fail closed: "Connected" needs a usable picture here, whatever the helper reports.
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
          alt: `${tv.name} app screen, layer ${i + 1}; may be incomplete`, width: frame.width, height: frame.height });
        await img.decode();
        figures.push(el("figure", {}, img));
      }
      const current = state.tvs.find((item) => item.alias === tv.alias);
      live = !offline && current?.status === "connected" && current.preview?.ready &&
        !current.preview.stale && current.preview.revision === p.revision && current.preview.frames?.length > 0;
      if (live) {
        card.frames.replaceChildren(...figures);
        card.revision = p.revision;
      }
    } catch (problem) {
      live = false;
      previewError = `Could not display the app preview: ${problem.message}. Open the app for preview again to retry.`;
    }
  }
  const status = tv.status === "connected" && !live ? (tv.held ? "developer" : "not-connected") : tv.status;
  card.name.textContent = tv.name;
  const working = Boolean(tv.pending);
  // One status per TV: the pill, the list row and the explanation all come from tvStatus().
  const shown = tvStatus(tv, live, previewError);
  card.pill.textContent = shown.label;
  card.pill.className = `pill ${shown.tone}`;
  card.reason.textContent = shown.detail;
  card.reason.className = `reason${shown.problem ? " problem" : ""}`;
  const free = !working;
  card.connect.hidden = status !== "not-connected" || !shown.canConnect;
  card.connect.textContent = tv.error ? "Retry" : "Connect";
  card.connect.disabled = !free || offline || state.sdk === "missing";
  card.identify.hidden = card.disconnect.hidden = !tv.held;
  card.identify.disabled = card.disconnect.disabled = !free || offline;
  const meta = tv.metadata || {}, year = meta.model_year || {}, devMode = tv.dev_mode || {};
  const yearText = year.value ? `Model year ${year.value}` : "Model year unknown";
  card.summary = [meta.model && tv.name.includes(meta.model) ? "" : meta.model || "Model unknown",
    year.value ? `Model year ${year.value}` : "Model year unknown"].filter(Boolean).join(" · ");
  const inName = meta.model && tv.name.includes(meta.model);
  card.info.textContent = [inName ? "" : meta.model || "Model unknown", yearText, meta.os?.text || "OS unknown"]
    .filter(Boolean).join(" · ");
  // The next step is shown only when the TV is not usable yet; it never restates the status.
  card.devLabel.textContent = "";
  card.devNext.textContent = shown.next ? `Next: ${shown.next}` : "";
  card.devNext.parentElement.hidden = !shown.next;
  card.install.textContent = tv.install || "";
  card.install.hidden = !tv.install;
  const guidance = tv.guidance;
  // Certificate/install guidance is optional machinery: it lives under Advanced, never in the main pane.
  const target = guidance && guidance.kind === "certificate" ? card.certGuide : card.guide;
  card.guide.hidden = card.certGuide.hidden = true;
  if (guidance) {
    target.hidden = false;
    const host = guidance.host_ip ? el("p", {}, "Host PC IP to enter on the TV: ", el("code", {}, guidance.host_ip)) : null;
    target.replaceChildren(...[el("h3", {}, guidance.title), host,
      el("ol", {}, ...guidance.steps.map((step) => el("li", {}, step.replace("{host}", guidance.host_ip || "this computer's address"))))].filter(Boolean));
  }
  const d = tv.details || {};
  const rows = [["Name source", tv.renamed ? "Your name" : tv.default_name === tv.name ? "From the TV" : "Default"],
    ["Network address", d.network], ["Developer port", d.developer_port], ["Link", d.sdk_link], ["Device ID", d.duid],
    ["Lock", d.lease], ["Certificate", tv.certificate],
    ["Manufacturer", meta.manufacturer], ["Model", meta.model], ["Family", meta.family],
    ["Model year", year.value ? `${year.value} (${year.provenance})` : "Unknown"],
    ["Manufacture date", "Unknown (model year is not a manufacture date)"],
    ["OS", meta.os ? `${meta.os.text} (${meta.os.provenance})` : null], ["Platform baseline", meta.baseline],
    ["Resolution", meta.resolution], ["The Frame", meta.frame_tv ? "Yes" : null], ["Firmware", meta.firmware],
    ["Developer Mode", devMode.explanation]];
  card.list.replaceChildren(...rows.filter(([, v]) => v !== undefined && v !== null && v !== "")
    .flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, String(v))]));
  card.run.disabled = !tv.held || tv.certificate !== "match" || working || offline;
  card.job.textContent = tv.job ? `Last run: ${tv.job.status}${tv.job.message ? ` — ${tv.job.message}` : ""}` :
    tv.held && tv.certificate !== "match" ? "Install is blocked until this TV is in your certificate." : "";
  card.opener.hidden = live || !tv.held;
  card.open.disabled = working || offline;
  card.refreshPicture.hidden = !live;
  card.refreshPicture.disabled = busy || !live;
  card.screenText.textContent = offline ? "Connection lost to this page's helper. No picture is shown; reopen the portal to retry."
    : previewError ? previewError
    : status === "not-connected" ? "Connect to this TV first."
    : live ? "" : "No app on screen. Open an app to see it here. Only the app's own drawing is shown, not the whole TV or video.";
  card.screenText.parentElement.hidden = live;
  card.previewReady = live;
  for (const key of card.keys) key.disabled = busy || !live || !p.input_allowed || (key.dataset.key === "ok" && !p.activation_allowed);
  card.hint.textContent = live && p.activation_allowed ? "Keyboard: arrows, Enter = OK, Backspace = Back" : "Keyboard: arrows, Backspace = Back";
  card.why.textContent = !live ? "The remote turns on once an app is open for preview."
    : !p.activation_allowed ? "Arrows and Back go to the app. OK is off: this app has no audited safe buttons." : busy ? "Sending…" : "Ready.";
  card.age.textContent = live && p.captured_at ? `Picture from ${new Date(p.captured_at * 1000).toLocaleTimeString()} · the app's own drawing, not the whole TV` : "";
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
  connect.addEventListener("click", async () => {
    const source = current(); if (!source) return;
    if (!confirmed.has(id)) {
      if ((await ask(sourceName(source), "", "Shows this app's own drawing (it may include account info) and sends remote keys to the app.")) === null) return;
      confirmed.add(id);
    }
    // One confirmation grants the session, the picture and remote keys for this run.
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
    if (key === "ok" && !s.activation_allowed) { error("OK is limited to safe, audited buttons on this app."); return; }
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
  card.why.textContent = offline ? "Connection lost. Keys are off until the page reconnects."
    : !ready ? "Choose Connect on this card to turn the remote on."
    : !s.activation_allowed ? "OK is off: this app has no audited safe buttons." : busy ? "Sending…" : "Ready.";
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
