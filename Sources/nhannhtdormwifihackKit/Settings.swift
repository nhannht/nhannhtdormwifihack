import Foundation

/// Everything the app needs to know, stored as JSON on disk.
///
/// The key names match the `settings.json` the Windows build already writes, so
/// an existing file loads unchanged and the two can run side by side during a
/// changeover. Keys the old file does not have fall back to the defaults here.
public struct Settings: Codable, Sendable, Equatable {

    // MARK: - Network

    /// The SSID to look for, and to join when `autoConnect` is on.
    public var wifiName: String = ""

    /// The router's address. Everything in the login sequence starts here.
    ///
    /// Empty, which is the default, means ask this machine which router it is
    /// currently using. Every network has a different one, so a fixed address
    /// stops working the moment you join a different hotspot. Set it only to
    /// force a particular address.
    public var gatewayIP: String = ""

    /// How long a session lasts, in minutes. The router decides this; the value
    /// here has to match, or the pre-emptive login fires at the wrong moment.
    public var interval: Int = 15

    /// How often to check that the connection is still up, in seconds.
    public var checkIntervalSeconds: Int = 2

    /// A network to fall back to when the internet has been gone for a while.
    ///
    /// This is the safety net, and setting it is what switches the net on. When
    /// the connection has been dead for fifteen seconds and nothing this app is
    /// doing explains it, it joins this network instead. A phone's personal
    /// hotspot is the intended use: the dorm hotspot can then be experimented
    /// with freely, because the worst outcome is fifteen seconds offline rather
    /// than an evening with no internet.
    ///
    /// It has to be a network this Mac has already saved. Join it by hand once
    /// and tick Remember, or `networksetup` cannot rejoin it later. Empty, the
    /// default, means there is no net and a failed join leaves you offline.
    public var fallbackWifiName: String = ""

    // MARK: - Portal

    /// Where to send the login when the router's page carries no form action.
    ///
    /// The router normally names its portal in the `action` of the login form and
    /// that value is used as-is. This is the fallback for when it does not, which
    /// does happen. It lives in the settings file rather than in the source so
    /// that pointing the app at a different portal needs no rebuild.
    public var portalFallbackURL: String = ""

    /// The portal's JSON endpoints, relative to whichever host is in play.
    public var portalEndpoints: PortalEndpoints = .init()

    /// Where to ask the router to send us once it lets us through.
    ///
    /// Apple's own connectivity check page, which answers with the single word
    /// Success. Landing on it is therefore proof the login worked, which the
    /// Windows build could not get from the Microsoft equivalent it used.
    public var destinationURL: String = "http://captive.apple.com/hotspot-detect.html"

    /// Hitting this logs the current session out. Optional.
    public var logoutURL: String = ""

    // MARK: - Behaviour

    /// Join `wifiName` automatically when it is not the current network.
    ///
    /// Checked before every login attempt. Reading the network name on macOS
    /// needs Location access, which a command-line tool cannot request, so on a
    /// machine that has not granted it this reports that it cannot tell rather
    /// than joining anything.
    public var autoConnect: Bool = true

    /// Launch at login.
    public var autoStart: Bool = false

    /// Check the network before every login rather than trusting the last check.
    ///
    /// Off, the check stops once the machine has been seen on `wifiName`, or
    /// once it is clear this machine cannot answer the question. On, it runs
    /// every time, which catches the network changing under a running watcher.
    public var checkWifiEveryTime: Bool = false

    /// When the last successful login happened, as a Unix timestamp.
    public var lastRunTimestamp: Double = 0

    // MARK: - Script versions

    /// The script the version picker currently has selected. Empty means use the
    /// built-in login path.
    public var selectedVersion: String = ""

    /// Display name and pinned state for each script, keyed by file name.
    public var versionMeta: [String: VersionMeta] = [:]

    // MARK: - AI

    public var geminiAPIKey: String = ""
    public var aiModel: String = "gemini-2.5-flash"

    // MARK: - Everything else in the file

    /// Keys found in the file that this build does not know about.
    ///
    /// The Windows build writes keys this one has never heard of. Re-encoding
    /// only the declared keys deleted them without a word, so a single run of
    /// `login` took the Windows tool's configuration with it. They are carried
    /// through untouched instead.
    public private(set) var unknownKeys: [String: JSONValue] = [:]

