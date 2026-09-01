import Foundation
import NetworkExtension
import nhannhtdormwifihackKit

/// The watcher, running somewhere iOS will not suspend it.
///
/// ## Why a tunnel holds a hotspot session
///
/// iOS suspends an app a few seconds after it leaves the screen, so the watcher
/// inside the app can only work while the user is looking at it. The API built
/// for this exact job, `NEHotspotHelper`, needs an entitlement Apple grants case
/// by case, and asking for it fails the build today:
///
///     error: Entitlement com.apple.developer.networking.HotspotHelper requires
///     approval from Apple to include in a profile.
///
/// A packet tunnel provider needs no such approval. Xcode's automatic signing
/// issued a profile carrying `packet-tunnel-provider` in seconds, with no
/// request to anyone. And a tunnel provider is a separate process that iOS keeps
/// running for as long as the tunnel is on, with the app closed and the phone
/// locked. That process is the only thing this file wants.
///
/// ## What this tunnel carries
///
/// Nothing. It is not a VPN and it moves no traffic.
///
/// `192.0.2.0/24` is TEST-NET-1, the block reserved by RFC 5737 for
/// documentation, so no real host is ever at one of these addresses. The tunnel
/// takes an address from it and routes one other address from it, which makes
/// this a properly configured tunnel that captures nothing. Every real packet
/// this phone sends still goes out over wifi exactly as it did before, including
/// the requests this file makes to the router and the portal.
///
/// The alternative, an empty route list, would have raised a question nobody
/// needed answering: whether iOS will bring up a tunnel that claims no routes at
/// all. One unused route costs nothing and does not ask.
///
/// ## What is different in here
///
/// Two settings the app already has must be repeated in this bundle's own
/// `Info.plist`, because App Transport Security and the local network prompt are
/// judged per bundle and this is a different bundle: `NSAllowsArbitraryLoads`,
/// since a captive portal is plain HTTP by nature, and
/// `NSLocalNetworkUsageDescription`, since every request starts at the router on
/// the local network.
final class PacketTunnelProvider: NEPacketTunnelProvider {

    private var loop: Task<Void, Never>?

    override func startTunnel(
        options: [String: NSObject]?,
        completionHandler: @escaping (Error?) -> Void
    ) {
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "192.0.2.1")

        settings.ipv4Settings = NEIPv4Settings(
            addresses: ["192.0.2.2"],
            subnetMasks: ["255.255.255.255"]
        )

        // One route, to an address nothing is at. See the note above.
        settings.ipv4Settings?.includedRoutes = [
            NEIPv4Route(destinationAddress: "192.0.2.3", subnetMask: "255.255.255.255")
        ]

        // Nothing reads `packetFlow`. Nothing is routed here, so nothing arrives
        // to read, and a reader waiting on an empty flow would only be a thread
        // sleeping forever.

        setTunnelNetworkSettings(settings) { [weak self] error in
            guard let self else {
                completionHandler(error)
                return
            }
            if error == nil { self.startWatching() }
            completionHandler(error)
        }
    }

    override func stopTunnel(
        with reason: NEProviderStopReason,
        completionHandler: @escaping () -> Void
    ) {
        loop?.cancel()
        loop = nil

        // Say why it stopped, in the shared log, where the app will show it.
        // A tunnel that ends on its own is the one thing this whole design has
        // to be able to detect, and a log that simply goes quiet cannot tell
        // "iOS shut it down" apart from "it is still running and idle".
        //
        // Written straight to the file rather than through the log actor. iOS
        // can take this process away as soon as the handler below returns, and
        // an awaited write would still be in flight.
        ActivityLog.appendDirectly(
            .warning, "Tunnel stopped: \(Self.plainEnglish(reason)).", to: Self.logURL
        )
        completionHandler()
    }

    /// Run the same loop the app runs, in a process that outlives the app.
    private func startWatching() {
        let log = Self.sharedLog()
        let settings = (try? Settings.load()) ?? Settings()

        loop = Task {
            await log.success("Tunnel is up.")
            await SessionRenewer(settings: settings, log: log, home: .tunnel).run()
        }
    }

    /// The file in the shared container that the app reads and shows.
    private static var logURL: URL {
        Settings.supportDirectory.appendingPathComponent("activity.log")
    }

    /// The log the loop writes to.
    ///
    /// Appending rather than truncating. Truncating on every start would erase
    /// the record of the previous run at the exact moment it becomes evidence,
    /// which is whenever the question is what happened while nobody was looking.
    private static func sharedLog() -> ActivityLog {
        ActivityLog(fileURL: logURL, truncateFile: false)
    }

    private static func plainEnglish(_ reason: NEProviderStopReason) -> String {
        switch reason {
        case .userInitiated: "switched off"
        case .providerFailed: "the tunnel hit an error"
        case .noNetworkAvailable: "this phone has no network"
        case .unrecoverableNetworkChange: "the network changed underneath it"
        case .providerDisabled: "it was disabled"
        case .authenticationCanceled: "sign-in was cancelled"
        case .configurationFailed: "the configuration is wrong"
        case .idleTimeout: "iOS ended it for being idle"
        case .configurationDisabled: "the configuration was switched off"
        case .configurationRemoved: "the configuration was deleted"
        case .superceded: "another tunnel took over"
        case .userLogout: "the user logged out"
        case .userSwitch: "the user was switched"
        case .connectionFailed: "the connection failed"
        case .sleep: "this phone went to sleep"
        case .appUpdate: "the app was updated"
        case .internalError: "an internal error"
        @unknown default: "reason \(reason.rawValue)"
        }
    }
}
