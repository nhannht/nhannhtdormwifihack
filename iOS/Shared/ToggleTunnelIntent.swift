import AppIntents
import Foundation

/// What the Control Center toggle does when it is tapped.
///
/// Compiled into the app as well as the widget extension. iOS runs a control's
/// action in the app's process when the app happens to be running and in the
/// extension's process otherwise, and it has to do the same thing either way,
/// so it has to exist in both.
///
/// It flips a configuration that is already there and never creates one. Adding
/// a VPN configuration raises a system prompt, and a prompt needs something on
/// screen to answer it, which a control tapped from the Lock Screen has not
/// got. So the first time is the app's job, and after that this works.
struct ToggleTunnelIntent: SetValueIntent {

    static let title: LocalizedStringResource = "Hold the hotspot session"

    static let description = IntentDescription(
        """
        Turns the background watcher on or off. On, it signs back in to the hotspot every time \
        the session runs out, with the app closed and the phone locked.
        """
    )

    @Parameter(title: "Holding the session")
    var value: Bool

    func perform() async throws -> some IntentResult {
        guard try await TunnelSwitch.flip(on: value) else { throw NotSetUp() }

        // Starting and stopping is exactly the change nothing else can work out
        // for itself, so say so rather than leaving the widget showing the last
        // thing the watcher wrote before it was switched off.
        Displays.refresh()
        return .result()
    }

    struct NotSetUp: Error, CustomLocalizedStringResourceConvertible {
        var localizedStringResource: LocalizedStringResource {
            "Open Dorm WiFi once and turn on \"Hold the session with the app closed\" first."
        }
    }
}
