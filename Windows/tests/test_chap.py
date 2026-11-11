"""The password scrambling. If any of this changes the router stops letting us in."""

from __future__ import annotations

import unittest

from dormwifi import chap

#: A 16 byte challenge in the exact form a MikroTik hotspot writes it.
CHALLENGE = "\\154\\263\\277\\262\\304\\264\\021\\246\\064\\361\\167\\164\\003\\016\\217\\217"
#: The matching one byte chap-id.
CHAP_ID = "\\044"

#: MD5(id + password + challenge) computed independently over those bytes.
GOLDEN_HASH = "56cc45ff762b4e347dee409ba9b5d21b"


class DecodingTests(unittest.TestCase):
    def test_one_escape_becomes_one_byte(self):
        self.assertEqual(chap.decode("\\044"), bytes([36]))

    def test_escapes_decode_in_sequence(self):
        self.assertEqual(chap.decode("\\001\\002"), bytes([1, 2]))

    def test_real_world_lengths(self):
        # network_debug.log recorded "DEBUG CHAP: ID_Len=1 Ch_Len=16" during a
        # real session, so these are the lengths the router actually sends.
        self.assertEqual(len(chap.decode(CHAP_ID)), 1)
        self.assertEqual(len(chap.decode(CHALLENGE)), 16)

    def test_first_and_last_bytes_survive(self):
        data = chap.decode(CHALLENGE)
        self.assertEqual(data[0], 0o154)
        self.assertEqual(data[-1], 0o217)

    def test_percent_encoded_backslash(self):
        self.assertEqual(chap.decode("%5C044"), bytes([36]))
        self.assertEqual(chap.decode("%5c044"), bytes([36]))

    def test_printable_text_passes_through(self):
        self.assertEqual(chap.decode("abc123"), b"abc123")

    def test_literal_backslash(self):
        self.assertEqual(chap.decode("\\abc"), b"\\abc")

    def test_trailing_backslash(self):
        self.assertEqual(chap.decode("ab\\"), b"ab\\")
        self.assertEqual(chap.decode("\\04"), b"\\04")

    def test_digits_eight_and_nine_are_rejected(self):
        # The old Python version threw here and silently fell back to a plain
        # MD5 the router rejects, and reported "invalid password".
        with self.assertRaises(chap.ParseError) as caught:
            chap.decode("\\888")
        self.assertEqual(caught.exception.digits, "888")

    def test_escape_too_large_for_a_byte_is_rejected(self):
        with self.assertRaises(chap.ParseError):
            chap.decode("\\400")  # 256
        with self.assertRaises(chap.ParseError):
            chap.decode("\\777")  # 511

    def test_largest_valid_escape(self):
        self.assertEqual(chap.decode("\\377"), bytes([255]))
        self.assertEqual(chap.decode("\\000"), bytes([0]))

    def test_percent_decoding_works_on_raw_bytes(self):
        # A byte that is not valid UTF-8 has to come through unchanged: decoding
        # into text first would replace it with U+FFFD and the hash would be
        # wrong with no error anywhere.
        self.assertEqual(chap.decode("%FF%00"), b"\xff\x00")


class HashingTests(unittest.TestCase):
    def test_golden_hash(self):
        self.assertEqual(
            chap.hash_password("testpass", CHAP_ID, CHALLENGE), GOLDEN_HASH
        )

    def test_hash_shape(self):
        hashed = chap.hash_password("testpass", CHAP_ID, CHALLENGE)
        self.assertEqual(len(hashed), 32)
        self.assertEqual(hashed, hashed.lower())
        int(hashed, 16)

    def test_ordering_is_id_then_password_then_challenge(self):
        correct = chap.hash_password("testpass", CHAP_ID, CHALLENGE)
        swapped = chap.hash_password("testpass", CHALLENGE, CHAP_ID)
        self.assertNotEqual(correct, swapped)

    def test_percent_encoded_challenge_hashes_the_same(self):
        plain = chap.hash_password("testpass", CHAP_ID, CHALLENGE)
        encoded = chap.hash_password(
            "testpass", CHAP_ID.replace("\\", "%5C"), CHALLENGE.replace("\\", "%5C")
        )
        self.assertEqual(plain, encoded)

    def test_plain_md5(self):
        self.assertEqual(chap.plain_md5("test"), "098f6bcd4621d373cade4e832627b4f6")


if __name__ == "__main__":
    unittest.main()
