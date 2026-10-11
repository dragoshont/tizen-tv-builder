import { handledShortcut, shortcut } from "/controls.mjs";

const $ = (id) => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem("workbench-token") || "";
if (token) sessionStorage.setItem("workbench-token", token);
history.replaceState(null, "", location.pathname);
const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
let source = null, busy = false, polling = false, stopped = false, setupState = {};
let revision = null;
let recording = null, encoders = {};
let recordingSaved = false;
const urls = [];
function error(message = "") { $("error").textContent = message; $("error").hidden = !message; }
async function request(path, data) {
  const response = await fetch(path, { method: data === undefined ? "GET" : "POST", headers,
    body: data === undefined ? undefined : JSON.stringify(data),
    signal: AbortSignal.timeout(path === "/api/record" ? 90000 : 30000) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || "Local operation failed.");
  return result;
}
function ready() { return source?.status === "connected" && source.expires_in > 0; }
function controls() {
  const connected = ready(), session = source && ["connected", "in-flight"].includes(source.status) && source.expires_in > 0;
  for (const id of ["discover", "connect-tv", "debug"]) $(id).disabled = busy || session || !setupState.sdb || !$("sdk-grant").checked;
  for (const id of ["find-network", "confirm-tv"]) $(id).disabled = busy || session || !$("sdk-grant").checked;
  $("attach").disabled = busy || session || !$("attach-grant").checked || setupState.forward_owned;
  $("select-page").disabled = busy || session || !setupState.page_count;
  $("release").disabled = busy || session || !setupState.forward_owned || !$("sdk-grant").checked;
  $("connect").disabled = busy || session || !source || !$("authorized").checked;
  $("disconnect").disabled = busy || !session;
  $("capture").disabled = busy || !connected || !source.pixels_allowed;
  $("stream").disabled = !session || !source.pixels_allowed || (busy && !source.streaming);
  $("stream").textContent = source?.streaming ? "Pause feed" : "Start feed";
  const capturing = recording?.state === "recording";
  const pendingExport = recording?.state === "stopped" && recording.frames > 0 && !recordingSaved;
  $("record").textContent = capturing ? "Stop recording" : pendingExport ? "Export recording" : "Record";
  $("record").disabled = busy || (!capturing && !pendingExport && (!connected || !source.pixels_allowed));
  if (capturing) { $("capture").disabled = true; $("stream").disabled = true; }
  for (const id of ["export", "still"]) $(id).disabled = busy || !recording?.frames || capturing || recording?.state === "failed";
  document.querySelectorAll("[data-key]").forEach((button) => {
    const key = button.dataset.key, basic = ["up", "down", "left", "right", "ok", "back"].includes(key);
    const supported = basic || source?.remote_keys?.includes(key);
    const granted = basic || source?.extended_input;
    button.disabled = busy || !connected || !source.input_allowed || !supported || !granted || (key === "ok" && !source.activation_allowed);
    button.title = !supported ? "Not exposed to this app" : !granted ? "Enable app actions" : key === "ok" && !source?.activation_allowed ? "Add audited safe OK targets" : !connected ? "Connect preview first" : `Send ${key} to app`;
  });
  $("connection").textContent = session ? "Connected" : source?.status === "error" ? "Connection lost" : source?.status === "expired" ? "Session ended" : "Not connected";
  $("connection").dataset.ready = String(Boolean(session));
}
function setupView(data) {
  setupState = data;
  $("tools").textContent = `SDB ${data.sdb ? "found" : "not found"} · Web CLI ${data.tizen ? "found" : "not found"} · ${data.platform}`;
  $("setup-message").textContent = data.message || (data.forward_owned && !data.page_count ? "Forward ready; page discovery incomplete. Release it in Advanced." : "");
  const previousTV = $("tv").value, previousApp = $("installed-app").value;
  $("tv").replaceChildren();
  for (const [i, serial] of (data.devices || []).entries()) $("tv").add(new Option(`Connected TV ${i + 1}`, serial));
  for (const tv of data.network_devices || []) {
    const serial = `${tv.ip}:26101`;
    if (!(data.devices || []).includes(serial)) $("tv").add(new Option(`${tv.name} · ${tv.model} · ${tv.year} · Dev ${tv.developer_mode}`, serial));
  }
  $("tv").disabled = !$("tv").options.length;
  if ([...$("tv").options].some((o) => o.value === previousTV)) $("tv").value = previousTV;
  if (data.serial) $("tv").value = data.serial;
  $("installed-app").replaceChildren();
  for (const app of data.apps || []) $("installed-app").add(new Option(app.split(".").slice(1).join("."), app));
  $("installed-app").disabled = !data.apps?.length;
  if ((data.apps || []).includes(previousApp)) $("installed-app").value = previousApp;
  $("page").disabled = !data.page_count;
  $("page").replaceChildren();
  if (!data.page_count) $("page").add(new Option("No pages discovered", ""));
  for (let i = 0; i < data.page_count; i++) $("page").add(new Option(`Page ${i + 1}`, String(i)));
  controls();
}
async function setup(operation, values) {
  if (busy) return;
  busy = true; error(); controls();
  $("status").textContent = "Setup in progress. Do not repeat this operation.";
  try {
    setupView(await request("/api/setup", { operation, ...values }));
    if (operation === "debug" && setupState.page_count === 1) {
      setupView(await request("/api/setup", { operation: "select", page: 0,
        safe_ids: $("safe-ids").value.split(",").map((id) => id.trim()).filter(Boolean) }));
    }
    source = null;
    revision = null;
    discardImages();
    $("views").textContent = "No current native capture. Select and connect a page, then Refresh.";
    await refresh();
    $("status").textContent = ["select", "debug"].includes(operation) && setupState.configured ? "App ready. Connect preview." : "Ready for the next step.";
  } catch (problem) { error(`${problem.message} No automatic retry. Check setup or Device Manager privately.`); }
  finally {
    try { setupView(await request("/api/setup")); }
    catch (problem) { error(`Setup status unavailable: ${problem.message}. Reconcile device state privately.`); }
    busy = false; controls();
  }
}
async function command(operation, data) {
  const pause = operation === "stream" && data.enabled === false;
  if (busy && !pause) return;
  if (operation === "key") $("remote").focus();
  if (!pause) busy = true;
  error(); controls();
  $("status").textContent = `${operation === "key" ? "Input" : "Preview operation"} in flight...`;
  try { await request(`/api/${operation}/${source.id}`, data); }
  catch (problem) { error(`${problem.message} No automatic retry was sent.`); }
  finally { if (!pause) busy = false; await refresh(); controls(); }
}
async function recordCommand(operation, extra = {}) {
  if (busy) return;
  if (operation === "start" && !$("save-grant").checked) {
    $("record-status").closest("details").open = true;
    $("save-grant").focus(); error("Authorize saving private captures, then press Record.");
    return;
  }
  busy = true; error(); controls();
  try {
    if (operation === "start" && source?.streaming) {
      await request(`/api/stream/${source.id}`, { enabled: false, interval: Number($("interval").value) });
    }
    const result = await request("/api/record", { operation, ...extra });
    recording = result;
    if (operation === "start") recordingSaved = false;
    if (result.state === "failed") error(result.error || "Recording failed. Partial frames may remain in its private folder.");
    if (result.output_path) {
      recordingSaved = true;
      $("record-receipt").textContent = `Saved in ${result.output_path}`;
    }
  } catch (problem) { error(`${problem.message}. Captured PNG frames remain available when admitted; no automatic retry.`); }
  finally { busy = false; await refresh(); controls(); }
}
function discardImages() {
  for (const url of urls) URL.revokeObjectURL(url);
  urls.length = 0;
}
async function renderFrames() {
  if (!source || source.stale || !source.frames.length) {
    if (revision !== null) {
      discardImages(); revision = null;
      $("views").replaceChildren();
      const empty = document.createElement("div"); empty.className = "empty";
      empty.textContent = "Connect preview, then Refresh.";
      $("views").append(empty);
    }
    return;
  }
  if (source.revision === revision) return;
  const next = document.createDocumentFragment(), created = [];
  try {
    for (let i = 0; i < source.frames.length; i++) {
      const frame = source.frames[i], response = await fetch(`/api/frame/${source.id}/${i}/${source.revision}`, { headers, signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error("Capture changed while loading. Refresh the frame.");
      const blob = await response.blob();
      if (blob.size > 8 * 1024 * 1024) throw new Error("Frame exceeds its limit.");
      const url = URL.createObjectURL(blob); created.push(url);
      const figure = document.createElement("figure"), image = document.createElement("img"), caption = document.createElement("figcaption");
      image.src = url; image.width = frame.width; image.height = frame.height;
      image.alt = `Native renderer paint layer ${i + 1}; not a complete panel capture`;
      caption.textContent = `App layer ${i + 1} · ${frame.width} × ${frame.height}`;
      caption.title = "Individual renderer paint layer; not composited. AVPlay video is not captured.";
      figure.append(image, caption); next.append(figure);
    }
    discardImages(); urls.push(...created); $("views").replaceChildren(next); revision = source.revision;
  } catch (problem) {
    for (const url of created) URL.revokeObjectURL(url);
    discardImages(); revision = null; $("views").textContent = "Capture unavailable. Refresh to retry.";
    throw problem;
  }
}
async function refresh() {
  if (polling || stopped) return;
  polling = true;
  try {
    const state = await request("/api/state");
    source = state.sources[0] || null;
    const info = await request("/api/record");
    recording = info.recording || null; encoders = info.encoder || {};
    if (!$("record-output").value && info.default_parent) $("record-output").value = info.default_parent;
    for (const option of $("record-format").options) option.disabled = !encoders[option.value];
    if (!encoders[$("record-format").value]) $("record-format").value = "png";
    $("encoder-status").textContent = encoders.setup || "ffmpeg found. Available formats are listed above.";
    $("record-status").textContent = recording ? `${recording.state} · ${recording.frames} frames · ${recording.length}s` : "No recording yet.";
    controls();
    if (!busy && source) $("status").textContent = `${ready() ? "Connected" : source.status} · ${source.expires_in}s remaining. ${source.error || ""}`;
    $("diagnostics").textContent = source ? `Input RTT: ${source.command_ms ?? "not measured"} ms. Capture: ${source.capture_ms ?? "not measured"} ms. ${source.provenance}. ${source.cleanup || ""}` : "No inspector session.";
    await renderFrames();
    if (source?.captured_at && !source.stale) $("status").textContent += ` Last native capture ${new Date(source.captured_at * 1000).toLocaleTimeString()}.`;
  } catch (problem) {
    source = null; discardImages(); revision = null;
    $("views").textContent = "Preview unavailable. Reconnect to refresh.";
    error(`${problem.message} Controls disabled. Reconnect only after checking the local service and device lane.`);
    controls();
  } finally { polling = false; }
}
for (const id of ["sdk-grant", "attach-grant", "authorized"]) $(id).addEventListener("change", controls);
$("connect-tv").addEventListener("click", () => setup("connect-tv", { serial: $("serial").value.trim(), authorized: $("sdk-grant").checked }));
$("discover").addEventListener("click", () => setup("discover", { serial: $("tv").value || $("serial").value.trim(), authorized: $("sdk-grant").checked }));
$("find-network").addEventListener("click", () => setup("find-network", { authorized: $("sdk-grant").checked }));
$("confirm-tv").addEventListener("click", () => setup("confirm-tv", { ip: $("manual-ip").value.trim(), authorized: $("sdk-grant").checked }));
$("tv").addEventListener("change", () => {
  if ((setupState.devices || []).includes($("tv").value)) setup("discover", { serial: $("tv").value, authorized: $("sdk-grant").checked });
  else {
    $("serial").value = $("tv").value;
    $("setup-message").textContent = "Enable Developer Mode for this computer, then Connect TV in Advanced.";
    $("onboarding").open = true;
  }
});
$("debug").addEventListener("click", () => setup("debug", { serial: $("tv").value || $("serial").value.trim(), authorized: $("sdk-grant").checked, app: $("installed-app").value || $("app-id").value.trim(), legacy: $("legacy").checked }));
$("attach").addEventListener("click", () => setup("attach", { port: Number($("port").value), authorized: $("attach-grant").checked }));
$("release").addEventListener("click", () => setup("release", { authorized: $("sdk-grant").checked }));
$("select-page").addEventListener("click", () => setup("select", { page: Number($("page").value), safe_ids: $("safe-ids").value.split(",").map((id) => id.trim()).filter(Boolean) }));
$("connect").addEventListener("click", () => command("connect", { authorized: $("authorized").checked, pixels: $("pixels").checked, input: $("input").checked, extended_input: $("extended-input").checked, run_seconds: Number($("duration").value) }));
$("disconnect").addEventListener("click", () => command("disconnect", {}));
$("capture").addEventListener("click", () => command("capture", {}));
$("stream").addEventListener("click", () => command("stream", { enabled: !source.streaming, interval: Number($("interval").value) }));
$("record").addEventListener("click", () => {
  if (recording?.state === "recording" || (recording?.state === "stopped" && recording.frames > 0 && !recordingSaved)) recordCommand("stop", { format: $("record-format").value });
  else recordCommand("start", { authorized_save: $("save-grant").checked, output_parent: $("record-output").value,
    fps: Number($("record-fps").value), width: Number($("record-width").value), height: Number($("record-height").value),
    duration: Number($("record-duration").value), layer: Number($("record-layer").value) });
});
$("export").addEventListener("click", () => recordCommand("export", { format: $("record-format").value, still: $("record-format").value === "webp" }));
$("still").addEventListener("click", () => recordCommand("export", { format: "png", still: true }));
document.querySelectorAll("[data-key]").forEach((button) => button.addEventListener("click", () => {
  $("remote").focus(); command("key", { key: button.dataset.key });
}));
function keyboard(event) {
  if (!handledShortcut(event)) return;
  event.preventDefault();
  const key = shortcut(event, ready() && source.input_allowed, busy);
  if (!key) return;
  if (key === "ok" && !source.activation_allowed) { error("OK requires audited non-write control IDs."); return; }
  command("key", { key });
}
$("remote").addEventListener("keydown", keyboard);
$("views").addEventListener("keydown", keyboard);
window.addEventListener("pagehide", () => {
  stopped = true; discardImages();
  if (source) fetch(`/api/disconnect/${source.id}`, { method: "POST", headers, body: "{}", keepalive: true })
    .catch(() => { /* The server's bounded lease is the teardown backstop. */ });
});
try { setupView(await request("/api/setup")); await refresh(); }
catch (problem) { error(`Workbench setup unavailable: ${problem.message}`); controls(); }
setInterval(refresh, 1000);
