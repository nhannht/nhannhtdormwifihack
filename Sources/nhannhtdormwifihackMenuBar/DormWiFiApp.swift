#if os(macOS)

    import SwiftUI
    import nhannhtdormwifihackKit

    /// SwiftUI ships its own `Settings` scene type, so the bare name is
    /// ambiguous inside a SwiftUI file. This names ours once.
    private typealias AppSettings = nhannhtdormwifihackKit.Settings

    /// What the menu reads from, and the only thing that owns the watcher.
    ///
    /// The watcher runs inside this app rather than as a background service, so
    /// there is nothing to message and nothing to keep in step. The scheduler
    /// pushes its state to a `StatusBoard` and this mirrors the latest value on
    /// the main actor for SwiftUI to read.
    @MainActor
    @Observable
    final class Watcher {

        private(set) var status = WatcherStatus()
        private(set) var settingsProblem: String?

        private let board = StatusBoard()
        /// `truncateFile: false` on purpose. The window is meant to be opened
        /// hours after something went wrong, and wiping the file at every launch
        /// threw away exactly the history worth reading. `ActivityLog` caps the
        /// file instead, so it cannot grow without bound.
        private let log = ActivityLog(
            fileURL: AppSettings.supportDirectory.appendingPathComponent("activity.log"),
            truncateFile: false
        )

        /// Everything logged this run, newest last.
        private(set) var entries: [LogEntry] = []

        /// Where the full history lives, including previous runs.
        var logFileURL: URL {
            AppSettings.supportDirectory.appendingPathComponent("activity.log")
        }

        private var task: Task<Void, Never>?
        private var feed: Task<Void, Never>?

        /// Held for as long as this app is watching, so a terminal cannot start
        /// a second watcher alongside it.
        private var lock: RunLock?

        var isRunning: Bool { task != nil }

        /// The network the watcher tries to keep this Mac on, as the settings
        /// file currently has it.
        ///
        /// Mirrored here rather than read from the file each time the menu is
        /// drawn, because the menu is drawn far more often than the file
        /// changes and reading it is the slower of the two.
        private(set) var hotspotName: String = ""

        /// The networks this Mac has saved, for the picker in the menu.
        let networks = NetworkList()

        private var lines: Task<Void, Never>?

        init() {
            feed = Task { [board] in
                for await update in await board.subscribe() {
                    status = update
                }
            }

            lines = Task { [log] in
                for await entry in await log.subscribe() {
                    entries.append(entry)
                    // The window only ever shows the recent past; the file keeps
                    // the rest.
                    if entries.count > 2000 { entries.removeFirst(entries.count - 2000) }
                }
            }

            start()
        }

        func start() {
            guard task == nil else { return }

            // Two watchers both post to /login the moment a session lapses, and
            // the router rejects the second for having too many sessions open.
            guard let taken = RunLock.acquire() else {
                settingsProblem = "Another watcher is already running in a terminal."
                return
            }
            lock = taken

            let settings: AppSettings
            do {
                settings = try AppSettings.load(from: AppSettings.defaultURL)
                hotspotName = settings.wifiName
                settingsProblem = nil
            } catch {
                settingsProblem = ErrorText.short(for: error)
                lock = nil
                return
            }

            task = Task { [board, log] in
                await SessionScheduler(settings: settings, log: log, board: board).run()
            }
        }

        /// Stop, and do not claim to be stopped until it really is.
        ///
        /// Cancelling returns at once while the loop may still be inside a sleep
        /// of up to a minute, or inside a wireless join that shells out and
        /// cannot notice cancellation at all. Reporting "Paused" during that
        /// would be a lie, and a pause the user cannot trust is worse than none.
        func pause() {
            guard let running = task else { return }
            task = nil
            Task { await finishStopping(running) }
        }

        /// The same stop, but the caller can wait for it to be real.
        private func pauseAndWait() async {
            guard let running = task else { return }
            task = nil
            await finishStopping(running)
        }

        private func finishStopping(_ running: Task<Void, Never>) async {
            var stopping = status
            stopping.connection = .stopping
            await board.publish(stopping)

            running.cancel()
            await running.value
            lock = nil

            var stopped = status
            stopped.connection = .stopped
            stopped.expiresAt = nil
            await board.publish(stopped)
        }

        // MARK: - Changing which network to use

        /// Point the watcher at a different network.
        ///
        /// Written to the settings file, so the change outlives this run and the
        /// command line tool sees the same value.
        func chooseHotspot(named ssid: String) {
            do {
                var settings = try AppSettings.load(from: AppSettings.defaultURL)
                guard settings.wifiName != ssid else { return }
                settings.wifiName = ssid
                try settings.save(to: AppSettings.defaultURL)
                hotspotName = ssid
                settingsProblem = nil
            } catch {
                settingsProblem = ErrorText.short(for: error)
                return
            }

            Task { await restart() }
        }

        /// Stop and start again, so a settings change takes effect now.
        ///
        /// The scheduler reads the settings file once, at the moment it starts,
        /// so writing a new network name means nothing to a loop already going
        /// round. Waiting for the stop to finish is not optional either: the run
        /// lock is only let go once the loop has really left, and starting
        /// before that happens fails to take it and leaves the app watching
        /// nothing.
        private func restart() async {
            guard isRunning else { return }
            await pauseAndWait()
            start()
        }
    }

    /// The wireless networks this Mac has saved, kept for a few seconds.
    ///
    /// Deliberately not `@Observable`. The menu asks for this list while it is
    /// being drawn, and observable state written during a view update is the
    /// thing SwiftUI warns about. Nothing here is published: the list is handed
    /// out, and quietly fetched again once the copy in hand has gone stale.
    ///
    /// Asking macOS costs two short subprocess calls. Holding the answer for a
    /// few seconds is long enough that redrawing the menu does not pay for it
    /// over and over, and short enough that a network joined by hand appears the
    /// next time the menu is opened rather than after a relaunch.
    @MainActor
    final class NetworkList {

        private static let keepForSeconds = 5.0

        private var cached: [String] = []
        private var readAt: Date?

        var saved: [String] {
            if let readAt, Date().timeIntervalSince(readAt) < Self.keepForSeconds {
                return cached
            }
            cached = WiFi.savedNetworks()
            readAt = Date()
            return cached
        }
    }


    /// The running log, for reading after something went wrong.
    struct LogWindow: View {

        let watcher: Watcher

        /// Follows the newest line, until you turn it off to read something.
        ///
        /// `ScrollViewReader` rather than `ScrollPosition`, which needs macOS
        /// 15. The rest of this package builds for macOS 14, and one scrolling
        /// convenience is not a reason to raise the floor for everything.
        @State private var sticksToBottom = true

        var body: some View {
            VStack(spacing: 0) {
                if watcher.entries.isEmpty {
                    ContentUnavailableView(
                        "Nothing logged yet",
                        systemImage: "text.alignleft",
                        description: Text(
                            "Lines appear here as the watcher works. Earlier runs are in the log file."
                        )
                    )
                } else {
                    ScrollViewReader { proxy in
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 2) {
                                ForEach(watcher.entries) { entry in
                                    LogLine(entry: entry)
                                }
                            }
                            .padding(12)
                        }
                        .onChange(of: watcher.entries.last?.id) { _, newest in
                            guard sticksToBottom, let newest else { return }
                            withAnimation(.smooth) { proxy.scrollTo(newest, anchor: .bottom) }
                        }
                        .onAppear {
                            guard let newest = watcher.entries.last?.id else { return }
                            proxy.scrollTo(newest, anchor: .bottom)
                        }
                    }
                }

                Divider()

                HStack {
                    Toggle("Follow new lines", isOn: $sticksToBottom)
                        .toggleStyle(.checkbox)

                    Spacer()

                    Text("\(watcher.entries.count) lines this run")
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    Button("Show Log File") {
                        NSWorkspace.shared.activateFileViewerSelecting([watcher.logFileURL])
                    }
                }
                .padding(10)
            }
            .frame(minWidth: 560, minHeight: 320)
        }
    }

    /// One line, coloured by how much it matters.
    struct LogLine: View {

        let entry: LogEntry

        var body: some View {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(marker)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(tint)
                    .frame(width: 22, alignment: .leading)

                Text(entry.formatted)
                    .font(.system(.body, design: .monospaced))
                    .foregroundStyle(entry.level == .info ? .secondary : .primary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }

        private var marker: String {
            switch entry.level {
            case .info: ""
            case .success: "OK"
            case .warning: "!"
            case .failure: "X"
            }
        }

        private var tint: Color {
            switch entry.level {
            case .info: .secondary
            case .success: .green
            case .warning: .orange
            case .failure: .red
            }
        }
    }

    struct MenuContent: View {

        let watcher: Watcher

        @Environment(\.openWindow) private var openWindow

        /// Ticks while the menu is open so the countdown moves as you read it.
        @State private var now = Date()

        private var status: WatcherStatus { watcher.status }

        var body: some View {
            if let problem = watcher.settingsProblem {
                Text("Cannot read the settings file")
                Text(problem)
                Divider()
                Button("Try again") { watcher.start() }
            } else {
                // `now` is read here so the headline is recomputed on every
                // tick. The countdown comes from a deadline, so this only
                // decides how often it is redrawn, never whether it is right.
                Text(status.headline)
                    .id(now)

                Divider()

                if !status.network.isEmpty {
                    Text("Network:  \(status.network)")
                }
                if !status.routerIP.isEmpty {
                    Text("Router:  \(status.routerIP)")
                }
                Text("Internet:  \(status.hasInternet ? "up" : "down")")

                Divider()

                switch status.connection {
                case .stopping:
                    Text("Stopping, waiting for the last step to finish")
                case .stopped:
                    Button("Start auto-connect") { watcher.start() }
                default:
                    Button("Pause auto-connect") { watcher.pause() }
                }

                hotspotPicker

                Button("Show Log") { showLog() }

                Divider()
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
        }

        /// Open the log window and put it in front of whatever the user was
        /// doing.
        ///
        /// Both halves are needed. `openWindow` creates the window, or, when it
        /// is already open, brings it to the front of this app's own windows.
        /// But this app has no dock icon, so it is never the active app, and
        /// being at the front of a background app means being behind everything
        /// on screen. Activating is what makes "front" mean what it looks like.
        ///
        /// This used to live in the window's own `onAppear`, which is why Show
        /// Log worked once and then stopped: `onAppear` runs when the view is
        /// created, and a window that is merely buried under Safari has not been
        /// created again, so nothing ever activated the app. Measured on macOS
        /// 27 with a copy of this scene: with the window open and the app
        /// inactive, `openWindow` alone left `NSApp.isActive` false and the
        /// window not key; activating first made both true.
        private func showLog() {
            NSApplication.shared.activate(ignoringOtherApps: true)
            openWindow(id: "log")
        }

        /// Choose which network the watcher signs in to.
        ///
        /// A list of the networks this Mac has saved, rather than somewhere to
        /// type a name. Joining goes through `networksetup`, which can only
        /// rejoin a network macOS already has, so a typed name that is not on
        /// this list could be stored but never used. Picking from the list
        /// cannot produce that, and it also cannot mistype a name: the one this
        /// Mac has saved for the phone carries a curly apostrophe, which almost
        /// nobody types by hand.
        ///
        /// So when the hotspot is renamed, join the new one by hand once and
        /// tick Remember. It appears here straight afterwards.
        @ViewBuilder
        private var hotspotPicker: some View {
            Menu("Hotspot Network") {
                let saved = watcher.networks.saved

                if saved.isEmpty {
                    Text("This Mac has no saved networks")
                }

                ForEach(saved, id: \.self) { ssid in
                    Toggle(
                        ssid,
                        isOn: Binding(
                            get: { ssid == watcher.hotspotName },
                            set: { chosen in
                                guard chosen else { return }
                                watcher.chooseHotspot(named: ssid)
                            }
                        )
                    )
                }

                // The name in the settings file is the one that counts, so it
                // has to be visible even when it is not on the list. It drops
                // off when the hotspot is renamed or the network is forgotten,
                // which is the very moment someone opens this menu wondering
                // why nothing is connecting.
                if !watcher.hotspotName.isEmpty, !saved.contains(watcher.hotspotName) {
                    Divider()
                    Text("Set to \(watcher.hotspotName), which this Mac has not saved")
                }
            }
        }
    }

    @main
    struct DormWiFiApp: App {

        @State private var watcher = Watcher()

        var body: some Scene {
            MenuBarExtra {
                MenuContent(watcher: watcher)
            } label: {
                // A label the system draws, created when the scene appears
                // rather than by this app. The hand-rolled NSStatusItem this
                // replaces was built as a stored property, which runs before
                // NSApplication finishes launching, and a status item made that
                // early never appears in the bar at all.
                Image(systemName: symbolName)
            }
            .menuBarExtraStyle(.menu)

            Window("Dorm WiFi Log", id: "log") {
                LogWindow(watcher: watcher)
            }
            .defaultSize(width: 760, height: 460)
        }

        /// Deliberately not the `wifi` glyph.
        ///
        /// That was the first choice and it was a mistake: macOS draws the
        /// system Wi-Fi menu with the same symbol, so this app's icon sat in the
        /// bar looking exactly like one already there and could not be picked
        /// out. A status icon has to be findable at a glance among thirty
        /// others, which rules out reusing a system one however apt it is.
        private var symbolName: String {
            switch watcher.status.connection {
            case .signedIn: "bolt.horizontal.circle.fill"
            case .starting, .signingIn: "arrow.triangle.2.circlepath"
            case .wrongNetwork: "exclamationmark.triangle.fill"
            case .onFallback: "iphone.radiowaves.left.and.right"
            case .stopped, .stopping: "pause.circle.fill"
            }
        }
    }

#endif
