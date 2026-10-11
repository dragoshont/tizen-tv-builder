"""App-level gates: scaffold, audit, deterministic unsigned pack and package checks.

Host-only. Nothing here contacts a TV, signs, or proves runtime support.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
from urllib.parse import unquote
import string
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from tizen import ASSETS, TIZEN, WIDGET, safe_path, stage, validate

PRIVILEGE = re.compile(r"https?://(?:tizen\.org|developer\.samsung\.com)/privilege/[a-z0-9._]+")
# API usage that fails at runtime (often SecurityError) without its privilege.
API_PRIVILEGES = {
    "http://tizen.org/privilege/tv.inputdevice": re.compile(r"\btvinputdevice\b"),
    "http://developer.samsung.com/privilege/avplay": re.compile(r"\bwebapis\.avplay\b"),
    "http://developer.samsung.com/privilege/productinfo": re.compile(r"\bwebapis\.productinfo\b"),
    "http://tizen.org/privilege/application.launch": re.compile(r"\btizen\.application\b[\s\S]{0,80}?\blaunch(?:AppControl)?\s*\("),
    "http://tizen.org/privilege/internet": re.compile(r"\bnew\s+(?:XMLHttpRequest|WebSocket|EventSource)\b|\bsendBeacon\s*\(|(?:(?<![\w$.])|\bwindow\.|\bself\.)fetch\s*\("),
}
KNOWN = set(API_PRIVILEGES)
SECRETS = [
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("plex-token", re.compile(r"(?i)x[-_]plex[-_]token[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_-]{8,}")),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("assigned-secret", re.compile(
        r"(?i)\b[\w-]*(?:api[_-]?key|token|secret|passw(?:or)?d)\b"
        r"[\"']?\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']")),
    ("private-address", re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")),
]
PRIVATE_FILES = {".p12", ".pfx", ".keystore", ".jks", ".pem", ".key", ".crt", ".cer", ".mobileprovision"}
MAX_TEXT = 4 * 1024 * 1024
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def _finding(level, code, message, where=None):
    entry = {"level": level, "code": code, "message": message}
    if where:
        entry["where"] = where
    return entry


def _strip_script(text):
    """Blank strings/comments/regex bodies so the guard sees syntax, keeping line numbers."""
    out, i, n, last = [], 0, len(text), ""
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j; continue
        if two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j])); i = j; continue
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(c + re.sub(r"[^\n]", " ", text[i + 1:j]) + c); i = j + 1; last = "x"; continue
        if c == "`":
            out.append("`"); i += 1; last = "x"; continue
        if c == "/" and (not last or last in "(,=:[!&|?{};+-*%<>~^"):
            j, klass = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2; continue
                if text[j] == "[":
                    klass = True
                elif text[j] == "]":
                    klass = False
                elif text[j] == "/" and not klass:
                    break
                j += 1
            out.append("/" + " " * (j - i - 1) + "/"); i = j + 1; last = "x"; continue
        out.append(c)
        if not c.isspace():
            last = c
        i += 1
    return "".join(out)


ES2015 = [
    ("arrow-function", re.compile(r"=>")),
    ("template-literal", re.compile(r"`")),
    ("block-binding", re.compile(r"(?<![\w$.])(?:let|const)\s+[\w$\[{]")),
    ("class", re.compile(r"(?<![\w$.])class\s+[\w$]+\s*(?:extends\b|\{)")),
    ("spread-rest", re.compile(r"\.\.\.")),
    ("for-of", re.compile(r"\bfor\s*\([^;)]*\bof\b")),
    ("async-await", re.compile(r"(?<![\w$.])(?:async\s+function|await\s+[\w$(])")),
    ("generator", re.compile(r"\bfunction\s*\*")),
    ("exponent", re.compile(r"\*\*")),
    ("module", re.compile(r"(?m)^\s*(?:import|export)\s+[\w${*]")),
]


def es5_findings(text, name):
    stripped = _strip_script(text)
    findings = []
    for code, pattern in ES2015:
        for match in pattern.finditer(stripped):
            line = stripped.count("\n", 0, match.start()) + 1
            findings.append(_finding("error", "es5-" + code, "ES2015+ syntax; legacy TV engines parse ES5 only", f"{name}:{line}"))
            break
    return findings


def _scripts(source):
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part.startswith(".") or part in {"private", "receipts", "node_modules"} for part in relative.parts):
            continue
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_TEXT:
            continue
        suffix = path.suffix.lower()
        if suffix == ".js":
            yield relative.as_posix(), path.read_text(encoding="utf-8", errors="replace"), 1
        elif suffix in {".html", ".htm"}:
            html = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"<script[^>]*type\s*=\s*[\"']?module", html, re.I):
                yield relative.as_posix(), "import x", 1
            for match in re.finditer(r"<script(?![^>]*\bsrc\s*=)[^>]*>([\s\S]*?)</script>", html, re.I):
                yield relative.as_posix(), match.group(1), html.count("\n", 0, match.start(1)) + 1


def _privileges(source):
    root = ET.fromstring(safe_path(Path(source), "config.xml").read_bytes())
    return root, [p.get("name", "") for p in root.findall(TIZEN + "privilege")]


def scan_text(name, text):
    findings = []
    for code, pattern in SECRETS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            level = "warning" if code == "private-address" else "error"
            findings.append(_finding(level, "secret-" + code, "Possible credential or private value; keep it out of the package", f"{name}:{line}"))
            break
    return findings


def audit(source):
    """Manifest identity, privileges vs API use, ES5 syntax guard and credential scan."""
    source = Path(source)
    findings = []
    identity = validate(source)
    root, privileges = _privileges(source)
    version = root.get("version", "")
    if not re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,5}", version) or any(
            int(part) > limit for part, limit in zip(version.split("."), (255, 255, 65535))):
        findings.append(_finding("error", "version", "Widget version must be x.y.z (255.255.65535 max)"))
    if identity["app"].split(".")[0] in {"PlxHelloTv", "AAAAAAAAAA"}:
        findings.append(_finding("warning", "example-id", "Example package ID; generate your own with `tizen.py new`"))
    seen = set()
    for name in privileges:
        if not PRIVILEGE.fullmatch(name):
            findings.append(_finding("error", "privilege-malformed", "Privilege must be a Tizen/Samsung privilege URI", name))
        elif name in seen:
            findings.append(_finding("error", "privilege-duplicate", "Privilege declared twice", name))
        elif name not in KNOWN:
            findings.append(_finding("info", "privilege-unchecked", "Declared; usage not checked by this audit", name))
        seen.add(name)
    for access in root.findall(WIDGET + "access"):
        if access.get("origin") == "*":
            findings.append(_finding("warning", "access-wildcard", "Network access allows every origin"))
    code = []
    for name, text, offset in _scripts(source):
        code.append(text)
        for finding in es5_findings(text, name):
            if offset > 1 and "where" in finding:
                file, line = finding["where"].rsplit(":", 1)
                finding["where"] = f"{file}:{int(line) + offset - 1}"
            findings.append(finding)
    usage = _strip_script("\n".join(code))
    for privilege, pattern in API_PRIVILEGES.items():
        used = bool(pattern.search(usage))
        if used and privilege not in seen:
            findings.append(_finding("error", "privilege-missing", "API is used without its privilege; expect SecurityError on TV", privilege))
        elif privilege in seen and not used:
            findings.append(_finding("warning", "privilege-unused", "Declared but no matching API use found", privilege))
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part.startswith(".") or part in {"private", "receipts", "node_modules"} for part in relative.parts[:-1]) \
                or not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() in PRIVATE_FILES:
            findings.append(_finding("error", "secret-file", "Certificate/key material inside the app source", relative.as_posix()))
        elif path.suffix.lower() in ASSETS | {".xml", ".json", ".txt"} and path.stat().st_size <= MAX_TEXT \
                and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf"}:
            findings.extend(scan_text(relative.as_posix(), path.read_text(encoding="utf-8", errors="replace")))
    errors = [f for f in findings if f["level"] == "error"]
    return {"app": identity["app"], "required_version": identity["required_version"],
            "version": version, "privileges": privileges, "findings": findings,
            "passed": not errors, "scope": "host static checks; runtime API support not proven"}


def pack(source, output):
    """Deterministic unsigned .wgt for CI receipts. Not installable on a retail TV."""
    output = Path(output)
    if output.exists() or output.is_symlink() or output.suffix.lower() != ".wgt" or not output.parent.is_dir():
        raise ValueError("Output must be a new .wgt file in an existing directory")
    report = audit(source)
    if not report["passed"]:
        raise ValueError("Audit failed; run `tizen.py audit` for findings")
    with tempfile.TemporaryDirectory(prefix="tizen-pack-") as temporary:
        staged = Path(temporary)
        stage(source, staged)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(p for p in staged.rglob("*") if p.is_file()):
                info = zipfile.ZipInfo(path.relative_to(staged).as_posix(), FIXED_TIME)
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    data = buffer.getvalue()
    with output.open("xb") as stream:
        stream.write(data)
    return {"package": str(output.name), "sha256": hashlib.sha256(data).hexdigest(),
            "signed": False, "app": report["app"], "installable": "no; sign with the SDK profile for a TV"}


DS = "{http://www.w3.org/2000/09/xmldsig#}"
DIGESTS = {"http://www.w3.org/2001/04/xmlenc#sha256": "sha256",
           "http://www.w3.org/2001/04/xmldsig-more#sha384": "sha384",
           "http://www.w3.org/2001/04/xmlenc#sha512": "sha512"}


def check_package(path, require_signed=False):
    """Structural/digest check of a .wgt. Certificate trust and crypto signature are NOT verified."""
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".wgt":
        raise ValueError("Package must be an existing real .wgt file")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 5000 or sum(i.file_size for i in infos) > 512 * 1024 * 1024:
            raise ValueError("Package exceeds entry or size limits")
        names = []
        for info in infos:
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts or "\\" in name or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Package contains an unsafe entry")
            if not name.endswith("/"):
                names.append(name)
        if len(set(names)) != len(names):
            raise ValueError("Package contains duplicate entries")
        if "config.xml" not in names:
            raise ValueError("Package has no root config.xml")
        content = {name: archive.read(name) for name in names}
    signatures = {}
    for name in ("author-signature.xml", "signature1.xml"):
        if name not in content:
            continue
        data = content[name]
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise ValueError("Signature XML contains entity declarations")
        root = ET.fromstring(data)
        references, mismatched = 0, []
        for reference in root.iter(DS + "Reference"):
            uri = reference.get("URI", "")
            if uri.startswith("#"):
                continue
            method = reference.find(DS + "DigestMethod")
            value = reference.find(DS + "DigestValue")
            algorithm = DIGESTS.get(method.get("Algorithm") if method is not None else "")
            target = unquote(uri)
            references += 1
            if not algorithm or value is None or target not in content or \
                    base64.b64encode(hashlib.new(algorithm, content[target]).digest()).decode() != "".join((value.text or "").split()):
                mismatched.append(target or "<empty>")
        signed_files = {unquote(r.get("URI", "")) for r in root.iter(DS + "Reference")}
        expected = {n for n in names if n not in {"author-signature.xml"} and not re.fullmatch(r"signature\d+\.xml", n)}
        signatures[name] = {"references": references, "digest_mismatches": mismatched,
                            "unreferenced": sorted(expected - signed_files),
                            "certificates": len(list(root.iter(DS + "X509Certificate")))}
    with tempfile.TemporaryDirectory(prefix="tizen-check-") as temporary:
        root = Path(temporary)
        for name, data in content.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        report = audit(root)
    signed = "author-signature.xml" in signatures and "signature1.xml" in signatures
    consistent = signed and all(not s["digest_mismatches"] and not s["unreferenced"] for s in signatures.values())
    problems = [f for f in report["findings"] if f["level"] == "error"]
    if require_signed and not consistent:
        problems.append(_finding("error", "signature", "Author and distributor signature digests must cover every file"))
    return {"package": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "app": report["app"], "files": len(names), "signed": signed,
            "signature_digests_consistent": consistent, "signatures": signatures,
            "audit": report["findings"], "passed": not problems,
            "scope": "digests only; certificate chain, DUID and signature crypto not verified"}


TEMPLATE_CONFIG = """<?xml version="1.0" encoding="UTF-8"?>
<widget xmlns="http://www.w3.org/ns/widgets" xmlns:tizen="http://tizen.org/ns/widgets"
        id="https://example.invalid/{slug}" version="0.1.0" viewmodes="maximized">
  <tizen:application id="{package}.{application}" package="{package}" required_version="3.0"/>
  <content src="index.html"/>
  <name>{name}</name>
  <tizen:privilege name="http://tizen.org/privilege/tv.inputdevice"/>
  <tizen:profile name="tv"/>
  <tizen:setting screen-orientation="landscape" context-menu="disable" background-support="disable" encryption="disable" install-location="auto" hwkey-event="enable"/>
