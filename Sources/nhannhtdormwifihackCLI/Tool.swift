import ArgumentParser
import Foundation
import nhannhtdormwifihackKit

@main
struct Tool: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "nhannhtdormwifihack",
        abstract: "Signs in to a captive-portal WiFi and keeps the session alive.",
        discussion: """
            With no subcommand this runs the watcher, which signs in and then keeps \
            renewing the session before it can expire.

            Settings live in ~/Library/Application Support/nhannhtdormwifihack/settings.json \
            unless --settings points somewhere else.
            """,
        version: "1.0.0",
        subcommands: [Run.self, Login.self, Status.self, Logout.self, Config.self, Install.self, Uninstall.self],
        defaultSubcommand: Run.self
    )
}

// MARK: - Shared options

struct GlobalOptions: ParsableArguments {
    @Option(name: [.customLong("settings"), .short], help: "Path to the settings file.")
    var settingsPath: String?

    @Flag(name: .shortAndLong, help: "Print every step, not just the outcome.")
    var verbose = false

    var settingsURL: URL {
        settingsPath.map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) }
            ?? Settings.defaultURL
    }

    func loadSettings() throws -> Settings {
        try Settings.load(from: settingsURL)
    }
}

// MARK: - Console

/// Prints log entries as they happen.
///
/// Progress goes to stderr and results go to stdout, so `status` and `config
/// show` can be piped somewhere without the running commentary mixed in, and so
/// the two streams cannot interleave out of order.
enum Console {
    static func attach(to log: ActivityLog, verbose: Bool) async -> Task<Void, Never> {
        // Subscribing happens here, before this returns, rather than inside the
        // task. Started inside, the task had not yet run by the time the caller
        // began work, so the opening entries were handed to an empty set of
        // subscribers and never printed - worst exactly when step 1 is the step
        // that failed.
        let entries = await log.subscribe()

        return Task {
            for await entry in entries {
                guard verbose || entry.level != .info else { continue }
                progress(render(entry))
            }
        }
    }

    static func progress(_ text: String) {
        guard let data = (text + "\n").data(using: .utf8) else { return }
        FileHandle.standardError.write(data)
    }

    static func render(_ entry: LogEntry) -> String {
        let marker =
            switch entry.level {
            case .info: "  "
            case .success: "OK"
            case .warning: "! "
            case .failure: "X "
            }
        return "\(marker) \(entry.formatted)"
    }

    static func line(_ text: String) {
        print(text)
    }
}

// MARK: - run

struct Run: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Sign in, then keep the session alive until stopped."
    )

    @OptionGroup var options: GlobalOptions

    @Option(help: "Mirror the log to this file.")
    var logFile: String?

    @Flag(
        name: .long,
        help: """
            Show the menu bar icon instead of watching here, and follow its log \
            in this terminal.
            """
    )
    var menu = false

    mutating func run() async throws {
        let settings = try options.loadSettings()
        try Preflight.check(settings)

        let logURL =
            logFile.map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) }
            ?? Settings.supportDirectory.appendingPathComponent("activity.log")

        if menu {
            try await runInMenuBar(logURL: logURL)
            return
        }

        // One watcher at a time. Two of them post to /login together the moment
        // a session lapses, and the router rejects the second for having too
        // many sessions open.
        guard let lock = RunLock.acquire() else {
            throw ValidationError(
                """
                Another watcher is already running, either the menu bar app or \
                another terminal. Stop that one first, or run with --menu to watch \
                the one that is already going.
                """
            )
        }
        defer { _ = lock }

        let log = ActivityLog(fileURL: logURL)
        let printer = await Console.attach(to: log, verbose: options.verbose)
        defer { printer.cancel() }

        Console.line("Watching. Press Control-C to stop.")
        await SessionScheduler(settings: settings, log: log).run()
    }

    /// Hand the watching to the menu bar app and just show its log here.
    ///
    /// The icon cannot be drawn from here. A status item only reaches the system
    /// menu bar when the process was launched from an app bundle through
    /// LaunchServices; a bare binary run from a terminal creates the item
    /// successfully and it never appears, whether or not the binary sits inside
    /// a bundle. So this starts the bundle and follows the log it writes, which
    /// keeps one watcher with two ways to look at it.
    private func runInMenuBar(logURL: URL) async throws {
        guard let app = Self.bundleLocation() else {
            throw ValidationError(
                """
                The menu bar app is not built. Run ./build-app.sh, which puts \
                "Dorm WiFi.app" in ~/Applications.
                """
            )
        }

        Console.progress("Starting the menu bar app.")
        guard Shell.run("/usr/bin/open", [app.path], timeout: 20) != nil else {
            throw ValidationError("Could not start \(app.path).")
        }

        Console.line("Menu bar app started. Following its log; Control-C stops watching here.")
        Console.line("The app keeps running after you stop this, and quits from its own menu.")

        try await follow(logURL)
    }

    /// Where the built app lives, or nil when it has not been built.
    static func bundleLocation() -> URL? {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let candidates = [
            home.appendingPathComponent("Applications/Dorm WiFi.app"),
            URL(fileURLWithPath: "/Applications/Dorm WiFi.app"),
        ]
        return candidates.first { FileManager.default.fileExists(atPath: $0.path) }
    }

    /// Print the log file as it grows, the way `tail -f` does.
    private func follow(_ url: URL) async throws {
        // Start at the end, so an old session's lines are not replayed as if
        // they were happening now.
        var offset = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? UInt64)
            ?? 0

        while !Task.isCancelled {
            try? await Task.sleep(for: .milliseconds(400))

            guard let handle = try? FileHandle(forReadingFrom: url) else { continue }
            defer { try? handle.close() }

            // A file that shrank was truncated by a new run, so start over.
            let size = (try? handle.seekToEnd()) ?? 0
            if size < offset { offset = 0 }
            guard size > offset else { continue }

            try? handle.seek(toOffset: offset)
            let data = (try? handle.readToEnd()) ?? Data()
            offset = size

            let text = String(decoding: data, as: UTF8.self)
            for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
                Console.progress(String(line))
            }
        }
    }
}

