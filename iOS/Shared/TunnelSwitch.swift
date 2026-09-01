import Foundation
import NetworkExtension

/// Finding, starting and stopping the tunnel.
///
/// Two processes do this and they have to mean the same configuration: the app,
/// from the switch on its screen, and the widget extension, from the Control
/// Center toggle. So the provider's bundle identifier and the save-then-reload
/// dance live here once and are compiled into both, rather than being written
/// out twice and kept in step by hand.
///
/// The widget extension is allowed to do this. It carries the same
/// `packet-tunnel-provider` entitlement and the same app group as the app,
/// which Xcode's automatic signing issues without asking anyone.
@MainActor
enum TunnelSwitch {

    /// Must match the tunnel extension target's bundle identifier.
    static let providerBundleID = "vn.io.nhannht.dormwifihack.tunnel"

    /// The configuration this app wrote, if it has been written.
    static func existing() async throws -> NETunnelProviderManager? {
        try await NETunnelProviderManager.loadAllFromPreferences().first { manager in
            (manager.protocolConfiguration as? NETunnelProviderProtocol)?
                .providerBundleIdentifier == providerBundleID
        }
    }

    /// The configuration, written first if it was not there yet.
    ///
    /// The first call raises the system's "Dorm WiFi Would Like to Add VPN
    /// Configurations" prompt. Every call after it is silent.
    static func installed() async throws -> NETunnelProviderManager {
        let manager = try await existing() ?? NETunnelProviderManager()

        let settings = NETunnelProviderProtocol()
        settings.providerBundleIdentifier = providerBundleID

        // Required by the API and shown in Settings under VPN. There is no
        // server, so this says what the tunnel is for instead of naming a host.
        settings.serverAddress = "Hotspot sign-in"

        manager.protocolConfiguration = settings
        manager.localizedDescription = "Dorm WiFi"
        manager.isEnabled = true

        try await manager.saveToPreferences()

        // Load it straight back. A manager holding the copy it just saved is
        // considered stale by the system and starting from it fails, which is
        // the one gotcha in this whole API.
        try await manager.loadFromPreferences()
        return manager
    }

    /// Start it, writing the configuration first if this is the first time.
    /// Only the app calls this: writing raises a system prompt, so something
    /// has to be on screen to answer it.
    static func start() async throws {
        try await installed().connection.startVPNTunnel()
    }

    /// Flip a configuration that is already there, and say whether there was
    /// one. The manager never leaves this actor: it is not `Sendable`, and the
    /// Control Center toggle's action runs outside any actor at all.
    static func flip(on: Bool) async throws -> Bool {
        guard let connection = try await existing()?.connection else { return false }

        if on {
            try connection.startVPNTunnel()
        } else {
            connection.stopVPNTunnel()
        }
        return true
    }

    /// Whether the tunnel is up, asked of the system rather than read from a
    /// file. The file says what the loop last found; this says whether the loop
    /// exists at all, which is the question a toggle is asking.
    static func isRunning() async -> Bool {
        guard let manager = try? await existing() else { return false }
        return running(manager.connection.status)
    }

    static func running(_ status: NEVPNStatus) -> Bool {
        status == .connected || status == .connecting || status == .reasserting
    }
}
