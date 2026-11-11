"""Working out where to send the login request, and how to encode a form."""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlsplit

from dormwifi.activity_log import ActivityLog
from dormwifi.login_engine import EngineError, LoginEngine
from dormwifi.portal_client import form_encoded, is_private_host
from dormwifi.settings import Settings


def engine(gateway: str = "10.5.50.1", fallback: str = "") -> LoginEngine:
    settings = Settings()
    settings.gateway_ip = gateway
    settings.portal_fallback_url = fallback
    return LoginEngine(settings, ActivityLog())


class PortalAddressTests(unittest.TestCase):
    def test_an_absolute_form_action_is_used_as_given(self):
        html = '<form action="http://portal.example.test/login"><input name="a" value="1"></form>'
        url = engine().portal_url_from(html)
        parts = urlsplit(url)
        self.assertEqual(parts.hostname, "portal.example.test")
        self.assertEqual(parts.path, "/login")
        self.assertIn("a=1", parts.query)

    def test_a_relative_form_action_belongs_to_the_router(self):
        html = '<form action="/login"><input name="a" value="1"></form>'
        url = engine(gateway="10.5.50.1").portal_url_from(html)
        self.assertTrue(url.startswith("http://10.5.50.1/login"))

    def test_with_no_form_action_the_configured_fallback_is_used(self):
        html = '<input name="a" value="1">'
        url = engine(fallback="http://portal.example.test/login").portal_url_from(html)
        self.assertEqual(urlsplit(url).hostname, "portal.example.test")
        self.assertIn("a=1", urlsplit(url).query)

    def test_with_no_form_action_and_no_fallback_say_so(self):
        # The Rust version silently substituted a hardcoded host here, so a
        # misconfigured setup looked like a network failure instead.
        with self.assertRaises(EngineError):
            engine().portal_url_from('<input name="a" value="1">')

    def test_hidden_fields_are_carried_across(self):
        html = """
            <form action="http://portal.example.test/login">
              <input name="dst" value="http://captive.apple.com/hotspot-detect.html">
              <input id="chap-id" value="\\044">
              <input name="username" value="">
            </form>
            """
        query = urlsplit(engine().portal_url_from(html)).query
        self.assertIn("dst=", query)
        self.assertIn("chap-id=", query)
        # Empty values are not worth forwarding.
        self.assertNotIn("username=", query)

    def test_a_bare_relative_action_resolves_against_the_login_page(self):
        # MikroTik templates use actions like this. Treating anything without a
        # leading slash as absolute produced a URL with no scheme at all.
        html = '<form action="alogin.html"><input name="a" value="1"></form>'
        parts = urlsplit(engine(gateway="10.5.50.1").portal_url_from(html))
        self.assertEqual(parts.scheme, "http")
        self.assertEqual(parts.hostname, "10.5.50.1")
        self.assertEqual(parts.path, "/alogin.html")

    def test_a_protocol_relative_action_keeps_its_own_host(self):
        html = '<form action="//portal.example.test/login"><input name="a" value="1"></form>'
        parts = urlsplit(engine(gateway="10.5.50.1").portal_url_from(html))
        self.assertEqual(parts.scheme, "http")
        self.assertEqual(parts.hostname, "portal.example.test")
        self.assertEqual(parts.path, "/login")

    def test_an_existing_query_is_kept(self):
        html = (
            '<form action="http://portal.example.test/login?tenant=7">'
            '<input name="a" value="1"></form>'
        )
        query = parse_qs(urlsplit(engine().portal_url_from(html)).query)
        self.assertEqual(query["tenant"], ["7"])
        self.assertEqual(query["a"], ["1"])

    def test_the_chap_challenge_is_encoded_but_survives_decoding(self):
        from dormwifi import chap

        html = '<form action="http://portal.example.test/login"><input id="chap-id" value="\\044"></form>'
        query = parse_qs(urlsplit(engine().portal_url_from(html)).query)
        self.assertEqual(chap.decode(query["chap-id"][0]), bytes([36]))


class AuthenticationFormTests(unittest.TestCase):
    def test_the_form_is_found_at_each_of_the_three_depths(self):
        form = "<input name='username' value='u'>"
        depths = (
            {"captiveContextDTO": {"captiveContext": {"contentAuthenForm": form}}},
            {"captiveContext": {"contentAuthenForm": form}},
            {"contentAuthenForm": form},
        )
        for payload in depths:
            with self.subTest(payload=list(payload)):
                self.assertEqual(engine().authentication_form(payload), form)

    def test_a_missing_or_empty_form_reads_as_absent(self):
        subject = engine()
        self.assertIsNone(subject.authentication_form({}))
        self.assertIsNone(subject.authentication_form({"contentAuthenForm": ""}))
        self.assertIsNone(subject.authentication_form({"contentAuthenForm": None}))


class FormEncodingTests(unittest.TestCase):
    def test_spaces_become_plus_signs(self):
        self.assertEqual(form_encoded([("q", "hello world")]), "q=hello+world")

    def test_reserved_characters_are_escaped(self):
        encoded = form_encoded([("dst", "http://x.test/a?b=1&c=2")])
        self.assertIn("%3A%2F%2F", encoded)
        self.assertIn("%26", encoded)

    def test_fields_are_joined_in_order(self):
        self.assertEqual(form_encoded([("a", "1"), ("b", "2")]), "a=1&b=2")

    def test_an_empty_value_still_produces_a_field(self):
        self.assertEqual(form_encoded([("username", "")]), "username=")


class PrivateHostTests(unittest.TestCase):
    def test_private_ranges_are_recognised(self):
        for host in (
            "192.168.1.1",
            "10.5.50.1",
            "172.16.0.1",
            "172.31.255.255",
            "127.0.0.1",
            "169.254.1.1",
            "localhost",
        ):
            with self.subTest(host=host):
                self.assertTrue(is_private_host(host))

    def test_public_addresses_and_names_are_not(self):
        for host in (
            "8.8.8.8",
            "1.1.1.1",
            "172.32.0.1",
            "portal.example.test",
            "captive.apple.com",
            "10.0.0.1.evil.com",  # the four-octet trick the Mac build fixed
            "0x0a.0.0.1",
            "256.1.1.1",
        ):
            with self.subTest(host=host):
                self.assertFalse(is_private_host(host))


if __name__ == "__main__":
    unittest.main()
