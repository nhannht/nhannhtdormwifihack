import Foundation

#if canImport(FoundationNetworking)
    import FoundationNetworking
#endif

/// What came back from one request.
public struct PortalResponse: Sendable {
    public let statusCode: Int
    /// Where the request ended up, which is not where it started when the
    /// router or the portal redirected it.
    public let finalURL: URL
    public let data: Data

    public var body: String {
        String(decoding: data, as: UTF8.self)
    }

    public var isSuccess: Bool {
        (200..<300).contains(statusCode)
    }

    public func json() throws -> JSONValue {
        try JSONValue.from(data)
    }
}

/// The HTTP side of talking to the router and the portal.
///
/// One instance is one browsing session: it keeps its own cookie jar and
/// forgets everything when it goes away. Each login attempt builds a fresh one,
/// so a stale cookie from a failed attempt can never leak into the next.
public final class PortalClient: NSObject, @unchecked Sendable {

    /// Sent on every request. The portal serves different content to clients it
    /// does not recognise as a desktop browser.
    public static let userAgent =
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        + "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

    public enum ClientError: Error, CustomStringConvertible {
        case invalidURL(String)
        case notHTTP(URL)

        public var description: String {
            switch self {
            case .invalidURL(let text):
                return "Not a usable URL: \(text)"
            case .notHTTP(let url):
                return "Expected an HTTP response from \(url)"
            }
        }
    }

    private let session: URLSession

    /// - Parameter protocolClasses: Handlers to answer requests instead of the
    ///   network. Tests pass a stub here so the order of calls can be asserted
    ///   without a hotspot; nothing in the app sets it.
    public init(timeout: TimeInterval = 20, protocolClasses: [AnyClass]? = nil) {
        let configuration = URLSessionConfiguration.ephemeral
        if let protocolClasses { configuration.protocolClasses = protocolClasses }
        configuration.httpCookieAcceptPolicy = .always
        configuration.httpShouldSetCookies = true
        configuration.httpCookieStorage = HTTPCookieStorage()
        configuration.timeoutIntervalForRequest = timeout
        configuration.timeoutIntervalForResource = timeout * 2
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.urlCache = nil
        configuration.httpAdditionalHeaders = [
            "User-Agent": Self.userAgent,
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        ]

        // The delegate is a separate object on purpose. URLSession keeps a
        // strong reference to its delegate until the session is invalidated, so
        // a client that is its own delegate keeps itself alive: deinit never
        // runs, nothing invalidates the session, and every login attempt leaks
        // one along with its operation queue and sockets.
        session = URLSession(
            configuration: configuration,
            delegate: GatewayTrust(),
            delegateQueue: nil
        )

        super.init()
    }

    deinit {
        session.invalidateAndCancel()
    }

    // MARK: - Requests

    public func get(
        _ url: URL,
        headers: [String: String] = [:],
        timeout: TimeInterval? = nil
    ) async throws -> PortalResponse {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue(
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            forHTTPHeaderField: "Accept"
        )
        request.setValue("1", forHTTPHeaderField: "Upgrade-Insecure-Requests")
        apply(headers, to: &request)
        if let timeout { request.timeoutInterval = timeout }
        return try await send(request)
    }

    /// A JSON call, as the portal's own page makes them.
    public func postJSON(
        _ url: URL,
        body: JSONValue,
        referer: URL?,
        origin: URL?,
        headers: [String: String] = [:],
        timeout: TimeInterval? = nil
    ) async throws -> PortalResponse {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = try body.encoded()
        request.setValue("application/json; charset=UTF-8", forHTTPHeaderField: "Content-Type")
        request.setValue("*/*", forHTTPHeaderField: "Accept")
        request.setValue("XMLHttpRequest", forHTTPHeaderField: "X-Requested-With")
        if let referer { request.setValue(referer.absoluteString, forHTTPHeaderField: "Referer") }
        if let origin { request.setValue(origin.absoluteString, forHTTPHeaderField: "Origin") }
        apply(headers, to: &request)
        if let timeout { request.timeoutInterval = timeout }
        return try await send(request)
    }

