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

    /// The same line as written to disk: `s [16:57:55] Logged in.`
    ///
    /// The leading letter is the level. On the phone this file is the only
    /// channel between the tunnel and the app, which are separate processes, so
    /// a line that loses its level arrives on screen as ordinary news even when
    /// it is a failure.
    public var forFile: String {
        "\(level.rawValue.prefix(1)) \(formatted)"
    }

    /// Read a line back, in either the current shape or the older one that had
    /// no level on the front.
    ///
    /// Returns nil for anything that is not a log line, such as the
    /// `--- session started at ... ---` banner.
    public static func parse(fileLine line: String, on day: Date = Date()) -> LogEntry? {
        var rest = Substring(line)
        var level = Level.info

        if rest.count > 2, rest.dropFirst(1).first == " ",
           let first = rest.first,
           let found = Level.allCases.first(where: { $0.rawValue.hasPrefix(String(first)) })
        {
            level = found
            rest = rest.dropFirst(2)
        }

        guard rest.first == "[", let close = rest.firstIndex(of: "]") else { return nil }

        let clock = String(rest[rest.index(after: rest.startIndex)..<close])
        guard let time = timeFormatter.date(from: clock) else { return nil }

        let message = String(rest[rest.index(after: close)...])
            .trimmingCharacters(in: .whitespaces)
        guard !message.isEmpty else { return nil }

        // The file stores a clock time and no date, so put it on `day`. Reading
        // yesterday's tail would otherwise stamp every line with 1 January 2000
        // and sort the list into nonsense.
        let parts = Calendar.current.dateComponents([.hour, .minute, .second], from: time)
        let dated = Calendar.current.date(
            bySettingHour: parts.hour ?? 0,
            minute: parts.minute ?? 0,
            second: parts.second ?? 0,
            of: day
        ) ?? day

        return LogEntry(date: dated, level: level, message: message)
    }

    static let timeFormatter: DateFormatter = {
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

    /// Append one line straight to the file, without going through the actor.
    ///
    /// For last words only. A tunnel being shut down has to hand control back
    /// to iOS immediately and the process can be gone a moment later, so a line
    /// handed to an actor is a line that never reaches the disk. That line is
    /// also the only evidence of why the tunnel stopped, which makes losing it
    /// the worst possible outcome.
    public static func appendDirectly(
        _ level: LogEntry.Level, _ message: String, to url: URL
    ) {
        let line = LogEntry(level: level, message: message).forFile + "\n"
        guard let data = line.data(using: .utf8) else { return }

        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )

        if let handle = try? FileHandle(forWritingTo: url) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
        } else {
            try? data.write(to: url, options: .atomic)
        }
    }

    /// The last `limit` lines of a log file, as entries.
    ///
    /// This is how the app sees what the tunnel did. They are separate
    /// processes, so nothing the tunnel logs arrives through `subscribe()`; the
    /// file both of them append to is the only thing they share.
    public static func entriesInFile(at url: URL, limit: Int = 300) -> [LogEntry] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return text
            .split(separator: "\n", omittingEmptySubsequences: true)
            .suffix(limit * 2)
            .compactMap { LogEntry.parse(fileLine: String($0)) }
            .suffix(limit)
    }

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
        guard let data = (entry.forFile + "\n").data(using: .utf8) else { return }

        if let handle = try? FileHandle(forWritingTo: fileURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
        } else {
            try? data.write(to: fileURL, options: .atomic)
        }
    }
}
