import Foundation

/// One `<input>` tag lifted off a page.
public struct HTMLInput: Sendable, Equatable {
    public let name: String?
    public let id: String?
    public let value: String?

    /// The key to send this field under: its `name`, or its `id` when it has no
    /// name. This is the rule the router's own page relies on.
    public var key: String? {
        if let name, !name.isEmpty { return name }
        if let id, !id.isEmpty { return id }
        return nil
    }

    public init(name: String?, id: String?, value: String?) {
        self.name = name
        self.id = id
        self.value = value
    }
}

/// Pulls hidden form fields out of the two pages the login sequence reads: the
/// router's hotspot login page, and the HTML fragment the portal returns.
///
/// This deliberately does not parse HTML in general. Both pages are known, and
/// the only thing wanted from either is the attributes of their `<input>` tags,
/// which is a job a pattern match does correctly. The alternative, SwiftSoup, is
/// not ready for Swift 6 concurrency checking and would be the project's only
/// third-party dependency.
public enum HTMLInputScraper {

    // Built on demand rather than stored. Regex is not Sendable, so a static
    // constant would be a shared mutable global under Swift 6 checking, and the
    // cost of building one is nothing next to the network round trip it serves.
    private static var inputTag: Regex<Substring> {
        /<input\b[^>]*>/.ignoresCase()
    }

    private static var formTag: Regex<Substring> {
        /<form\b[^>]*>/.ignoresCase()
    }

    private static var attribute: Regex<(Substring, Substring, Substring?, Substring?, Substring?)> {
        /([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>=`]+))/
    }

    // MARK: - Reading inputs

    /// Every `<input>` on the page, in the order it appears.
    public static func inputs(in html: String) -> [HTMLInput] {
        html.matches(of: inputTag).map { match in
            let attrs = attributes(in: match.output)
            return HTMLInput(name: attrs["name"], id: attrs["id"], value: attrs["value"])
        }
    }

    /// The value of the input with this `name`.
    public static func value(named name: String, in html: String) -> String? {
        inputs(in: html).first { $0.name == name }?.value
    }

    /// The value of the input with this `id`, falling back to its `name`.
    ///
    /// The router labels the two CHAP fields inconsistently across firmware
    /// versions, so both spellings have to be tried.
    public static func value(identifiedBy identifier: String, in html: String) -> String? {
        let all = inputs(in: html)
        if let byID = all.first(where: { $0.id == identifier })?.value { return byID }
        return all.first { $0.name == identifier }?.value
    }

    /// Every field that has both a key and a value, ready to hang off a URL.
    ///
    /// A key that appears more than once keeps the position of its first
    /// appearance and the value of its last, which is how the original built
    /// this list.
    public static func queryItems(in html: String) -> [URLQueryItem] {
        var order: [String] = []
        var values: [String: String] = [:]

        for input in inputs(in: html) {
            guard let key = input.key,
                  let value = input.value, !value.isEmpty
            else { continue }
            if values[key] == nil { order.append(key) }
            values[key] = value
        }

        return order.compactMap { key in
            values[key].map { URLQueryItem(name: key, value: $0) }
        }
    }

    /// Where the page's first form posts to, if it says.
    public static func formAction(in html: String) -> String? {
        guard let match = html.firstMatch(of: formTag) else { return nil }
        let action = attributes(in: match.output)["action"]
        return (action?.isEmpty ?? true) ? nil : action
    }

    // MARK: - Attributes

    private static func attributes(in tag: Substring) -> [String: String] {
        var result: [String: String] = [:]
        for match in tag.matches(of: attribute) {
            let quoted = match.2 ?? match.3 ?? match.4 ?? ""
            result[match.1.lowercased()] = decodingEntities(String(quoted))
        }
        return result
    }

    /// The handful of entities that actually turn up in these values.
    private static func decodingEntities(_ text: String) -> String {
        guard text.contains("&") else { return text }
        var out = text
        for (entity, character) in [
            ("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""),
            ("&#39;", "'"), ("&apos;", "'"), ("&nbsp;", " "),
        ] {
            out = out.replacingOccurrences(of: entity, with: character)
        }
        // Ampersand goes last, so `&amp;lt;` ends up as `&lt;` and not as `<`.
        return out.replacingOccurrences(of: "&amp;", with: "&")
    }
}
