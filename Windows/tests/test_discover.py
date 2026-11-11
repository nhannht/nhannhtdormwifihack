"""Reading a hotspot login page for the portal's address."""

from __future__ import annotations

import unittest

from dormwifi import discover

#: The shape this hotspot really serves: no form action, a MikroTik
#: `link-login-only` that points at the router itself, and the advert portal
#: named only in a script — which is exactly the case that needs the setting.
PAGE_WITH_SCRIPT_PORTAL = r"""
<html><head><title>hotspot login</title><script src="/md5.js"></script></head>
<body>
<form id="authForm" style="display:none;">
  <input type="hidden" id="chap-id" value="\362">
  <input type="hidden" id="chap-challenge" value="\217\160\360\062\345\151\304\161\311\202\001\310\142\167\155\307">
  <input type="hidden" id="link-login-only" value="http://192.168.200.1/login">
  <input type="hidden" id="userurl" value="http://www.msftconnecttest.com/redirect">
</form>
<script>setTimeout(function(){ location.href = 'http://portal.inet.vn/welcome?session=12'; }, 2000);</script>
</body></html>
"""

#: A page that names its portal in the form action, so nothing has to be set.
PAGE_WITH_FORM_ACTION = r"""
<html><body>
<form action="http://portal.example.test/login" method="post">
  <input type="hidden" id="chap-id" value="\044">
  <input type="hidden" id="chap-challenge" value="\154\263\277\262">
</form>
</body></html>
"""

#: A normal router, not a hotspot.
PAGE_NOT_A_HOTSPOT = """
<html><head><title>Router admin</title></head><body>
<form action="/cgi-bin/login"><input name="password" type="password"></form>
</body></html>
"""


class InspectTests(unittest.TestCase):
    def test_a_portal_named_only_in_a_script_is_found(self):
        finding = discover.inspect(PAGE_WITH_SCRIPT_PORTAL, "192.168.200.1")

        self.assertTrue(finding.looks_like_login_page)
        self.assertTrue(finding.chap_id)
        self.assertEqual(finding.challenge_bytes, 16)
        self.assertIsNone(finding.form_action)
        self.assertEqual(finding.candidates, ["http://portal.inet.vn"])

    def test_the_routers_own_urls_are_not_candidates(self):
        # `link-login-only` and the asset script point at the router; offering
        # them as the portal would set a setting that can never work.
        finding = discover.inspect(PAGE_WITH_SCRIPT_PORTAL, "192.168.200.1")
        self.assertNotIn("http://192.168.200.1", finding.candidates)
        self.assertNotIn("http://192.168.200.1", [root for root in finding.candidates])

    def test_a_form_action_is_reported_as_the_answer(self):
        finding = discover.inspect(PAGE_WITH_FORM_ACTION, "192.168.200.1")
        self.assertEqual(finding.form_action, "http://portal.example.test/login")
        self.assertEqual(finding.candidates, ["http://portal.example.test"])

    def test_a_page_with_no_hotspot_markers_is_said_to_be_something_else(self):
        finding = discover.inspect(PAGE_NOT_A_HOTSPOT, "192.168.1.1")
        self.assertFalse(finding.looks_like_login_page)
        text = "\n".join(discover.render(finding, "192.168.1.1"))
        self.assertIn("does not look like a hotspot login page", text)

    def test_a_malformed_challenge_is_named_rather_than_guessed_at(self):
        page = '<input id="chap-id" value="\\044"><input id="chap-challenge" value="\\888">'
        finding = discover.inspect(page, "10.0.0.1")
        self.assertTrue(finding.chap_id)
        self.assertIsNone(finding.challenge_bytes)
        self.assertIn("malformed", finding.unreachable_challenge or "")

    def test_a_page_with_no_chap_fields_says_so(self):
        page = '<form><input name="username"></form>'
        finding = discover.inspect(page, "10.0.0.1")
        self.assertFalse(finding.chap_id)
        self.assertIn("no chap-id", finding.unreachable_challenge or "")

    def test_two_urls_on_one_host_become_one_candidate(self):
        page = (
            "<script src='http://portal.inet.vn/a.js'></script>"
            "<a href='http://portal.inet.vn/welcome'>go</a>"
        )
        finding = discover.inspect(page, "192.168.200.1")
        self.assertEqual(finding.candidates, ["http://portal.inet.vn"])


class LoginPageTests(unittest.TestCase):
    """`discover` must read a page that names no portal — that is its whole job."""

    def scripted_client(self, body: str, status: int = 200):
        from dormwifi.portal_client import PortalResponse

        class Client:
            def get(self, url, headers=None, timeout=None):  # noqa: ARG002
                return PortalResponse(status_code=status, final_url=url, data=body.encode())

        return Client()

    def engine(self):
        from dormwifi.activity_log import ActivityLog
        from dormwifi.login_engine import LoginEngine
        from dormwifi.settings import Settings

        return LoginEngine(Settings(gateway_ip="192.168.200.1"), ActivityLog())

    def test_a_page_naming_no_portal_is_returned_instead_of_raising(self):
        # The bug this covers: discover asked the engine for the gateway, the
        # engine resolved the portal, and resolving it raised — so the command
        # died on the one page it exists to explain.
        page = self.engine().fetch_login_page(self.scripted_client(PAGE_WITH_SCRIPT_PORTAL))
        self.assertIn("chap-id", page.body)

        finding = discover.inspect(page.body, "192.168.200.1")
        self.assertEqual(finding.candidates, ["http://portal.inet.vn"])

    def test_a_page_that_is_not_a_hotspot_still_raises(self):
        from dormwifi.login_engine import EngineError

        with self.assertRaises(EngineError):
            self.engine().fetch_login_page(self.scripted_client(PAGE_NOT_A_HOTSPOT))

    def test_reading_the_whole_gateway_still_resolves_the_portal(self):
        gateway = self.engine().fetch_gateway(self.scripted_client(PAGE_WITH_FORM_ACTION))
        self.assertEqual(gateway.portal_url.split("?")[0], "http://portal.example.test/login")


class RenderTests(unittest.TestCase):
    def test_the_report_prints_the_command_that_sets_the_address(self):
        finding = discover.inspect(PAGE_WITH_SCRIPT_PORTAL, "192.168.200.1")
        text = "\n".join(discover.render(finding, "192.168.200.1"))
        self.assertIn('dormwifi config set portal_fallback_url "http://portal.inet.vn"', text)
        self.assertIn("present, 16 bytes", text)

    def test_a_page_naming_no_portal_sends_the_person_to_the_browser(self):
        page = '<input id="chap-id" value="\\044"><input id="chap-challenge" value="\\001">'
        finding = discover.inspect(page, "10.0.0.1")
        text = "\n".join(discover.render(finding, "10.0.0.1"))
        self.assertIn("No portal address appears anywhere on the page", text)
        self.assertIn("copy the address", text)

    def test_a_form_action_is_shown_rather_than_a_suggestion(self):
        finding = discover.inspect(PAGE_WITH_FORM_ACTION, "192.168.200.1")
        text = "\n".join(discover.render(finding, "192.168.200.1"))
        self.assertIn("Form posts: http://portal.example.test/login", text)


if __name__ == "__main__":
    unittest.main()
