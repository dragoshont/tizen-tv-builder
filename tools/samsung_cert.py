"""One-click Samsung TV certificates: one browser sign-in, everything else without prompts.

The person clicks once and signs in to Samsung. This module then creates the keys and requests,
asks Samsung's certificate service for the certificates, packs them as PKCS#12 with a generated
password, keeps that password where Tizen Studio itself keeps it, and registers an active signing
profile in profiles.xml.

Independently written from the protocol documented by gifflet/tizen-cert-cli (MIT) and Tizen
Studio's own file formats. Samsung's CA certificates are read from the person's own Tizen Studio
installation and are never shipped with this toolkit. Passwords, tokens and keys are never logged.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import string
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

PROFILE = "tv-companion"
SIGNIN_PORT = 4794
CALLBACK = "/signin/callback"
SIGNIN_URL = ("https://account.samsung.com/mobile/account/check.do?serviceID=v285zxnl3h&actionID=StartOAuth2"
              "&accessToken=Y&redirect_uri=http://localhost:4794/signin/callback")
API = "https://svdca.samsungqbe.com/apis"
EMAIL_HELP = "https://account.samsung.com/membership/getProfileInfo.do"
SIGNIN_TIMEOUT = 600
HTTP_TIMEOUT = 40
MAX_BODY = 1 << 20
MAX_DEVICES = 50
# Tizen's fixed key for passwords kept inline in profiles.xml (what its CLI decrypts).
PASSWORD_KEY = b"KYANINYLhijklmnopqrstuvw"
# Where Tizen Studio keeps profile passwords on macOS: this service, account = the .pwd path.
KEYCHAIN_SERVICE = "certificate-manager"
CA_FILES = {"author": "res/ca/vd_tizen_dev_author_ca.cer", "distributor": "res/ca/vd_tizen_dev_public2.crt"}
DUID = re.compile(r"[A-Za-z0-9]{1,64}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}")
# Printable ASCII only: tokens go into multipart fields, so no CR/LF or spaces.
TOKEN = re.compile(r"[!-~]{1,4096}")


class CertError(Exception):
    """A certificate step failed. `message` is the real reason; `next` is the one thing to do."""

    def __init__(self, code, message, next_step="Try again"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next = next_step


# ---- Tools on this computer -------------------------------------------------------------------

def find_openssl(which=shutil.which, system=None):
    found = which("openssl")
    if found:
        return found
    if (system or platform.system()) == "Windows":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"):
            for rel in (r"Git\usr\bin\openssl.exe", r"Git\mingw64\bin\openssl.exe"):
                candidate = Path(base) / rel
                if candidate.is_file():
                    return str(candidate)
    return None


def sdk_roots(*tools):
    """Tizen Studio folders: next to the sdb/tizen found, TIZEN_SDK_ROOT, then the default places."""
    roots = []
    for tool in tools:
        if tool:
            path = Path(tool)
            # sdb lives in ROOT/tools; tizen in ROOT/tools/ide/bin.
            roots += [path.parent.parent, path.parent.parent.parent.parent]
    if os.environ.get("TIZEN_SDK_ROOT"):
        roots.append(Path(os.environ["TIZEN_SDK_ROOT"]))
    roots += [Path.home() / "tizen-studio", Path("C:/tizen-studio")]
    unique = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


CA_PLACES = ("tools/certificate-manager/certificate-manager.app/Contents/Eclipse/plugins",
             "tools/certificate-manager/plugins", "tools/certificate-manager",
             "TizenStudio.app/Contents/Eclipse/plugins", "ide/plugins")


def find_ca(roots):
    """Samsung's TV CA certificates (PEM text) from the newest Tizen Studio certificate plug-in."""
    jars = []
    for root in roots:
        for place in CA_PLACES:
            folder = Path(root) / place
            if folder.is_dir():
                jars += folder.glob("org.tizen.common.cert_*.jar")
    for jar in sorted(set(jars), key=lambda p: p.name, reverse=True):
        try:
            with zipfile.ZipFile(jar) as archive:
                found = {}
                for kind, name in CA_FILES.items():
                    data = archive.read(name)
                    if len(data) > 65536 or not data.lstrip().startswith(b"-----BEGIN CERTIFICATE-----"):
                        raise ValueError(name)
                    found[kind] = data.decode("ascii")
                return found
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
    return None


