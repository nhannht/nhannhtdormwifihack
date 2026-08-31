import Foundation

/// What the router says about the current session.
public struct SessionInfo: Sendable, Equatable {
    public let ipAddress: String?
    public let macAddress: String?
    /// How long this session has been up, in seconds.
    public let connectedFor: Int?
    /// How long it has left, in seconds. The router publishes this, so there is
    /// no need to guess from a local timer.
    public let remaining: Int?

    public init(
        ipAddress: String? = nil,
        macAddress: String? = nil,
        connectedFor: Int? = nil,
        remaining: Int? = nil
    ) {
        self.ipAddress = ipAddress
        self.macAddress = macAddress
        self.connectedFor = connectedFor
        self.remaining = remaining
    }
}

/// Asks the router whether we are signed in, and for how much longer.
///
/// This has to run before anything else. On this hotspot `/login` does not
/// answer at all while a session is open: it accepts the connection and then
/// hangs until the client gives up. Only `/status` is safe to ask first, and it
/// replies in about 15ms.
public enum SessionProbe {

    public enum Status: Sendable, Equatable {
        case active(SessionInfo)
        case expired
        /// The router answered, but the page names no session.
        ///
        /// Different from `unreachable` and the difference matters: this means
        /// we are on the right network and signed out, so the thing to do is
        /// sign in. At the moment a session lapses the router serves a status
        /// page with the session rows simply absent, which lands here.
        case unknown(String)
        /// The router did not answer at all, so this Mac is probably on some
        /// other network. Signing in cannot help; getting onto the right
        /// network is what is needed.
        case unreachable(String)

        public var isActive: Bool {
            if case .active = self { return true }
            return false
        }

        public var info: SessionInfo? {
            if case .active(let info) = self { return info }
            return nil
        }
    }

    public static func status(
        gatewayIP: String,
        client: PortalClient,
        timeout: TimeInterval = 5
    ) async -> Status {
        guard let url = URL(string: "http://\(gatewayIP)/status") else {
            return .unreachable("Cannot build a status URL for \(gatewayIP)")
        }

        do {
            let response = try await client.get(url, timeout: timeout)
            guard response.isSuccess else {
                return .unreachable("Status page answered HTTP \(response.statusCode)")
            }
            return interpret(body: response.body, finalURL: response.finalURL)
        } catch {
            // A transport failure is the router not being there, which is a
            // different problem from a page that carries no session.
            return .unreachable(ErrorText.short(for: error))
        }
    }

    // MARK: - Reading the page

    static func interpret(body: String, finalURL: URL) -> Status {
        // Being sent to the login page settles it immediately.
        if finalURL.path.lowercased().contains("login") { return .expired }

        // Script and style bodies go before anything is read out of the page, so
        // a status page whose JavaScript happens to name the challenge is not
        // read as a login page. Getting that wrong reported an open session as
        // signed out on every pass, which sent the watcher into a login loop.
        let visible = withoutScriptsAndStyles(body)

        let lower = visible.lowercased()
        if lower.contains("chap-challenge") || lower.contains("chap-id") { return .expired }

        let fields = textFields(in: visible)
        let info = SessionInfo(
            ipAddress: value(for: ipLabels, in: fields),
            macAddress: value(for: macLabels, in: fields),
            connectedFor: value(for: connectedLabels, in: fields).flatMap(duration),
            remaining: value(for: remainingLabels, in: fields).flatMap(duration)
        )

        // A session page names at least one of these. A login page names none.
        if info.remaining != nil || info.connectedFor != nil || info.macAddress != nil {
            return .active(info)
        }

        if lower.contains("uptime") || lower.contains("bytes in") { return .active(info) }
        if lower.contains("<form") && lower.contains("password") { return .expired }

        return .unknown("Status page did not identify itself")
    }

    // Both the Vietnamese page this was built against and the stock MikroTik
    // English one, so a firmware or language change does not blind it.
    private static let ipLabels = ["địa chỉ ip", "ip address", "ip"]
    private static let macLabels = ["địa chỉ mac", "mac address", "mac"]
    private static let connectedLabels = ["thời gian kết nối", "connected", "uptime"]
    private static let remainingLabels = ["thời gian còn lại", "time left", "session time left"]

    /// Everything between a `script` or `style` tag and its close, removed.
    static func withoutScriptsAndStyles(_ html: String) -> String {
        var text = html
        for tag in ["script", "style"] {
            // `[\s\S]*?` rather than `.*?`, because the option that makes a dot
            // cross a line break belongs to NSRegularExpression and cannot be
            // reached through String.CompareOptions. With a plain dot a script
            // body spanning more than one line survived into the fields, where
            // its text was read as labels and its numbers as durations.
            text = text.replacingOccurrences(
                of: #"<\#(tag)[^>]*>[\s\S]*?</\#(tag)>"#,
                with: " ",
                options: [.regularExpression, .caseInsensitive]
            )
        }
        return text
    }

    /// The page is a two column table, so the value is the cell after the label.
    static func textFields(in html: String) -> [String] {
        let text = withoutScriptsAndStyles(html)
            .replacingOccurrences(of: "<[^>]+>", with: "\u{1}", options: .regularExpression)

        return text.components(separatedBy: "\u{1}")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private static func value(for labels: [String], in fields: [String]) -> String? {
        for (index, field) in fields.enumerated() {
            let normalized = field.lowercased().trimmingCharacters(
                in: CharacterSet(charactersIn: ": \t")
            )
            guard labels.contains(normalized), index + 1 < fields.count else { continue }
            return fields[index + 1]
        }
        return nil
    }

    /// `12m25s`, `1h2m3s`, `45s`, `1d2h3m4s` into seconds.
    ///
    /// Every arithmetic step is overflow-checked and a number too large to hold
    /// is rejected. This text arrives from the router over plain HTTP, so a run
    /// of twenty digits is something the page can genuinely say, and unchecked
    /// `number * 10 + digit` traps on it and takes the whole watcher down.
    static func duration(_ text: String) -> Int? {
        let units: [(Character, Int)] = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]

        var total = 0
        var number = 0
        var sawAnyUnit = false

        for character in text.lowercased() {
            if let digit = character.wholeNumberValue, character.isNumber {
                let (shifted, shiftOverflowed) = number.multipliedReportingOverflow(by: 10)
                guard !shiftOverflowed else { return nil }
                let (grown, addOverflowed) = shifted.addingReportingOverflow(digit)
                guard !addOverflowed else { return nil }
                number = grown
            } else if let unit = units.first(where: { $0.0 == character }) {
                let (scaled, scaleOverflowed) = number.multipliedReportingOverflow(by: unit.1)
                guard !scaleOverflowed else { return nil }
                let (sum, sumOverflowed) = total.addingReportingOverflow(scaled)
                guard !sumOverflowed else { return nil }
                total = sum
                number = 0
                sawAnyUnit = true
            } else if !character.isWhitespace {
                return nil
            }
        }

        return sawAnyUnit ? total : nil
    }
}
