"""The settings file, and the state the window shows."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from dormwifi.settings import PortalEndpoints, Settings


class SettingsFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "settings.json"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_defaults_when_there_is_no_file(self):
        settings = Settings.load(self.path)
        self.assertEqual(settings.interval, 15)
        self.assertEqual(settings.check_interval_secs, 2)
        self.assertEqual(settings.wifi_name, "")
        self.assertEqual(settings.fallback_wifi_name, "")
        self.assertTrue(settings.auto_connect)
        self.assertEqual(settings.portal_endpoints.verify_url, "/Home/VerifyUrl")

    def test_a_round_trip_keeps_a_fallback_network(self):
        # The name goes in the settings file, never in the source: it is a real
        # identifier belonging to a real person's phone.
        settings = Settings(fallback_wifi_name="Some Saved Hotspot")
        settings.save(self.path)

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("fallback_wifi_name", text)
        self.assertEqual(Settings.load(self.path).fallback_wifi_name, "Some Saved Hotspot")

    def test_the_file_is_readable_only_by_its_owner(self):
        Settings().save(self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_unknown_keys_survive_a_save(self):
        # The Windows build writes keys this one has never heard of. Re-encoding
        # only the declared keys deleted them without a word, so a single run of
        # `login` took that tool's configuration with it.
        self.path.write_text(
            json.dumps(
                {
                    "wifi_name": "Dorm",
                    "windows_only_flag": True,
                    "windows_only_table": {"n": 7},
                }
            ),
            encoding="utf-8",
        )

        settings = Settings.load(self.path)
        settings.interval = 20
        settings.save(self.path)

        again = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(again["interval"], 20)
        self.assertEqual(again["windows_only_flag"], True)
        self.assertEqual(again["windows_only_table"]["n"], 7)

    def test_a_known_key_is_never_taken_for_an_unknown_one(self):
        self.path.write_text(
            '{"interval":9,"gateway_ip":"10.5.50.1"}', encoding="utf-8"
        )
        settings = Settings.load(self.path)
        self.assertEqual(settings.unknown_keys, {})

        settings.save(self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count('"gateway_ip"'), 1)

    def test_a_partial_portal_endpoint_block_falls_back_per_key(self):
        self.path.write_text(
            '{"portal_endpoints": {"verifyURL": "/Custom/Verify"}}', encoding="utf-8"
        )
        endpoints = Settings.load(self.path).portal_endpoints
        self.assertEqual(endpoints.verify_url, "/Custom/Verify")
        self.assertEqual(endpoints.get_customer, "/Content/GetCustomer")

    def test_the_interval_is_clamped_to_one_day(self):
        self.assertEqual(Settings(interval=0).session_timeout_seconds, 60)
        self.assertEqual(Settings(interval=-5).session_timeout_seconds, 60)
        self.assertEqual(Settings(interval=10_000).session_timeout_seconds, 86_400)
        self.assertEqual(Settings(interval=15).session_timeout_seconds, 900)

    def test_an_explicit_router_address_is_left_alone(self):
        settings = Settings(gateway_ip="10.5.50.1")
        self.assertEqual(settings.resolving_gateway().gateway_ip, "10.5.50.1")

    def test_an_empty_router_address_is_filled_in_from_the_route(self):
        from dormwifi import default_route

        self.assertEqual(
            Settings().resolving_gateway().gateway_ip, default_route.gateway() or ""
        )

    def test_unknown_keys_are_not_written_twice(self):
        settings = Settings(unknown_keys={"interval": 999})
        payload = settings.to_json()
        self.assertEqual(payload["interval"], settings.interval)


class PortalEndpointTests(unittest.TestCase):
    def test_the_wire_names_are_the_ones_the_original_wrote(self):
        self.assertEqual(
            PortalEndpoints().to_json(),
            {
                "verifyURL": "/Home/VerifyUrl",
                "getCustomer": "/Content/GetCustomer",
                "getCampaignHTML": "/Content/GetCampaignHtml",
                "analytic": "/Analytic/Send",
            },
        )

    def test_reading_nothing_gives_the_defaults(self):
        self.assertEqual(PortalEndpoints.from_json(None), PortalEndpoints())


class StatusTests(unittest.TestCase):
    def status(self, **overrides):
        from dormwifi.status_board import WatcherStatus

        base = WatcherStatus(network="Some Hotspot")
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_every_state_has_something_to_say(self):
        # The window prints this string directly, so a state with an empty
        # headline is a blank line in front of the user.
        for connection in (
            "starting",
            "signedIn",
            "signingIn",
            "wrongNetwork",
            "onFallback",
            "stopping",
            "stopped",
        ):
            with self.subTest(connection=connection):
                headline = self.status(connection=connection, detail="Phone").headline
                self.assertTrue(headline)
                self.assertEqual(headline, headline.strip())

    def test_the_countdown_reads_as_a_time(self):
        status = self.status(connection="signedIn", expires_at=time.time() + 745)
        self.assertEqual(status.headline, "Signed in, 12m 25s left")

        # The router does not always give a number, and the window still has to
        # say something true.
        status.expires_at = None
        self.assertEqual(status.headline, "Signed in")

    def test_the_countdown_keeps_counting_without_another_router_check(self):
        # The watcher can sleep a full minute between checks. Storing seconds
        # meant the window showed a number up to a minute out of date.
        status = self.status(connection="signedIn", expires_at=time.time() - 5)
        self.assertEqual(status.remaining_now, 0)
        self.assertEqual(status.headline, "Signed in, 0s left")

    def test_a_heartbeat_that_found_the_same_session_is_not_a_change(self):
        earlier = self.status(connection="signedIn", expires_at=time.time() + 600)
        later = self.status(connection="signedIn", expires_at=time.time() + 602)
        self.assertFalse(later.differs_materially_from(earlier))

    def test_a_new_session_and_a_sign_out_are_changes(self):
        signed_in = self.status(connection="signedIn", expires_at=time.time() + 30)
        renewed = self.status(connection="signedIn", expires_at=time.time() + 900)
        signed_out = self.status(connection="signingIn", expires_at=None)

        self.assertTrue(renewed.differs_materially_from(signed_in))
        self.assertTrue(signed_out.differs_materially_from(signed_in))

    def test_being_on_the_fallback_network_only_counts_as_healthy_with_internet(self):
        # Colour is not the only signal, but a green dot while the phone hotspot
        # is passing no traffic would still be a lie.
        status = self.status(connection="onFallback", detail="Phone", has_internet=False)
        self.assertFalse(status.is_healthy)
        status.has_internet = True
        self.assertTrue(status.is_healthy)

    def test_a_status_nobody_has_written_for_two_minutes_is_stale(self):
        status = self.status(updated=time.time() - 121)
        self.assertTrue(status.is_stale)
        status.updated = time.time()
        self.assertFalse(status.is_stale)


class StatusBoardTests(unittest.TestCase):
    def test_a_late_reader_gets_the_current_state(self):
        from dormwifi.status_board import StatusBoard, WatcherStatus

        board = StatusBoard()
        published = board.publish(WatcherStatus(connection="signedIn"))

        heard = []
        unsubscribe = board.subscribe(heard.append)
        self.assertEqual(heard[0].connection, "signedIn")
        self.assertEqual(board.snapshot().connection, "signedIn")

        published.connection = "stopped"
        board.publish(published)
        self.assertEqual(heard[-1].connection, "stopped")

        unsubscribe()
        board.publish(published)
        self.assertEqual(len(heard), 2)

    def test_a_broken_listener_does_not_stop_the_others(self):
        from dormwifi.status_board import StatusBoard, WatcherStatus

        board = StatusBoard()
        heard = []

        def broken(_status):
            raise RuntimeError("this listener is broken")

        board.subscribe(broken)
        board.subscribe(heard.append)
        board.publish(WatcherStatus(connection="signedIn"))

        # The first thing a listener hears is the current value, so the published
        # one is the last.
        self.assertEqual(heard[-1].connection, "signedIn")


class StartupAnnouncementTests(unittest.TestCase):
    def test_an_unset_hotspot_is_named_as_the_reason_nothing_is_joined(self):
        # An empty wifi_name makes the join step a no-op, and the loop then says
        # "probably not on the configured network" forever, which reads like a
        # network fault. This line has to say which setting is missing.
        from dormwifi.activity_log import ActivityLog
        from dormwifi.session_scheduler import SessionScheduler
        from dormwifi.settings import Settings

        log = ActivityLog()
        SessionScheduler(Settings(), log)._announce_hotspot()

        messages = [entry.message for entry in log.all]
        self.assertEqual(len(messages), 1)
        self.assertIn("No hotspot is set", messages[0])
        self.assertIn("wifi_name", messages[0])
        self.assertIn("config set wifi_name", messages[0])

    def test_a_configured_hotspot_is_named(self):
        from dormwifi.activity_log import ActivityLog
        from dormwifi.session_scheduler import SessionScheduler
        from dormwifi.settings import Settings

        log = ActivityLog()
        SessionScheduler(Settings(wifi_name="INET - Free WiFi"), log)._announce_hotspot()

        self.assertEqual(log.all[0].message, "Watching for INET - Free WiFi.")


class StatusFileTests(unittest.TestCase):
    def test_a_status_survives_a_round_trip_through_a_file(self):
        from dormwifi.status_board import StatusFile, WatcherStatus

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            original = WatcherStatus(
                connection="signedIn",
                expires_at=1_800_000_000.0,
                router_ip="192.168.1.1",
                network="Dorm",
                has_internet=True,
            )
            StatusFile(path).write(original)
            read = StatusFile(path).read()

            self.assertIsNotNone(read)
            self.assertEqual(read.connection, "signedIn")
            self.assertEqual(read.router_ip, "192.168.1.1")
            self.assertTrue(read.has_internet)

    def test_a_missing_or_broken_file_reads_as_nothing(self):
        from dormwifi.status_board import StatusFile

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            self.assertIsNone(StatusFile(path).read())
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(StatusFile(path).read())


if __name__ == "__main__":
    unittest.main()
