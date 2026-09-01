import SwiftUI

@main
struct DormWiFiApp: App {

    @State private var watcher = Watcher()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            ContentView(watcher: watcher)
        }
        .onChange(of: phase) { _, now in
            // The loop only runs while the app is on the screen, because iOS
            // suspends it otherwise. Handing over deliberately, rather than
            // being suspended mid-request, is what lets it leave a reminder for
            // the moment the session ends.
            Task {
                switch now {
                case .active: await watcher.cameBackToScreen()
                case .background: await watcher.goingToBackground()
                default: break
                }
            }
        }
    }
}
