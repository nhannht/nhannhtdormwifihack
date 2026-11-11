"""The Windows routing-table reader's small decisions.

The ctypes table itself needs a real adapter, but the rules that keep a
link-local place-holder from being mistaken for the router do not.
"""

from __future__ import annotations

import unittest

from dormwifi import default_route


class UsableAddressTests(unittest.TestCase):
    def test_a_real_private_router_is_usable(self):
        for text in ("192.168.200.1", "10.11.0.1", "172.16.0.1", "8.8.8.8"):
            with self.subTest(text=text):
                self.assertTrue(default_route._usable(text))

    def test_dhcp_place_holders_are_not_usable(self):
        for text in (
            "169.254.0.1",
            "169.254.83.107",
            "0.0.0.0",
            "127.0.0.1",
            "224.0.0.1",
            "",
            None,
            "not-an-address",
        ):
            with self.subTest(text=text):
                self.assertFalse(default_route._usable(text))

    def test_the_live_gateway_is_usable_or_there_is_none(self):
        found = default_route.gateway()
        if found is None:
            self.skipTest("this machine has no default route to read")
        self.assertTrue(default_route._usable(found), found)


if __name__ == "__main__":
    unittest.main()
