#!/usr/bin/env python3
"""Build a deterministic plugin ZIP and provenance from a clean committed tree."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

from tizen import execute

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"plugin.json", ".github/plugin/marketplace.json", "README.md", "AGENTS.md",
              "LICENSE", "CHANGELOG.md", "PRODUCT.md", "DESIGN.md"}
CANVAS_FILES = {".github/extensions/tizen-live/" + name for name in
                ("extension.mjs", "index.html", "ui.js", "ui.css", "controls.mjs")}
TREES = ("skills/", "com.github.copilot/agents/", "docs/", "tools/", "tests/", "examples/hello-tv/")
SUFFIXES = {".md", ".py", ".html", ".xml", ".mjs", ".js", ".css"}


def payload_paths(paths):
    selected = []
    for name in paths:
        if name in ROOT_FILES | CANVAS_FILES or (name.startswith(TREES) and Path(name).suffix in SUFFIXES):
            if name not in ROOT_FILES | CANVAS_FILES and any(
                    part.startswith(".") or part in {"private", "receipts", "node_modules"}
                    for part in Path(name).parts):
                raise ValueError("Private/generated paths are not release content")
            selected.append(name)
        elif name not in {".gitignore", ".github/workflows/workbench-windows.yml"}:
            raise ValueError("Unclassified tracked file; explicitly review release scope: " + name)
    required = ROOT_FILES | {"skills/tizen-tv-builder/SKILL.md",
                             "com.github.copilot/agents/tizen-tv-builder.agent.md",
                             "tools/tizen.py", "tools/release.py"}
    if not required.issubset(selected):
        raise ValueError("Release is missing canonical components")
    return sorted(selected)


def build(output):
    if execute(["git", "-C", str(ROOT), "status", "--porcelain"]).strip():
        raise ValueError("Commit changes and clean the checkout before building a release")
    commit = execute(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).strip()
    manifest = json.loads((ROOT / "plugin.json").read_text())
    version = manifest["version"]
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Release version must be stable major.minor.patch")
    if manifest.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
        raise ValueError("Release must use the reviewed Agent Plugins 1.0 schema")
    names = payload_paths(execute(["git", "-C", str(ROOT), "ls-files"]).splitlines())
    output = Path(output)
    if not output.is_dir():
        raise ValueError("Output must be an existing directory outside the repository")
    if output.resolve().is_relative_to(ROOT):
        raise ValueError("Release output must stay outside the repository")
    archive = output / f"tizen-tv-builder-{version}.zip"
    receipt = output / "release.json"
    checksums = output / "SHA256SUMS"
    if any(path.exists() for path in (archive, receipt, checksums)):
        raise ValueError("Release output files already exist; never overwrite artifacts")
    contents = {}
    for name in names:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Release content must be real files, not symlinks")
        contents[name] = path.read_bytes()
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as package:
        for name, data in contents.items():
            entry = zipfile.ZipInfo("tizen-tv-builder/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            package.writestr(entry, data)
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    provenance = {
        "name": manifest["name"], "version": version, "source_commit": commit,
        "archive": archive.name, "sha256": archive_hash,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
    }
    with receipt.open("x") as stream:
        stream.write(json.dumps(provenance, indent=2) + "\n")
    with checksums.open("x") as stream:
        stream.write(f"{archive_hash}  {archive.name}\n")
        stream.write(f"{hashlib.sha256(receipt.read_bytes()).hexdigest()}  {receipt.name}\n")
    return {"version": version, "source_commit": commit, "archive": archive.name,
            "sha256": archive_hash, "files": len(contents)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    try:
        print(json.dumps(build(parser.parse_args().output), indent=2))
    except (ValueError, OSError, KeyError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
