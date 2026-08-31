import CryptoKit
import Foundation

/// MikroTik hotspot CHAP authentication.
///
/// A MikroTik hotspot never accepts a plaintext password. Its login page carries
/// two hidden fields, `chap-id` and `chap-challenge`, and the password you post
/// back has to be `MD5(id + password + challenge)` taken over the raw bytes of
/// each part, in that order.
///
/// Both fields arrive as escape strings. Any byte that is not printable ASCII is
/// written as a backslash followed by three octal digits, so `\154` means the
/// single byte 108. On top of that the whole value is sometimes percent-encoded,
/// which turns each backslash into `%5C`.
public enum CHAP {

    public enum ParseError: Error, Equatable, CustomStringConvertible {
        /// A backslash was followed by three digits that do not form a byte.
        case malformedEscape(String)

        public var description: String {
            switch self {
            case .malformedEscape(let digits):
                return """
                    Malformed octal escape \\\(digits). Expected three digits in the \
                    range 0 to 7 whose value is 255 or less.
                    """
            }
        }
    }

    // MARK: - Hashing

    /// The password to post back to the router: `MD5(id + password + challenge)`
    /// over raw bytes, as lowercase hex.
    public static func hash(
        password: String,
        chapID: String,
        chapChallenge: String
    ) throws -> String {
        var md5 = Insecure.MD5()
        md5.update(data: try decode(chapID))
        md5.update(data: Data(password.utf8))
        md5.update(data: try decode(chapChallenge))
        return hex(md5.finalize())
    }

    /// Plain `MD5(password)`. Used only when the login page carries no CHAP
    /// fields at all, which means the hotspot is not asking for a challenge.
    public static func plainMD5(_ password: String) -> String {
        hex(Insecure.MD5.hash(data: Data(password.utf8)))
    }

    // MARK: - Decoding

    /// Turn one escape string into the bytes it stands for.
    ///
    /// A backslash that is not followed by three octal digits is passed through
    /// as a literal backslash, because the router escapes a real backslash as
    /// `\134` and so should never emit a bare one. A backslash followed by three
    /// digits that cannot be a byte is an error rather than a literal, since
    /// letting it through would produce a wrong hash and the router would answer
    /// with nothing more useful than "invalid password".
    public static func decode(_ input: String) throws -> Data {
        let bytes = percentDecoded(Array(input.utf8))
        let backslash = UInt8(ascii: "\\")

        var out = Data()
        out.reserveCapacity(bytes.count)

        var i = 0
        while i < bytes.count {
            guard bytes[i] == backslash, i + 3 < bytes.count else {
                out.append(bytes[i])
                i += 1
                continue
            }

            let digits = (bytes[i + 1], bytes[i + 2], bytes[i + 3])
            guard isASCIIDigit(digits.0), isASCIIDigit(digits.1), isASCIIDigit(digits.2) else {
                out.append(bytes[i])
                i += 1
                continue
            }

            guard let byte = octalByte(digits) else {
                throw ParseError.malformedEscape(
                    String(decoding: [digits.0, digits.1, digits.2], as: UTF8.self)
                )
            }

            out.append(byte)
            i += 4
        }

        return out
    }

    /// Percent-decode over raw bytes rather than over text.
    ///
    /// Decoding into a String first, which both the Python and the Rust versions
    /// do, runs the result through UTF-8 validation. Any byte of the challenge
    /// that is not valid UTF-8 gets replaced with U+FFFD and the hash comes out
    /// wrong with no error anywhere.
    private static func percentDecoded(_ bytes: [UInt8]) -> [UInt8] {
        let percent = UInt8(ascii: "%")
        guard bytes.contains(percent) else { return bytes }

        var out: [UInt8] = []
        out.reserveCapacity(bytes.count)

        var i = 0
        while i < bytes.count {
            if bytes[i] == percent, i + 2 < bytes.count,
               let high = hexNibble(bytes[i + 1]), let low = hexNibble(bytes[i + 2]) {
                out.append(high << 4 | low)
                i += 3
            } else {
                out.append(bytes[i])
                i += 1
            }
        }
        return out
    }

    private static func isASCIIDigit(_ byte: UInt8) -> Bool {
        byte >= UInt8(ascii: "0") && byte <= UInt8(ascii: "9")
    }

    /// Three ASCII digits to one byte, or nil when a digit is 8 or 9, or when the
    /// value does not fit in a byte. `\400` is 256, which no byte can hold.
    private static func octalByte(_ digits: (UInt8, UInt8, UInt8)) -> UInt8? {
        let zero = UInt8(ascii: "0")
        let seven = UInt8(ascii: "7")
        guard digits.0 <= seven, digits.1 <= seven, digits.2 <= seven else { return nil }

        let value = Int(digits.0 - zero) * 64
            + Int(digits.1 - zero) * 8
            + Int(digits.2 - zero)
        return value <= 0xFF ? UInt8(value) : nil
    }

    private static func hexNibble(_ byte: UInt8) -> UInt8? {
        switch byte {
        case UInt8(ascii: "0")...UInt8(ascii: "9"): byte - UInt8(ascii: "0")
        case UInt8(ascii: "a")...UInt8(ascii: "f"): byte - UInt8(ascii: "a") + 10
        case UInt8(ascii: "A")...UInt8(ascii: "F"): byte - UInt8(ascii: "A") + 10
        default: nil
        }
    }

    private static func hex(_ digest: some Sequence<UInt8>) -> String {
        digest.map { String(format: "%02x", $0) }.joined()
    }
}
