import Foundation

/// What the watcher is doing right now, in the words the menu shows.
///
/// This is the single source of truth for anything displaying the state. The
/// menu deliberately does not ask the router itself: two probes running side by
/// side would eventually disagree, and then the app and the tool would be
/// telling the user different stories about the same connection.
public struct WatcherStatus: Sendable, Equatable {

    public enum Connection: Sendable, Equatable {
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
