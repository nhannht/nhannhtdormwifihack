import Foundation

/// One line of the running commentary.
public struct LogEntry: Sendable, Identifiable, Equatable {
    public let id: UUID
    public let date: Date
    public let level: Level
    public let message: String

    public enum Level: String, Sendable, CaseIterable, Equatable {
        case info
        case success
        case warning
        case failure
    }

    public init(id: UUID = UUID(), date: Date = Date(), level: Level, message: String) {
        self.id = id
        self.date = date
        self.level = level
        self.message = message
    }

    /// `[16:57:55] Gateway answered with a login page`
    public var formatted: String {
        "[\(Self.timeFormatter.string(from: date))] \(message)"
    }

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()
}

/// Collects what the app is doing, for the window and for the log file.
///
/// This replaces the two separate loggers the Python version kept, one writing
/// to the screen and one to `network_debug.log`, which meant some failures were
/// only ever visible in one of the two places.
public actor ActivityLog {

    private var entries: [LogEntry] = []
    private let limit: Int
    private let fileURL: URL?
    private var subscribers: [UUID: AsyncStream<LogEntry>.Continuation] = [:]

    /// - Parameters:
    ///   - fileURL: where to mirror the log, or nil to keep it in memory only.
    ///   - limit: how many entries to hold in memory.
    ///   - truncateFile: start the file fresh, the way each Windows run did.
    public init(fileURL: URL? = nil, limit: Int = 2000, truncateFile: Bool = true) {
        self.fileURL = fileURL
        self.limit = limit

        if let fileURL {
            try? FileManager.default.createDirectory(
                at: fileURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )

            if truncateFile {
                let header = "--- session started at \(Date()) ---\n"
                try? Data(header.utf8).write(to: fileURL, options: .atomic)
            } else {
                // Keeping the file means it grows forever, so drop the oldest
                // part of it now rather than letting one long-running install
                // fill the disk a line at a time.
                Self.trim(fileURL, to: Self.longestKeptFile)
                let header = "\n--- session started at \(Date()) ---\n"
                if let handle = try? FileHandle(forWritingTo: fileURL) {
                    defer { try? handle.close() }
                    _ = try? handle.seekToEnd()
                    try? handle.write(contentsOf: Data(header.utf8))
                } else {
                    try? Data(header.utf8).write(to: fileURL, options: .atomic)
                }
            }
        }
    }

    /// How much history to keep on disk, in bytes.
    private static let longestKeptFile = 512 * 1024

    /// Keep only the last `bytes` of the file, starting at a line boundary.
    private static func trim(_ url: URL, to bytes: Int) {
        guard let size = (try? FileManager.default.attributesOfItem(atPath: url.path))?[.size]
            as? Int, size > bytes
        else { return }

        guard let handle = try? FileHandle(forReadingFrom: url) else { return }
        try? handle.seek(toOffset: UInt64(size - bytes))
        let tail = (try? handle.readToEnd()) ?? Data()
        try? handle.close()

        // Start at the first whole line, so the file never opens mid-sentence.
        let newline = UInt8(ascii: "\n")
        let kept = tail.firstIndex(of: newline).map { tail[(($0) + 1)...] } ?? tail[...]
        try? Data(kept).write(to: url, options: .atomic)
    }

    // MARK: - Writing

    public func log(_ level: LogEntry.Level, _ message: String) {
        let entry = LogEntry(level: level, message: message)

        entries.append(entry)
        if entries.count > limit {
            entries.removeFirst(entries.count - limit)
        }

        for continuation in subscribers.values {
            continuation.yield(entry)
        }

        appendToFile(entry)
    }

    public func info(_ message: String) { log(.info, message) }
    public func success(_ message: String) { log(.success, message) }
    public func warning(_ message: String) { log(.warning, message) }
    public func failure(_ message: String) { log(.failure, message) }

    // MARK: - Reading

    /// Everything held in memory, oldest first.
    public var all: [LogEntry] { entries }

    /// The last `count` entries. The AI fixer sends a slice of these along with
    /// its request.
    public func recent(_ count: Int) -> [LogEntry] {
        Array(entries.suffix(count))
    }

    /// A live feed of entries logged from now on. Read `all` first if the
    /// history matters.
    public func subscribe() -> AsyncStream<LogEntry> {
        let id = UUID()
        let (stream, continuation) = AsyncStream<LogEntry>.makeStream()
        subscribers[id] = continuation
        continuation.onTermination = { [weak self] _ in
            Task { await self?.unsubscribe(id) }
        }
        return stream
    }

    private func unsubscribe(_ id: UUID) {
        subscribers[id] = nil
    }

    // MARK: - File

    private func appendToFile(_ entry: LogEntry) {
        guard let fileURL else { return }
        guard let data = (entry.formatted + "\n").data(using: .utf8) else { return }

        if let handle = try? FileHandle(forWritingTo: fileURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
        } else {
            try? data.write(to: fileURL, options: .atomic)
        }
    }
}