    public init() {}

    // MARK: - Derived

    /// How long a session lasts, in seconds.
    ///
    /// The upper clamp is a day. `interval` comes out of a file that can say
    /// anything, and multiplying an unbounded number by 60 traps.
    public var sessionTimeoutSeconds: Int {
        min(max(interval, 1), 1440) * 60
    }



    public struct VersionMeta: Codable, Sendable, Equatable {
        public var name: String = ""
        public var pinned: Bool = false

        public init(name: String = "", pinned: Bool = false) {
            self.name = name
            self.pinned = pinned
        }
    }

    /// The portal's API paths. Defaults match the portal this was built against.
    public struct PortalEndpoints: Codable, Sendable, Equatable {
        public var verifyURL: String = "/Home/VerifyUrl"
        public var getCustomer: String = "/Content/GetCustomer"
        public var getCampaignHTML: String = "/Content/GetCampaignHtml"
        public var analytic: String = "/Analytic/Send"

        public init() {}
    }

    /// Lets `encode(to:)` write back a key whose name is only known at runtime.
    struct PassthroughKey: CodingKey {
        let stringValue: String
        var intValue: Int? { nil }

        init(_ stringValue: String) { self.stringValue = stringValue }
        init?(stringValue: String) { self.init(stringValue) }
        init?(intValue: Int) { nil }
    }

    enum CodingKeys: String, CodingKey, CaseIterable {
        case wifiName = "wifi_name"
        case gatewayIP = "gateway_ip"
        case interval
        case checkIntervalSeconds = "check_interval_secs"
        case fallbackWifiName = "fallback_wifi_name"
        case portalFallbackURL = "portal_fallback_url"
        case portalEndpoints = "portal_endpoints"
        case destinationURL = "destination_url"
        case logoutURL = "logout_url"
        case autoConnect = "auto_connect"
        case autoStart = "auto_start"
        case checkWifiEveryTime = "check_wifi_every_time"
        case lastRunTimestamp = "last_run_timestamp"
        case selectedVersion = "selected_version"
        case versionMeta = "version_meta"
        case geminiAPIKey = "gemini_api_key"
        case aiModel = "ai_model"
    }

    /// The key names this build writes for itself.
    static let declaredKeys = Set(CodingKeys.allCases.map(\.rawValue))

    public init(from decoder: any Decoder) throws {
        // The whole file first, so anything not declared below can be kept.
        if let whole = try? decoder.singleValueContainer()
            .decode([String: JSONValue].self)
        {
            unknownKeys = whole.filter { !Self.declaredKeys.contains($0.key) }
        }

        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = Settings()
        // Every key is optional so that a file written by an older build, or by
        // hand, still loads instead of failing outright.
        wifiName = try c.decodeIfPresent(String.self, forKey: .wifiName) ?? d.wifiName
        gatewayIP = try c.decodeIfPresent(String.self, forKey: .gatewayIP) ?? d.gatewayIP
        interval = try c.decodeIfPresent(Int.self, forKey: .interval) ?? d.interval
        checkIntervalSeconds =
            try c.decodeIfPresent(Int.self, forKey: .checkIntervalSeconds) ?? d.checkIntervalSeconds
        fallbackWifiName =
            try c.decodeIfPresent(String.self, forKey: .fallbackWifiName) ?? d.fallbackWifiName
        portalFallbackURL =
            try c.decodeIfPresent(String.self, forKey: .portalFallbackURL) ?? d.portalFallbackURL
        portalEndpoints =
            try c.decodeIfPresent(PortalEndpoints.self, forKey: .portalEndpoints) ?? d.portalEndpoints
        destinationURL =
            try c.decodeIfPresent(String.self, forKey: .destinationURL) ?? d.destinationURL
        logoutURL = try c.decodeIfPresent(String.self, forKey: .logoutURL) ?? d.logoutURL
        autoConnect = try c.decodeIfPresent(Bool.self, forKey: .autoConnect) ?? d.autoConnect
        autoStart = try c.decodeIfPresent(Bool.self, forKey: .autoStart) ?? d.autoStart
        checkWifiEveryTime =
            try c.decodeIfPresent(Bool.self, forKey: .checkWifiEveryTime) ?? d.checkWifiEveryTime
        lastRunTimestamp =
            try c.decodeIfPresent(Double.self, forKey: .lastRunTimestamp) ?? d.lastRunTimestamp
        selectedVersion =
            try c.decodeIfPresent(String.self, forKey: .selectedVersion) ?? d.selectedVersion
        versionMeta =
            try c.decodeIfPresent([String: VersionMeta].self, forKey: .versionMeta) ?? d.versionMeta
        geminiAPIKey = try c.decodeIfPresent(String.self, forKey: .geminiAPIKey) ?? d.geminiAPIKey
        aiModel = try c.decodeIfPresent(String.self, forKey: .aiModel) ?? d.aiModel
    }

