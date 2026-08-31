import Foundation
import Testing

@testable import nhannhtdormwifihackKit

@Suite("CHAP")
struct CHAPTests {

    /// A 16 byte challenge in the exact form a MikroTik hotspot writes it.
    static let challenge =
        "\\154\\263\\277\\262\\304\\264\\021\\246\\064\\361\\167\\164\\003\\016\\217\\217"

    /// The matching one byte chap-id.
    static let chapID = "\\044"

    // MARK: - Decoding

    @Test("One escape becomes one byte")
    func singleEscape() throws {
        #expect(try CHAP.decode("\\044") == Data([36]))
    }

    @Test("Escapes decode in sequence")
    func multipleEscapes() throws {
        #expect(try CHAP.decode("\\001\\002") == Data([1, 2]))
    }

    @Test("The full challenge is 16 bytes and the id is 1 byte")
    func realWorldLengths() throws {
        // network_debug.log recorded "DEBUG CHAP: ID_Len=1 Ch_Len=16" during a
        // real session, so these are the lengths the router actually sends.
        #expect(try CHAP.decode(Self.chapID).count == 1)
        #expect(try CHAP.decode(Self.challenge).count == 16)
    }

    @Test("The first and last bytes of the challenge survive decoding")
    func challengeContent() throws {
        let bytes = try CHAP.decode(Self.challenge)
        #expect(bytes.first == 0o154)
        #expect(bytes.last == 0o217)
    }

    @Test("A percent-encoded backslash decodes the same as a literal one")
    func percentEncoded() throws {
        #expect(try CHAP.decode("%5C044") == Data([36]))
        #expect(try CHAP.decode("%5c044") == Data([36]))
    }

    @Test("Printable text passes through untouched")
    func printablePassthrough() throws {
        #expect(try CHAP.decode("abc123") == Data("abc123".utf8))
    }

    @Test("A backslash that starts no escape stays a literal backslash")
    func literalBackslash() throws {
        #expect(try CHAP.decode("\\abc") == Data("\\abc".utf8))
    }

    @Test("A trailing backslash with nothing after it does not crash")
    func trailingBackslash() throws {
        #expect(try CHAP.decode("ab\\") == Data("ab\\".utf8))
        #expect(try CHAP.decode("\\04") == Data("\\04".utf8))
    }

    @Test("Digits 8 and 9 are not octal and are rejected")
    func rejectsNonOctalDigits() {
        // The Python version throws here and silently falls back to a plain MD5
        // the router rejects. The Rust version emits a stray backslash instead.
        // Both produce a wrong hash, so this reports the real problem instead.
        #expect(throws: CHAP.ParseError.malformedEscape("888")) {
            try CHAP.decode("\\888")
        }
    }

    @Test("An escape too large for a byte is rejected")
    func rejectsOverflow() {
        // \400 is 256.
        #expect(throws: CHAP.ParseError.malformedEscape("400")) {
            try CHAP.decode("\\400")
        }
        #expect(throws: CHAP.ParseError.self) {
            try CHAP.decode("\\777")
        }
    }

    @Test("The largest valid escape is accepted")
    func acceptsMaximumByte() throws {
        #expect(try CHAP.decode("\\377") == Data([255]))
        #expect(try CHAP.decode("\\000") == Data([0]))
    }

    // MARK: - Hashing

    @Test("The hash matches a known good value")
    func goldenHash() throws {
        // MD5(id + password + challenge) computed independently over the byte
        // sequences above. If this changes, the router will start rejecting us.
        let hash = try CHAP.hash(
            password: "testpass",
            chapID: Self.chapID,
            chapChallenge: Self.challenge
        )
        #expect(hash == "56cc45ff762b4e347dee409ba9b5d21b")
    }

    @Test("The hash is 32 lowercase hex characters")
    func hashShape() throws {
        let hash = try CHAP.hash(
            password: "testpass",
            chapID: Self.chapID,
            chapChallenge: Self.challenge
        )
        #expect(hash.count == 32)
        #expect(hash.allSatisfy { $0.isHexDigit && !$0.isUppercase })
    }

    @Test("Ordering is id, then password, then challenge")
    func hashOrdering() throws {
        // Swapping the id and the challenge has to change the result, otherwise
        // the concatenation order is not being honoured.
        let correct = try CHAP.hash(
            password: "testpass",
            chapID: Self.chapID,
            chapChallenge: Self.challenge
        )
        let swapped = try CHAP.hash(
            password: "testpass",
            chapID: Self.challenge,
            chapChallenge: Self.chapID
        )
        #expect(correct != swapped)
    }

    @Test("A percent-encoded challenge hashes the same as a plain one")
    func percentEncodedHashesIdentically() throws {
        let plain = try CHAP.hash(
            password: "testpass",
            chapID: Self.chapID,
            chapChallenge: Self.challenge
        )
        let encoded = try CHAP.hash(
            password: "testpass",
            chapID: Self.chapID.replacingOccurrences(of: "\\", with: "%5C"),
            chapChallenge: Self.challenge.replacingOccurrences(of: "\\", with: "%5C")
        )
        #expect(plain == encoded)
    }

    @Test("Plain MD5 matches the reference value")
    func plainMD5() {
        #expect(CHAP.plainMD5("test") == "098f6bcd4621d373cade4e832627b4f6")
    }
}
