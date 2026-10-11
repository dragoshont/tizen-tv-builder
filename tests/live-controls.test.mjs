import assert from "node:assert/strict";
import { shortcuts, shortcut, coordinate } from "../.github/extensions/tizen-live/controls.mjs";
const remote = { closest: (selector) => selector === ".remote,.view" ? {} : null };
for (const [key, action] of Object.entries(shortcuts)) assert.equal(shortcut({ key, target: remote }, true, false), action);
for (const flag of ["repeat", "isComposing", "ctrlKey", "altKey", "metaKey", "shiftKey"])
  assert.equal(shortcut({ key: "Enter", target: remote, [flag]: true }, true, false), null);
assert.equal(shortcut({ key: "Enter", target: remote }, false, false), null);
assert.equal(shortcut({ key: "Enter", target: remote }, true, true), null);
assert.equal(shortcut({ key: "Enter", target: { closest: () => ({}) } }, true, false), null);
assert.equal(shortcut({ key: "Enter", target: { closest: () => null } }, true, false), null);
assert.deepEqual(coordinate({ left: 10, top: 20, width: 500, height: 250 }, 1920, 1080, 260, 145), { x: 960, y: 540 });
assert.equal(coordinate({ left: 10, top: 20, width: 500, height: 250 }, 1920, 1080, 510, 20), null);
console.error("Live shortcut, focus, modifier, repeat, backpressure and coordinate tests passed.");
