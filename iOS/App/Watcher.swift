import Foundation
import Observation
import UIKit
import UserNotifications
import nhannhtdormwifihackKit

/// Everything the screen shows, and the only thing that starts or stops work.
///
/// The view never asks the router anything itself. Two things probing the same
/// router eventually disagree, and then the app is telling the user two stories
/// about one connection. So the loop asks, writes what it found into the shared
/// container, and this reads those files.
///
/// Files rather than anything in memory, because the loop is not always in this
/// process. When the tunnel is holding the session it runs in its own process,
/// and the shared container is the only thing the two of them can both see.
///
/// There are two loops that could run, and never at the same time. The tunnel
/// runs one in a separate process that outlives the app; the app runs one only
/// while it is on the screen. When the tunnel is on it owns the job and this
/// class does not start its own, for the same reason: one owner per router.
@MainActor
@Observable
final class Watcher {

    // MARK: - What the screen reads

    private(set) var status = WatcherStatus()
    private(set) var entries: [LogEntry] = []
    private(set) var isWatching = false

    /// True while a one-off sign-in is in flight, so the button can say so.
    private(set) var isSigningIn = false

    /// The background watcher. Off by default; the user switches it on.
    let tunnel = TunnelController()

    var settings: Settings {
        didSet {
            try? settings.save()
            // Hand them to the loop too. Without this the router address a user
            // types in to rescue a failing app sits in the file, saved and
            // ignored, while the loop keeps using the value it started with.
            Task { [renewer, settings] in await renewer?.use(settings) }
        }
    }

    // MARK: - The parts underneath

    private let log: ActivityLog
    private var renewer: SessionRenewer?
    private var loop: Task<Void, Never>?
    private var watchers: [Task<Void, Never>] = []

    /// The log both processes append to.
    private let logFileURL = Settings.supportDirectory.appendingPathComponent("activity.log")

    /// When the log file was last written, so an unchanged file is not reparsed.
    private var logFileSeenAt: Date?

    /// The status the running loop writes, wherever that loop is.
    private let statusFile = StatusFile.shared
    private var statusSeenAt: Date?

    /// Where "the watcher was on" is remembered between launches.
    ///
    /// Switching it on is the user saying "hold my session". Forgetting that
    /// every time the app closes means the one setting that matters is off
    /// exactly when they come back needing it.
    private static let watchingKey = "watcher.on"

    init() {
        settings = (try? Settings.load()) ?? Settings()
        isWatching = UserDefaults.standard.bool(forKey: Self.watchingKey)
        log = ActivityLog(fileURL: logFileURL, truncateFile: false)

        refresh()
    }

    // MARK: - The log

    /// Re-read the shared file, which is where the tunnel's lines arrive.
    ///
    /// The tunnel is a separate process, so nothing it logs comes through the
    /// in-memory feed this app subscribes to. Both processes append to one file
    /// and the screen shows that file, which also means the app can never show
    /// a different story from the one on disk.
    func reloadLog() {
        let written = (try? FileManager.default.attributesOfItem(atPath: logFileURL.path))?[
            .modificationDate] as? Date

        guard written != logFileSeenAt else { return }
        logFileSeenAt = written
        entries = ActivityLog.entriesInFile(at: logFileURL)
    }

    /// Read both shared files. Everything the screen shows comes from here.
    func refresh() {
        reloadLog()

        guard let written = statusFile.changedAt else { return }
        guard written != statusSeenAt else { return }
        statusSeenAt = written
        if let found = statusFile.read() { status = found }
    }

    // MARK: - Starting and stopping

    func startWatching() {
        isWatching = true
        UserDefaults.standard.set(true, forKey: Self.watchingKey)
        Task { await askToBeAllowedToRemind() }
        startForegroundLoop()
    }

    func stopWatching() {
        loop?.cancel()
        loop = nil
        renewer = nil
        isWatching = false
        UserDefaults.standard.set(false, forKey: Self.watchingKey)
        UIApplication.shared.isIdleTimerDisabled = false
        Task { await forgetReminder() }
    }

    /// A loop for this process, wired to tell the widget when things change.
    private func makeRenewer() -> SessionRenewer {
        SessionRenewer(
            settings: settings,
            log: log,
            home: .appOnScreen,
            announce: { Displays.refresh() }
        )
    }

