import AppIntents
import SwiftUI
import WidgetKit

/// The toggle in Control Center.
///
/// It exists because the tunnel's whole promise is that the app is closed. A
/// switch that can only be reached by opening the app is the one place the user
/// will not be when they want it.
///
/// The state it shows is asked of the system, not read from the status file.
/// The file says what the watcher last found about the hotspot; this toggle is
/// asking a different question, which is whether the watcher is running at all.
@available(iOS 18.0, *)
struct TunnelControl: ControlWidget {

    static let kind = "vn.io.nhannht.dormwifihack.tunnel-toggle"

    var body: some ControlWidgetConfiguration {
        StaticControlConfiguration(kind: Self.kind, provider: Provider()) { running in
            ControlWidgetToggle(
                "Dorm WiFi",
                isOn: running,
                action: ToggleTunnelIntent()
            ) { isOn in
                Label(
                    isOn ? "Holding the session" : "Off",
                    systemImage: isOn ? "wifi" : "wifi.slash"
                )
            }
            .tint(.green)
        }
        .displayName("Dorm WiFi")
        .description("Sign back in to the hotspot automatically, with the app closed.")
    }
}

@available(iOS 18.0, *)
extension TunnelControl {

    struct Provider: ControlValueProvider {

        /// What the controls gallery shows before the control is added. Off,
        /// because that is what it will be for anyone who has not turned it on.
        var previewValue: Bool { false }

        func currentValue() async throws -> Bool {
            await TunnelSwitch.isRunning()
        }
    }
}