# ---- openssl ----------------------------------------------------------------------------------

def _ssl(openssl, args, stdin=None, cwd=None):
    try:
        done = subprocess.run([openssl, *args], input=stdin, capture_output=True, timeout=60, cwd=cwd)
    except (OSError, subprocess.SubprocessError) as error:
        raise CertError("openssl", f"openssl {args[0]} could not run: {type(error).__name__}: {error}",
                        "Check that openssl works in a terminal, then try again") from error
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CertError("openssl", f"openssl {args[0]} failed: {detail[-1] if detail else 'exit ' + str(done.returncode)}",
                        "Check that openssl works in a terminal, then try again")
    return done.stdout


def encrypt_password(openssl, password):
    """Tizen's inline profiles.xml password form: 3DES with its fixed key, base64."""
    out = _ssl(openssl, ["enc", "-des-ede3", "-K", PASSWORD_KEY.hex(), "-nosalt", "-a", "-A"],
               stdin=password.encode("ascii"))
    text = out.decode("ascii").strip()
    if not text:
        raise CertError("openssl", "openssl enc returned no output")
    return text


def make_request(openssl, folder, stem, common_name, email=None, duids=()):
    """A new RSA key (stem.pri) and certificate request (stem.csr); returns the request text."""
    lines = ["[req]", "distinguished_name=dn", "prompt=no"]
    if duids:
        lines.append("req_extensions=ext")
    lines += ["[dn]", f"CN={common_name}"]
    if email:
        lines.append(f"emailAddress={email}")
    if duids:
        sans = ["URI:URN:tizen:packageid="] + [f"URI:URN:tizen:deviceid={d}" for d in duids]
        lines += ["[ext]", "subjectAltName=" + ",".join(sans)]
    config = Path(folder) / f"{stem}.cnf"
    config.write_text("\n".join(lines) + "\n", encoding="ascii")
    _ssl(openssl, ["req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{stem}.pri", "-out", f"{stem}.csr",
                   "-sha512", "-config", f"{stem}.cnf"], cwd=folder)
    os.chmod(Path(folder) / f"{stem}.pri", 0o600)
    config.unlink()
    return (Path(folder) / f"{stem}.csr").read_text(encoding="ascii")


def make_p12(openssl, folder, stem, certificate, ca, password):
    """stem.p12 from stem.pri, Samsung's answer and its CA, with algorithms Tizen's Java can read."""
    folder = Path(folder)
    (folder / f"{stem}.crt").write_text(certificate, encoding="ascii")
    (folder / f"{stem}-ca.crt").write_text(ca, encoding="ascii")
    _ssl(openssl, ["pkcs12", "-export", "-inkey", f"{stem}.pri", "-in", f"{stem}.crt", "-certfile", f"{stem}-ca.crt",
                   "-name", "usercertificate", "-passout", "stdin", "-keypbe", "PBE-SHA1-3DES",
                   "-certpbe", "PBE-SHA1-3DES", "-macalg", "sha1", "-out", f"{stem}.p12"],
         stdin=password.encode("ascii") + b"\n", cwd=folder)
    os.chmod(folder / f"{stem}.p12", 0o600)
    (folder / f"{stem}-ca.crt").unlink()


# ---- Samsung sign-in --------------------------------------------------------------------------

def parse_code(value):
    """The sign-in callback's `code`: JSON with the access token, user ID and the account email."""
    try:
        data = json.loads(value or "")
    except ValueError:
        raise CertError("signin", "Samsung's sign-in answer could not be read") from None
    if not isinstance(data, dict):
        raise CertError("signin", "Samsung's sign-in answer could not be read")
    token, user = data.get("access_token"), data.get("userId")
    if not isinstance(token, str) or not TOKEN.fullmatch(token) or not isinstance(user, str) \
            or not TOKEN.fullmatch(user):
        raise CertError("signin", "Samsung's sign-in answer had no access token or user ID")
    email = data.get("inputEmailID")
    return {"access_token": token, "user_id": user,
            "email": email if isinstance(email, str) and EMAIL.fullmatch(email) else None}