    /// Run the loop inside this app, unless the tunnel is already running one.
    private func startForegroundLoop() {
        guard loop == nil, !tunnel.isRunning else { return }

        let renewer = makeRenewer()
        self.renewer = renewer
        loop = Task { await renewer.run() }

        // Hold the screen awake while watching.
        //
        // Not a convenience. This loop only runs while the app is on the
        // screen, so letting the phone auto-lock stops the very thing the user
        // just switched on, silently, a minute later. The tunnel needs none of
        // this, which is exactly what makes it better.
        UIApplication.shared.isIdleTimerDisabled = true
    }

    /// Switch the background watcher on or off.
    func setTunnel(on: Bool) async {
        if on {
            // Stop the in-app loop first. Two loops signing in to one router
            // race each other into the router's "one session at a time" rule.
            loop?.cancel()
            loop = nil
            UIApplication.shared.isIdleTimerDisabled = false
            await forgetReminder()
            await tunnel.start()
        } else {
            tunnel.stop()
            if isWatching { startForegroundLoop() }
        }
    }

    /// Sign in once, without starting either loop.
    func signInNow() async {
        guard !isSigningIn else { return }
        isSigningIn = true
        defer { isSigningIn = false }

        let renewer = self.renewer ?? makeRenewer()
        await renewer.signInOnce()
        refresh()
    }

    // MARK: - Leaving the screen

    /// What happens when the app is put away depends on who is holding the
    /// session. With the tunnel on, nothing: it keeps working and there is
    /// nothing to warn about. Without it, iOS suspends this app within seconds,
    /// so the honest move is to leave a notification at the moment the router
    /// said the session ends.
    func goingToBackground() async {
        loop?.cancel()
        loop = nil

        // Give the screen back. Nothing in this app is watching once it is off
        // the screen, so holding the display awake would only drain the battery.
        UIApplication.shared.isIdleTimerDisabled = false

        if tunnel.isRunning {
            await forgetReminder()
            return
        }

        guard isWatching, let renewer, let ends = await renewer.sessionEndsAt else {
            await forgetReminder()
            return
        }

        // Nothing useful to schedule for a session that is already over.
        let seconds = ends.timeIntervalSinceNow
        guard seconds > 5 else { return }

        let content = UNMutableNotificationContent()
        content.title = "Hotspot session ending"
        content.body = "Open Dorm WiFi to sign in again."
        content.sound = .default

        let request = UNNotificationRequest(
            identifier: Self.reminderID,
            content: content,
            trigger: UNTimeIntervalNotificationTrigger(timeInterval: seconds, repeats: false)
        )

        await forgetReminder()
        try? await UNUserNotificationCenter.current().add(request)
        await log.info(
            "Leaving the screen, so the watcher stops. Reminder set for \(Self.clock(ends))."
        )
    }

    func cameBackToScreen() async {
        await forgetReminder()
        await tunnel.load()
        refresh()

        // The tunnel is holding the session. Nothing for this app to start, and
        // starting anything would be a second loop on the same router.
        guard !tunnel.isRunning else { return }

        guard isWatching else {
            // Not watching, but opening the app is itself a request to be
            // online. The screen would otherwise say "Starting" and "not found
            // yet" until the user pressed something, which is the wrong thing to
            // show to someone who opened this app because their internet
            // stopped.
            //
            // Guarded by the same flag as the button. iOS reports the scene
            // becoming active several times in a burst, once for the launch and
            // again around each system alert, and without this every one of them
            // started its own sign-in: six went out in four seconds on the phone.
            guard !isSigningIn else { return }
            isSigningIn = true
            defer { isSigningIn = false }

            let renewer = self.renewer ?? makeRenewer()
            self.renewer = renewer
            await renewer.catchUp()
            refresh()
            return
        }

        startForegroundLoop()
    }

    // MARK: - Reminders

    private static let reminderID = "session-ending"

    private func askToBeAllowedToRemind() async {
        _ = try? await UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound])
    }

    private func forgetReminder() async {
        UNUserNotificationCenter.current()
            .removePendingNotificationRequests(withIdentifiers: [Self.reminderID])
    }

    private static func clock(_ date: Date) -> String {
        date.formatted(date: .omitted, time: .shortened)
    }
}
