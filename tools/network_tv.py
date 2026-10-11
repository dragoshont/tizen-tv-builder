"""Local Samsung TV discovery. Read-only: SSDP plus port-8001 REST info. No pairing or writes."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import html
import ipaddress
import json
import re
import socket
import time

from tizen import _DeadlineConnection

SERVICES = ("urn:dial-multiscreen-org:service:dial:1",
            "urn:samsung.com:service:RemoteControlReceiver:1",
            "urn:schemas-upnp-org:device:MediaRenderer:1")
PRIVATE = tuple(ipaddress.ip_network(value) for value in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))


def local_address(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return any(address in network for network in PRIVATE)


def text(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return "unknown"
    value = html.unescape(str(value))
    return value[:80] if value and not any(ord(c) < 32 for c in value) else "unknown"


def metadata(data, address):
    if not isinstance(data, dict) or not isinstance(data.get("device"), dict):
        raise ValueError("Samsung REST device metadata is missing")
    device = data["device"]
    # A model string is not evidence of model year. Many sets omit these fields.
    developer = device.get("developerMode")
    # "model" is Samsung's platform code such as "19_MUSEL_UHD": its leading digits are the
    # model year the TV itself reports. Resolution and Frame support are reported capabilities.
    code = device.get("model")
    platform_year = (2000 + int(code[:2]) if isinstance(code, str) and re.match(r"(1[5-9]|2[0-9])_[A-Z0-9_]{2,40}$", code)
                     else None)
    resolution = device.get("resolution")
    firmware = text(device.get("firmwareVersion"))
    return {"ip": address, "name": text(device.get("name", data.get("name"))),
            "model": text(device.get("modelName")), "year": text(device.get("modelYear")),
            "platform_year": str(platform_year) if platform_year else "unknown",
            "manufacturer": "Samsung" if "samsung" in text(device.get("type", data.get("type"))).lower() else "unknown",
            "resolution": resolution if isinstance(resolution, str) and re.fullmatch(r"\d{3,4}x\d{3,4}", resolution) else "unknown",
            "frame_tv": "yes" if device.get("FrameTVSupport") in ("true", True) else "unknown",
            "os": text(device.get("OS")), "firmware": "unknown" if firmware.lower() == "unknown" else firmware,
            "developer_mode": "on" if developer in ("1", 1, True, "true") else
                              "off" if developer in ("0", 0, False, "false") else "unknown"}


def confirm(address, budget=1, identity=False):
    if not local_address(address):
        raise ValueError("Discovery accepts private local IPv4 addresses only")
    connection = _DeadlineConnection(address, 8001, time.monotonic() + budget)
    try:
        connection.request("GET", "/api/v2/")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Samsung REST confirmation refused")
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("Samsung REST metadata exceeded its limit")
        data = json.loads(raw)
        result = metadata(data, address)
        if identity:
            # Private, in-memory only: a stable key so a DHCP address change keeps the TV's name.
            device = data["device"]
            uid = next((str(device[k]) for k in ("id", "wifiMac") if isinstance(device.get(k), str)
                        and re.fullmatch(r"[A-Za-z0-9:._-]{4,80}", device[k])), None)
            result["uid"] = uid
        return result
    finally:
        connection.close()


def interfaces():
    """Private IPv4 addresses of this computer, without sending any packet."""
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    return sorted(a for a in found if local_address(a) and not a.startswith("169.254."))


def _open(host, port, timeout):
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def sweep(address, probe=_open, port=8001, timeout=.4, workers=64):
    """Find hosts answering on the Samsung TV port inside this computer's private /24."""
    if not local_address(address):
        return set()
    hosts = [str(h) for h in ipaddress.ip_network(address + "/24", strict=False).hosts() if str(h) != address]
    with ThreadPoolExecutor(workers) as pool:
        return {h for h, ok in zip(hosts, pool.map(lambda h: probe(h, port, timeout), hosts)) if ok}


def ssdp_hosts(seconds=1.5, limit=32):
    """Private hosts answering an SSDP search for TV services. Multicast may be blocked: then empty."""
    addresses = set()
    deadline = time.monotonic() + seconds
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        sent = 0
        try:
            udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            udp.bind(("", 0))
            for service in SERVICES:
                request = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                           'MAN: "ssdp:discover"\r\nMX: 1\r\nST: ' + service + "\r\n\r\n")
                try:
                    udp.sendto(request.encode("ascii"), ("239.255.255.250", 1900))
                    sent += 1
                except OSError:
                    continue
        except OSError:
            pass
        while sent and time.monotonic() < deadline and len(addresses) < limit:
            udp.settimeout(max(.01, deadline - time.monotonic()))
            try:
                packet, sender = udp.recvfrom(8192)
            except (socket.timeout, OSError):
                break
            if local_address(sender[0]) and packet.startswith(b"HTTP/1.1 200"):
                addresses.add(sender[0])
    return addresses


def discover(seconds=3, confirmer=confirm, interfaces=interfaces, sweeper=sweep, searcher=None):
    """SSDP when multicast works, plus a subnet sweep so blocked multicast never hides a TV."""
    searcher = searcher or (lambda: ssdp_hosts(seconds, limit=12))
    addresses = set()
    local = interfaces()
    with ThreadPoolExecutor(max(1, len(local))) as pool:
        sweeps = [pool.submit(sweeper, address) for address in local]
        try:
            addresses.update(searcher())
        except OSError:
            pass
        for job in sweeps:
            try:
                addresses.update(job.result())
            except OSError:
                pass
    devices, unavailable = [], 0
    for address in sorted(addresses, key=ipaddress.IPv4Address)[:32]:
        try:
            devices.append(confirmer(address))
        except (OSError, ValueError, http.client.HTTPException):
            unavailable += 1
    return {"devices": devices, "unconfirmed": unavailable}
