import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_canvas", ROOT / "tools/install_canvas.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class CanvasInstallTests(unittest.TestCase):
    def test_install_complete_payload_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "tizen-live"
            result = installer.install(destination)
            self.assertEqual(result["installed"], "tizen-live")
            for name in ("extension.mjs", "index.html", "ui.js", "ui.css", "controls.mjs",
                         "backend/live_tv.py", "backend/tizen.py", "plugin.json"):
                self.assertTrue((destination / name).is_file())
            self.assertEqual(json.loads((destination / "plugin.json").read_text())["name"], "tizen-tv-builder")
            with self.assertRaisesRegex(ValueError, "never overwrite"):
                installer.install(destination)

    def test_relative_and_repository_destination_refused(self):
        for path in ("relative-extension", ROOT / "new-extension"):
            with self.assertRaises(ValueError):
                installer.install(path)

    def test_canonical_workflow_and_packaged_adapter_paths(self):
        manifest = json.loads((ROOT / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "tizen-tv-builder")
        skill = (ROOT / "skills/tizen-tv-builder/SKILL.md").read_text()
        agent = (ROOT / "com.github.copilot/agents/tizen-tv-builder.agent.md").read_text()
        for text in (skill, agent):
            self.assertIn("tizen-live", text)
            self.assertIn("status", text)
            self.assertIn("host-only", text)
        self.assertIn("cookie", skill)
        self.assertIn("user-provided/observed/unknown", skill)
        self.assertIn("docs/live-canvas.md", skill)
        spec = importlib.util.spec_from_file_location("canvas_release", ROOT / "tools/release.py")
        release = importlib.util.module_from_spec(spec); spec.loader.exec_module(release)
        names = release.ROOT_FILES | {
            "skills/tizen-tv-builder/SKILL.md", "com.github.copilot/agents/tizen-tv-builder.agent.md",
            "tools/tizen.py", "tools/release.py", "tools/live_tv.py", "tools/install_canvas.py",
        } | release.CANVAS_FILES
        packaged = release.payload_paths(names)
        self.assertTrue(release.CANVAS_FILES.issubset(packaged))
        self.assertIn("tools/install_canvas.py", packaged)