PAGE = ("<!doctype html><meta charset=utf-8><title>{title}</title>"
        "<body style='font:17px -apple-system,system-ui,sans-serif;max-width:32em;margin:15vh auto;padding:0 1em'>"
        "<h1 style='font-size:24px'>{title}</h1><p>{text}</p></body>")


class SignIn:
    """A loopback-only listener for Samsung's sign-in redirect (http://localhost:4794/signin/callback)."""

    def __init__(self, port=SIGNIN_PORT, host="127.0.0.1"):
        self.done = threading.Event()
        self.result = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # The callback carries an access token: never logged.

            def do_GET(self):
                self.callback(b"")

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = -1
                if not 0 <= length <= MAX_BODY:
                    self.page(413, "Sign-in failed", "The sign-in answer was too large.")
                    return
                self.callback(self.rfile.read(length))

            def callback(self, body):
                url = urllib.parse.urlsplit(self.path)
                if url.path != CALLBACK:
                    self.page(404, "Not found", "This address only receives Samsung's sign-in answer.")
                    return
                fields = urllib.parse.parse_qs(body.decode("utf-8", "replace"))
                if "code" not in fields:
                    fields = urllib.parse.parse_qs(url.query)
                try:
                    result = parse_code((fields.get("code") or [""])[0])
                except CertError as error:
                    self.page(400, "Sign-in failed", error.message + ". Go back to the TV Companion and try again.")
                    return
                owner.result = result
                owner.done.set()
                self.page(200, "Signed in", "You can close this tab. The TV Companion is finishing your certificate.")

            def page(self, status, title, text):
                body = PAGE.format(title=title, text=text).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        try:
            self.server = ThreadingHTTPServer((host, port), Handler)
        except OSError as error:
            raise CertError("signin-port", f"Port {port} for Samsung's sign-in answer is in use ({error.strerror or error})",
                            "Close tizen-cert or Tizen Studio's Certificate Manager, then try again") from error
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def wait(self, timeout=SIGNIN_TIMEOUT, cancel=None):
        """The sign-in answer; `cancel` (a threading.Event) stops waiting within a fraction of a second."""
        deadline = time.monotonic() + timeout
        while not self.done.is_set():
            if cancel is not None and cancel.is_set():
                raise CertError("cancelled", "Samsung sign-in was cancelled", "Start a new sign-in")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CertError("signin-timeout", f"Samsung sign-in didn't finish within {timeout // 60} minutes")
            self.done.wait(min(remaining, 0.2 if cancel is not None else remaining))
        return self.result

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---- Samsung's certificate service ------------------------------------------------------------

BOUNDARY = "*****"


def multipart(fields, filename, content):
    parts = [f"--{BOUNDARY}\r\nContent-Type: text/plain; charset=utf-8\r\n"
             f"Content-Disposition: form-data; name={name}\r\n\r\n{value}\r\n" for name, value in fields]
    parts.append(f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=csr; filename={filename}; "
                 f"filename*=utf-8''{filename}\r\n\r\n{content}\r\n--{BOUNDARY}--\r\n")
    return "".join(parts).encode("utf-8")


