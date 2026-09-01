import Foundation
import NetworkExtension
import Observation

/// Switches the background watcher on and off.
///
/// The watcher that survives the app closing runs inside a Network Extension,
/// which is a separate process. This app cannot start that process directly.
/// What it does instead is write a tunnel configuration into the system's own
/// settings and ask the system to bring it up; iOS then launches the extension
/// and keeps it running. That indirection is the whole reason this works: the
/// thing holding the session is owned by iOS, not by this app, so putting the
/// app away does not stop it.
///
/// The configuration is a real entry in Settings under VPN. Deleting it there,
/// or with `forget()`, is the complete uninstall.
@MainActor
@Observable
final class TunnelController {

    private(set) var status: NEVPNStatus = .invalid

    /// Why the last attempt to save or start failed, in words for the screen.
    private(set) var problem: String?

    private var manager: NETunnelProviderManager?
    private var watching: Task<Void, Never>?

    // MARK: - What the screen reads

    var isRunning: Bool { TunnelSwitch.running(status) }

    var isInstalled: Bool { manager != nil }

    var plainStatus: String {
        switch status {
        case .invalid: "Not set up yet"
        case .disconnected: "Off"
        case .connecting: "Starting"
        case .connected: "Holding the session, app closed or not"
        case .reasserting: "Reconnecting"
        case .disconnecting: "Stopping"
        @unknown default: "Unknown"
        }
    }

    // MARK: - Setting it up

    /// Find the configuration this app wrote, if it is already there.
    func load() async {
        do {
            manager = try await TunnelSwitch.existing()
            status = manager?.connection.status ?? .invalid
            watchStatus()
        } catch {
            problem = "Could not read the VPN settings: \(error.localizedDescription)"
        }
    }

    // MARK: - Starting and stopping

    func start() async {
        problem = nil
        do {
            let manager = try await TunnelSwitch.installed()
            self.manager = manager
            watchStatus()
            try manager.connection.startVPNTunnel()
        } catch {
            problem = Self.plainEnglish(error)
        }
    }

    func stop() {
        manager?.connection.stopVPNTunnel()
    }

    /// Delete the configuration entirely. The complete undo.
    func forget() async {
        guard let manager else { return }
        try? await manager.removeFromPreferences()
        self.manager = nil
        status = .invalid
        watching?.cancel()
        watching = nil
    }

    // MARK: - Following it

    private func watchStatus() {
        guard let connection = manager?.connection else { return }

        watching?.cancel()
        watching = Task { [weak self] in
            let changes = NotificationCenter.default.notifications(
                named: .NEVPNStatusDidChange,
                object: connection
            )
            for await _ in changes {
                guard let self else { return }
                self.status = connection.status
            }
        }
    }

    private static func plainEnglish(_ error: any Error) -> String {
        let text = error.localizedDescription

        // The one failure a user can actually do something about.
        if text.contains("permission") || (error as NSError).code == 1 {
            return """
                iOS did not allow the tunnel to be added. It asks once, with a system prompt \
                titled "Dorm WiFi Would Like to Add VPN Configurations". Tap Allow.
                """
        }
        return text
    }
}
