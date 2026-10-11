#!/usr/bin/env python3
"""Install only the standalone live canvas adapter into a chosen extension directory."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def install(destination):
    destination = Path(destination)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("Choose a new absolute extension directory; never overwrite an installed adapter")
    if destination.resolve().is_relative_to(ROOT):
        raise ValueError("Install outside this toolkit checkout")
    if not destination.parent.is_dir():
        raise ValueError("Extension parent must already exist")
    source = ROOT / ".github/extensions/tizen-live"
    payload = {name: source / name for name in ("extension.mjs", "index.html", "ui.js", "ui.css", "controls.mjs")}
    payload.update({f"backend/{name}": ROOT / "tools" / name for name in (
        "live_tv.py", "tizen.py", "tv.py", "tv_fleet.py", "flows.py", "network_tv.py", "samsung_models.py", "workbench.py", "recording.py", "appkit.py")})
    # flows.BUDGETS resolves to <destination>/docs/budgets.json from the backend folder.
    payload["docs/budgets.json"] = ROOT / "docs/budgets.json"
    for path in payload.values():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Canvas payload is missing or is a symlink")
    destination.mkdir(mode=0o700)
    try:
        for name, path in payload.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        # Backend tizen imports version from its relative root.
        shutil.copyfile(ROOT / "plugin.json", destination / "plugin.json")
    except BaseException:
        # Delete only files installed here, not a broad directory or user-owned content.
        for name in list(payload) + ["plugin.json"]:
            target = destination / name
            if target.is_file():
                target.unlink()
        for folder in ("backend", "docs"):
            if (destination / folder).exists():
                (destination / folder).rmdir()
        destination.rmdir()
        raise
    return {"installed": "tizen-live", "files": len(payload),
            "version": json.loads((ROOT / "plugin.json").read_text())["version"],
            "activation": "Reload extensions or start a fresh session; no device connected"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    try:
        print(json.dumps(install(parser.parse_args().destination), indent=2))
    except (ValueError, OSError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
