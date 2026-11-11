"""MikroTik hotspot CHAP authentication.

A MikroTik hotspot never accepts a plaintext password. Its login page carries
two hidden fields, ``chap-id`` and ``chap-challenge``, and the password posted
back has to be ``MD5(id + password + challenge)`` over the raw bytes of each
part, in that order.

Both fields arrive as escape strings. Any byte that is not printable ASCII is
written as a backslash followed by three octal digits, so ``\\154`` means the
single byte 108. On top of that the whole value is sometimes percent-encoded,
which turns each backslash into ``%5C``.
"""

from __future__ import annotations

import hashlib


class ParseError(ValueError):
    """A backslash was followed by three digits that do not form a byte."""

    def __init__(self, digits: str) -> None:
        self.digits = digits
        super().__init__(self.description)

    @property
    def description(self) -> str:
        return (
            f"Malformed octal escape \\{self.digits}. Expected three digits in "
            "the range 0 to 7 whose value is 255 or less."
        )


_BACKSLASH = 0x5C
_PERCENT = 0x25
_ZERO = ord("0")
_SEVEN = ord("7")


def hash_password(password: str, chap_id: str, chap_challenge: str) -> str:
    """The password to post back: MD5(id + password + challenge), lowercase hex."""
    digest = hashlib.md5()
    digest.update(decode(chap_id))
    digest.update(password.encode("utf-8"))
    digest.update(decode(chap_challenge))
    return digest.hexdigest()


def plain_md5(password: str) -> str:
    """Plain MD5(password), for a login page that carries no CHAP fields."""
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def decode(text: str) -> bytes:
    """Turn one escape string into the bytes it stands for.

    A backslash that is not followed by three octal digits is passed through as
    a literal backslash, because the router escapes a real backslash as ``\\134``
    and so should never emit a bare one. A backslash followed by three digits
    that cannot be a byte is an error rather than a literal, since letting it
    through would produce a wrong hash and the router would answer with nothing
    more useful than "invalid password".
    """
    data = percent_decode(text.encode("utf-8"))

    out = bytearray()
    i = 0
    length = len(data)
    while i < length:
        if not (data[i] == _BACKSLASH and i + 3 < length):
            out.append(data[i])
            i += 1
            continue

        digits = data[i + 1 : i + 4]
        if not all(ord("0") <= byte <= ord("9") for byte in digits):
            out.append(data[i])
            i += 1
            continue

        value = octal_byte(digits)
        if value is None:
            raise ParseError(bytes(digits).decode("ascii", "replace"))
        out.append(value)
        i += 4

    return bytes(out)


def percent_decode(data: bytes) -> bytes:
    """Percent-decode over raw bytes rather than over text.

    Decoding into text first runs the result through UTF-8 validation. Any byte
    of the challenge that is not valid UTF-8 gets replaced with U+FFFD and the
    hash comes out wrong with no error anywhere.
    """
    if _PERCENT not in data:
        return data

    out = bytearray()
    i = 0
    while i < len(data):
        if (
            data[i] == _PERCENT
            and i + 2 < len(data)
            and hex_nibble(data[i + 1]) is not None
            and hex_nibble(data[i + 2]) is not None
        ):
            out.append((hex_nibble(data[i + 1]) << 4) | hex_nibble(data[i + 2]))
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def octal_byte(digits: bytes) -> int | None:
    """Three ASCII digits to one byte, or None when it is not a byte.

    ``\\400`` is 256, which no byte can hold, and the digits 8 and 9 are not
    octal at all.
    """
    if any(byte < _ZERO or byte > _SEVEN for byte in digits):
        return None
    value = (digits[0] - _ZERO) * 64 + (digits[1] - _ZERO) * 8 + (digits[2] - _ZERO)
    return value if value <= 0xFF else None


def hex_nibble(byte: int) -> int | None:
    if ord("0") <= byte <= ord("9"):
        return byte - ord("0")
    if ord("a") <= byte <= ord("f"):
        return byte - ord("a") + 10
    if ord("A") <= byte <= ord("F"):
        return byte - ord("A") + 10
    return None
