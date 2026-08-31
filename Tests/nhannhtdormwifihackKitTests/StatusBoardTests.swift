import Foundation
import Testing

@testable import nhannhtdormwifihackKit

@Suite("What the menu shows")
struct StatusBoardTests {

    @Test("Every state has something to say")
    func everyStateHasAHeadline() {
        // The menu prints this string directly, so a state with an empty or
        // placeholder headline is a blank line in the user's menu.
        var status = WatcherStatus()
        status.network = "Some Hotspot"

        let states: [WatcherStatus.Connection] = [
            .starting, .signedIn,
            .signingIn, .wrongNetwork("timed out"), .onFallback("Phone"),
            .stopping, .stopped,
        ]

        for state in states {
            status.connection = state
            #expect(!status.headline.isEmpty)
            #expect(status.headline.trimmingCharacters(in: .whitespaces) == status.headline)
        }
    }

    @Test("The countdown reads as a time, not a number of seconds")
    func countdownIsReadable() {
        var status = WatcherStatus()
        status.connection = .signedIn
        status.expiresAt = Date().addingTimeInterval(745)
        #expect(status.headline == "Signed in, 12m 25s left")

        // The router does not always give a number, and the menu still has to
        // say something true.
        status.expiresAt = nil
        #expect(status.headline == "Signed in")
    }

    @Test("The countdown keeps counting down without another router check")
    func countdownIsLiveNotFrozen() {
        // The watcher can sleep a full minute between checks. Storing seconds
        // meant the menu showed a number that was up to a minute out of date,
        // and at a session boundary that reads as time remaining when there is
        // none. Storing the moment it ends makes the menu correct whenever it
        // is opened.
        var status = WatcherStatus()
        status.connection = .signedIn
        status.expiresAt = Date().addingTimeInterval(-5)

        #expect(status.remainingNow == 0)
        #expect(status.headline == "Signed in, 0s left")
    }

    @Test("Being on the fallback network only counts as healthy with internet")
    func fallbackHealthNeedsInternet() {
        // The icon is the at-a-glance signal. Showing it as healthy while the
        // phone hotspot is not actually passing traffic would be a lie.
        var status = WatcherStatus()
        status.connection = .onFallback("Phone")
        status.hasInternet = false
        #expect(!status.isHealthy)

        status.hasInternet = true
        #expect(status.isHealthy)
    }

    @Test("A late reader gets the current state, not silence")
    func subscriberGetsCurrentValue() async {
        // The menu subscribes once at launch and can open at any moment. If the
        // stream only carried future changes, a menu opened during a quiet
        // minute would show nothing at all.
        let board = StatusBoard()
        var sent = WatcherStatus()
        sent.connection = .signedIn
        await board.publish(sent)

        var received: WatcherStatus?
        for await status in await board.subscribe() {
            received = status
            break
        }

        #expect(received?.connection == .signedIn)
        #expect(await board.snapshot().connection == .signedIn)
    }
}
