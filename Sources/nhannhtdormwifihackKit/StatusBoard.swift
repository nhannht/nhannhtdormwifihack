import Foundation

/// What the watcher is doing right now, in the words the menu shows.
///
/// Whatever is displaying the state reads this and never asks the router
/// itself. Two probes running side by side eventually disagree, and then the
/// app and the tool are telling the user different stories about one
/// connection.
///
/// Getting this from the watcher to the screen takes two different routes,
/// because on a phone they are not always in the same process. `StatusBoard`
/// hands it over inside one process, which is all the Mac ever needs.
/// `StatusFile` writes it to the shared container, which is what the phone
/// needs when the watcher is running inside the tunnel.
public struct WatcherStatus: Sendable, Equatable, Codable {

    public enum Connection: Sendable, Equatable, Codable {
        /// Nothing has happened yet.
        case starting
        /// Signed in. The countdown lives in `expiresAt` rather than here, so
        /// a menu drawn a minute after the last check still counts down
        /// correctly instead of showing a number that ran out.
        case signedIn
        /// Signed out and working on it.
        case signingIn
        /// The hotspot router is not answering, so this Mac is elsewhere.
        case wrongNetwork(String)
        /// The safety net caught us and we are on the phone hotspot.
        case onFallback(String)
        /// The user pressed pause.
        case stopped
        /// Stopping, but something slow is still finishing.
        case stopping
    }

    public var connection: Connection = .starting

    /// When the router said this session ends, or nil when it did not say.
    ///
    /// A moment, not a duration. The watcher can sleep a full minute between
    /// checks, so a stored "745 seconds left" is wrong by up to a minute by the
    /// time anyone reads it. A deadline stays true however long ago it was set.
    public var expiresAt: Date?

    public var routerIP: String = ""
    public var network: String = ""
    public var hasInternet: Bool = false
    public var lastMessage: String = ""
    public var updated: Date = Date()

    public init() {}

    /// How long this session has left, worked out now rather than when the
    /// router was last asked.
    public var remainingNow: Int? {
        guard let expiresAt else { return nil }
        // Rounded, not truncated: truncating turns 744.99 seconds into 744 and
        // the menu reads one second short of the truth from the moment it is set.
        return max(Int(expiresAt.timeIntervalSinceNow.rounded()), 0)
    }

    /// The one line at the top of the menu.
    public var headline: String {
        switch connection {
        case .starting:
            return "Starting"
        case .signedIn:
            guard let left = remainingNow else { return "Signed in" }
            return "Signed in, \(ErrorText.duration(left)) left"
        case .signingIn:
            return "Signing in"
        case .wrongNetwork:
            return network.isEmpty ? "Not on the hotspot" : "Not on \(network)"
        case .onFallback(let ssid):
            return "On \(ssid)"
        case .stopped:
            return "Paused"
        case .stopping:
            return "Stopping"
        }
    }

    /// Too old to believe.
    ///
    /// A status is a snapshot, and the only thing that keeps it true is a loop
    /// writing a new one. When nothing is watching, `expiresAt` keeps counting
    /// down all the same, so a screen that trusts an abandoned status ends up
    /// insisting "Signed in, 0s left" forever. The watcher writes at least once
    /// a minute, so nothing older than two is worth showing as current.
    public var isStale: Bool {
        Date().timeIntervalSince(updated) > 120
    }

    /// Whether anything showing `other` would now be showing the wrong thing.
    ///
    /// Not plain inequality. The watcher rewrites `expiresAt` on every heartbeat
    /// with the same deadline measured a minute later, so comparing the whole
    /// status would call every heartbeat a change. What a bystander notices is
    /// the state changing, or a new session starting, and a new session moves
    /// the deadline by minutes rather than by the second or two that a fresh
    /// reading of the same session moves it.
    public func differsMateriallyFrom(_ other: WatcherStatus) -> Bool {
        if connection != other.connection { return true }

        switch (expiresAt, other.expiresAt) {
        case (nil, nil):
            return false
        case let (mine?, theirs?):
            return abs(mine.timeIntervalSince(theirs)) > 60
        default:
            return true
        }
    }

    /// Whether the connection is currently good, for the menu bar icon.
    public var isHealthy: Bool {
        if case .signedIn = connection { return true }
        if case .onFallback = connection { return hasInternet }
        return false
    }
}

/// Broadcasts the watcher's state to whoever is displaying it.
///
/// Same shape as `ActivityLog`: writers push, readers subscribe to a stream, and
/// a late reader can ask for the current value rather than waiting for the next
/// change. A menu that opens between two updates needs exactly that.
public actor StatusBoard {

    private var current = WatcherStatus()
    private var subscribers: [UUID: AsyncStream<WatcherStatus>.Continuation] = [:]

    public init() {}

    public func publish(_ status: WatcherStatus) {
        var next = status
        next.updated = Date()
        current = next

        for continuation in subscribers.values {
            continuation.yield(next)
        }
    }

    /// The current value, for a reader that has just arrived.
    public func snapshot() -> WatcherStatus { current }

    public func subscribe() -> AsyncStream<WatcherStatus> {
        let id = UUID()
        let (stream, continuation) = AsyncStream<WatcherStatus>.makeStream()
        subscribers[id] = continuation
        continuation.onTermination = { [weak self] _ in
            Task { await self?.unsubscribe(id) }
        }
        continuation.yield(current)
        return stream
    }

    private func unsubscribe(_ id: UUID) {
        subscribers[id] = nil
    }
}


// MARK: - Across processes

/// The watcher's state, put where another process can read it.
///
/// On the phone the watcher can be running inside the tunnel, which is a
/// separate process from the app showing the screen. Nothing an actor publishes
/// in one process reaches the other, so the state goes through a file in the
/// shared container, exactly like the activity log does.
///
/// Written whole and replaced atomically, so a reader either gets the previous
/// status or the new one and never half of each.
public struct StatusFile: Sendable {

    public let url: URL

    public init(url: URL) {
        self.url = url
    }

    /// Where it lives next to the settings and the log.
    public static var shared: StatusFile {
        StatusFile(url: Settings.supportDirectory.appendingPathComponent("status.json"))
    }

    public func write(_ status: WatcherStatus) {
        var stamped = status
        stamped.updated = Date()

        guard let data = try? JSONEncoder().encode(stamped) else { return }
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try? data.write(to: url, options: .atomic)
    }

    public func read() -> WatcherStatus? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(WatcherStatus.self, from: data)
    }

    /// When it was last written, for a reader deciding whether to re-read it.
    public var changedAt: Date? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }
}
