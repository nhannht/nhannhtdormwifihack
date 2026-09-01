import Foundation
import Network
import os

/// Is there real internet, right now?
///
/// This opens a TCP connection to a public DNS server rather than fetching a web
/// page. A captive portal answers HTTP requests with its own page, so an HTTP
/// check can come back looking healthy while the connection is still locked.
/// A TCP handshake on port 53 either completes or it does not, and it settles in
/// a fraction of the time a page fetch takes.
public enum NetworkProbe {

    public struct Target: Sendable, Equatable {
        public let host: String
        public let port: UInt16

        public init(host: String, port: UInt16) {
            self.host = host
            self.port = port
        }
    }

    /// Google first, Cloudflare second, so one operator being unreachable does
    /// not read as the whole connection being down.
    public static let defaultTargets = [
        Target(host: "8.8.8.8", port: 53),
        Target(host: "1.1.1.1", port: 53),
    ]

    /// - Parameter avoiding: kinds of connection that must not be used to answer.
    ///   A phone has to pass `[.cellular]`. It keeps mobile data running
    ///   alongside the wireless network, so an unrestricted probe answers over
    ///   mobile data and reports a healthy internet at the exact moment the
    ///   hotspot is holding the wifi captive. That reading is worse than none,
    ///   because it is precisely backwards.
    ///
    ///   Naming what to avoid rather than what to require is deliberate. Asking
    ///   for wifi only also rules out every other way a device can be genuinely
    ///   online, and the question here is never "is this wifi": it is "is this
    ///   real internet, or is it mobile data covering for a captive hotspot".
    public static func hasInternet(
        timeout: Duration = .milliseconds(1500),
        targets: [Target] = defaultTargets,
        avoiding: [NWInterface.InterfaceType] = []
    ) async -> Bool {
        for target in targets {
            if await canConnect(to: target, timeout: timeout, avoiding: avoiding) {
                return true
            }
        }
        return false
    }

    public static func canConnect(
        to target: Target,
        timeout: Duration,
        avoiding: [NWInterface.InterfaceType] = []
    ) async -> Bool {
        guard let port = NWEndpoint.Port(rawValue: target.port) else { return false }

        let parameters = NWParameters.tcp
        parameters.prohibitedInterfaceTypes = avoiding

        let connection = NWConnection(
            host: NWEndpoint.Host(target.host),
            port: port,
            using: parameters
        )

        // The state handler and the timeout race each other, and whichever wins
        // has to be the only one to resume.
        let settled = OSAllocatedUnfairLock(initialState: false)

        return await withTaskCancellationHandler {
            await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
                @Sendable func settle(_ reachable: Bool) {
                    let alreadySettled = settled.withLock { done -> Bool in
                        defer { done = true }
                        return done
                    }
                    guard !alreadySettled else { return }
                    connection.cancel()
                    continuation.resume(returning: reachable)
                }

                connection.stateUpdateHandler = { state in
                    switch state {
                    case .ready:
                        settle(true)
                    case .failed, .cancelled:
                        settle(false)
                    case .waiting:
                        // No route, or the packets are being dropped. For a probe
                        // this short, waiting is a no.
                        settle(false)
                    case .setup, .preparing:
                        break
                    @unknown default:
                        break
                    }
                }

                connection.start(queue: .global(qos: .utility))

                Task {
                    try? await Task.sleep(for: timeout)
                    settle(false)
                }
            }
        } onCancel: {
            connection.cancel()
        }
    }
}
