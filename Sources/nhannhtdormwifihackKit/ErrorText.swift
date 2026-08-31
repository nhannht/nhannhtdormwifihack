import Foundation

/// Turns networking errors into something worth reading.
///
/// A URLSession failure prints as several hundred characters of nested domains
/// and userInfo dictionaries. In a log that scrolls past every two seconds, that
/// buries the one word that matters.
public enum ErrorText {

    public static func short(for error: any Error) -> String {
        // Our own errors already say what went wrong in plain words.
        switch error {
        case let error as LoginEngine.EngineError: return error.description
        case let error as PortalClient.ClientError: return error.description
        case let error as CHAP.ParseError: return error.description
        default: break
        }

        let nsError = error as NSError

        guard nsError.domain == NSURLErrorDomain else {
            return nsError.localizedDescription
        }

        return switch nsError.code {
        case NSURLErrorTimedOut: "timed out"
        case NSURLErrorCannotConnectToHost: "nothing is listening there"
        case NSURLErrorCannotFindHost: "that name does not resolve"
        case NSURLErrorNotConnectedToInternet: "this machine has no network"
        case NSURLErrorNetworkConnectionLost: "the connection dropped"
        case NSURLErrorDNSLookupFailed: "DNS lookup failed"
        case NSURLErrorSecureConnectionFailed: "the secure connection failed"
        case NSURLErrorServerCertificateUntrusted: "the certificate was not trusted"
        case NSURLErrorBadServerResponse: "the reply was not valid HTTP"
        case NSURLErrorUnsupportedURL: "unsupported URL"
        default: nsError.localizedDescription
        }
    }

    /// `77 days`, `4h 20m`, `45s`. For telling someone how long ago something was.
    public static func duration(_ seconds: Int) -> String {
        let seconds = max(seconds, 0)

        if seconds < 60 { return "\(seconds)s" }
        if seconds < 3600 { return "\(seconds / 60)m \(seconds % 60)s" }
        if seconds < 86400 { return "\(seconds / 3600)h \(seconds % 3600 / 60)m" }

        let days = seconds / 86400
        return days == 1 ? "1 day" : "\(days) days"
    }
}
