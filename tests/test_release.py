import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("release", ROOT / "tools/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_manifest_and_canonical_components(self):
        manifest = json.loads((ROOT / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "tizen-tv-builder")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["$schema"], "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json")
        self.assertEqual(manifest["license"], "MIT")
        marketplace = json.loads((ROOT / ".github/plugin/marketplace.json").read_text())
        self.assertEqual(marketplace["name"], "tizen-tv-tools")
        self.assertEqual(marketplace["metadata"]["version"], manifest["version"])
        self.assertEqual(marketplace["plugins"][0]["version"], manifest["version"])
        self.assertEqual(marketplace["plugins"][0]["name"], manifest["name"])
        self.assertEqual(marketplace["plugins"][0]["source"], ".")
        self.assertFalse(set(manifest) - {"$schema", "name", "version", "description", "author",
                                         "homepage", "repository", "license", "keywords", "extensions"})
        for name in ("skills/tizen-tv-builder/SKILL.md",
                     "com.github.copilot/agents/tizen-tv-builder.agent.md"):
            self.assertTrue((ROOT / name).is_file())
            self.assertIn("name: tizen-tv-builder", (ROOT / name).read_text())
        self.assertFalse((ROOT / "mcp.json").exists())
        self.assertFalse((ROOT / "com.github.copilot/hooks").exists())

    def fixture(self, root):
        names = sorted(release.ROOT_FILES | {
            "skills/tizen-tv-builder/SKILL.md",
            "com.github.copilot/agents/tizen-tv-builder.agent.md",
            "tools/tizen.py", "tools/release.py",
        })
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / name).read_bytes())
        return names

    def test_release_is_deterministic_and_has_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            source = parent / "source"
            source.mkdir()
            names = self.fixture(source)
            first, second = parent / "first", parent / "second"
            first.mkdir()
            second.mkdir()
            def git(arguments, timeout=120):
                return ("" if arguments[-1] == "--porcelain" else
                        "a" * 40 if arguments[-1] == "HEAD" else "\n".join(names))
            with patch.object(release, "ROOT", source), patch.object(release, "execute", side_effect=git):
                result = release.build(first)
                self.assertEqual(result, release.build(second))
                with self.assertRaisesRegex(ValueError, "already exist"):
                    release.build(first)
            archive = first / result["archive"]
            self.assertEqual(archive.read_bytes(), (second / result["archive"]).read_bytes())
            provenance = json.loads((first / "release.json").read_text())
            self.assertEqual(provenance["source_commit"], "a" * 40)
            self.assertEqual(set(provenance["files"]), set(names))
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(package.namelist(), ["tizen-tv-builder/" + name for name in names])
                self.assertTrue(all(not info.is_dir() for info in package.infolist()))
            self.assertIn(result["sha256"], (first / "SHA256SUMS").read_text())

    def test_dirty_checkout_refused_before_any_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.object(release, "execute", return_value=" M README.md"):
                with self.assertRaisesRegex(ValueError, "clean"):
                    release.build(output)
            self.assertEqual(list(output.iterdir()), [])

    def test_private_unknown_and_missing_paths_fail_closed(self):
        names = sorted(release.ROOT_FILES | {
            "skills/tizen-tv-builder/SKILL.md",
            "com.github.copilot/agents/tizen-tv-builder.agent.md",
            "tools/tizen.py", "tools/release.py",
        })
        for extra in ("key.p12", "docs/private/receipt.md", "tools/.secret.py",
                      "com.github.copilot/hooks/hooks.json", "examples/account.json"):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                release.payload_paths(names + [extra])
        with self.assertRaisesRegex(ValueError, "missing"):
            release.payload_paths(["README.md"])

    def test_release_rejects_symlinks_without_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            source, output = parent / "source", parent / "out"
            source.mkdir()
            output.mkdir()
            names = self.fixture(source)
            (source / "LICENSE").unlink()
            (source / "LICENSE").symlink_to(ROOT / "LICENSE")
            with patch.object(release, "ROOT", source), \
                    patch.object(release, "execute", side_effect=["", "a" * 40, "\n".join(names)]):
                with self.assertRaisesRegex(ValueError, "symlinks"):
                    release.build(output)
            self.assertEqual(list(output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
