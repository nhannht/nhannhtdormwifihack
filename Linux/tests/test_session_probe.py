"""Reading the router's own status page."""

from __future__ import annotations

import unittest

from dormwifi import session_probe

ANY_URL = "http://192.168.200.1/status"

#: The status page this hotspot actually serves, trimmed to its table.
STATUS_PAGE = """
<html><head><title>Status - INET</title>
<meta http-equiv="refresh" content="60"></head>
<body><div class="status-card"><table>
<tr><td>Địa chỉ IP</td><td>192.168.0.42</td></tr>
<tr><td>Địa chỉ MAC</td><td>00:00:5E:00:53:2A</td></tr>
<tr><td>Thời gian kết nối</td><td>2m35s</td></tr>
<tr><td>Thời gian còn lại</td><td>12m25s</td></tr>
</table></div></body></html>
"""


class InterpretTests(unittest.TestCase):
    def test_session_details_mean_signed_in(self):
        answer = session_probe.interpret(STATUS_PAGE, ANY_URL)
        self.assertTrue(answer.is_active)
        self.assertEqual(answer.info.ip_address, "192.168.0.42")
        self.assertEqual(answer.info.mac_address, "00:00:5E:00:53:2A")

    def test_the_routers_own_countdown_is_read(self):
        info = session_probe.interpret(STATUS_PAGE, ANY_URL).info
        self.assertEqual(info.connected_for, 155)
        self.assertEqual(info.remaining, 745)
        # 2m35s used plus 12m25s left is exactly the 15 minute session.
        self.assertEqual(info.connected_for + info.remaining, 900)

    def test_the_stock_english_page_is_understood(self):
        page = "<table><tr><td>Uptime</td><td>1h2m3s</td></tr></table>"
        answer = session_probe.interpret(page, ANY_URL)
        self.assertTrue(answer.is_active)
        self.assertEqual(answer.info.connected_for, 3723)

    def test_redirect_to_the_login_page_means_signed_out(self):
        url = "http://192.168.200.1/login?dst=x"
        self.assertEqual(session_probe.interpret("<html></html>", url).kind, "expired")

    def test_a_fresh_challenge_means_signed_out(self):
        body = '<form><input id="chap-challenge" value="\\044"></form>'
        self.assertEqual(session_probe.interpret(body, ANY_URL).kind, "expired")

    def test_the_page_served_at_a_lapse_is_not_a_lost_router(self):
        # Captured at a real session boundary: the router answers, but the
        # session rows are simply gone. Reading that as an unreachable router
        # made the watcher rejoin the wireless network at every session boundary,
        # costing about eight seconds and a dropped link each time.
        page = (
            "<html><head><title>Status - INET</title></head>"
            '<body><div class="status-card"><table></table></div></body></html>'
        )
        self.assertEqual(session_probe.interpret(page, ANY_URL).kind, "unknown")

    def test_an_unrecognised_page_is_unknown_not_success(self):
        self.assertEqual(session_probe.interpret("<html>hi</html>", ANY_URL).kind, "unknown")

    def test_script_and_style_contents_are_not_fields(self):
        page = """
            <style>.a { content: "Thời gian còn lại"; }</style>
            <script>var x = "Địa chỉ IP";</script>
            <table><tr><td>Thời gian còn lại</td><td>5m</td></tr></table>
            """
        self.assertEqual(session_probe.interpret(page, ANY_URL).info.remaining, 300)

    def test_a_script_spanning_several_lines_is_stripped(self):
        page = """
            <script>
              var labels = "Thời gian còn lại";
              var v = "9m9s";
            </script>
            <table><tr><td>Thời gian còn lại</td><td>5m</td></tr></table>
            """
        self.assertEqual(session_probe.interpret(page, ANY_URL).info.remaining, 300)

    def test_a_script_that_merely_mentions_the_challenge_is_not_a_login_page(self):
        # Reading the challenge out of script text reported an open session as
        # signed out on every pass, which sent the watcher into a login loop.
        page = """
            <script>
              var field = "chap-id";
            </script>
            <table><tr><td>Thời gian còn lại</td><td>5m</td></tr></table>
            """
        self.assertTrue(session_probe.interpret(page, ANY_URL).is_active)


class DurationTests(unittest.TestCase):
    def test_durations_parse(self):
        for text, expected in (
            ("45s", 45),
            ("2m35s", 155),
            ("12m25s", 745),
            ("1h2m3s", 3723),
            ("1d2h3m4s", 93784),
            ("15m", 900),
            ("1h 2m", 3720),
        ):
            with self.subTest(text=text):
                self.assertEqual(session_probe.duration(text), expected)

    def test_anything_that_is_not_a_duration_is_rejected(self):
        # An IP address must never be mistaken for a time, or the scheduler would
        # sleep for a nonsense interval.
        for text in ("192.168.0.42", "", None, "hello", "00:00:5E:00:53:2A"):
            with self.subTest(text=text):
                self.assertIsNone(session_probe.duration(text))

    def test_a_number_too_large_to_hold_is_rejected(self):
        # This text comes off the router over plain HTTP.
        self.assertIsNone(session_probe.duration("999999999999999999999s"))
        self.assertIsNone(session_probe.duration("99999999999999999999d"))
        self.assertEqual(session_probe.duration(f"{2**63 - 1}s"), 2**63 - 1)


if __name__ == "__main__":
    unittest.main()