def urllib_opener(url, body, headers, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as error:
        return error.code, error.read(MAX_BODY + 1)


def samsung_error(status, body):
    if status == 403:
        return CertError("rate-limited", "Samsung is limiting certificate requests right now (HTTP 403)",
                         "Wait a few minutes, then try again")
    detail = {}
    try:
        detail = (json.loads(body.decode("utf-8", "replace")) or {}).get("error") or {}
    except (ValueError, AttributeError):
        pass
    if not isinstance(detail, dict):
        detail = {}
    code, text = str(detail.get("code") or ""), str(detail.get("description") or detail.get("name") or "")
    if status == 401 and code == "303":
        return CertError("no-email", "Your Samsung account has no email address, which certificates need",
                         f"Add an email address at {EMAIL_HELP}, then try again")
    if not text:
        text = body.decode("utf-8", "replace").strip()[:200] or "no details"
    return CertError("samsung", f"Samsung's certificate service answered HTTP {status}"
                                f"{' (code ' + code + ')' if code else ''}: {text}")


def post(path, fields, filename, content, opener=urllib_opener, base=API):
    headers = {"Content-Type": f'multipart/form-data; boundary="{BOUNDARY}"', "Accept": "*/*"}
    try:
        status, body = opener(base + path, multipart(fields, filename, content), headers, HTTP_TIMEOUT)
    except (OSError, ValueError) as error:
        raise CertError("network", f"Couldn't reach Samsung's certificate service: {type(error).__name__}: {error}",
                        "Check the internet connection, then try again") from error
    if len(body) > MAX_BODY:
        raise CertError("samsung", "Samsung's certificate service sent an answer that was too large")
    if status != 200:
        raise samsung_error(status, body)
    return body.decode("utf-8", "replace")


def certificates(text, what):
    found = re.findall(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----", text)
    if not found:
        raise CertError("samsung", f"Samsung's answer had no {what} certificate in it")
    return "\n".join(found) + "\n"


def device_profile(text, duids):
    try:
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ET.ParseError("DTD")
        root = ET.fromstring(text)
    except ET.ParseError:
        raise CertError("samsung", "Samsung's device list (device-profile.xml) could not be read") from None
    listed = {(d.text or "").strip() for d in root.iter("TestDevice")}
    missing = [d for d in duids if d not in listed]
    if missing:
        raise CertError("samsung", f"Samsung's device list leaves out {', '.join(missing)}")
    return text


# ---- profiles.xml and passwords ---------------------------------------------------------------

def _load(path):
    path = Path(path)
    if not path.is_file():
        return ET.Element("profiles", {"active": "", "version": "3.1"})
    if path.is_symlink():
        raise CertError("profiles", f"{path} is a symbolic link; refusing to change it", "Replace it with a file")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise CertError("profiles", f"{path} has a DTD; refusing to change it", "Check the file, then try again")
    try:
        return ET.fromstring(text)
    except ET.ParseError as error:
        raise CertError("profiles", f"{path} could not be read: {error}", "Check the file, then try again") from None


def reusable_author(path):
    """(key, password attribute) of the active profile's author certificate when it is a Samsung one.

    Keeping the author certificate means apps already installed stay updatable. The password
    attribute is copied verbatim (inline ciphertext or the Keychain's .pwd path); it is never read."""
    try:
        root = _load(path)
    except (CertError, OSError):
        return None
    active = root.get("active")
    profile = next((p for p in root.iter("profile") if active and p.get("name") == active), None)
    if profile is None:
        return None
    items = {item.get("distributor"): item for item in profile.iter("profileitem")}
    author, distributor = items.get("0"), items.get("1")
    # A Samsung profile has a distributor certificate with a device list next to it.
    if author is None or distributor is None or not distributor.get("key"):
        return None
    if not (Path(distributor.get("key")).expanduser().parent / "device-profile.xml").is_file():
        return None
    key = author.get("key") or ""
    if not key or not Path(key).expanduser().is_file() or not author.get("password"):
        return None
    return key, author.get("password")


def has_profile(path):
    try:
        return any(True for _ in _load(path).iter("profile"))
    except (CertError, OSError):
        return False


def register(path, name, author, distributor):
    """Add or replace profile `name` in profiles.xml and make it active; other profiles are kept."""
    os.replace(prepare_register(path, name, author, distributor), path)


def prepare_register(path, name, author, distributor):
    """Write the new profiles.xml beside the current one; returns the file to os.replace() into place."""
    path = Path(path)
    root = _load(path)
    for old in [p for p in root.findall("profile") if p.get("name") == name]:
        root.remove(old)
    profile = ET.SubElement(root, "profile", {"name": name})
    for index, (key, password) in enumerate((author, distributor, ("", ""))):
        ET.SubElement(profile, "profileitem", {"ca": "", "distributor": str(index), "key": key,
                                               "password": password, "rootca": ""})
    root.set("active", name)
    root.set("version", root.get("version") or "3.1")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' +
                         ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
    return temporary


def keychain_store(account, password, system=None, run=subprocess.run):
    """Store the password the way Tizen Studio does on macOS. False when not possible here."""
    if (system or platform.system()) != "Darwin" or '"' in account or "\\" in account or "\n" in account:
        return False
    if not password.isalnum():
        return False
    # `security -i` reads the command from stdin, so the password never appears in a process list.
    command = f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a "{account}" -w {password}\n'
    try:
        done = run(["security", "-i"], input=command.encode("ascii"), capture_output=True, timeout=30)
        check = run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
                    capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError, UnicodeEncodeError):
        return False
    return done.returncode == 0 and check.returncode == 0


def new_password():
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(24))


