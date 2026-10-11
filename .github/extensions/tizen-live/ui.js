import { shortcut, handledShortcut, coordinate } from "/controls.mjs";

const $ = (id) => document.getElementById(id);
const headers = { "Content-Type": "application/json" };
const fields = ["model", "year", "os_version", "firmware"];
const fieldLabels = { model: "Model", year: "Year", os_version: "Tizen version", firmware: "Firmware" };
const urls = new Map(), cards = new Map(), confirmed = new Set();
let state = { sources: [], tvs: [], scan: { status: "never" }, sdk: "available" };
let busy = false, stopped = false, offline = false;

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
function ask(name, mustType) {
  const dialog = $("confirm");
  $("confirm-title").textContent = `Connect to ${name}?`;
  $("confirm-text").textContent = "Screen images may show account info; remote keys will be sent.";
  $("confirm-type").hidden = !mustType;
  $("confirm-type-text").textContent = mustType ? `Another session recently used a TV here. Type ${mustType} to continue.` : "";
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
  try { state = { ...state, ...(await request(`/api/tv/${op}/${alias}`, data)) }; }
  catch (problem) { error(problem.message); }
  render();
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
  const card = cards.get(`source:${id}`);
  if (operation === "key" && document.activeElement?.closest(".remote")) card?.remote.focus();
  if (operation === "click") card?.view.focus();
  busy = true; error(""); render();
  try {
    const completed = await request(`/api/${operation}/${id}`, data);
    const source = state.sources.find((s) => s.id === id);
    if (["key", "click"].includes(operation) && source?.pixels_allowed) {
      await refresh(); // Acknowledge measured input before requesting costly pixels.
      if (card) card.reason.textContent = `Key sent in ${completed.command_ms} ms. Updating the picture…`;
      await request(`/api/capture/${id}`, {});
    }
  } catch (problem) { error(`${problem.message}. Nothing was retried automatically.`); }
  finally { busy = false; await refresh(); }
}

// ---- remote ---------------------------------------------------------------------
function remotePanel() {
  const button = (key, label, text) => el("button", { type: "button", data: { key }, "aria-label": label }, text);
  const why = el("p", { class: "why" });
  const remote = el("div", { class: "remote", tabIndex: 0, role: "group", "aria-label": "Remote" },
    el("h3", {}, "Remote"),
    el("div", { class: "dpad" }, button("up", "Up", "▲"), button("left", "Left", "◀"), button("ok", "OK", "OK"),
      button("right", "Right", "▶"), button("down", "Down", "▼")),
    el("div", { class: "remote-row" }, button("back", "Back", "Back")),
    why, el("p", { class: "hint" }, "Keyboard: arrows, Enter = OK, Backspace = Back"));
  return { remote, why, keys: [...remote.querySelectorAll("[data-key]")] };
}