// MARK: - login

struct Login: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Sign in once and exit."
    )

    @OptionGroup var options: GlobalOptions

    mutating func run() async throws {
        var settings = try options.loadSettings()
        try Preflight.check(settings)

        let log = ActivityLog()
        let printer = await Console.attach(to: log, verbose: options.verbose)
        defer { printer.cancel() }

        await WiFiJoiner(settings: settings, log: log).joinConfiguredNetwork()

        let active = await settings.resolvingGateway(waitingUpTo: 20)
        guard !active.gatewayIP.isEmpty else {
            throw ValidationError(
                "This Mac still has no network 20s after joining. Is the hotspot in range?"
            )
        }

        let result = await LoginEngine(settings: active, log: log).run()
        Console.line(result.summary)

        if result.isConnected {
            settings.lastRunTimestamp = Date().timeIntervalSince1970
            try? settings.save(to: options.settingsURL)
        } else {
            throw ExitCode.failure
        }
    }
}

// MARK: - status

struct Status: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Report whether the session and the internet are up."
    )

    @OptionGroup var options: GlobalOptions

    mutating func run() async throws {
        let settings = await (try options.loadSettings()).resolvingGateway(waitingUpTo: 5)
        guard !settings.gatewayIP.isEmpty else {
            throw ValidationError("This Mac has no network connection at all.")
        }
        let client = PortalClient()

        async let internet = NetworkProbe.hasInternet(timeout: .seconds(2))
        async let session = SessionProbe.status(gatewayIP: settings.gatewayIP, client: client)

        let (hasInternet, sessionStatus) = await (internet, session)

        Console.line("Router:    \(settings.gatewayIP)")
        Console.line("Internet:  \(hasInternet ? "up" : "down")")

        switch sessionStatus {
        case .active(let info):
            Console.line("Session:   signed in")
            if let ip = info.ipAddress { Console.line("  address:   \(ip)") }
            if let mac = info.macAddress { Console.line("  mac:       \(mac)") }
            if let connected = info.connectedFor {
                Console.line("  connected: \(ErrorText.duration(connected))")
            }
            // The router's own countdown, not a guess from a local timer.
            if let remaining = info.remaining {
                Console.line("  expires:   in \(ErrorText.duration(remaining))")
            }
        case .expired:
            Console.line("Session:   signed out")
        case .unknown(let why):
            Console.line("Session:   signed out (\(why))")
        case .unreachable(let why):
            Console.line("Session:   router did not answer (\(why))")
            Console.line("           This Mac may be on a different network.")
        }

        if !hasInternet { throw ExitCode.failure }
    }
}