</widget>
"""

TEMPLATE_INDEX = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=1920,initial-scale=1">
<title>{name}</title><link rel="stylesheet" href="css/app.css">
<script>window.__tvMarks = {{}};</script>
</head>
<body>
<header><h1>{name}</h1><p id="status">Ready</p></header>
<main id="home" class="grid" aria-label="Home"></main>
<script src="js/focus.js"></script>
<script src="js/app.js"></script>
</body>
</html>
"""

TEMPLATE_CSS = """html,body{margin:0;background:#101114;color:#f5f5f7;font:32px/1.3 "Samsung One",Helvetica,Arial,sans-serif}
header{padding:60px 96px 24px}h1{margin:0;font-size:56px;font-weight:600}#status{margin:8px 0 0;color:#a1a1a6}
.grid{display:flex;flex-wrap:wrap;padding:24px 84px}
.tile{width:400px;height:225px;margin:12px;border-radius:16px;background:#2c2c2e;display:flex;align-items:flex-end;padding:24px;box-sizing:border-box;transition:transform .15s}
.tile.focused{background:#f5f5f7;color:#101114;transform:scale(1.06)}
"""

TEMPLATE_FOCUS = """(function (root) {
    "use strict";
    function FocusGrid(count, columns) {
        this.count = count;
        this.columns = columns;
        this.index = 0;
    }
    FocusGrid.prototype.move = function (key) {
        var i = this.index, c = this.columns;
        if (key === "left" && i % c > 0) { i -= 1; }
        else if (key === "right" && i % c < c - 1 && i + 1 < this.count) { i += 1; }
        else if (key === "up" && i - c >= 0) { i -= c; }
        else if (key === "down" && i + c < this.count) { i += c; }
        var moved = i !== this.index;
        this.index = i;
        return moved;
    };
    var KEYS = {37: "left", 38: "up", 39: "right", 40: "down", 13: "ok", 10009: "back"};
    FocusGrid.keyName = function (code) { return KEYS[code] || null; };
    if (typeof module === "object" && module.exports) { module.exports = FocusGrid; }
    else { root.FocusGrid = FocusGrid; }
}(this));
"""