// ---- scanned TV cards -------------------------------------------------------------
function tvCard(alias) {
  const name = el("h2"), pill = el("span", { class: "pill" }), reason = el("p", { class: "reason" });
  const rename = el("button", { type: "button", class: "link", "aria-label": "Rename", title: "Rename" }, "✎ Rename");
  const connect = el("button", { type: "button", class: "primary" }, "Connect");
  const identify = el("button", { type: "button", class: "secondary" }, "Identify");
  const disconnect = el("button", { type: "button", class: "secondary" }, "Disconnect");
  const info = el("p", { class: "subtle" }), guide = el("div", { class: "guide", hidden: true });
  const list = el("dl"), job = el("p", { class: "subtle" });
  const app = el("input", { maxLength: 40, placeholder: "Package.AppName", "aria-label": "App ID", autocomplete: "off" });
  const pkg = el("input", { maxLength: 1024, placeholder: "/path/to/app.wgt (optional)", "aria-label": "Package file", autocomplete: "off" });
  const run = el("button", { type: "button", class: "secondary" }, "Install and run…");
  const details = el("details", {}, el("summary", {}, "Details"), list,
    el("div", { class: "fields" }, el("label", {}, "App ID", app), el("label", {}, "Package", pkg), el("div", { class: "row" }, run, job)));
  const { remote, why, keys } = remotePanel();
  const root = el("article", { class: "device", "aria-label": alias },
    el("div", { class: "device-main" },
      el("div", { class: "title-row" }, name, pill, rename),
      reason, el("div", { class: "row" }, connect, identify, disconnect), info, guide, details),
    remote);
  const card = { root, name, pill, reason, connect, identify, disconnect, info, guide, list, job, app, pkg, run, remote, why, keys };
  connect.addEventListener("click", async () => {
    const tv = state.tvs.find((t) => t.alias === alias); if (!tv) return;
    const answer = await ask(tv.name, tv.caution ? tv.alias : "");
    if (answer !== null) tvCommand("connect", alias, { confirm: answer });
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
function updateTv(card, tv) {
  card.name.textContent = tv.name;
  const working = Boolean(tv.pending);
  const label = working ? `${tv.pending[0].toUpperCase()}${tv.pending.slice(1)}ing…`.replace("eing", "ing")
    : { connected: "Connected", busy: "Busy", "not-connected": "Not connected" }[tv.status] || tv.status;
  card.pill.textContent = label;
  card.pill.className = `pill ${working ? "working" : tv.status === "connected" ? "connected" : tv.status === "busy" ? "busy" : ""}`;
  card.root.classList.toggle("busy", tv.status === "busy");
  const reason = tv.error || (tv.status === "busy" ? tv.busy_reason : tv.status === "not-connected" ? tv.not_connected_reason : "") || "";
  card.reason.textContent = reason;
  card.reason.className = `reason${tv.error ? " problem" : ""}`;
  const free = !working && tv.status !== "busy";
  card.connect.hidden = tv.status !== "not-connected";
  card.connect.textContent = tv.error ? "Retry" : "Connect";
  card.connect.disabled = !free || offline || state.sdk === "missing";
  card.identify.hidden = card.disconnect.hidden = tv.status !== "connected" || !tv.held;
  card.identify.disabled = card.disconnect.disabled = !free || offline;
  const parts = [];
  if (tv.model?.value) parts.push(tv.model.value);
  if (tv.year) parts.push(String(tv.year));
  if (tv.identity) parts.push(`Tizen ${tv.identity.tizen}`);
  parts.push(tv.reachable ? "Developer Mode reachable" : "Developer Mode not reachable");
  if (tv.certificate === "match") parts.push("Ready to install");
  card.info.textContent = parts.join(" · ");
  const guidance = tv.guidance;
  card.guide.hidden = !guidance;
  if (guidance) {
    const host = guidance.host_ip ? el("p", {}, "Host PC IP to enter on the TV: ", el("code", {}, guidance.host_ip)) : null;
    card.guide.replaceChildren(el("h3", {}, guidance.title), host,
      el("ol", {}, ...guidance.steps.map((step) => el("li", {}, step.replace("{host}", guidance.host_ip || "this computer's address")))));
  }
  const d = tv.details || {};
  const rows = [["Name source", tv.renamed ? "Your name" : tv.default_name === tv.name ? "From the TV" : "Default"],
    ["Network address", d.network], ["Developer port", d.developer_port], ["Link", d.sdk_link], ["Device ID", d.duid],
    ["Lock", d.lease], ["Certificate", tv.certificate], ["Model", tv.model ? `${tv.model.value || "unknown"} (${tv.model.provenance})` : null]];
  card.list.replaceChildren(...rows.filter(([, v]) => v !== undefined && v !== null && v !== "")
    .flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, String(v))]));
  card.run.disabled = tv.status !== "connected" || tv.certificate !== "match" || working || offline;
  card.job.textContent = tv.job ? `Last run: ${tv.job.status}${tv.job.message ? ` — ${tv.job.message}` : ""}` :
    tv.status === "connected" && tv.certificate !== "match" ? "Install is blocked until this TV is in your certificate." : "";
  for (const key of card.keys) key.disabled = true;
  card.why.textContent = tv.status === "busy" ? "Busy: another session is using this TV."
    : tv.status !== "connected" ? "Choose Connect to start. The remote turns on once an app is open for inspection."
    : "Connected. The remote turns on once an app on this TV is opened for inspection (tizen.py live).";
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
  const { remote, why, keys } = remotePanel();
  const root = el("article", { class: "device", "aria-label": id },
    el("div", { class: "device-main" }, el("div", { class: "title-row" }, name, kind, pill),
      reason, el("div", { class: "row" }, connect, disconnect), details),
    remote, picture);
  const card = { root, name, kind, pill, reason, connect, disconnect, capture, stream, duration, interval, frames, age, view, list, limits, inputs, remote, why, keys, filled: false, revision: null };
  const current = () => state.sources.find((s) => s.id === id);
  connect.addEventListener("click", async () => {
    const source = current(); if (!source) return;
    if (!confirmed.has(id)) {
      if ((await ask(sourceName(source), "")) === null) return;
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

// ---- page ---------------------------------------------------------------------------
let rendering = false;
async function render() {
  if (rendering) return;
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
      updateTv(cards.get(key), tv); wanted.push(key);
    }
    for (const s of state.sources || []) {
      const key = `source:${s.id}`;
      if (!cards.has(key)) cards.set(key, sourceCard(s.id));
      await updateSource(cards.get(key), s, keep); wanted.push(key);
    }
    for (const key of [...cards.keys()]) if (!wanted.includes(key)) { cards.get(key).root.remove(); cards.delete(key); }
    const list = $("tvs");
    wanted.forEach((key, index) => { const node = cards.get(key).root; if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null); });
    $("empty").hidden = wanted.length > 0;
    for (const [key, url] of urls) if (!keep.has(key)) { URL.revokeObjectURL(url); urls.delete(key); }
  } catch (problem) { error(problem.message); }
  finally { rendering = false; }
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