def backup(folder, profiles_path, stamp):
    """Copy the existing certificate files and profiles.xml to a private, timestamped folder."""
    folder = Path(folder)
    target = folder / "backups" / stamp
    files = [f for f in folder.iterdir() if f.is_file() and not f.is_symlink()] if folder.is_dir() else []
    if Path(profiles_path).is_file():
        files.append(Path(profiles_path))
    if not files:
        return None
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    for path in files:
        shutil.copy2(path, target / path.name)
    return str(target)


def commit(folder, work, names, prepare_profiles, profiles_path):
    """Swap in the new certificate folder and profiles.xml together, or leave both as they were.

    The new folder is staged next to the old one (a copy plus the new files), then renamed into
    place; profiles.xml is replaced last. If either step fails the previous folder comes back.
    Passwords already written to the Keychain are not rolled back."""
    folder = Path(folder)
    folder.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".tv-companion-stage-", dir=folder.parent))
    old = None
    try:
        if folder.is_dir():
            shutil.copytree(folder, stage, symlinks=True, dirs_exist_ok=True)
        os.chmod(stage, 0o700)
        for name in names:
            shutil.copyfile(work / name, stage / name)
            os.chmod(stage / name, 0o600)
        profiles = prepare_profiles()
        if folder.exists():
            old = folder.with_name(stage.name.replace("-stage-", "-old-"))
            os.replace(folder, old)
        try:
            os.replace(stage, folder)
            try:
                os.replace(profiles, profiles_path)
            except BaseException:
                os.replace(folder, stage)
                raise
        except BaseException:
            if old is not None:
                os.replace(old, folder)
                old = None
            Path(profiles).unlink(missing_ok=True)
            raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)


# ---- Removing signing profiles -----------------------------------------------------------------

PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def keychain_delete(account, system=None, run=subprocess.run):
    """(state, detail) for deleting one Tizen Studio password from the macOS Keychain."""
    if (system or platform.system()) != "Darwin":
        return "not-applicable", "no Keychain on this system"
    try:
        done = run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
                   capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return "error", "security could not run: " + type(error).__name__
    if done.returncode == 0:
        return "deleted", ""
    if done.returncode == 44:  # errSecItemNotFound: already gone.
        return "absent", ""
    detail = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    return "error", "security exited " + str(done.returncode) + (": " + detail[-1][:200] if detail else "")


def reset_plan(names, *, profiles_path, cert_home):
    """What `cert reset` would remove, for exactly the named profiles. Raises CertError, changes nothing.

    A profile's certificate folder is <cert_home>/<name>; key files outside it are kept and listed.
    Refused: unsafe names, symbolic links, folders outside cert_home, and a folder another
    (kept) profile still uses, because deleting it would break that profile."""
    names = list(dict.fromkeys(names))
    if not names or not all(isinstance(n, str) and PROFILE_NAME.fullmatch(n) for n in names):
        raise CertError("invalid", "Name the signing profiles to remove exactly, e.g. demo-tv tv-companion",
                        "Use letters, digits, '.', '_' or '-'")
    cert_home = Path(cert_home)
    if cert_home.is_symlink():
        raise CertError("symlink", f"{cert_home} is a symbolic link; refusing to change it", "Replace it with a folder")
    root = _load(profiles_path)
    plan = []
    for name in names:
        entries = [p for p in root.findall("profile") if p.get("name") == name]
        folder = cert_home / name
        if folder.is_symlink():
            raise CertError("symlink", f"{folder} is a symbolic link; refusing to remove it", "Check the folder")
        if folder.exists() and (not folder.is_dir() or not _inside(folder, cert_home)):
            raise CertError("outside", f"{folder} is not a certificate folder; refusing to remove it", "Check the folder")
        keys, passwords = [], []
        for entry in entries:
            for item in entry.iter("profileitem"):
                if item.get("key"):
                    keys.append(item.get("key"))
                password = item.get("password") or ""
                if password.endswith(".pwd"):
                    passwords.append(password)
        if folder.is_dir():
            for path in folder.rglob("*"):
                if path.is_symlink():
                    raise CertError("symlink", f"{path} is a symbolic link; refusing to remove {folder}",
                                    "Check the folder")
                if path.suffix == ".pwd" and path.is_file():
                    passwords.append(str(path))
        plan.append({"profile": name, "entry": bool(entries), "active": root.get("active") == name,
                     "folder": str(folder) if folder.is_dir() else None,
                     "kept_files": sorted({k for k in keys if not _inside(Path(k).expanduser(), folder)}),
                     "keychain": sorted({p for p in passwords if _inside(Path(p).expanduser(), folder) or
                                         Path(p).expanduser().parent == folder})})
    removing = {Path(p["folder"]).resolve() for p in plan if p["folder"]}
    for other in root.findall("profile"):
        if other.get("name") in names:
            continue
        for item in other.iter("profileitem"):
            for value in (item.get("key") or "", item.get("password") or ""):
                if value and any(_inside(Path(value).expanduser(), f) for f in removing):
                    raise CertError("in-use", f"Profile {other.get('name')!r} still uses files in a folder to remove",
                                    "Reset that profile too, or keep this one")
    return plan


