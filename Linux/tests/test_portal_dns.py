"""Asking the router what a name means.

Every test here runs against bytes or a socket on this machine: no hotspot and no
resolver involved. The packets are built by hand so the parser is exercised
against exactly the shapes a DNS reply takes, including the broken ones a portal
can send.
"""

from __future__ import annotations

import socket
import struct
import threading
import unittest

from dormwifi.portal_dns import (
    GatewayResolver,
    Unreachable,
    answers,
    query_a,
    wire_name,
)

_NAME = "v1.awingconnect.vn"
_TRANSACTION = 0x04D2


def _wire(name: str) -> bytes:
    labels = name.rstrip(".").split(".")
    return b"".join(bytes([len(label)]) + label.encode() for label in labels) + b"\x00"


def _a_record(address: str, owner: str = _NAME) -> bytes:
    return _wire(owner) + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(address)


def _cname_record(target: str, owner: str = _NAME) -> bytes:
    data = _wire(target)
    return _wire(owner) + struct.pack("!HHIH", 5, 1, 60, len(data)) + data


def _reply(
    records: list[bytes], transaction: int = _TRANSACTION, rcode: int = 0, questions: int = 1
) -> bytes:
    flags = 0x8180 | rcode
    header = struct.pack("!HHHHHH", transaction, flags, questions, len(records), 0, 0)
    question = _wire(_NAME) + struct.pack("!HH", 1, 1) if questions else b""
    return header + question + b"".join(records)


class ParsingTests(unittest.TestCase):
    def test_an_address_is_read_out_of_the_answer(self):
        self.assertEqual(answers(_reply([_a_record("10.11.0.1")]), _TRANSACTION), ["10.11.0.1"])

    def test_a_cname_chain_still_yields_its_address(self):
        # The alias is the owner of the chain and the address belongs to the
        # target, which is why every A record in the answer section is collected
        # rather than only the one named after the question.
        packet = _reply([_cname_record("cdn.awing.vn"), _a_record("1.2.3.4", owner="cdn.awing.vn")])
        self.assertEqual(answers(packet, _TRANSACTION), ["1.2.3.4"])

    def test_no_such_name_is_an_empty_answer(self):
        self.assertEqual(answers(_reply([], rcode=3), _TRANSACTION), [])

    def test_a_reply_to_another_question_is_ignored(self):
        packet = _reply([_a_record("10.11.0.1")], transaction=_TRANSACTION + 1)
        self.assertEqual(answers(packet, _TRANSACTION), [])

    def test_no_answer_at_all_is_an_empty_answer(self):
        self.assertEqual(answers(_reply([], questions=0), _TRANSACTION), [])

    def test_a_truncated_reply_never_raises(self):
        # This parses bytes from a device on a network anyone can join. Every
        # prefix has to come back as "no answer", never as an exception.
        packet = _reply([_a_record("10.11.0.1")])
        for length in range(len(packet)):
            self.assertEqual(answers(packet[:length], _TRANSACTION), [], length)

    def test_a_name_is_dotted_and_zero_terminated(self):
        self.assertEqual(wire_name("a.vn"), b"\x01a\x02vn\x00")
        self.assertEqual(wire_name("a.vn."), b"\x01a\x02vn\x00")

    def test_an_unusable_name_is_refused(self):
        for name in ("", "..", "a..b", "."):
            with self.assertRaises(ValueError, msg=name):
                wire_name(name)


class _Answerer(threading.Thread):
    """One DNS answer, and a record of the question that was asked."""

    def __init__(self, reply: bytes | None) -> None:
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(5)
        self.reply = reply
        self.asked: bytes = b""

    @property
    def port(self) -> int:
        return self.sock.getsockname()[1]

    def run(self) -> None:
        try:
            self.asked, address = self.sock.recvfrom(4096)
        except OSError:
            return
        if self.reply is None:
            return
        # Echo the transaction id the client chose; it is what ties a reply to a
        # question and the client checks it.
        self.sock.sendto(self.asked[:2] + self.reply[2:], address)

    def stop(self) -> None:
        self.sock.close()


