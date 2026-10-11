"""Bounded local Samsung discovery. No pairing, device writes or LOCATION fetch."""
import http.client
import ipaddress
import json
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
    value = str(value)
    return value[:80] if value and not any(ord(c) < 32 for c in value) else "unknown"


def metadata(data, address):
    if not isinstance(data, dict) or not isinstance(data.get("device"), dict):
        raise ValueError("Samsung REST device metadata is missing")
    device = data["device"]
    # A model string is not evidence of model year. Many sets omit these fields.
    developer = device.get("developerMode")
    return {"ip": address, "name": text(device.get("name", data.get("name"))),
            "model": text(device.get("modelName")), "year": text(device.get("modelYear")),
            "os": text(device.get("OS")), "firmware": text(device.get("firmwareVersion")),
            "developer_mode": "on" if developer in ("1", 1, True, "true") else
                              "off" if developer in ("0", 0, False, "false") else "unknown"}


def confirm(address, budget=1):
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
        return metadata(json.loads(raw), address)
    finally:
        connection.close()


def discover(seconds=3, confirmer=confirm):
    addresses = set()
    deadline = time.monotonic() + seconds
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        udp.bind(("", 0))
        for service in SERVICES:
            request = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                       'MAN: "ssdp:discover"\r\nMX: 1\r\nST: ' + service + "\r\n\r\n")
            udp.sendto(request.encode("ascii"), ("239.255.255.250", 1900))
        while time.monotonic() < deadline and len(addresses) < 12:
            udp.settimeout(max(.01, deadline - time.monotonic()))
            try:
                packet, sender = udp.recvfrom(8192)
            except socket.timeout:
                break
            if local_address(sender[0]) and packet.startswith(b"HTTP/1.1 200"):
                addresses.add(sender[0])
    devices, unavailable = [], 0
    for address in sorted(addresses):
        try:
            devices.append(confirmer(address))
        except (OSError, ValueError, http.client.HTTPException):
            unavailable += 1
    return {"devices": devices, "unconfirmed": unavailable}
