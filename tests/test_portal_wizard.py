"""The portal's "Set up a new TV" wizard: the web face of tv_setup.evaluate. Static checks; no TV or browser."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANVAS = ROOT / ".github" / "extensions" / "tizen-live"


def read(path):
    return path.read_text(encoding="utf-8")


class PortalWizard(unittest.TestCase):
    def setUp(self):
        self.script = read(CANVAS / "ui.js")
        self.page = read(CANVAS / "index.html")
        start = self.script.index("// ---- setup wizard")
        self.wizard = self.script[start:self.script.index("// ---- page ----")]

    def test_page_has_the_wizard_and_a_way_back_to_it(self):
        self.assertIn('id="setup-tv"', self.page)
        self.assertIn(">Set up a new TV</button>", self.page)
        self.assertIn('<section id="wizard" class="wizard" aria-labelledby="wizard-title" hidden>', self.page)

    def test_every_action_the_guide_offers_has_a_handler(self):
        offered = set(re.findall(r'"action": "([a-z-]+)"', read(ROOT / "tools" / "tv_setup.py")))
        self.assertEqual(offered, {"install-sdk", "scan", "connect", "cert", "profile", "choose", "sample", "preview"})
        handlers = self.wizard[self.wizard.index("const wizardActions = {"):]
        handlers = handlers[:handlers.index("};")]
        for action in offered:
            self.assertRegex(handlers, rf'(^|\s|"){re.escape(action)}"?: ', action)

    def test_it_reads_the_server_guide_and_starts_only_the_offered_operations(self):
        self.assertIn('request("/api/wizard", wizard.alias ? { tv: wizard.alias } : {})', self.wizard)
        self.assertIn('request("/api/install-sdk", {})', self.wizard)
        self.assertIn('tvCommand("sample", alias, {})', self.wizard)
        self.assertIn('request("/api/choose-package", {})', self.wizard)

    def test_it_opens_by_itself_only_when_no_tv_is_ready(self):
        self.assertIn("const tvReady = (tv) => tv.held || tv.reachable;", self.wizard)
        self.assertIn("!(state.tvs || []).some(tvReady)", self.wizard)
        # Not before the first scan finished: remembered TVs are only "Checking…" then.
        self.assertIn('["done", "failed"].includes(state.scan?.status)', self.wizard)
        self.assertIn('sessionStorage.setItem("tizen-wizard-closed", "1")', self.wizard)

    def test_waiting_for_the_tv_checks_again_on_its_own(self):
        self.assertIn('step.id === "developer-mode"', self.wizard)
        self.assertIn("WIZARD_SCAN_MS", self.wizard)

    def test_steps_show_real_errors_output_and_the_command_by_hand(self):
        for text in ("step.error", "step.output", "step.command", "Or do it by hand:", "step.status_line",
                     'aria-current", "step"'):
            self.assertIn(text, self.wizard)

    def test_ui_parses_as_a_module(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        with tempfile.TemporaryDirectory() as folder:
            copy = Path(folder) / "ui.mjs"
            copy.write_text(self.script, encoding="utf-8")
            done = subprocess.run([node, "--check", str(copy)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_changelog_names_the_guided_setup(self):
        changelog = read(ROOT / "CHANGELOG.md")
        unreleased = changelog[changelog.index("## Unreleased"):changelog.index("## 0.1.0")]
        for text in ("python3 tools/tizen.py setup", "tv setup", "Set up a new TV", "tv cert add",
                     "install PACKAGE_OR_HTTPS_URL TV"):
            self.assertIn(text, unreleased)


if __name__ == "__main__":
    unittest.main()
