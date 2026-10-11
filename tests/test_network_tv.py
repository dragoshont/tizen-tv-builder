import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import network_tv


class NetworkTests(unittest.TestCase):
    def test_only_local_private_ipv4_is_confirmed(self):
        for address in ["127.0.0.1", "8.8.8.8", "::1", "0.0.0.0", "239.255.255.250"]:
            self.assertFalse(network_tv.local_address(address))
            with self.assertRaises(ValueError):
                network_tv.confirm(address)
        self.assertTrue(network_tv.local_address("192.168.0.10"))

    def test_fields_are_observed_not_guessed_and_sensitive_data_not_returned(self):
        result = network_tv.metadata({"device": {"name": "Test TV", "modelName": "UE55MU6172",
                                                "duid": "PRIVATE-DUID", "token": "PRIVATE-TOKEN"}}, "192.168.0.10")
        self.assertEqual(result["year"], "unknown")
        self.assertEqual(result["developer_mode"], "unknown")
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(network_tv.metadata({"device": {"developerMode": "0"}}, "192.168.0.10")["developer_mode"], "off")

    def test_api_duid_is_never_used_as_identity_or_certificate_duid(self):
        # The 8001 "duid" is a SmartThings UUID, not the certificate DUID (that comes only from getduid).
        result = network_tv.metadata({"id": "uuid:fixture-id", "device": {"duid": "uuid:SMARTTHINGS-FIXTURE",
                                      "modelName": "UE43RU7092UXXH"}}, "192.168.0.10")
        self.assertNotIn("SMARTTHINGS-FIXTURE", repr(result))
        self.assertNotIn("duid", result)
        self.assertNotIn("uid", result)

    def test_html_entities_in_reported_names_are_decoded_as_text(self):
        result = network_tv.metadata({"device": {"name": "55&quot; Neo QLED &lt;b&gt;"}}, "192.168.0.10")
        self.assertEqual(result["name"], '55" Neo QLED <b>')

    def test_discovery_uses_sender_address_not_advertised_location(self):
        class UDP:
            def __init__(self, *args):
                self.sent = []
                self.received = False
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def setsockopt(self, *args):
                pass
            def bind(self, address):
                pass
            def settimeout(self, timeout):
                pass
            def sendto(self, body, address):
                self.sent.append((body, address))
            def recvfrom(self, size):
                if self.received:
                    raise network_tv.socket.timeout()
                self.received = True
                return b"HTTP/1.1 200 OK\r\nLOCATION: http://8.8.8.8/private\r\n", ("192.168.0.10", 1900)
        udp = UDP()
        with patch.object(network_tv.socket, "socket", return_value=udp):
            called = []
            def confirmer(address):
                called.append(address)
                return {"ip": address}
            result = network_tv.discover(confirmer=confirmer, interfaces=lambda: [], sweeper=lambda ip: set())
        self.assertEqual(called, ["192.168.0.10"])
        self.assertEqual(len(udp.sent), 3)
        self.assertEqual(result["devices"], [{"ip": "192.168.0.10"}])

    def test_blocked_multicast_still_finds_tvs_by_subnet_sweep(self):
        class Blocked:
            def __init__(self, *args):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def setsockopt(self, *args):
                pass
            def bind(self, address):
                pass
            def settimeout(self, timeout):
                pass
            def sendto(self, body, address):
                raise OSError(65, "No route to host")
            def recvfrom(self, size):
                raise network_tv.socket.timeout()
        swept = []
        with patch.object(network_tv.socket, "socket", Blocked):
            result = network_tv.discover(confirmer=lambda ip: {"ip": ip}, interfaces=lambda: ["192.168.0.5"],
                                         sweeper=lambda ip: swept.append(ip) or {"192.168.0.10"})
        self.assertEqual(swept, ["192.168.0.5"])
        self.assertEqual(result["devices"], [{"ip": "192.168.0.10"}])

    def test_sweep_stays_inside_the_private_slash_24(self):
        probed = []
        def probe(host, port, timeout):
            probed.append(host)
            return host == "192.168.0.10"
        found = network_tv.sweep("192.168.0.5", probe=probe)
        self.assertEqual(found, {"192.168.0.10"})
        self.assertEqual(len(probed), 253)
        self.assertTrue(all(h.startswith("192.168.0.") for h in probed))
        self.assertEqual(network_tv.sweep("8.8.8.8", probe=probe), set())