TEMPLATE_APP = """(function () {
    "use strict";
    var marks = window.__tvMarks;
    var start = window.performance && performance.timing ? performance.timing.navigationStart : Date.now();
    function mark(name) { if (marks[name] === undefined) { marks[name] = Date.now() - start; } }
    var raf = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 16); };
    raf(function () { mark("firstFrame"); });

    var titles = ["Continue", "Library", "Search", "Settings", "Recent", "Favourites", "Help", "About"];
    var home = document.getElementById("home");
    var grid = new window.FocusGrid(titles.length, 4);
    var tiles = [];
    for (var i = 0; i < titles.length; i += 1) {
        var tile = document.createElement("div");
        tile.className = "tile";
        tile.textContent = titles[i];
        home.appendChild(tile);
        tiles.push(tile);
    }
    function paint() {
        for (var j = 0; j < tiles.length; j += 1) {
            tiles[j].className = j === grid.index ? "tile focused" : "tile";
        }
    }
    paint();
    raf(function () { mark("home"); });

    try {
        if (window.tizen && tizen.tvinputdevice) {
            tizen.tvinputdevice.registerKeyBatch(["MediaPlayPause", "ColorF0Red"]);
        }
    } catch (error) {
        document.getElementById("status").textContent = "Key registration unavailable";
    }
    document.addEventListener("keydown", function (event) {
        var name = window.FocusGrid.keyName(event.keyCode);
        if (name === "back") {
            try { tizen.application.getCurrentApplication().exit(); } catch (ignore) { }
        } else if (name === "ok") {
            document.getElementById("status").textContent = titles[grid.index];
        } else if (name && grid.move(name)) {
            paint();
        }
    });
}());
"""

