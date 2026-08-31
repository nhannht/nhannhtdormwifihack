import Foundation
import Testing

@testable import nhannhtdormwifihackKit

@Suite("Login flow")
struct LoginEngineTests {

    private func engine(
        gateway: String = "10.5.50.1",
        fallback: String = ""
    ) -> LoginEngine {
        var settings = Settings()
        settings.gatewayIP = gateway
        settings.portalFallbackURL = fallback
        return LoginEngine(settings: settings, log: ActivityLog())
    }

    // MARK: - Working out where the portal is

    @Test("An absolute form action is used as given")
    func absoluteAction() throws {
        let html = #"<form action="http://portal.example.test/login"><input name="a" value="1"></form>"#
        let url = try engine().portalURL(from: html)

        #expect(url.host() == "portal.example.test")
        #expect(url.path == "/login")
        #expect(url.query()?.contains("a=1") == true)
    }

    @Test("A relative form action belongs to the router")
    func relativeAction() throws {
        let html = #"<form action="/login"><input name="a" value="1"></form>"#
        let url = try engine(gateway: "10.5.50.1").portalURL(from: html)

        #expect(url.absoluteString.hasPrefix("http://10.5.50.1/login"))
    }

    @Test("With no form action the configured fallback is used")
    func fallbackAction() throws {
        let html = #"<input name="a" value="1">"#
        let url = try engine(fallback: "http://portal.example.test/login")
            .portalURL(from: html)

        #expect(url.host() == "portal.example.test")
        #expect(url.query()?.contains("a=1") == true)
    }

    @Test("With no form action and no fallback, say so clearly")
    func noPortalAtAll() {
        // The Rust version silently substituted a hardcoded host here, so a
        // misconfigured setup looked like a network failure instead.
        #expect(throws: LoginEngine.EngineError.self) {
            try engine().portalURL(from: #"<input name="a" value="1">"#)
        }
    }

    @Test("Hidden fields are carried across to the portal")
    func carriesHiddenFields() throws {
        let html = """
            <form action="http://portal.example.test/login">
              <input name="dst" value="http://captive.apple.com/hotspot-detect.html">
              <input id="chap-id" value="\\044">
              <input name="username" value="">
            </form>
            """
        let url = try engine().portalURL(from: html)
        let query = url.query() ?? ""

        #expect(query.contains("dst="))
        #expect(query.contains("chap-id="))
        // Empty values are not worth forwarding.
        #expect(!query.contains("username="))
    }

    @Test("A bare relative action resolves against the router's login page")
    func bareRelativeAction() throws {
        // MikroTik templates use actions like this. Treating anything without a
        // leading slash as absolute produced a URL with no scheme at all, which
        // failed two steps later as an unexplained "unsupported URL".
        let html = #"<form action="alogin.html"><input name="a" value="1"></form>"#
        let url = try engine(gateway: "10.5.50.1").portalURL(from: html)

        #expect(url.scheme == "http")
        #expect(url.host() == "10.5.50.1")
        #expect(url.path == "/alogin.html")
    }

    @Test("A protocol-relative action keeps its own host")
    func protocolRelativeAction() throws {
        let html =
            #"<form action="//portal.example.test/login"><input name="a" value="1"></form>"#
        let url = try engine(gateway: "10.5.50.1").portalURL(from: html)

        #expect(url.scheme == "http")
        #expect(url.host() == "portal.example.test")
        #expect(url.path == "/login")
    }

    @Test("A form action that already has a query keeps it")
    func preservesExistingQuery() throws {
        let html = #"<form action="http://portal.example.test/login?tenant=7"><input name="a" value="1"></form>"#
        let url = try engine().portalURL(from: html)
        let query = url.query() ?? ""

        #expect(query.contains("tenant=7"))
        #expect(query.contains("a=1"))
    }

    // MARK: - The router's real login page

