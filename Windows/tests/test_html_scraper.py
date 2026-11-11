"""Reading the two pages the login sequence depends on."""

from __future__ import annotations

import unittest

from dormwifi import html_scraper as scraper

#: A MikroTik hotspot login page, in the shape the router actually serves: one
#: form, the CHAP fields carried by id rather than name, and the empty credential
#: boxes the user would normally type into.
GATEWAY_PAGE = """
<html><head><title>hotspot login</title></head>
<body>
<form name="login" action="http://portal.example.test/login" method="post">
  <input type="hidden" name="dst" value="http://captive.apple.com/hotspot-detect.html" />
  <input type="hidden" name="popup" value="true" />
  <input type="hidden" id="chap-id" value="\\044" />
  <input type="hidden" id="chap-challenge" value="\\154\\263\\277\\262" />
  <input type="hidden" name="link-login-only" value="http://10.5.50.1/login" />
  <input name="username" type="text" value="" />
  <input name="password" type="password" />
</form>
</body></html>
"""

#: What the portal returns inside its JSON: a bare fragment, single quoted,
#: carrying the account to use.
AUTHEN_FORM = """
<form name='sendin' action='http://10.5.50.1/login' method='post'>
  <input type='hidden' name='username' value='pt-9f2c1a'>
  <input type='hidden' name='password' value='6bd41f0e'>
  <input type='hidden' name='dst' value='http://captive.apple.com/hotspot-detect.html'>
  <input type='hidden' name='popup' value='false'>
</form>
"""

#: The shape of the page this hotspot actually serves, captured from the live
#: router at the moment a session lapsed. Not one input carries a name
#: attribute, and the form has no action.
REAL_LOGIN_PAGE = """
<html><head><title>hotspot login</title>
<script src="/md5.js"></script></head><body>
<form id="authForm" style="display:none;">
  <input type="hidden" id="serial"         value="00-00-5E-00-53-00">
  <input type="hidden" id="client_mac"     value="00:00:5E:00:53:2A">
  <input type="hidden" id="client_ip"      value="192.168.0.42">
  <input type="hidden" id="userurl"        value="http://www.msftconnecttest.com/redirect">
  <input type="hidden" id="login_url"      value="http://192.168.0.1/login">
  <input type="hidden" id="chap-id"        value="\\362">
  <input type="hidden" id="chap-challenge" value="\\217\\160\\360\\062\\345\\151\\304\\161\\311\\202\\001\\310\\142\\167\\155\\307">
</form>
<script>setTimeout(function(){ msg("Đang chuyển hướng..."); }, 2000);</script>
</body></html>
"""


class InputTests(unittest.TestCase):
    def test_every_input_is_found(self):
        self.assertEqual(len(scraper.inputs_in(GATEWAY_PAGE)), 7)

    def test_chap_fields_found_by_id(self):
        self.assertEqual(scraper.value_identified_by("chap-id", GATEWAY_PAGE), "\\044")
        self.assertEqual(
            scraper.value_identified_by("chap-challenge", GATEWAY_PAGE),
            "\\154\\263\\277\\262",
        )

    def test_id_lookup_falls_back_to_name(self):
        page = '<input name="chap-id" value="\\044">'
        self.assertEqual(scraper.value_identified_by("chap-id", page), "\\044")

    def test_single_quoted_attributes(self):
        self.assertEqual(scraper.value_named("username", AUTHEN_FORM), "pt-9f2c1a")
        self.assertEqual(scraper.value_named("password", AUTHEN_FORM), "6bd41f0e")
        self.assertEqual(scraper.value_named("popup", AUTHEN_FORM), "false")

    def test_unquoted_attributes(self):
        self.assertEqual(
            scraper.value_named("popup", "<input name=popup value=true>"), "true"
        )


class QueryItemTests(unittest.TestCase):
    def test_empty_values_are_skipped(self):
        names = [name for name, _ in scraper.query_items(GATEWAY_PAGE)]
        self.assertIn("dst", names)
        self.assertIn("chap-id", names)
        self.assertIn("chap-challenge", names)
        # username has an empty value and password has none at all.
        self.assertNotIn("username", names)
        self.assertNotIn("password", names)

    def test_a_field_with_no_name_is_keyed_by_its_id(self):
        items = dict(scraper.query_items(GATEWAY_PAGE))
        self.assertEqual(items["chap-id"], "\\044")

    def test_document_order_is_kept(self):
        names = [name for name, _ in scraper.query_items(GATEWAY_PAGE)]
        self.assertEqual(names[0], "dst")

    def test_repeated_key_keeps_first_position_and_last_value(self):
        page = (
            '<input name="a" value="1"><input name="b" value="2">'
            '<input name="a" value="3">'
        )
        items = scraper.query_items(page)
        self.assertEqual([name for name, _ in items], ["a", "b"])
        self.assertEqual(items[0][1], "3")


class FormActionTests(unittest.TestCase):
    def test_action_is_found(self):
        self.assertEqual(
            scraper.form_action(GATEWAY_PAGE), "http://portal.example.test/login"
        )

    def test_no_form_reports_no_action(self):
        self.assertIsNone(scraper.form_action("<html><body>nothing here</body></html>"))

    def test_empty_action_counts_as_none(self):
        self.assertIsNone(scraper.form_action('<form action="">'))


class EntityTests(unittest.TestCase):
    def test_entities_are_decoded(self):
        page = '<input name="dst" value="http://x.test/?a=1&amp;b=2">'
        self.assertEqual(
            scraper.value_named("dst", page), "http://x.test/?a=1&b=2"
        )

    def test_a_double_escaped_entity_is_decoded_once(self):
        page = '<input name="x" value="&amp;lt;">'
        self.assertEqual(scraper.value_named("x", page), "&lt;")


class RealRouterPageTests(unittest.TestCase):
    def test_chap_fields_read_from_id_because_there_is_no_name(self):
        self.assertEqual(scraper.value_identified_by("chap-id", REAL_LOGIN_PAGE), "\\362")
        self.assertIsNotNone(scraper.value_identified_by("chap-challenge", REAL_LOGIN_PAGE))

    def test_every_hidden_field_is_forwarded_keyed_by_id(self):
        names = {name for name, _ in scraper.query_items(REAL_LOGIN_PAGE)}
        self.assertIn("chap-id", names)
        self.assertIn("chap-challenge", names)
        self.assertIn("serial", names)
        self.assertIn("client_mac", names)
        self.assertIn("login_url", names)

    def test_the_page_carries_no_form_action(self):
        # Confirmed on the live capture and in the original's own log. The
        # configured portal fallback is load-bearing, not a rare edge case.
        self.assertIsNone(scraper.form_action(REAL_LOGIN_PAGE))


if __name__ == "__main__":
    unittest.main()