TEMPLATE_TEST = """"use strict";
const test = require("node:test");
const assert = require("node:assert");
const FocusGrid = require("../app/js/focus.js");

test("focus moves within the grid and stops at edges", () => {
    const grid = new FocusGrid(8, 4);
    assert.equal(grid.move("left"), false);
    assert.equal(grid.move("right"), true);
    assert.equal(grid.index, 1);
    assert.equal(grid.move("down"), true);
    assert.equal(grid.index, 5);
    assert.equal(grid.move("down"), false);
});

test("Samsung key codes map to names", () => {
    assert.equal(FocusGrid.keyName(10009), "back");
    assert.equal(FocusGrid.keyName(13), "ok");
    assert.equal(FocusGrid.keyName(1), null);
});
"""

TEMPLATE_README = """# {name}

Generated by tizen-tv-builder. `app/` is the packaged source; `tests/` stays on the host.

```
node --test tests/                                   # host logic tests
python3 <toolkit>/tools/tizen.py audit app           # IDs, privileges, ES5, credentials
python3 <toolkit>/tools/tizen.py pack app --output build/{package}.wgt   # unsigned CI receipt
python3 <toolkit>/tools/tizen.py package app --profile <profile> --output build/{package}-signed.wgt
```

App ID: `{package}.{application}`. Keep certificates and private values outside this folder.
"""