    /// The shape of the page this hotspot actually serves, captured from the
    /// live router at the moment a session lapsed. Identifiers are replaced with
    /// documentation values; the structure is byte-for-byte what the router
    /// sent, and the structure is the part that matters.
    ///
    /// Two things here have caught every implementation of this: the inputs
    /// carry `id` and no `name` at all, and the form has no `action`.
    static let realLoginPage = """
        <html><head><title>hotspot login</title>
        <script src="/md5.js"></script></head><body>
        <form id="authForm" style="display:none;">
          <input type="hidden" id="serial"         value="00-00-5E-00-53-00">
          <input type="hidden" id="client_mac"     value="00:00:5E:00:53:2A">
          <input type="hidden" id="client_ip"      value="192.168.0.42">
          <input type="hidden" id="userurl"        value="http://www.msftconnecttest.com/redirect">
          <input type="hidden" id="login_url"      value="http://192.168.0.1/login">
          <input type="hidden" id="chap-id"        value="\\362">
          <input type="hidden" id="chap-challenge" value="\\217\\160\\360\\062\\345\\151\\304\\161\\311\\202\\001\\310\\142\\167\\155\\307">
        </form>
        <script>setTimeout(function(){ msg("Đang chuyển hướng..."); }, 2000);</script>
        </body></html>
        """

    @Test("The CHAP fields are read from id, because the page has no name at all")
    func readsChapFieldsByID() throws {
        // Not one input on the real page carries a name attribute. Looking up by
        // name finds nothing and makes a perfectly good login page look broken.
        let id = HTMLInputScraper.value(identifiedBy: "chap-id", in: Self.realLoginPage)
        let challenge = HTMLInputScraper.value(
            identifiedBy: "chap-challenge", in: Self.realLoginPage
        )

        #expect(id == #"\362"#)
        #expect(challenge != nil)

        // The router's own log recorded ID_Len=1 Ch_Len=16 for this exact shape.
        #expect(try CHAP.decode(#"\362"#).count == 1)
        #expect(try CHAP.decode(challenge ?? "").count == 16)
        #expect(Array(try CHAP.decode(#"\362"#)) == [242])
    }

    @Test("Every hidden field is carried to the portal, keyed by its id")
    func forwardsFieldsKeyedByID() {
        let names = Set(HTMLInputScraper.queryItems(in: Self.realLoginPage).map(\.name))

        #expect(names.contains("chap-id"))
        #expect(names.contains("chap-challenge"))
        #expect(names.contains("serial"))
        #expect(names.contains("client_mac"))
        #expect(names.contains("login_url"))
    }

    @Test("The page carries no form action, so the fallback portal is used")
    func realPageHasNoFormAction() throws {
        // Confirmed on the live capture and in the original's own log. The
        // hardcoded portal fallback is load-bearing, not a rare edge case.
        #expect(HTMLInputScraper.formAction(in: Self.realLoginPage) == nil)

        let url = try engine(fallback: "http://portal.example.test/login")
            .portalURL(from: Self.realLoginPage)

        #expect(url.host() == "portal.example.test")
        #expect(url.query()?.contains("chap-id") == true)
    }

    // MARK: - Digging the login form out of the portal's JSON

    @Test("The form is found at each of the three nesting depths")
    func findsFormAtEveryDepth() {
        let form = JSONValue.string("<input name='username' value='u'>")
        let e = engine()

        let deep = JSONValue.object([
            "captiveContextDTO": .object(["captiveContext": .object(["contentAuthenForm": form])])
        ])
        let middle = JSONValue.object([
            "captiveContext": .object(["contentAuthenForm": form])
        ])
        let shallow = JSONValue.object(["contentAuthenForm": form])

        #expect(e.authenticationForm(in: deep) == form.stringValue)
        #expect(e.authenticationForm(in: middle) == form.stringValue)
        #expect(e.authenticationForm(in: shallow) == form.stringValue)
    }

    @Test("A missing or empty form reads as absent")
    func missingForm() {
        let e = engine()
        #expect(e.authenticationForm(in: .object([:])) == nil)
        #expect(e.authenticationForm(in: .object(["contentAuthenForm": .string("")])) == nil)
        #expect(e.authenticationForm(in: .object(["contentAuthenForm": .null])) == nil)
    }
}