    /// A normal form post, which is what the router's login expects.
    public func postForm(
        _ url: URL,
        fields: [(name: String, value: String)],
        referer: URL?,
        origin: URL?,
        headers: [String: String] = [:],
        timeout: TimeInterval? = nil
    ) async throws -> PortalResponse {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = Data(Self.formEncoded(fields).utf8)
        request.setValue(
            "application/x-www-form-urlencoded",
            forHTTPHeaderField: "Content-Type"
        )
        request.setValue("1", forHTTPHeaderField: "Upgrade-Insecure-Requests")
        if let referer { request.setValue(referer.absoluteString, forHTTPHeaderField: "Referer") }
        if let origin { request.setValue(origin.absoluteString, forHTTPHeaderField: "Origin") }
        apply(headers, to: &request)
        if let timeout { request.timeoutInterval = timeout }
        return try await send(request)
    }

    private func send(_ request: URLRequest) async throws -> PortalResponse {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw ClientError.notHTTP(request.url ?? URL(string: "about:blank")!)
        }
        return PortalResponse(
            statusCode: http.statusCode,
            finalURL: http.url ?? request.url ?? URL(string: "about:blank")!,
            data: data
        )
    }

    private func apply(_ headers: [String: String], to request: inout URLRequest) {
        for (field, value) in headers {
            request.setValue(value, forHTTPHeaderField: field)
        }
    }

    /// Form bodies use `+` for a space, which `addingPercentEncoding` will not do.
    static func formEncoded(_ fields: [(name: String, value: String)]) -> String {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._* ")

        func escape(_ text: String) -> String {
            (text.addingPercentEncoding(withAllowedCharacters: allowed) ?? text)
                .replacingOccurrences(of: " ", with: "+")
        }

        return fields.map { "\(escape($0.name))=\(escape($0.value))" }.joined(separator: "&")
    }
}

// MARK: - Certificate handling

/// Answers the TLS challenge on the session's behalf.
///
/// Kept separate from `PortalClient` so the client is not its own delegate. See
/// the note in `PortalClient.init`.
private final class GatewayTrust: NSObject, URLSessionDelegate {

    /// Accept whatever certificate the gateway presents.
    ///
    /// A captive portal's router serves a self-signed certificate for an IP
    /// address, which no trust chain will ever validate. Refusing it would mean
    /// never being able to log in. This is limited to hosts on a private
    /// network, so a certificate problem out on the internet still fails the way
    /// it should.
    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping @Sendable (
            URLSession.AuthChallengeDisposition, URLCredential?
        ) -> Void
    ) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              PortalClient.isPrivateHost(challenge.protectionSpace.host)
        else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}

extension PortalClient {

    /// Private address ranges, plus loopback. Anything else is a public host.
    ///
    /// Every label has to be a plain decimal octet, and there have to be exactly
    /// four of them. An earlier version dropped the labels that were not numbers
    /// before counting, so `10.0.0.1.evil.com` counted as four octets, matched
    /// the 10.x range, and had any certificate it presented accepted. The portal
    /// host is read from the router's login page, which an attacker on the
    /// captive network controls, so that name is reachable.
    static func isPrivateHost(_ host: String) -> Bool {
        if host == "localhost" || host == "::1" { return true }

        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        guard labels.count == 4 else { return false }

        var octets: [UInt8] = []
        octets.reserveCapacity(4)
        for label in labels {
            // Digits only, so "0x0a", "+1" and "١٠" are all rejected rather than
            // being coerced into an octet.
            guard label.count <= 3,
                  label.allSatisfy({ $0.isASCII && $0.isNumber }),
                  let octet = UInt8(label)
            else { return false }
            octets.append(octet)
        }

        switch (octets[0], octets[1]) {
        case (10, _): return true
        case (127, _): return true
        case (192, 168): return true
        case (172, 16...31): return true
        case (169, 254): return true
        default: return false
        }
    }
}