def scaffold(target, name, package=None, application="App"):
    target = Path(target)
    if target.exists() or target.is_symlink():
        raise ValueError("Target folder must not exist")
    if not target.parent.is_dir():
        raise ValueError("Parent folder must already exist")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 ]{0,39}", name or ""):
        raise ValueError("Name must be 1-40 letters, digits or spaces, starting with a letter")
    alphabet = string.ascii_letters + string.digits
    package = package or secrets.choice(string.ascii_letters) + "".join(secrets.choice(alphabet) for _ in range(9))
    if not re.fullmatch(r"[A-Za-z0-9]{10}", package):
        raise ValueError("Package ID must be ten letters or digits")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,51}", application):
        raise ValueError("Application name must be letters, digits or underscore")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "app"
    values = {"name": name, "package": package, "application": application, "slug": slug}
    files = {
        "app/config.xml": TEMPLATE_CONFIG, "app/index.html": TEMPLATE_INDEX,
        "app/css/app.css": TEMPLATE_CSS, "app/js/focus.js": TEMPLATE_FOCUS,
        "app/js/app.js": TEMPLATE_APP, "tests/focus.test.js": TEMPLATE_TEST,
        "README.md": TEMPLATE_README,
        ".gitignore": "build/\nprivate/\nreceipts/\n*.wgt\n*.p12\n*.pfx\n",
    }
    target.mkdir()
    for relative, template in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = template if relative.endswith((".js", ".css", ".gitignore")) else template.format(**values)
        path.write_text(text, encoding="utf-8", newline="\n")
    report = audit(target / "app")
    return {"created": str(target.name), "app": f"{package}.{application}", "source": "app",
            "audit_passed": report["passed"], "next": "node --test tests/ && tizen.py audit app"}


def dumps(value):
    return json.dumps(value, indent=2)