class QueryTests(unittest.TestCase):
    def test_an_answer_arrives_over_udp(self):
        answerer = _Answerer(_reply([_a_record("10.11.0.1")], transaction=0))
        answerer.start()
        try:
            found = query_a("127.0.0.1", _NAME, timeout=2, port=answerer.port)
        finally:
            answerer.stop()
        self.assertEqual(found, ["10.11.0.1"])

    def test_the_question_names_the_host_and_asks_for_an_address(self):
        answerer = _Answerer(_reply([_a_record("10.11.0.1")], transaction=0))
        answerer.start()
        try:
            query_a("127.0.0.1", _NAME, timeout=2, port=answerer.port)
        finally:
            answerer.stop()

        self.assertEqual(answerer.asked[12:], _wire(_NAME) + struct.pack("!HH", 1, 1))

    def test_a_server_that_says_nothing_is_unreachable(self):
        answerer = _Answerer(None)
        answerer.start()
        try:
            with self.assertRaises(Unreachable):
                query_a("127.0.0.1", _NAME, timeout=0.2, port=answerer.port)
        finally:
            answerer.stop()


class ResolverTests(unittest.TestCase):
    def _resolver(self, table: dict[str, list[str] | Exception], timeout: float = 0.2):
        asked: list[str] = []

        def query(server: str, name: str, timeout: float | None = None) -> list[str]:
            asked.append(name)
            found: list[str] | Exception = table.get(name, [])
            if isinstance(found, Exception):
                raise found
            return list(found)

        reported: list[tuple[str, str]] = []
        resolver = GatewayResolver(
            "192.168.200.1",
            timeout=timeout,
            query=query,
            on_answer=lambda name, address: reported.append((name, address)),
        )
        return resolver, asked, reported

    def test_the_router_answers_for_the_portal(self):
        # The state that matters: the machine's own resolver is outside the
        # walled garden, and the router says the gate is what serves the portal.
        resolver, _, _ = self._resolver({_NAME: ["10.11.0.1"]})
        self.assertEqual(resolver.address_for(_NAME), "10.11.0.1")

    def test_a_name_the_router_does_not_know_is_left_to_the_system(self):
        resolver, _, _ = self._resolver({})
        self.assertIsNone(resolver.address_for("example.com"))

    def test_a_filtered_answer_is_not_an_address(self):
        # A blocking resolver answers 0.0.0.0 or a loopback address, which would
        # connect this machine to itself.
        for blocked in (["0.0.0.0"], ["127.0.0.1"], ["224.0.0.1"]):
            resolver, _, _ = self._resolver({_NAME: blocked})
            self.assertIsNone(resolver.address_for(_NAME), blocked)

    def test_an_address_needs_no_lookup(self):
        resolver, asked, _ = self._resolver({})
        self.assertIsNone(resolver.address_for("10.11.0.1"))
        self.assertEqual(asked, [])

    def test_one_timeout_stops_the_question_being_asked_again(self):
        resolver, asked, _ = self._resolver({_NAME: Unreachable("timed out")})
        self.assertIsNone(resolver.address_for(_NAME))
        self.assertIsNone(resolver.address_for("v1.awingconnect.com"))
        self.assertEqual(asked, [_NAME])

    def test_an_answer_is_kept_for_the_rest_of_the_session(self):
        resolver, asked, _ = self._resolver({_NAME: ["10.11.0.1"]})
        resolver.address_for(_NAME)
        resolver.address_for(_NAME.upper())
        self.assertEqual(asked, [_NAME])

    def test_which_resolver_answered_is_reported_once(self):
        resolver, _, reported = self._resolver({_NAME: ["10.11.0.1"]})
        resolver.address_for(_NAME)
        resolver.address_for(_NAME)
        self.assertEqual(reported, [(_NAME, "10.11.0.1")])


if __name__ == "__main__":
    unittest.main()
