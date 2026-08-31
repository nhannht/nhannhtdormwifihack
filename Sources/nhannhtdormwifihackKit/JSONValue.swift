import Foundation

/// Any JSON value, kept whole.
///
/// The portal hands back a large context object, expects it passed to the next
/// call unchanged, and only two small pieces of it are ever read. Modelling the
/// whole shape as Swift types would mean guessing at fields that are not used
/// and breaking whenever the portal adds one, so the object is carried opaquely
/// instead.
public enum JSONValue: Codable, Sendable, Equatable {
    case null
    case bool(Bool)
    /// Whole numbers keep their exact value and their plain spelling.
    ///
    /// The context carries session and customer ids well past the 2^53 a Double
    /// can hold exactly, and it has to go back to the portal unchanged, so an id
    /// routed through Double would return as a different number or in
    /// exponential notation and the portal would refuse to issue an account.
    case integer(Int)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    // MARK: - Reading

    public subscript(key: String) -> JSONValue? {
        guard case .object(let fields) = self else { return nil }
        return fields[key]
    }

    public subscript(index: Int) -> JSONValue? {
        guard case .array(let items) = self, items.indices.contains(index) else { return nil }
        return items[index]
    }

    /// Walk a chain of keys, as in `value(at: "captiveContext", "customer")`.
    public func value(at path: String...) -> JSONValue? {
        value(at: path)
    }

    public func value(at path: [String]) -> JSONValue? {
        path.reduce(self as JSONValue?) { current, key in current?[key] }
    }

    public var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    public var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    public var doubleValue: Double? {
        switch self {
        case .integer(let value): Double(value)
        case .number(let value): value
        default: nil
        }
    }

    public var intValue: Int? {
        switch self {
        case .integer(let value): value
        case .number(let value): Int(exactly: value)
        default: nil
        }
    }

    public var isNull: Bool {
        if case .null = self { return true }
        return false
    }

    // MARK: - Codable

    public init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()

        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            // Int before Double, so a whole number survives the round trip.
            self = .integer(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Value is not valid JSON."
            )
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .integer(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }

    // MARK: - Building

    public static func from(_ data: Data) throws -> JSONValue {
        try JSONDecoder().decode(JSONValue.self, from: data)
    }

    public func encoded() throws -> Data {
        try JSONEncoder().encode(self)
    }
}

extension JSONValue: ExpressibleByStringLiteral,
    ExpressibleByBooleanLiteral,
    ExpressibleByFloatLiteral,
    ExpressibleByIntegerLiteral,
    ExpressibleByDictionaryLiteral,
    ExpressibleByArrayLiteral,
    ExpressibleByNilLiteral
{
    public init(stringLiteral value: String) { self = .string(value) }
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(floatLiteral value: Double) { self = .number(value) }
    public init(integerLiteral value: Int) { self = .integer(value) }
    public init(nilLiteral: ()) { self = .null }

    public init(dictionaryLiteral elements: (String, JSONValue)...) {
        self = .object(Dictionary(elements) { _, last in last })
    }

    public init(arrayLiteral elements: JSONValue...) {
        self = .array(elements)
    }
}
