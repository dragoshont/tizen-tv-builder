"""Offline checks for the generic Samsung workflow skills."""
import importlib.util
from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    "samsung-tv-lease", "samsung-tv-connect", "samsung-tv-debug",
    "samsung-tv-inspect", "samsung-tv-certificates", "samsung-tv-new-app",
}


class SkillStructureTests(unittest.TestCase):
    def test_workflows_discoverable_without_manifest_changes(self):
        for name in WORKFLOWS:
            with self.subTest(name=name):
                path = ROOT / "skills" / name / "SKILL.md"
                self.assertTrue(path.is_file(), name)
                text = path.read_text()
                match = re.match(r"\A---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---\n", text)
                self.assertIsNotNone(match, name)
                self.assertEqual(match[1], name)
                self.assertIn("not", match[2].lower())
                self.assertIn("../..", text)
                self.assertIn("permission", text.lower())
                self.assertIn("[Common contract](../common.md)", text)
                self.assertLess(len(text.splitlines()), 100)

    def test_local_markdown_links_resolve(self):
        paths = list((ROOT / "skills").rglob("*.md")) + [
            ROOT / "docs/getting-started.md", ROOT / "docs/prerequisites.md",
        ]
        for path in paths:
            self.assertTrue(path.is_file())
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
                if "://" in target or target.startswith("#"):
                    continue
                with self.subTest(path=path.name, target=target):
                    destination, _, anchor = target.partition("#")
                    linked = path.parent / destination
                    self.assertTrue(linked.is_file())
                    if anchor and linked.suffix == ".md":
                        headings = re.findall(r"^#+ (.+)$", linked.read_text(), re.MULTILINE)
                        slugs = {
                            re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                            for heading in headings
                        }
                        self.assertIn(anchor, slugs)

    def test_release_selector_includes_all_skill_files(self):
        sys.path.insert(0, str(ROOT / "tools"))
        try:
            spec = importlib.util.spec_from_file_location("skill_release_check", ROOT / "tools/release.py")
            release = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(release)
            skills = {p.relative_to(ROOT).as_posix() for p in (ROOT / "skills").rglob("*.md")}
            paths = release.ROOT_FILES | skills | {
                "com.github.copilot/agents/tizen-tv-builder.agent.md",
                "tools/tizen.py", "tools/release.py",
            }
            self.assertTrue(skills.issubset(release.payload_paths(paths)))
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
