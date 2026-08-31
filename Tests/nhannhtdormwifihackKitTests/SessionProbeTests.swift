import Foundation
import Testing

@testable import nhannhtdormwifihackKit

@Suite("Session probe")
struct SessionProbeTests {

    private let anyURL = URL(string: "http://192.168.200.1/status")!

    /// The status page this hotspot actually serves, trimmed to its table.
    static let statusPage = """
        <html><head><title>Status - INET</title>
        <meta http-equiv="refresh" content="60"></head>
        <body><div class="status-card"><table>
        <tr><td>Địa chỉ IP</td><td>192.168.0.42</td></tr>
        <tr><td>Địa chỉ MAC</td><td>00:00:5E:00:53:2A</td></tr>
        <tr><td>Thời gian kết nối</td><td>2m35s</td></tr>
        <tr><td>Thời gian còn lại</td><td>12m25s</td></tr>
        </table></div></body></html>
        """

    @Test("A page with session details means we are signed in")
    func activeSession() {
        let status = SessionProbe.interpret(body: Self.statusPage, finalURL: anyURL)

        #expect(status.isActive)
        #expect(status.info?.ipAddress == "192.168.0.42")
        #expect(status.info?.macAddress == "00:00:5E:00:53:2A")
    }

    @Test("The router's own countdown is read rather than guessed")
    func readsRemainingTime() {
        let info = SessionProbe.interpret(body: Self.statusPage, finalURL: anyURL).info

        #expect(info?.connectedFor == 155)
        #expect(info?.remaining == 745)
        // 2m35s used plus 12m25s left is exactly the 15 minute session.
        #expect((info?.connectedFor ?? 0) + (info?.remaining ?? 0) == 900)
    }

    @Test("The stock English status page is understood too")
    func englishStatusPage() {
        let page = "<table><tr><td>Uptime</td><td>1h2m3s</td></tr></table>"
        let status = SessionProbe.interpret(body: page, finalURL: anyURL)

        #expect(status.isActive)
        #expect(status.info?.connectedFor == 3723)
    }

    @Test("Being bounced to the login page means we are signed out")
    func redirectedToLogin() {
        let url = URL(string: "http://192.168.200.1/login?dst=x")!
        #expect(SessionProbe.interpret(body: "<html></html>", finalURL: url) == .expired)
    }

    @Test("A page carrying a fresh challenge means we are signed out")
    func challengePageMeansExpired() {
        let body = #"<form><input id="chap-challenge" value="\044"></form>"#
        #expect(SessionProbe.interpret(body: body, finalURL: anyURL) == .expired)
    }

    @Test("The page served at the moment a session lapses is not a lost router")
    func lapsedSessionPage() {
        // Captured at a real session boundary: the router answers, but the
        // session rows are simply gone. Reading that as an unreachable router
        // made the watcher rejoin the wireless network at every session
        // boundary, costing about eight seconds and a dropped link each time.
        // The router answered, so we are on the right network and signed out.
        let page = """
            <html><head><title>Status - INET</title></head>
            <body><div class="status-card"><table></table></div></body></html>
            """
        let status = SessionProbe.interpret(body: page, finalURL: anyURL)

        #expect(status != .unreachable("Status page did not identify itself"))
        guard case .unknown = status else {
            Issue.record("A page with no session rows should read as no session")
            return
        }
    }

    @Test("An unrecognised page is reported as unknown, not as success")
    func unknownPage() {
        // The old code treated anything it could not read as a win, so a failed
        // sign-in could report success.
        guard case .unknown = SessionProbe.interpret(body: "<html>hi</html>", finalURL: anyURL)
        else {
            Issue.record("Expected an unknown status")
            return
        }
    }

    @Test(
        "Durations parse",
        arguments: [
            ("45s", 45), ("2m35s", 155), ("12m25s", 745),
            ("1h2m3s", 3723), ("1d2h3m4s", 93784), ("15m", 900),
        ]
    )
    func parsesDurations(text: String, expected: Int) {
        #expect(SessionProbe.duration(text) == expected)
    }

    @Test("Anything that is not a duration is rejected")
    func rejectsNonDurations() {
        // An IP address must never be mistaken for a time, or the scheduler
        // would sleep for a nonsense interval.
        #expect(SessionProbe.duration("192.168.0.42") == nil)
        #expect(SessionProbe.duration("") == nil)
        #expect(SessionProbe.duration("hello") == nil)
        #expect(SessionProbe.duration("00:00:5E:00:53:2A") == nil)
    }

    @Test("A number too large to hold is rejected rather than crashing")
    func rejectsOverflowingDurations() {
        // This text comes off the router over plain HTTP. Unchecked
        // `number * 10 + digit` trapped on a long enough run of digits and took
        // the whole watcher down with it.
        #expect(SessionProbe.duration("999999999999999999999s") == nil)
        #expect(SessionProbe.duration("99999999999999999999d") == nil)
        #expect(SessionProbe.duration("\(Int.max)s") == 9_223_372_036_854_775_807)
    }

    @Test("Script and style contents are not mistaken for fields")
    func ignoresScriptAndStyle() {
        let page = """
            <style>.a { content: "Thời gian còn lại"; }</style>
            <script>var x = "Địa chỉ IP";</script>
            <table><tr><td>Thời gian còn lại</td><td>5m</td></tr></table>
            """
        #expect(SessionProbe.interpret(body: page, finalURL: anyURL).info?.remaining == 300)
    }

    @Test("A script spanning several lines is stripped too")
    func ignoresMultiLineScript() {
        // A dot does not cross a line break, so the body of a script written
        // over more than one line survived into the fields, where its text was
        // read as labels and its numbers as durations.
        let page = """
            <script>
              var labels = "Thời gian còn lại";
              var v = "9m9s";
            </script>
            <table><tr><td>Thời gian còn lại</td><td>5m</td></tr></table>
            """
        #expect(SessionProbe.interpret(body: page, finalURL: anyURL).info?.remaining == 300)
    }

    @Test("A script that merely mentions the challenge is not a login page")
    func scriptMentioningChallenge() {
        // Reading the challenge out of script text reported an open session as
        // signed out on every pass, which sent the watcher into a login loop.
        let page = """
            <script>
              var field = "chap-id";
            </script>
            <table><tr><td>Thời gian còn lại</td><td>5m</td></tr></table>
            """
        #expect(SessionProbe.interpret(body: page, finalURL: anyURL).isActive)
    }
}