@Suite("Form encoding")
struct FormEncodingTests {

    @Test("Spaces become plus signs, not percent escapes")
    func encodesSpacesAsPlus() {
        let encoded = PortalClient.formEncoded([("q", "hello world")])
        #expect(encoded == "q=hello+world")
    }

    @Test("Reserved characters are escaped")
    func escapesReserved() {
        let encoded = PortalClient.formEncoded([("dst", "http://x.test/a?b=1&c=2")])
        #expect(encoded.contains("%3A%2F%2F"))
        #expect(encoded.contains("%26"))
    }

    @Test("Fields are joined in order")
    func joinsFields() {
        let encoded = PortalClient.formEncoded([("a", "1"), ("b", "2")])
        #expect(encoded == "a=1&b=2")
    }
}

@Suite("Private host detection")
struct PrivateHostTests {

    @Test("Private ranges are recognised", arguments: [
        "192.168.1.1", "10.5.50.1", "172.16.0.1", "172.31.255.255", "127.0.0.1", "169.254.1.1",
    ])
    func privateAddresses(host: String) {
        #expect(PortalClient.isPrivateHost(host))
    }

    @Test("Public addresses and names are not", arguments: [
        "8.8.8.8", "1.1.1.1", "172.32.0.1", "portal.example.test", "captive.apple.com",
    ])
    func publicAddresses(host: String) {
        #expect(!PortalClient.isPrivateHost(host))
    }

    @Test("A public name carrying a private address is still public", arguments: [
        "10.0.0.1.evil.example", "192.168.1.1.attacker.example", "portal.10.0.0.1",
        "10.0.0.1.example.test", "127.0.0.1.example.test",
    ])
    func addressShapedNames(host: String) {
        // Non-numeric labels used to be dropped before the count, so all of
        // these looked like four octets in a private range and any certificate
        // they presented was accepted. The portal host comes from the router's
        // login page, which anyone on the captive network can serve.
        #expect(!PortalClient.isPrivateHost(host))
    }

    @Test("Only plain decimal octets count", arguments: [
        "10.0.0.1.", "10.0.0", "10.0.0.1.1", "10.0.0.0x1", "10.0.0.+1", "10..0.1",
    ])
    func malformedAddresses(host: String) {
        #expect(!PortalClient.isPrivateHost(host))
    }

    @Test("Loopback by name is private")
    func loopbackNames() {
        #expect(PortalClient.isPrivateHost("localhost"))
        #expect(PortalClient.isPrivateHost("::1"))
    }
}

@Suite("Client lifetime")
struct PortalClientLifetimeTests {

    final class Box: @unchecked Sendable {
        weak var client: PortalClient?
    }

    /// Not inlined, so the local reference cannot be kept alive past the call.
    @inline(never)
    private func makeAndDrop(recordingInto box: Box) {
        let client = PortalClient(timeout: 1)
        box.client = client
        withExtendedLifetime(client) {}
    }

    @Test("A client that goes out of scope is released")
    func clientIsReleased() {
        // URLSession holds its delegate strongly until the session is
        // invalidated, so a client acting as its own delegate kept itself alive:
        // deinit never ran, nothing invalidated the session, and the watcher
        // leaked one per loop pass until it ran out of file descriptors.
        let box = Box()
        autoreleasepool {
            makeAndDrop(recordingInto: box)
        }
        #expect(box.client == nil, "PortalClient leaked; the session still holds it.")
    }
}

@Suite("JSON values")
struct JSONValueTests {

