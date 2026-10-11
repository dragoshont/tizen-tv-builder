export const shortcuts = Object.freeze({
  ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
  " ": "ok", Enter: "ok", Backspace: "back",
});
export function handledShortcut(event) {
  if (event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return null;
  const target = event.target;
  if (!target?.closest?.(".remote,.view") || target.closest("input,textarea,select,[contenteditable]:not([contenteditable=false])")) return null;
  return shortcuts[event.key] || null;
}
export function shortcut(event, ready, busy) {
  return ready && !busy && !event.repeat ? handledShortcut(event) : null;
}
export function coordinate(rect, width, height, x, y) {
  if (!rect.width || !rect.height || x < rect.left || y < rect.top ||
      x >= rect.left + rect.width || y >= rect.top + rect.height) return null;
  return { x: Math.floor((x - rect.left) * width / rect.width),
    y: Math.floor((y - rect.top) * height / rect.height) };
}
