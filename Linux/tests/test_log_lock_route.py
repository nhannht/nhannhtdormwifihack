"""The log file, the singleton lock, and finding the router."""

from __future__ import annotations

import socket
import struct
import tempfile
import unittest
from pathlib import Path

from dormwifi.activity_log import ActivityLog, LogEntry, entries_in_file
from dormwifi.default_route import first_host
from dormwifi.run_lock import RunLock


class LogFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "activity.log"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_history_survives_a_restart(self):
        # The window is meant to be opened hours after something went wrong.
        first = ActivityLog(file_path=self.path, truncate_file=False)
        first.warning("something went wrong earlier")

        second = ActivityLog(file_path=self.path, truncate_file=False)
        second.info("a later run")

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("something went wrong earlier", text)
        self.assertIn("a later run", text)

    def test_starting_fresh_still_wipes_the_file_when_asked(self):
        first = ActivityLog(file_path=self.path, truncate_file=False)
        first.warning("from an old run")

        ActivityLog(file_path=self.path, truncate_file=True)

        self.assertNotIn("from an old run", self.path.read_text(encoding="utf-8"))

    def test_a_file_kept_across_runs_cannot_grow_without_bound(self):
        self.path.write_text("an old line that no longer matters\n" * 40_000, encoding="utf-8")
        before = self.path.stat().st_size
        self.assertGreater(before, 1_000_000)

        ActivityLog(file_path=self.path, truncate_file=False)

        after = self.path.stat().st_size
        self.assertLess(after, before)
        self.assertLess(after, 600 * 1024)
        # Trimmed at a line boundary, so the file never opens mid-sentence.
        self.assertTrue(self.path.read_text(encoding="utf-8").startswith("an old line"))

    def test_a_subscriber_hears_entries_logged_after_it_arrives(self):
        log = ActivityLog()
        heard = []
        unsubscribe = log.subscribe(heard.append)

        log.success("it worked")

        self.assertEqual(heard[0].message, "it worked")
        self.assertEqual(heard[0].level, "success")

        unsubscribe()
        log.info("after unsubscribing")
        self.assertEqual(len(heard), 1)

    def test_a_line_keeps_its_level_on_disk_and_comes_back_whole(self):
        log = ActivityLog(file_path=self.path)
        log.failure("the router said no")

        entry = entries_in_file(self.path, limit=10)[-1]
        self.assertEqual(entry.level, "failure")
        self.assertEqual(entry.message, "the router said no")

    def test_a_line_with_no_level_still_parses(self):
        # The older builds wrote no level. Those lines are still worth reading.
        entry = LogEntry.parse_file_line("[16:57:55] logged in")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.level, "info")
        self.assertEqual(entry.message, "logged in")

    def test_the_banner_is_not_a_log_line(self):
        self.assertIsNone(LogEntry.parse_file_line("--- session started at 2026-11-05 ---"))
        self.assertIsNone(LogEntry.parse_file_line(""))


class RunLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "watcher.lock"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_a_second_watcher_cannot_start_while_the_first_holds_the_lock(self):
        # Two watchers both post to /login the moment a session lapses, and the
        # router rejects the second for having too many sessions open.
        first = RunLock.acquire(self.path)
        self.assertIsNotNone(first)

        self.assertIsNone(RunLock.acquire(self.path))

        first.release()
        self.assertIsNotNone(RunLock.acquire(self.path))

    def test_the_lock_is_released_when_the_holder_goes_away(self):
        holder = RunLock.acquire(self.path)
        self.assertIsNotNone(holder)
        del holder

        afterwards = RunLock.acquire(self.path)
        self.assertIsNotNone(afterwards, "The lock outlived its holder")

    def test_separate_lock_files_do_not_block_each_other(self):
        other = Path(self.directory.name) / "other.lock"
        first = RunLock.acquire(self.path)
        second = RunLock.acquire(other)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)


def _address(text: str) -> int:
    """An IPv4 address the way a `sockaddr_in` carries it."""
    return struct.unpack("I", socket.inet_aton(text))[0]


class FirstHostTests(unittest.TestCase):
    def test_both_of_this_hotspots_networks_put_the_router_on_the_first_address(self):
        # A /24 on one network, a /16 on the other, and the same rule finds both.
        self.assertEqual(first_host(_address("192.168.200.5"), _address("255.255.255.0")), "192.168.200.1")
        self.assertEqual(first_host(_address("10.11.7.9"), _address("255.255.0.0")), "10.11.0.1")

    def test_the_first_address_comes_from_the_mask_not_the_last_octet(self):
        # A machine at .130 in a /25 is on the upper half of the block, so its
        # router is .129. Reading only the last octet would answer .1, which is
        # on the other network entirely.
        self.assertEqual(first_host(_address("192.168.1.130"), _address("255.255.255.128")), "192.168.1.129")

    def test_a_mask_with_no_room_for_both_a_router_and_a_host_is_refused(self):
        self.assertIsNone(first_host(_address("10.0.0.1"), _address("255.255.255.255")))
        self.assertIsNone(first_host(_address("10.0.0.1"), _address("255.255.255.254")))
        self.assertIsNone(first_host(_address("10.0.0.1"), _address("0.0.0.0")))


class LiveRouteTests(unittest.TestCase):
    def test_the_gateway_is_a_usable_address_or_there_is_none(self):
        # Read-only, and it steps aside when this machine has no network rather
        # than failing.
        from dormwifi import default_route

        found = default_route.gateway()
        if found is None:
            self.skipTest("this machine has no default route to read")
        socket.inet_aton(found)


if __name__ == "__main__":
    unittest.main()
