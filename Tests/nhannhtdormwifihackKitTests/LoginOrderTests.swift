import Foundation
import Testing

@testable import nhannhtdormwifihackKit

/// Answers requests from a script instead of from the network, and records the
/// order they arrived in.
final class ScriptedRouter: URLProtocol, @unchecked Sendable {

    /// Paths mapped to the body the router should reply with.
    nonisolated(unsafe) static var replies: [String: String] = [:]

    /// Every path asked for, in order. This is the thing under test: the bug was
    /// entirely about which request went out first.
    nonisolated(unsafe) static var asked: [String] = []

    private static let lock = NSLock()

    static func reset(replies: [String: String]) {
        lock.lock()
        defer { lock.unlock() }
        Self.replies = replies
        Self.asked = []
    }

    static func record(_ path: String) {
        lock.lock()
        defer { lock.unlock() }
        asked.append(path)
    }

    static func trail() -> [String] {
        lock.lock()
        defer { lock.unlock() }
        return asked
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let path = request.url?.path ?? ""
        Self.record(path)

        guard let url = request.url, let body = Self.replies[path] else {
            client?.urlProtocol(
                self,
                didFailWithError: URLError(.timedOut)
            )
            return
        }

        let response = HTTPURLResponse(
            url: url, statusCode: 200, httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "text/html; charset=utf-8"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@Suite("Order of the login steps", .serialized)
struct LoginOrderTests {

    private func settings() -> Settings {
        var settings = Settings()
        settings.gatewayIP = "192.168.200.1"
        settings.autoConnect = false
        return settings
    }

    private func client() -> PortalClient {
        PortalClient(protocolClasses: [ScriptedRouter.self])
    }

    @Test("An open session is left alone, and /login is never touched")
    func openSessionIsNotDisturbed() async {
        // This is the bug that kept dropping the connection. Rejoining the
        // wireless network resumes the existing session, because the router
        // keys sessions on MAC address. Asking /login in that state stalls
        // forever, the stall is reported as a failed login, and the caller
        // rejoins the network and starts the whole loop again.
        ScriptedRouter.reset(replies: ["/status": SessionProbeTests.statusPage])

        let result = await LoginEngine(settings: settings(), log: ActivityLog())
            .run(using: client())

        #expect(result == .alreadyActive)
        #expect(result.isConnected)
        #expect(ScriptedRouter.trail() == ["/status"])
        #expect(!ScriptedRouter.trail().contains("/login"))
    }

    @Test("A silent router is reported as the wrong network, not a failed login")
    func silentRouterIsNotALoginFailure() async {
        // Nothing answers. Asking for a login page three times over cannot
        // change that, and the answer the caller needs is "you are not on that
        // network" rather than "the login failed".
        ScriptedRouter.reset(replies: [:])

        let result = await LoginEngine(settings: settings(), log: ActivityLog())
            .run(using: client())

        #expect(!result.isConnected)
        #expect(ScriptedRouter.trail() == ["/status"])
        #expect(!ScriptedRouter.trail().contains("/login"))
    }

    @Test("Once the session has lapsed, /login is asked for")
    func lapsedSessionReachesTheLoginPage() async {
        // The mirror image: with no session open the router answers /login, so
        // this is the one state where asking it is correct.
        ScriptedRouter.reset(replies: [
            "/status": "<html><body>Signed out</body></html>",
            "/login": #"<form><input id="chap-id" value="\362"></form>"#,
        ])

        _ = await LoginEngine(settings: settings(), log: ActivityLog())
            .run(using: client())

        let trail = ScriptedRouter.trail()
        #expect(trail.first == "/status")
        #expect(trail.contains("/login"))
    }
}

@Suite("The safety net")
struct SafetyNetTests {

    @Test("A fallback network survives a save and a load")
    func fallbackRoundTrips() throws {
        // The name goes in the settings file, never in the source: it is a real
        // identifier belonging to a real person's phone.
        var settings = Settings()
        settings.fallbackWifiName = "Some Saved Hotspot"

        let data = try JSONEncoder().encode(settings)
        let text = String(decoding: data, as: UTF8.self)
        #expect(text.contains("fallback_wifi_name"))

        let loaded = try JSONDecoder().decode(Settings.self, from: data)
        #expect(loaded.fallbackWifiName == "Some Saved Hotspot")
    }

    @Test("The net is off until a network is named")
    func offByDefault() {
        // An unset fallback has to mean "no net", not "join something". The
        // check that fires the net reads this exact emptiness.
        #expect(Settings().fallbackWifiName.isEmpty)
    }

    @Test("An unsaved fallback network is not joinable")
    func unsavedFallbackIsRefused() {
        // macOS can only rejoin networks it has saved, so a net naming one it
        // has not is worse than no net: it would be trusted while absent.
        let outcome = WiFi.join(ssid: "definitely not a saved network \(UUID().uuidString)")
        #expect(outcome != .joined)
        if case .notSaved = outcome {} else if case .noInterface = outcome {} else {
            Issue.record("Expected a refusal, got \(outcome)")
        }
    }
}
