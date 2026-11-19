"""The HTTP client: cookies, redirects, and not raising on an error status.

A local server stands in for the router, so these run with no hotspot and no
network.
"""

from __future__ import annotations

import http.server
import threading
import unittest
import urllib.request

from dormwifi.portal_client import PortalClient, PortalResponse
from dormwifi.portal_dns import GatewayResolver

LOGIN_PAGE = (
    '<html><body><form>'
    '<input type="hidden" id="chap-id" value="\\362">'
    '<input name="username" value="">'
    "</form></body></html>"
)


class _Router(http.server.BaseHTTPRequestHandler):
    """A hotspot's two most important answers."""

    seen: list[str] = []
    cookies_seen: list[str] = []
    hosts_seen: list[str] = []

    def do_GET(self):  # noqa: N802 - interface method
        type(self).seen.append(self.path)
        type(self).cookies_seen.append(self.headers.get("Cookie", ""))
        type(self).hosts_seen.append(self.headers.get("Host", ""))

        if self.path.startswith("/status"):
            # What this hotspot really does when a session has lapsed.
            self._redirect("/login?dst=http%3A%2F%2Fcaptive.apple.com")
        elif self.path.startswith("/login"):
            self._reply(200, LOGIN_PAGE, extra=(("Set-Cookie", "session=abc; Path=/"),))
        elif self.path.startswith("/boom"):
            self._reply(500, "oops")
        elif self.path.startswith("/loop"):
            self._redirect("/loop")
        else:
            self._reply(404, "not here")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _reply(self, status: int, body: str, extra: tuple[tuple[str, str], ...] = ()) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for field, value in extra:
            self.send_header(field, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802 - interface method
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self._reply(200, f'{self.headers.get("Content-Type", "")}|{body.decode("utf-8")}')

    def log_message(self, format, *args):  # noqa: D102 - silence the test output
        pass


class _PinTo(GatewayResolver):
    """Sends every name to one address, so a local server can stand behind a name.

    The policy of which answers are usable lives in the resolver and is tested in
    ``test_portal_dns``; this only exercises what the client does with an answer.
    """

    def __init__(self, address: str) -> None:
        super().__init__("192.0.2.1")
        self.address = address
        self.asked: list[str] = []

    def address_for(self, host: str) -> str | None:
        self.asked.append(host)
        return self.address


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _Router.seen = []
        _Router.cookies_seen = []
        _Router.hosts_seen = []
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Router)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def test_a_redirect_is_followed_and_the_landing_page_is_returned(self):
        # This hotspot answers /status with 302 /login as soon as the session
        # lapses. Not following it made every signed-out state read as "the
        # router did not answer", which is the wrong diagnosis and hides the
        # login page that was one hop away.
        response = PortalClient().get(f"{self.base}/status")

        self.assertEqual(response.status_code, 200)
        self.assertIn("/login", response.final_url)
        self.assertIn("chap-id", response.body)

    def test_an_error_status_is_returned_rather_than_raised(self):
        # The login sequence reads status codes and names them; a raised
        # exception arrives as an unexplained transport failure instead.
        response = PortalClient().get(f"{self.base}/boom")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.body, "oops")
        self.assertFalse(response.is_success)

    def test_a_missing_page_is_returned_too(self):
        response = PortalClient().get(f"{self.base}/nothing-here")
        self.assertEqual(response.status_code, 404)

    def test_a_redirect_loop_fails_instead_of_spinning(self):
        response = PortalClient().get(f"{self.base}/loop", timeout=5)
        self.assertFalse(response.is_success)

    def test_cookies_from_the_first_visit_are_sent_on_the_next(self):
        # The portal's later calls depend on the cookie its landing page set.
        client = PortalClient()
        client.get(f"{self.base}/login")
        _Router.cookies_seen.clear()
        client.get(f"{self.base}/login")

        self.assertTrue(
            any("session=abc" in cookie for cookie in _Router.cookies_seen),
            _Router.cookies_seen,
        )

    def test_a_post_keeps_its_json_body_and_method(self):
        response = PortalClient().post_json(
            f"{self.base}/login", {"a": 1}, referer=None, origin=None
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.body)
        self.assertTrue(response.body.endswith('{"a":1}'))

    def test_a_form_post_is_urlencoded_with_plus_for_spaces(self):
        response = PortalClient().post_form(
            f"{self.base}/login", [("q", "a b")], referer=None, origin=None
        )
        self.assertIn("application/x-www-form-urlencoded", response.body)
        self.assertTrue(response.body.endswith("q=a+b"))

    def test_the_portal_is_reached_at_the_address_the_router_gave(self):
        # The case this exists for: the machine's own resolver is a DNS server it
        # can no longer reach, and the router inside the walled garden is the only
        # one that can say where the portal lives. The name still has to be the
        # name — `Host`, SNI and certificate checks all see it, not the address.
        if urllib.request.getproxies().get("http"):
            self.skipTest("a proxy is configured, so the connection opens to it")

        resolver = _PinTo("127.0.0.1")
        _Router.hosts_seen.clear()
        host = f"portal.invalid:{self.server.server_port}"

        response = PortalClient(resolver=resolver).get(f"http://{host}/status")

        self.assertEqual(response.status_code, 200)
        self.assertIn("chap-id", response.body)
        self.assertEqual(_Router.hosts_seen[0], host)
        self.assertEqual(resolver.asked, ["portal.invalid", "portal.invalid"])

    def test_a_name_the_router_cannot_place_is_left_to_the_system(self):
        # The pin is not a rewrite of every request: an answer the router does not
        # give leaves the ordinary resolver path alone, which for a reserved name
        # is a real failure rather than a connection to somewhere else.
        if urllib.request.getproxies().get("http"):
            self.skipTest("a proxy is configured, so the connection opens to it")

        resolver = GatewayResolver("192.0.2.1", query=lambda server, name, timeout=None: [])
        with self.assertRaises(OSError):
            PortalClient(resolver=resolver).get("http://portal.invalid:1/status", timeout=2)

    def test_the_response_body_is_text_even_when_the_bytes_are_not(self):
        response = PortalResponse(status_code=200, final_url="http://x.test/", data=b"\xff\xfe")
        self.assertIsInstance(response.body, str)


if __name__ == "__main__":
    unittest.main()
