import Foundation
import Network

/// Keeps the session alive for as long as it is allowed to run.
///
/// This is the phone's watcher. It is the session-keeping half of
/// `SessionScheduler` and nothing else: ask the router how long is left, wait
/// for the session to end, sign in again the moment it does. The other half of
/// the Mac's watcher, the part that joins and rejoins wireless networks, has no
/// iOS equivalent and is not missing by accident. See `WiFi.swift`.
///
/// One loop, two places it can run, and the difference matters enough that it
/// has to be said out loud rather than assumed. Inside the app it stops a few
/// seconds after the app leaves the screen, because iOS suspends the app.
/// Inside the tunnel it keeps going with the app closed and the phone locked,
/// because a Network Extension is a separate process that iOS does not suspend.
/// `home` decides which of those this run is, and the log says so on the first
/// line so nobody has to guess which promise is in force.
///
/// `sessionEndsAt` is what the app reads when it is about to be suspended, so
/// it can leave a notification at the moment the session ends.
public actor SessionRenewer {

    /// Where this loop is running, and therefore what it can promise.
    public enum Home: Sendable {

        /// In the app, on the screen. Ends when the app is put away.
        case appOnScreen

        /// In the tunnel extension. Survives the app closing and the screen
        /// locking, and ends only when the tunnel is switched off.
        case tunnel

        var openingNote: String {
            switch self {
            case .appOnScreen:
                return """
                    iOS stops this the moment the app leaves the screen. Leave it open to hold \
                    the session; otherwise sign in again when you come back.
                    """
            case .tunnel:
                return """
                    Running inside the tunnel, so it keeps going with the app closed and the \
                    phone locked.
                    """
            }
        }
    }

    /// Not a constant. The settings screen can change the router address or the
    /// portal while the loop is running, and those are exactly the two things a
    /// user changes when it is not working. A copy taken once at startup would
    /// mean the fix they just typed in has no effect and nothing says why.
    private var settings: Settings
    private let log: ActivityLog

    /// Where the state goes so the screen can read it.
    ///
    /// A file rather than an in-process broadcast, because this loop is not
    /// always in the same process as the screen. Inside the tunnel it is not,
    /// and anything published in here would simply never arrive.
    private let statusFile: StatusFile

    private var status = WatcherStatus()

    /// When the router said the open session ends, or nil when there is none.
    ///
    /// Read by the app as it goes to the background, so it can put a
    /// notification at that moment. A deadline rather than a countdown, because
    /// the app may read it a while after the router was last asked.
    public private(set) var sessionEndsAt: Date?

    private let home: Home

    /// Told when the state changed enough that anything else showing it is now
    /// showing the wrong thing.
    ///
    /// The status file is the whole story and this carries none of it; it only
    /// says "look again". The phone's widget needs that because a widget cannot
    /// keep reading a file on its own. The Mac passes nothing, because the menu
    /// it feeds is in this process and already subscribed.
    private let announce: @Sendable () -> Void

    /// The last state that was announced, so a heartbeat that found the same
    /// session again does not announce it a second time.
    private var announced: WatcherStatus?

    public init(
        settings: Settings,
        log: ActivityLog,
        statusFile: StatusFile = .shared,
        home: Home = .appOnScreen,
        announce: @escaping @Sendable () -> Void = {}
    ) {
        self.settings = settings
        self.log = log
        self.statusFile = statusFile
        self.home = home
        self.announce = announce
    }

    /// Find out where we stand, and if we are locked out, get back in.
    ///
    /// What the app does the moment it opens. Someone opening this app has just
    /// found their internet not working; reporting "signed out" back at them and
    /// waiting to be prodded is answering a question they did not ask. This is
    /// what the Mac's plain `nhannhtdormwifihack` command does too: sign in,
    /// then watch.
    public func catchUp() async {
        // Say what it found. Opening the app and getting a silent screen leaves
        // the user with no way to tell "it looked and we are fine" apart from
        // "it never looked", and those need completely different reactions.
        switch await look() {
        case .active(let info):
            let left = info.remaining.map { ", \(ErrorText.duration($0)) left" } ?? ""
            await log.success("Router is \(status.routerIP). Signed in\(left).")
        case .signedOut:
            await log.info("Router is \(status.routerIP). Signed out, so signing in.")
            await signInAndWait()
        case .noRouter(let why):
            await log.warning(Self.noRouterLine(at: status.routerIP, why: why))
        }
    }

    /// Use these settings from the next pass onwards.
    public func use(_ updated: Settings) {
        settings = updated
    }

    // MARK: - The loop

    public func run() async {
        await log.info(
            """
            Watching. This router allows one \
            \(ErrorText.duration(settings.sessionTimeoutSeconds)) session at a time and will not \
            renew one early, so expect a gap of a few seconds once per session.
            """
        )
        await log.info(home.openingNote)

        await report(.starting)

        while !Task.isCancelled {
            switch await look() {

            case .active(let info):
                let left = info.remaining ?? SessionPacing.blindSleepSeconds
                let nap = SessionPacing.napWhileActive(secondsLeft: left)
                if left > SessionPacing.pounceSeconds {
                    await log.info(
                        "Signed in, \(ErrorText.duration(left)) left. Next check in \(nap)s."
                    )
                }
                try? await Task.sleep(for: .seconds(nap))

            case .signedOut:
                await signInAndWait()

            case .noRouter(let why):
                await log.warning(Self.noRouterLine(at: status.routerIP, why: why))
                try? await Task.sleep(for: .seconds(max(checkDelay, 5)))
            }
        }

        await report(.stopped)
        await log.info("Stopped watching.")
    }

    // MARK: - Signing in

    /// Sign in, and keep trying briefly while the router is still refusing.
    ///
    /// `/login` only starts answering once the old session has actually gone, so
    /// the first attempt on a boundary can still be refused. Retrying for a
    /// short window costs nothing and closes the gap to a few seconds.
    private func signInAndWait() async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(SessionPacing.pounceRetrySeconds))

        while !Task.isCancelled {
            if await signInOnce().isConnected { return }

            guard ContinuousClock.now < deadline else {
                await log.failure(
                    "Could not sign in within \(SessionPacing.pounceRetrySeconds)s. Still trying."
                )
                return
            }

            try? await Task.sleep(for: .seconds(SessionPacing.retryEverySeconds))
        }
    }

    /// One attempt, reported the same way the Mac reports it.
    @discardableResult
    public func signInOnce() async -> LoginResult {
        let active = await settings.resolvingGateway(waitingUpTo: 10)

        guard !active.gatewayIP.isEmpty else {
            await log.warning("This phone has no network to sign in on.")
            return .failed("No network")
        }

        await log.info("Router is \(active.gatewayIP).")

        let started = ContinuousClock.now
        let result = await LoginEngine(settings: active, log: log).run()
        let took = String(format: "%.1f", preciseSeconds(since: started))
        await log.info("\(result.summary) (\(took)s)")

        // Look again before returning, so the screen shows the router, the
        // countdown and the state that this attempt produced. Without it a
        // sign-in pressed by hand writes lines into the log and leaves the rest
        // of the screen reading "Starting" and "not found yet".
        _ = await look()

        return result
    }

    // MARK: - Reading the connection

    /// Ask the router what is going on, and tell the screen.
    ///
    /// Every reading the app shows comes from here, whether the loop asked or
    /// the user pressed the button. One place that asks and publishes is what
    /// keeps the button and the loop from telling two different stories about
    /// the same connection.
    private func look() async -> Sighting {
        let active = settings.resolvingGateway()
        status.routerIP = active.gatewayIP

        guard !active.gatewayIP.isEmpty else {
            // Two different things land here and the message has to admit both.
            // Either this phone really is on no wireless network, or it is on
            // one and iOS declined to say which addresses it has. Naming only
            // the first would send the user to look at their wifi settings,
            // which are fine, instead of at the box that fixes it.
            let why = """
                could not work out the router's address. Either this phone is on no wireless                 network, or iOS is not saying. Type the router's address into Settings if you                 know it.
                """
            sessionEndsAt = nil
            status.expiresAt = nil
            status.hasInternet = false
            await report(.wrongNetwork(why))
            return .noRouter(why)
        }

        switch await SessionProbe.status(gatewayIP: active.gatewayIP, client: PortalClient()) {

        case .active(let info):
            sessionEndsAt = info.remaining.map { Date().addingTimeInterval(Double($0)) }
            status.expiresAt = sessionEndsAt
            status.hasInternet = await onlineWithoutCellular()
            await report(.signedIn)
            return .active(info)

        case .expired, .unknown:
            // The router answered and named no session, so it is there and we
            // are signed out. `/login` answers in this state, and only in it.
            sessionEndsAt = nil
            status.expiresAt = nil
            await report(.signingIn)
            return .signedOut

        case .unreachable(let why):
            // Nothing is listening on the router's address. On a Mac this is
            // where the watcher would rejoin the hotspot. A phone cannot, so the
            // honest move is to say where it is looking and look again. iOS
            // keeps the user online over cellular meanwhile.
            sessionEndsAt = nil
            status.expiresAt = nil
            status.hasInternet = false
            await report(.wrongNetwork(why))
            return .noRouter(why)
        }
    }

    /// Why there is no session to watch, in words that match what happened.
    ///
    /// Three different things end up here and they need three different
    /// reactions. Nothing at the address at all means the wrong network or the
    /// wrong address. An answer that is not a hotspot page means this network
    /// simply has no captive portal, which is what any ordinary home or office
    /// router does, and the app has nothing to do here. No address at all means
    /// the phone could not work out where to look.
    private static func noRouterLine(at address: String, why: String) -> String {
        guard !address.isEmpty else { return "Cannot look for a session: \(why)" }

        if why.lowercased().contains("http") {
            return """
                \(address) answered, but not as a hotspot: \(why). This network probably has no \
                sign-in page, so there is nothing to do here. Join the hotspot and come back.
                """
        }

        return """
            Nothing answered at \(address): \(why). Join the hotspot in Settings, then come back.
            """
    }

    /// What one look at the router found.
    private enum Sighting {
        case active(SessionInfo)
        case signedOut
        case noRouter(String)
    }

    /// Is the connection carrying real traffic, not counting mobile data?
    ///
    /// A phone keeps cellular running alongside the wireless network, so an
    /// unrestricted probe would answer over mobile data and call the hotspot
    /// healthy at the exact moment it is holding the connection captive.
    private func onlineWithoutCellular() async -> Bool {
        await NetworkProbe.hasInternet(timeout: .milliseconds(1500), avoiding: [.cellular])
    }

    private func report(_ connection: WatcherStatus.Connection) async {
        status.connection = connection
        statusFile.write(status)

        guard announced.map({ status.differsMateriallyFrom($0) }) ?? true else { return }
        announced = status
        announce()
    }

    private var checkDelay: Int {
        min(max(settings.checkIntervalSeconds, 1), 60)
    }

    private func preciseSeconds(since instant: ContinuousClock.Instant) -> Double {
        let parts = (ContinuousClock.now - instant).components
        return Double(parts.seconds) + Double(parts.attoseconds) / 1e18
    }
}