    @Test("Large whole numbers survive a round trip unchanged")
    func preservesLargeIntegers() throws {
        // The portal's context carries ids past the 2^53 a Double holds exactly
        // and expects them echoed back. Through Double, 9007199254740993 came
        // back as ...992 and the long id in exponential notation, and the portal
        // answered by refusing to issue an account.
        let source = #"{"id":9007199254740993,"n":1234567890123456789}"#
        let value = try JSONValue.from(Data(source.utf8))

        #expect(value["id"]?.intValue == 9_007_199_254_740_993)
        #expect(value["n"]?.intValue == 1_234_567_890_123_456_789)

        let encoded = String(decoding: try value.encoded(), as: UTF8.self)
        #expect(encoded.contains("9007199254740993"))
        #expect(encoded.contains("1234567890123456789"))
        #expect(!encoded.lowercased().contains("e+"))
    }

    @Test("Fractions are still doubles")
    func keepsFractions() throws {
        let value = try JSONValue.from(Data(#"{"a":1.5,"b":-0.25}"#.utf8))

        #expect(value["a"]?.doubleValue == 1.5)
        #expect(value["b"]?.doubleValue == -0.25)
        #expect(value["a"]?.intValue == nil)
    }

    @Test("The other kinds still round trip")
    func keepsEverythingElse() throws {
        let source = #"{"a":null,"b":true,"c":"x","d":[1,2]}"#
        let value = try JSONValue.from(Data(source.utf8))

        #expect(value["a"]?.isNull == true)
        #expect(value["b"]?.boolValue == true)
        #expect(value["c"]?.stringValue == "x")
        #expect(value["d"]?[1]?.intValue == 2)
    }
}

@Suite("Settings")
struct SettingsTests {

    @Test("A zero interval cannot produce a zero timeout")
    func zeroInterval() {
        var settings = Settings()
        settings.interval = 0
        #expect(settings.sessionTimeoutSeconds == 60)
    }

    @Test("An absurd interval cannot overflow the session length")
    func absurdInterval() {
        var settings = Settings()
        settings.interval = .max

        // interval comes out of a file that can say anything, and multiplying an
        // unbounded number by 60 traps.
        #expect(settings.sessionTimeoutSeconds == 1440 * 60)
    }

    @Test("Keys this build does not know about survive a save")
    func keepsUnknownKeys() throws {
        // Re-encoding only the declared keys deleted everything else, so a
        // single run of `login` took the Windows build's own settings with it.
        let json = """
            {
                "interval": 15,
                "windows_only_flag": true,
                "windows_only_table": { "n": 7 }
            }
            """
        let settings = try JSONDecoder().decode(Settings.self, from: Data(json.utf8))
        #expect(settings.unknownKeys["windows_only_flag"]?.boolValue == true)

        let again = try JSONDecoder().decode(
            Settings.self,
            from: try JSONEncoder().encode(settings)
        )

        #expect(again.interval == 15)
        #expect(again.unknownKeys["windows_only_flag"]?.boolValue == true)
        #expect(again.unknownKeys["windows_only_table"]?["n"]?.intValue == 7)
    }

    @Test("An explicit router address is left alone")
    func explicitGatewayWins() {
        var settings = Settings()
        settings.gatewayIP = "10.5.50.1"

        // Only an empty value asks the machine. A set one is an override and is
        // never second-guessed.
        #expect(settings.resolvingGateway().gatewayIP == "10.5.50.1")
    }

    @Test("An empty router address is filled in from the live default route")
    func resolvesGatewayFromRoute() {
        let resolved = Settings().resolvingGateway().gatewayIP

        // Whatever this machine is on, it must agree with the system route
        // table, and must not be the address that used to be baked in.
        #expect(resolved == (DefaultRoute.gateway() ?? ""))
    }

    @Test("A known key is never taken for an unknown one")
    func knownKeysAreNotDuplicated() throws {
        let json = #"{"interval":9,"gateway_ip":"10.5.50.1"}"#
        let settings = try JSONDecoder().decode(Settings.self, from: Data(json.utf8))

        #expect(settings.unknownKeys.isEmpty)

        let encoded = String(
            decoding: try JSONEncoder().encode(settings),
            as: UTF8.self
        )
        #expect(encoded.components(separatedBy: "\"gateway_ip\"").count == 2)
    }
}
