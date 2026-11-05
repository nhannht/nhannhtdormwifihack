"""The order the login steps go out in.

The bug this guards against was entirely about which request went first: asking
``/login`` while a session is open stalls until the timeout, the caller reads the
stall as a failed login, rejoins the wireless network, and the loop starts again.

A scripted client stands in for the router, so nothing here touches the network.
"""

from __future__ import annotations

import unittest
from urllib.parse import urlsplit

from dormwifi.activity_log import ActivityLog
from dormwifi.login_engine import LoginEngine
from dormwifi.portal_client import PortalResponse
from dormwifi.settings import Settings

STATUS_PAGE = """
<html><head><title>Status - INET</title></head>
<body><table>
<tr><td>Địa chỉ IP</td><td>192.168.0.42</td></tr>
<tr><td>Địa chỉ MAC</td><td>00:00:5E:00:53:2A</td></tr>
<tr><td>Thời gian còn lại</td><td>12m25s</td></tr>
</table></body></html>
"""


class ScriptedRouter:
    """Answers from a script, and records the order the paths arrived in."""

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.asked: list[str] = []

    def _answer(self, url: str) -> PortalResponse:
        path = urlsplit(url).path
        self.asked.append(path)
        body = self.replies.get(path)
        if body is None:
            raise TimeoutError("timed out")
        return PortalResponse(status_code=200, final_url=url, data=body.encode("utf-8"))

    def get(self, url, headers=None, timeout=None):  # noqa: ARG002 - interface match
        return self._answer(url)

    def post_json(self, url, body, referer, origin, headers=None, timeout=None):  # noqa: ARG002
        return self._answer(url)

    def post_form(self, url, fields, referer, origin, headers=None, timeout=None):  # noqa: ARG002
        return self._answer(url)


def settings() -> Settings:
    subject = Settings()
    subject.gateway_ip = "192.168.200.1"
    subject.auto_connect = False
    return subject


class LoginOrderTests(unittest.TestCase):
    def test_an_open_session_is_left_alone_and_login_is_never_touched(self):
        router = ScriptedRouter({"/status": STATUS_PAGE})

        result = LoginEngine(settings(), ActivityLog()).run(router)

        self.assertEqual(result.kind, "alreadyActive")
        self.assertTrue(result.is_connected)
        self.assertEqual(router.asked, ["/status"])
        self.assertNotIn("/login", router.asked)

    def test_a_silent_router_is_the_wrong_network_not_a_failed_login(self):
        router = ScriptedRouter({})

        result = LoginEngine(settings(), ActivityLog()).run(router)

        self.assertFalse(result.is_connected)
        self.assertEqual(router.asked, ["/status"])
        self.assertNotIn("/login", router.asked)

    def test_an_account_in_the_settings_never_touches_the_portal(self):
        # On a hotspot that intercepts every request and resets TLS while
        # captive, the portal cannot be reached from the side that needs it. An
        # account typed in once has to be enough: the router gets the CHAP hash
        # and nothing else is asked of anyone.
        subject = settings()
        subject.username = "qgv785141"
        subject.password = "qgv785141"

        router = ScriptedRouter(
            {
                "/status": "<html><body>Signed out</body></html>",
                "/login": '<form><input id="chap-id" value="\\362">'
                          '<input id="chap-challenge" value="\\001"></form>',
            }
        )
        result = LoginEngine(subject, ActivityLog()).run(router)

        # The outcome, not just the traffic: an earlier version of this test
        # checked only which URLs were touched, so it passed while the login was
        # in fact failing on a demand for the portal's address.
        self.assertTrue(result.is_connected, result.summary)
        self.assertIn("/login", router.asked)
        self.assertTrue(all(path in ("/status", "/login") for path in router.asked), router.asked)

    def test_once_the_session_has_lapsed_login_is_asked_for(self):
        router = ScriptedRouter(
            {
                "/status": "<html><body>Signed out</body></html>",
                "/login": '<form><input id="chap-id" value="\\362"></form>',
            }
        )

        LoginEngine(settings(), ActivityLog()).run(router)

        self.assertEqual(router.asked[0], "/status")
        self.assertIn("/login", router.asked)


if __name__ == "__main__":
    unittest.main()