    public func encode(to encoder: any Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(wifiName, forKey: .wifiName)
        try c.encode(gatewayIP, forKey: .gatewayIP)
        try c.encode(interval, forKey: .interval)
        try c.encode(checkIntervalSeconds, forKey: .checkIntervalSeconds)
        try c.encode(fallbackWifiName, forKey: .fallbackWifiName)
        try c.encode(portalFallbackURL, forKey: .portalFallbackURL)
        try c.encode(portalEndpoints, forKey: .portalEndpoints)
        try c.encode(destinationURL, forKey: .destinationURL)
        try c.encode(logoutURL, forKey: .logoutURL)
        try c.encode(autoConnect, forKey: .autoConnect)
        try c.encode(autoStart, forKey: .autoStart)
        try c.encode(checkWifiEveryTime, forKey: .checkWifiEveryTime)
        try c.encode(lastRunTimestamp, forKey: .lastRunTimestamp)
        try c.encode(selectedVersion, forKey: .selectedVersion)
        try c.encode(versionMeta, forKey: .versionMeta)
        try c.encode(geminiAPIKey, forKey: .geminiAPIKey)
        try c.encode(aiModel, forKey: .aiModel)

        // Then whatever else was in the file. A declared key always wins, so a
        // stale entry here can never shadow a real setting.
        var passthrough = encoder.container(keyedBy: PassthroughKey.self)
        for (key, value) in unknownKeys where !Self.declaredKeys.contains(key) {
            try passthrough.encode(value, forKey: PassthroughKey(key))
        }
    }
}

// MARK: - The router in use right now

extension Settings {

    /// A copy with `gatewayIP` filled in from the live default route, for the
    /// usual case where the file leaves it empty.
    ///
    /// Call this once per pass rather than caching it. Switching networks
    /// changes the answer, and noticing that within one cycle is the whole
    /// point.
    public func resolvingGateway() -> Settings {
        guard gatewayIP.isEmpty else { return self }

        var resolved = self
        resolved.gatewayIP = DefaultRoute.gateway() ?? ""
        return resolved
    }

    /// The same, but giving a network that is still coming up time to finish.
    ///
    /// Use this right after joining a network, or on a one-shot command that has
    /// no next pass to try again on.
    public func resolvingGateway(waitingUpTo seconds: Int) async -> Settings {
        guard gatewayIP.isEmpty else { return self }

        var resolved = self
        resolved.gatewayIP = await DefaultRoute.waitForGateway(upTo: seconds) ?? ""
        return resolved
    }
}

// MARK: - Storage

extension Settings {

    /// `~/Library/Application Support/nhannhtdormwifihack/`
    public static var supportDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first ?? FileManager.default.temporaryDirectory
        return base.appendingPathComponent("nhannhtdormwifihack", isDirectory: true)
    }

    public static var defaultURL: URL {
        supportDirectory.appendingPathComponent("settings.json")
    }

    /// Load from disk, or hand back defaults when there is nothing there yet.
    public static func load(from url: URL = defaultURL) throws -> Settings {
        guard FileManager.default.fileExists(atPath: url.path) else { return Settings() }
        let decoder = JSONDecoder()
        return try decoder.decode(Settings.self, from: Data(contentsOf: url))
    }

    public func save(to url: URL = defaultURL) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(self).write(to: url, options: .atomic)

        // The file holds an API key, so it is not for other accounts on this
        // machine to read. An atomic write replaces the file rather than
        // rewriting it, so the mode has to be set after every save.
        try? FileManager.default.setAttributes(
            [.posixPermissions: 0o600],
            ofItemAtPath: url.path
        )
    }
}