// MARK: - logout

struct Logout: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "End the current session."
    )

    @OptionGroup var options: GlobalOptions

    mutating func run() async throws {
        let settings = try options.loadSettings().resolvingGateway()

        let target =
            settings.logoutURL.isEmpty
            ? "http://\(settings.gatewayIP)/logout"
            : settings.logoutURL

        guard let url = URL(string: target) else {
            throw ValidationError("Not a usable logout URL: \(target)")
        }

        let client = PortalClient()
        do {
            let response = try await client.get(url, timeout: 5)
            Console.line("Logged out (HTTP \(response.statusCode)).")
        } catch {
            Console.line("Could not reach \(target): \(ErrorText.short(for: error))")
            throw ExitCode.failure
        }
    }
}

// MARK: - config

struct Config: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Show or change the settings file.",
        subcommands: [Show.self, Set.self, Path.self],
        defaultSubcommand: Show.self
    )

    struct Show: AsyncParsableCommand {
        static let configuration = CommandConfiguration(abstract: "Print the current settings.")

        @OptionGroup var options: GlobalOptions

        mutating func run() async throws {
            let settings = try options.loadSettings()
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            Console.line(String(decoding: try encoder.encode(settings), as: UTF8.self))
        }
    }

    struct Path: AsyncParsableCommand {
        static let configuration = CommandConfiguration(
            abstract: "Print where the settings file lives."
        )

        @OptionGroup var options: GlobalOptions

        mutating func run() async throws {
            Console.line(options.settingsURL.path)
        }
    }

    struct Set: AsyncParsableCommand {
        static let configuration = CommandConfiguration(
            abstract: "Change one setting.",
            discussion: """
                Keys: wifi_name, fallback_wifi_name, gateway_ip, interval, \
                check_interval_secs, portal_fallback_url, destination_url, logout_url, \
                auto_connect, auto_start, check_wifi_every_time, gemini_api_key, ai_model
                """
        )

        @OptionGroup var options: GlobalOptions

        @Argument(help: "The setting to change.")
        var key: String

        @Argument(help: "Its new value.")
        var value: String

        mutating func run() async throws {
            var settings = try options.loadSettings()

            switch key {
            case "wifi_name": settings.wifiName = value
            case "gateway_ip": settings.gatewayIP = value
            case "interval": settings.interval = try intValue()
            case "check_interval_secs": settings.checkIntervalSeconds = try intValue()
            case "fallback_wifi_name": settings.fallbackWifiName = value
            case "portal_fallback_url": settings.portalFallbackURL = value
            case "destination_url": settings.destinationURL = value
            case "logout_url": settings.logoutURL = value
            case "auto_connect": settings.autoConnect = try boolValue()
            case "auto_start": settings.autoStart = try boolValue()
            case "check_wifi_every_time": settings.checkWifiEveryTime = try boolValue()
            case "gemini_api_key": settings.geminiAPIKey = value
            case "ai_model": settings.aiModel = value
            default:
                throw ValidationError("There is no setting called \(key).")
            }

            try settings.save(to: options.settingsURL)

            // A secret is not echoed. This line goes to the terminal, into shell
            // history, and for the installed agent into launchd.out.log.
            if Self.secretKeys.contains(key) {
                Console.line("Set \(key).")
            } else {
                Console.line("Set \(key) to \(value).")
            }
        }

        /// Settings whose value must not be printed back.
        static let secretKeys: Swift.Set<String> = ["gemini_api_key"]

        private func intValue() throws -> Int {
            guard let number = Int(value) else {
                throw ValidationError("\(key) needs a whole number, not \(value).")
            }
            return number
        }

        private func boolValue() throws -> Bool {
            switch value.lowercased() {
            case "true", "yes", "1", "on": true
            case "false", "no", "0", "off": false
            default: throw ValidationError("\(key) needs true or false, not \(value).")
            }
        }
    }
}

// MARK: - Preflight

enum Preflight {
    /// Catch the settings that make a login impossible before any request goes out.
    static func check(_ settings: Settings) throws {
        // gateway_ip is deliberately allowed to be empty: that means "ask this
        // machine which router it is on", which is the normal setting.
        if settings.interval < 1 {
            throw ValidationError("interval has to be at least 1 minute.")
        }
    }
}
