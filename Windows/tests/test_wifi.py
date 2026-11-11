"""Reading the reports `netsh wlan` prints.

The parsing is the part that has to survive a Windows update or a different
language of Windows, and it is the part that can be tested with no adapter.
"""

from __future__ import annotations

import unittest

from dormwifi import wifi

PROFILES = """
Profiles on interface Wi-Fi:

Group policy profiles (read only)
---------------------------------
    <None>

User profiles
-------------
    All User Profile     : INET - Free WiFi
    All User Profile     : WIFI INET 5G
    All User Profile     : my phone
"""

INTERFACES = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    GUID                   : 12345678-1234-1234-1234-123456789012
    Physical address       : 00:00:5e:00:53:2a
    State                  : connected
    SSID                   : INET - Free WiFi
    BSSID                  : 00:00:5e:00:53:2b
    Network type           : Infrastructure
    Radio type             : 802.11ac
"""

NOT_CONNECTED = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    State                  : disconnected
"""

NO_INTERFACE = "The wireless local area network interface is not present."


class ProfileTests(unittest.TestCase):
    def test_profiles_are_read_out_of_the_report(self):
        self.assertEqual(
            wifi.parse_profiles(PROFILES),
            ["INET - Free WiFi", "WIFI INET 5G", "my phone"],
        )

    def test_a_report_with_no_profiles_is_empty(self):
        self.assertEqual(wifi.parse_profiles("Profiles on interface Wi-Fi:\n\n    <None>\n"), [])

    def test_the_same_name_is_not_listed_twice(self):
        output = (
            "    All User Profile     : Dorm\n"
            "    All User Profile     : dorm\n"
        )
        self.assertEqual(wifi.parse_profiles(output), ["Dorm"])


class InterfaceTests(unittest.TestCase):
    def test_the_current_network_is_read_from_the_interface_report(self):
        self.assertEqual(wifi.current_ssid_from(INTERFACES), "INET - Free WiFi")
        self.assertIsNone(wifi.current_ssid_from(NOT_CONNECTED))

    def test_a_bssid_line_is_not_the_ssid(self):
        self.assertIsNone(wifi.current_ssid_from("    BSSID : 00:00:5e:00:53:2b\n"))

    def test_an_interface_report_means_there_is_a_wireless_adapter(self):
        self.assertTrue(wifi._interface_present(INTERFACES))
        self.assertTrue(wifi._interface_present(NOT_CONNECTED))
        self.assertFalse(wifi._interface_present(NO_INTERFACE))


class OutcomeTests(unittest.TestCase):
    def test_a_missing_saved_network_says_what_to_do(self):
        outcome = wifi.JoinOutcome("not_saved", reason="dorm")
        self.assertIn("Join it by hand once", outcome.summary)

    def test_a_failed_join_keeps_the_reason_windows_gave(self):
        outcome = wifi.JoinOutcome("failed", reason="The profile is not available.")
        self.assertFalse(outcome.joined)
        self.assertIn("not available", outcome.summary)

    def test_a_failure_with_no_reason_is_still_a_sentence(self):
        self.assertEqual(
            wifi.JoinOutcome("failed").summary,
            "the join did not report why it failed",
        )

    def test_no_interface_is_explained(self):
        self.assertIn("no Wi-Fi interface", wifi.JoinOutcome("no_interface").summary)


if __name__ == "__main__":
    unittest.main()
