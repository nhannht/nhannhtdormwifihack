"""The HTTP client: cookies, redirects, and not raising on an error status.

A local server stands in for the router, so these run with no hotspot and no
network.
"""

from __future__ import annotations

import http.server
import threading
import unittest

from dormwifi.portal_client import PortalClient, PortalResponse

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

    def do_GET(self):  # noqa: N802 - interface method
        type(self).seen.append(self.path)
        type(self).cookies_seen.append(self.headers.get("Cookie", ""))

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

    def log_message(self, *args):  # noqa: D102 - silence the test output
        pass


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _Router.seen = []
        _Router.cookies_seen = []
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

    def test_the_response_body_is_text_even_when_the_bytes_are_not(self):
        response = PortalResponse(status_code=200, final_url="http://x.test/", data=b"\xff\xfe")
        self.assertIsInstance(response.body, str)


if __name__ == "__main__":
    unittest.main()
