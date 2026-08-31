import Foundation

/// Keeps the connection up, indefinitely.
///
/// The router hands out a fixed session, fifteen minutes on this hotspot, and
/// there is no way to extend one. Signing in again while a session is open is
/// the obvious thing to try and it does not work: the router accepts the
/// connection to `/login` and then never answers until the session has actually
/// gone. So the only strategy available is to know exactly when the session ends
/// and sign in again the moment it does.
///
/// The router publishes the countdown itself on `/status`, which answers in
/// about 15ms whether signed in or out. That number is better than any clock
/// kept here: it survives sleep, a closed lid and clock drift, and it comes from
/// the device that actually enforces the limit. So this asks `/status` on every
/// pass and does what the answer says. There is no local timer and no state
/// carried between passes to get out of step with the router.
///
/// The cost is a gap of a few seconds once per session, while the old session
/// lapses and the new sign-in goes through. That gap is inherent to a router
/// that refuses early renewal; it cannot be closed from here.
public actor SessionScheduler {

    /// How often to retry while the router is refusing, in seconds. Deliberately
    /// impatient: `/login` starts answering the instant the old session goes,
    /// and every attempt before that is free.
    private static let retryEverySeconds = 2

    /// Stop sleeping and start watching closely once the session has this many
    /// seconds left, so the sign-in goes in as soon as the old one lapses.
    private static let pounceSeconds = 5

    /// How long to keep trying after a lapse before giving up on this pass.
    private static let pounceRetrySeconds = 90

    /// Never sleep longer than this, so a session that vanishes early is
    /// noticed and stopping stays responsive.
    private static let maximumSleep = 60

    /// What to assume is left when the router reports an open session without
    /// saying how long. Short on purpose: guessing high means missing the lapse.
    private static let blindSleepSeconds = 30

    /// How long the internet may be gone before the safety net fires, in
    /// seconds. Set by the user at fifteen.
    private static let internetGraceSeconds = 15

    /// How long to leave the hotspot alone after falling back, in seconds, and
    /// the ceiling that wait grows to.
    ///
    /// Without this the two networks flap: on the phone hotspot the dorm router
    /// is unreachable, so we join the dorm hotspot, which fails, so the safety
    /// net puts us back on the phone, where the dorm router is unreachable
    /// again. Two joins a cycle, forever, each dropping the link for half a
    /// minute. Doubling the wait after every failure and only clearing it on a
    /// session that actually works is what stops that.
    private static let firstBackoffSeconds = 120
    private static let longestBackoffSeconds = 1800

    /// How many times in a row the router has to ignore us before we conclude
    /// this Mac is on the wrong network and rejoin.
    ///
    /// Not one. A single timeout means nothing: the router gets busy, the radio
    /// hiccups, and this Mac roams between the two access points that serve this
    /// hotspot. Rejoining on one missed reply would drop a working link for no
    /// reason, which is the exact failure this whole file exists to avoid.
    private static let unreachableBeforeRejoining = 3

    private let settings: Settings
    private let log: ActivityLog
    private let wifi: WiFiJoiner

    /// Where the menu reads its state from. Nil when running from the command
    /// line, where the log is the display.
    private let board: StatusBoard?
    private var status = WatcherStatus()

    /// When the internet went away, or nil while it is working.
    private var offlineSince: ContinuousClock.Instant?

    /// Don't touch the dorm hotspot until this moment, and how long the next
    /// such wait will be.
    private var leaveHotspotAloneUntil: ContinuousClock.Instant?
    private var backoffSeconds = SessionScheduler.firstBackoffSeconds

    public init(settings: Settings, log: ActivityLog, board: StatusBoard? = nil) {
        self.settings = settings
        self.log = log
        self.board = board
        self.wifi = WiFiJoiner(settings: settings, log: log)
        self.status.network = settings.wifiName
    }

    /// Tell whoever is displaying the state what just changed.
    private func report(_ connection: WatcherStatus.Connection, router: String? = nil) async {
        guard let board else { return }
        status.connection = connection
        if let router { status.routerIP = router }
        await board.publish(status)
    }

    public func run() async {
        await log.info(
            """
            Watching. This router allows one \
            \(ErrorText.duration(settings.sessionTimeoutSeconds)) session at a time and will \
            not renew one early, so expect a gap of a few seconds once per session.
            """
        )

        await announceSafetyNet()
        await report(.starting)

        var unreachableInARow = 0

        while !Task.isCancelled {
            // The safety net goes first, before anything is asked of the
            // hotspot. Its whole job is to make sure a failed experiment costs
            // fifteen seconds of internet rather than an evening of it.
            if await safetyNetFired() {
                unreachableInARow = 0
                continue
            }

            switch await currentSession() {

            case .active(let info):
                unreachableInARow = 0

                // A session that actually works clears the debt from every
                // earlier failure, so the next problem starts from a short wait
                // again rather than a long one.
                leaveHotspotAloneUntil = nil
                backoffSeconds = Self.firstBackoffSeconds

                status.hasInternet = true
                status.expiresAt = info.remaining.map { Date().addingTimeInterval(Double($0)) }
                await report(.signedIn, router: settings.resolvingGateway().gatewayIP)

                // The router's own number, not a clock kept here.
                let left = info.remaining ?? Self.blindSleepSeconds

                if left > Self.pounceSeconds {
                    let nap = min(left - Self.pounceSeconds, Self.maximumSleep)
                    await log.info(
                        "Signed in, \(ErrorText.duration(left)) left. Next check in \(nap)s."
                    )
                    try? await Task.sleep(for: .seconds(max(nap, 1)))
                } else {
                    // On the boundary. Keep looking, closely, until the router
                    // says the session has gone.
                    try? await Task.sleep(for: .seconds(Self.retryEverySeconds))
                }

            case .signedOut:
                unreachableInARow = 0
                await report(.signingIn)
                await signInAndWait()

            case .unreachable(let why):
                unreachableInARow += 1

                guard unreachableInARow >= Self.unreachableBeforeRejoining else {
                    await log.info(
                        "Router did not answer (\(why)). Looking again, \(unreachableInARow) "
                            + "of \(Self.unreachableBeforeRejoining)."
                    )
                    try? await Task.sleep(for: .seconds(checkDelay))
                    continue
                }

                // Persistently silent. The hotspot router always answers when we
                // are on the hotspot, so this means we are somewhere else: a
                // phone hotspot, a cable, the wrong wireless network. Whether
                // that other network has working internet is beside the point,
                // because being online is not the same as being where we are
                // meant to be.
                //
                // Unless we are serving out a wait after a failed attempt. Then
                // sitting still is the entire point: the last try cost fifteen
                // seconds offline and repeating it immediately would cost that
                // again, on a loop.
                if let until = leaveHotspotAloneUntil, ContinuousClock.now < until {
                    let left = seconds(until: until)
                    await log.info(
                        """
                        Not on \(settings.wifiName), but the last attempt failed, so leaving it \
                        alone for another \(ErrorText.duration(left)).
                        """
                    )
                    unreachableInARow = 0
                    try? await Task.sleep(for: .seconds(min(left, Self.maximumSleep)))
                    continue
                }

                await log.warning(
                    """
                    The router has ignored \(unreachableInARow) checks in a row, so this Mac is \
                    probably not on \(settings.wifiName).
                    """
                )
                await report(.wrongNetwork(why))
                await wifi.joinConfiguredNetwork()
                offlineSince = nil
                unreachableInARow = 0
            }
        }

        await report(.stopped)
        await log.info("Stopped watching.")
    }

    // MARK: - The safety net

    /// Say at startup whether there is a floor under this or not.
    private func announceSafetyNet() async {
        guard !settings.fallbackWifiName.isEmpty else {
            await log.warning(
                """
                No fallback network is set, so if this Mac cannot get onto \
                \(settings.wifiName) it will simply have no internet. Set fallback_wifi_name to \
                a saved network, a phone's hotspot for instance, to put a floor under that.
                """
            )
            return
        }

        // A network this Mac has not saved cannot be rejoined from a command
        // line, so a net naming one is decorative and worse than none: it would
        // be trusted while not being there.
        guard WiFi.savedNetworks().contains(settings.fallbackWifiName) else {
            await log.failure(
                """
                \(settings.fallbackWifiName) is not one of this Mac's saved networks, so it \
                cannot be rejoined automatically and the safety net will not work. Join it by \
                hand once with Remember ticked, then start this again.
                """
            )
            return
        }

        await log.success(
            """
            Safety net on: if the internet stays down for \(Self.internetGraceSeconds)s, this \
            joins \(settings.fallbackWifiName) instead.
            """
        )
    }

    /// Did the net have to catch us? True means the network changed underneath
    /// everything and the loop should start over.
    ///
    /// The clock measures unexplained downtime. Joining a network takes the link
    /// away for about half a minute all by itself, so `joinFallbackNetwork` and
    /// `joinConfiguredNetwork` both restart the count rather than being punished
    /// for the gap they just caused. A sign-in does not restart it: a sign-in
    /// that is working restores the connection in a couple of seconds, so if
    /// fifteen of them pass with no internet, signing in is not working.
    private func safetyNetFired() async -> Bool {
        guard !settings.fallbackWifiName.isEmpty else { return false }

        if await stillOnline() {
            offlineSince = nil
            status.hasInternet = true
            return false
        }
        status.hasInternet = false

        let since = offlineSince ?? ContinuousClock.now
        offlineSince = since

        let down = seconds(since: since)
        guard down >= Self.internetGraceSeconds else { return false }

        await log.warning(
            """
            No internet for \(down)s. Falling back to \(settings.fallbackWifiName) so this Mac \
            is not left offline.
            """
        )

        let joined = await wifi.joinFallbackNetwork()
        offlineSince = nil

        if joined {
            await log.success("On \(settings.fallbackWifiName). You have internet again.")
            status.hasInternet = true
            await report(.onFallback(settings.fallbackWifiName))
        } else {
            await log.failure("Could not get onto \(settings.fallbackWifiName) either.")
        }

        // Either way, stop hammering the hotspot that just failed us.
        leaveHotspotAloneUntil = ContinuousClock.now.advanced(by: .seconds(backoffSeconds))
        await log.info(
            "Leaving \(settings.wifiName) alone for \(ErrorText.duration(backoffSeconds))."
        )
        backoffSeconds = min(backoffSeconds * 2, Self.longestBackoffSeconds)

        return true
    }

    /// Is the connection actually working? Asked twice, because the probe gives
    /// false negatives often enough to cause pointless network changes.
    private func stillOnline() async -> Bool {
        if await NetworkProbe.hasInternet(timeout: .milliseconds(1500)) { return true }
        try? await Task.sleep(for: .milliseconds(500))
        return await NetworkProbe.hasInternet(timeout: .seconds(2))
    }

    // MARK: - Signing in

    /// Sign in, and keep trying briefly while the router is still refusing.
    ///
    /// `/login` only starts answering once the old session has actually gone, so
    /// the first attempt on a boundary can still be refused. Retrying for a
    /// short window costs nothing and closes the gap to a few seconds.
    ///
    /// This deliberately reports nothing back to the loop. The loop asks the
    /// router what the truth is on its next pass rather than believing a return
    /// value from here, so there is no way for the two to get out of step.
    private func signInAndWait() async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(Self.pounceRetrySeconds))
        let started = ContinuousClock.now

        while !Task.isCancelled {
            if await signIn().isConnected { return }

            guard ContinuousClock.now < deadline else {
                await log.failure(
                    "Could not sign in within \(Self.pounceRetrySeconds)s. Still trying."
                )
                return
            }

            // Hand control back once the safety net's patience is up. Retrying
            // in here for a minute and a half while the net sits unused outside
            // the loop would defeat the point of having one: the whole promise
            // is fifteen seconds offline, not ninety.
            if !settings.fallbackWifiName.isEmpty,
               seconds(since: started) >= Self.internetGraceSeconds
            {
                await log.warning(
                    "Still not signed in after \(seconds(since: started))s. Handing over."
                )
                return
            }

            try? await Task.sleep(for: .seconds(Self.retryEverySeconds))
        }
    }

    /// What the router says about the session, reduced to what the loop needs.
    private enum SessionState {
        case active(SessionInfo)
        case signedOut
        case unreachable(String)
    }

    private func currentSession() async -> SessionState {
        // Resolved every time. Switching networks changes which router the
        // portal is on, and one settings file has to work on all of them.
        let active = settings.resolvingGateway()
        guard !active.gatewayIP.isEmpty else {
            return .unreachable("this Mac has no network at all")
        }

        switch await SessionProbe.status(gatewayIP: active.gatewayIP, client: PortalClient()) {
        case .active(let info):
            return .active(info)

        case .expired:
            return .signedOut

        case .unknown:
            // The router answered but named no session. That is what it serves
            // at the moment a session lapses, so this is a sign-out, not a lost
            // router. Reading it as unreachable meant rejoining the wireless
            // network at every session boundary, which costs about eight
            // seconds and a dropped link for no reason at all.
            return .signedOut

        case .unreachable(let why):
            return .unreachable(why)
        }
    }

    private func signIn() async -> LoginResult {
        let active = await settings.resolvingGateway(waitingUpTo: 10)

        guard !active.gatewayIP.isEmpty else {
            await log.warning("This Mac has no network to sign in on.")
            return .failed("No network")
        }

        // Always say which router is being tried. Without it, "nothing is
        // listening there" names no address and cannot be acted on: the usual
        // cause is being on a different network altogether, such as a phone
        // hotspot, and the address is the only thing that reveals that.
        await log.info("Router is \(active.gatewayIP).")

        let started = ContinuousClock.now
        let result = await LoginEngine(settings: active, log: log).run()
        let took = String(format: "%.1f", preciseSeconds(since: started))
        await log.info("\(result.summary) (\(took)s)")

        return result
    }

    // MARK: - Timing

    /// How often to look at the connection while nothing is due.
    private var checkDelay: Int {
        min(max(settings.checkIntervalSeconds, 1), 60)
    }

    private func seconds(since instant: ContinuousClock.Instant) -> Int {
        Int((ContinuousClock.now - instant).components.seconds)
    }

    private func seconds(until instant: ContinuousClock.Instant) -> Int {
        max(Int((instant - ContinuousClock.now).components.seconds), 0)
    }

    private func preciseSeconds(since instant: ContinuousClock.Instant) -> Double {
        let parts = (ContinuousClock.now - instant).components
        return Double(parts.seconds) + Double(parts.attoseconds) / 1e18
    }
}
