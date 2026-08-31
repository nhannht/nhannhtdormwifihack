import Foundation

#if canImport(CoreWLAN)
    import CoreWLAN
#endif

/// The router this machine currently sends its traffic through.
///
/// The captive portal always sits on it, and its address is different on every
/// network: one iNET network answers on `192.168.200.1`, another on `10.11.0.1`.
/// Asking the system each time is what lets one settings file work on both
/// without being edited in between, which a fixed address cannot do.
public enum DefaultRoute {

    public static func gateway() -> String? {
        guard let output = Shell.run("/sbin/route", ["-n", "get", "default"], timeout: 5) else {
            return nil
        }

        for line in output.split(separator: "\n") {
            let halves = line.split(separator: ":", maxSplits: 1)
            guard halves.count == 2,
                  halves[0].trimmingCharacters(in: .whitespaces) == "gateway"
            else { continue }

            let address = halves[1].trimmingCharacters(in: .whitespaces)
            return address.isEmpty ? nil : address
        }

        return nil
    }

    /// Wait for a default route to exist, for up to `seconds`.
    ///
    /// Joining a network returns as soon as the radio associates, which is
    /// several seconds before DHCP hands out an address and installs a route.
    /// Asking for the router inside that gap gets nothing, and reporting "no
    /// network at all" then is wrong: the network is a few seconds away from
    /// being ready.
    public static func waitForGateway(upTo seconds: Int = 20) async -> String? {
        for _ in 0..<max(seconds, 1) {
            if let found = gateway() { return found }
            try? await Task.sleep(for: .seconds(1))
        }
        return gateway()
    }
}

/// Joining a wireless network by name.
///
/// `networksetup` rather than CoreWLAN, deliberately. Joining a saved network
/// needs no special permission, while CoreWLAN's `associate`, and even reading
/// which network you are on, are gated behind Location access that a
/// command-line tool has no way to request. The Windows original does the same
/// thing with `netsh wlan connect`.
public enum WiFi {

    public enum JoinOutcome: Sendable, Equatable {
        case joined
        case noInterface
        case notSaved(String)
        case failed(String)

        public var summary: String {
            switch self {
            case .joined:
                return "joined"
            case .noInterface:
                return "there is no Wi-Fi interface on this machine"
            case .notSaved(let ssid):
                return """
                    \(ssid) is not one of this Mac's saved networks. Join it by hand once, \
                    tick Remember, and this can rejoin it from then on
                    """
            case .failed(let reason):
                return reason
            }
        }
    }

    /// The Wi-Fi device name, usually `en0`.
    public static func interfaceName() -> String? {
        guard let output = Shell.run(
            "/usr/sbin/networksetup", ["-listallhardwareports"], timeout: 5
        ) else { return nil }

        // Blocks read "Hardware Port: Wi-Fi" then "Device: en0" on the next line.
        var isWiFi = false
        for line in output.split(separator: "\n") {
            let text = line.trimmingCharacters(in: .whitespaces)
            if text.hasPrefix("Hardware Port:") {
                isWiFi = text.contains("Wi-Fi") || text.contains("AirPort")
            } else if isWiFi, text.hasPrefix("Device:") {
                let device = text.dropFirst("Device:".count).trimmingCharacters(in: .whitespaces)
                return device.isEmpty ? nil : device
            }
        }

        return nil
    }

    /// Networks this Mac has saved, most preferred first.
    public static func savedNetworks() -> [String] {
        guard let device = interfaceName(),
              let output = Shell.run(
                  "/usr/sbin/networksetup", ["-listpreferredwirelessnetworks", device], timeout: 5
              )
        else { return [] }

        // The first line is a heading; every network is indented under it.
        return output.split(separator: "\n")
            .dropFirst()
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    public static func join(ssid: String) -> JoinOutcome {
        guard let device = interfaceName() else { return .noInterface }

        guard savedNetworks().contains(ssid) else { return .notSaved(ssid) }

        guard let output = Shell.run(
            "/usr/sbin/networksetup", ["-setairportnetwork", device, ssid], timeout: 25
        ) else {
            return .failed("could not run networksetup")
        }

        // It prints nothing at all when it works.
        let complaint = output.trimmingCharacters(in: .whitespacesAndNewlines)
        return complaint.isEmpty ? .joined : .failed(complaint)
    }

    /// Which network we are on, when macOS is willing to say.
    ///
    /// Reading the name needs Location access, which a bare command-line binary
    /// cannot request, so on most setups this is nil. It is only ever an
    /// optimisation: it saves a pointless rejoin when we are already where we
    /// want to be. Joining is what actually guarantees the network.
    public static func currentSSID() -> String? {
        #if canImport(CoreWLAN)
            return CWWiFiClient.shared().interface()?.ssid()
        #else
            return nil
        #endif
    }
}

/// Puts this Mac back on the hotspot when the router stops answering.
///
/// It does not ask which network you are on first. macOS will not say without
/// Location access, and the question is not worth answering anyway: joining a
/// network you are already on is free, so the useful move is simply to join.
public actor WiFiJoiner {

    /// Joining takes a few seconds and drops the link while it happens, so a
    /// failing router does not get to trigger one every pass.
    private static let cooldownSeconds = 30

    private let settings: Settings
    private let log: ActivityLog
    private var lastAttempt: ContinuousClock.Instant?

    public init(settings: Settings, log: ActivityLog) {
        self.settings = settings
        self.log = log
    }

    public func joinConfiguredNetwork() async {
        guard settings.autoConnect, !settings.wifiName.isEmpty else { return }
        await join(ssid: settings.wifiName, respectingCooldown: true)
    }

    /// Join the safety net network.
    ///
    /// No cooldown and no `autoConnect` check. This runs when the internet has
    /// already been gone for fifteen seconds, which is the one moment where
    /// waiting politely is the wrong thing to do. `autoConnect` governs joining
    /// the hotspot we are trying to use; setting a fallback network is itself
    /// the permission to join that one.
    public func joinFallbackNetwork() async -> Bool {
        guard !settings.fallbackWifiName.isEmpty else { return false }
        return await join(ssid: settings.fallbackWifiName, respectingCooldown: false)
    }

    @discardableResult
    private func join(ssid wanted: String, respectingCooldown: Bool) async -> Bool {
        // Free when macOS allows it, nil when it does not.
        if let current = WiFi.currentSSID(), current == wanted { return true }

        if respectingCooldown,
           !settings.checkWifiEveryTime,
           let last = lastAttempt,
           ContinuousClock.now - last < .seconds(Self.cooldownSeconds)
        {
            return false
        }
        lastAttempt = ContinuousClock.now

        await log.info("Joining \(wanted).")

        switch WiFi.join(ssid: wanted) {
        case .joined:
            await log.success("Joined \(wanted).")

            // Associating is not the same as being on the network. Wait for
            // DHCP to install a route, or the very next step asks which router
            // we are on, gets no answer, and calls it "no network at all".
            //
            // Measured at about 28 seconds on this hotspot, so the wait is
            // generous. Running out is not an error and must not be reported as
            // one: the caller checks again, and on the run that produced the
            // "handed out no address" warning the address turned up seven
            // seconds later and the login went through.
            if let gateway = await DefaultRoute.waitForGateway(upTo: 45) {
                await log.info("Network ready, router is \(gateway).")
            } else {
                await log.info("\(wanted) has not handed out an address yet. Carrying on.")
            }
            return true

        case let outcome:
            await log.warning("Could not join \(wanted): \(outcome.summary)")
            return false
        }
    }
}
