import http.server
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flows
from live_tv import LiveError
import workbench


class Inspector:
    """Loopback /json/list fixture whose answers change per request (engine still starting)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = 0
        fixture = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                fixture.requests += 1
                body = json.dumps(fixture.answers[min(fixture.requests, len(fixture.answers)) - 1]).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class PageDiscoveryTests(unittest.TestCase):
    def test_empty_list_explains_what_was_seen_without_blaming_other_inspectors(self):
        inspector = Inspector([[{"type": "other", "id": "x"}, {"type": "page", "id": "bad id:1"}]])
        self.addCleanup(inspector.close)
        with self.assertRaises(LiveError) as caught:
            workbench.pages(inspector.port)
        message = str(caught.exception)
        self.assertNotIn("competing", message)
        self.assertIn("other", message)
        self.assertIn("1 page with an unusable id", message)

    def test_wait_retries_while_the_engine_registers_its_page(self):
        inspector = Inspector([[], [{"type": "other", "id": "w"}], [{"type": "page", "id": "PAGE-1"}]])
        self.addCleanup(inspector.close)
        clock = Clock()
        self.assertEqual(flows.wait_pages(workbench.pages, inspector.port, clock=clock, sleep=clock.sleep),
                         ["PAGE-1"])
        self.assertEqual(inspector.requests, 3)

    def test_wait_is_bounded_and_reports_the_last_observation(self):
        inspector = Inspector([[]])
        self.addCleanup(inspector.close)
        clock = Clock()
        with self.assertRaises(LiveError) as caught:
            flows.wait_pages(workbench.pages, inspector.port, seconds=2, clock=clock, sleep=clock.sleep)
        self.assertEqual(caught.exception.code, "discovery")
        self.assertIn("2 s", str(caught.exception))
        self.assertIn("empty", str(caught.exception))
        self.assertLessEqual(inspector.requests, 10)

    def test_wait_does_not_retry_other_failures(self):
        calls = []

        def refused(port):
            calls.append(port)
            raise LiveError("limit", "Inspector discovery exceeded its limit")
        clock = Clock()
        with self.assertRaises(LiveError):
            flows.wait_pages(refused, 1, clock=clock, sleep=clock.sleep)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