def reset_profiles(names, *, profiles_path, cert_home, backup_root, clock=time.time, system=None,
                   run=subprocess.run):
    """Remove exactly the named signing profiles: profiles.xml entries, certificate folders, Keychain passwords.

    Everything removed is first copied to a private, timestamped folder under backup_root (outside
    the active certificate folders). A profile that is not there is a no-op. Returns a receipt."""
    plan = reset_plan(names, profiles_path=profiles_path, cert_home=cert_home)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
    receipt = {"time": stamp, "profiles": plan, "backup": None, "keychain_errors": []}
    todo = [p for p in plan if p["entry"] or p["folder"]]
    if not todo:
        for item in plan:
            item["state"] = "not-present"
        return receipt
    profiles_path = Path(profiles_path)
    if _inside(backup_root, cert_home):
        raise CertError("backup", "The backup folder must be outside the certificate folder", "Choose another folder")
    Path(backup_root).mkdir(mode=0o700, parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="cert-reset-" + stamp + "-", dir=backup_root))
    if profiles_path.is_file():
        shutil.copy2(profiles_path, target / "profiles.xml")
    for item in todo:
        if item["folder"]:
            shutil.copytree(item["folder"], target / item["profile"], symlinks=True)
    receipt["backup"] = str(target)

    root = _load(profiles_path)
    gone = {p["profile"] for p in todo}
    for old in [p for p in root.findall("profile") if p.get("name") in gone]:
        root.remove(old)
    if root.get("active") in gone:
        root.set("active", "")
    aside = []
    try:
        for item in todo:
            if item["folder"]:
                moved = Path(tempfile.mkdtemp(prefix=".reset-", dir=Path(item["folder"]).parent))
                os.replace(item["folder"], moved / "folder")
                aside.append((moved, item["folder"]))
        if profiles_path.is_file():
            temporary = profiles_path.with_name(profiles_path.name + ".tmp")
            temporary.write_text('<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' +
                                 ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
            os.replace(temporary, profiles_path)
    except BaseException:
        for moved, folder in aside:
            os.replace(moved / "folder", folder)
            shutil.rmtree(moved, ignore_errors=True)
        raise
    for moved, _ in aside:
        shutil.rmtree(moved, ignore_errors=True)
    for item in plan:
        item["state"] = "removed" if item in todo else "not-present"
        item["keychain_result"] = {}
        for account in item["keychain"] if item in todo else []:
            state, detail = keychain_delete(account, system=system, run=run)
            item["keychain_result"][account] = state
            if state == "error":
                receipt["keychain_errors"].append(account + ": " + detail)
    (target / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


# ---- The whole job ----------------------------------------------------------------------------

def add_tvs(duids, *, profiles_path, cert_home, openssl, ca, progress, browser, signin=SignIn,
            opener=urllib_opener, clock=time.time, system=None, run=subprocess.run, timeout=SIGNIN_TIMEOUT,
            cancel=None):
    """Create or renew the "tv-companion" profile so its certificate lists `duids`.

    One Samsung sign-in covers every Device ID in `duids`. progress(step, text, done) reports each
    step; `cancel` (a threading.Event) stops the job before anything on this computer changes.
    Returns what happened; raises CertError."""
    duids = list(dict.fromkeys(duids))
    if not duids or len(duids) > MAX_DEVICES or not all(isinstance(d, str) and DUID.fullmatch(d) for d in duids):
        raise CertError("invalid", f"A certificate lists 1 to {MAX_DEVICES} valid Device IDs", "Reconnect the TV")
    if not openssl:
        raise CertError("openssl", "openssl was not found on this computer",
                        "Install Git for Windows (it includes openssl)" if (system or platform.system()) == "Windows"
                        else "Install openssl, then try again")
    if not ca:
        raise CertError("samsung-ca", "Samsung's TV certificate authority files weren't found in Tizen Studio",
                        "Install Tizen Studio's Certificate Manager with the Samsung TV extension")
    reuse = reusable_author(profiles_path)
    had_profile = has_profile(profiles_path)
    folder = Path(cert_home) / PROFILE

    def stopped():
        if cancel is not None and cancel.is_set():
            raise CertError("cancelled", "Samsung sign-in was cancelled", "Start a new sign-in")

    listener = signin()
    try:
        progress("signin", "Waiting for Samsung sign-in…", False)
        browser(SIGNIN_URL)
        account = listener.wait(timeout) if cancel is None else listener.wait(timeout, cancel=cancel)
    finally:
        listener.close()  # Port 4794 is free again whether sign-in finished, failed or was cancelled.
    stopped()
    progress("signin", "Signed in to Samsung", True)

    work = Path(tempfile.mkdtemp(prefix="tv-companion-cert-"))
    try:
        os.chmod(work, 0o700)
        fields = [("access_token", account["access_token"]), ("user_id", account["user_id"])]
        password = new_password()
        progress("request", "Requesting your certificate from Samsung…", False)
        if reuse is None:
            csr = make_request(openssl, work, "author", PROFILE)
            answer = post("/v3/authors", fields + [("platform", "VD")], "author.csr", csr, opener)
            make_p12(openssl, work, "author", certificates(answer, "author"), ca["author"], password)
        csr = make_request(openssl, work, "distributor", "TizenSDK", account["email"], duids)
        dist_fields = fields + [("privilege_level", "Public"), ("developer_type", "Individual"), ("platform", "VD")]
        profile_xml = device_profile(post("/v1/distributors", dist_fields, "distributor.csr", csr, opener), duids)
        answer = post("/v3/distributors", dist_fields, "distributor.csr", csr, opener)
        make_p12(openssl, work, "distributor", certificates(answer, "distributor"), ca["distributor"], password)
        (work / "device-profile.xml").write_text(profile_xml, encoding="utf-8")
        progress("request", "Samsung issued your certificate", True)
        stopped()  # Last point to stop: nothing on this computer has changed yet.

        saved = backup(folder, profiles_path, time.strftime("%Y%m%d-%H%M%S", time.localtime(clock())))
        if saved:
            progress("backup", "Backed up your previous certificate files", True)
        names = ["distributor.p12", "distributor.crt", "device-profile.xml"]
        if reuse is None:
            names += ["author.p12", "author.crt"]
        stored = "keychain"
        entries = {}
        for stem in (["author"] if reuse is None else []) + ["distributor"]:
            account_path = str(folder / f"{stem}.pwd")
            if keychain_store(account_path, password, system=system, run=run):
                entries[stem] = account_path
            else:
                entries[stem] = encrypt_password(openssl, password)
                stored = "profiles.xml"
        author = reuse or (str(folder / "author.p12"), entries["author"])
        commit(folder, work, names, lambda: prepare_register(
            profiles_path, PROFILE, author, (str(folder / "distributor.p12"), entries["distributor"])), profiles_path)
        progress("register", "Saved and set for installs", True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"profile": PROFILE, "folder": str(folder), "backup": saved, "new_author": reuse is None,
            "reinstall": reuse is None and had_profile, "password_storage": stored}
