import SwiftUI
import nhannhtdormwifihackKit

struct ContentView: View {

    @Bindable var watcher: Watcher
    @State private var showingSettings = false

    var body: some View {
        NavigationStack {
            List {
                Section { headline }
                Section("Connection") { facts }
                Section { controls }
                Section("In the background") { background }
                Section("What it did") { logLines }
            }
            .navigationTitle("Dorm WiFi")
            .task {
                // The tunnel is another process appending to the same file, so
                // its lines only arrive by re-reading that file. Cheap: the
                // read is skipped entirely unless the file has been written to.
                while !Task.isCancelled {
                    watcher.refresh()
                    try? await Task.sleep(for: .seconds(2))
                }
            }
            .toolbar {
                Button("Settings", systemImage: "gearshape") { showingSettings = true }
            }
            .sheet(isPresented: $showingSettings) {
                SettingsView(settings: $watcher.settings, tunnel: watcher.tunnel)
            }
        }
    }

    // MARK: - The one line that matters

    private var headline: some View {
        // A timeline, so the countdown ticks. The status carries the moment the
        // session ends rather than a number of seconds, so redrawing a minute
        // later shows the truth instead of a stale number.
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            VStack(alignment: .leading, spacing: 6) {
                Text(watcher.status.isStale ? "Nothing is watching" : watcher.status.headline)
                    .font(.title2.weight(.semibold))
                    .contentTransition(.numericText())

                Text(explanation)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 4)
        }
    }

    private var explanation: String {
        // A status nobody has refreshed keeps counting down to zero and then
        // insists it is signed in with 0s left, which is the one thing it
        // certainly is not. Say that no one has looked instead.
        if watcher.status.isStale {
            return """
                Nothing has checked the connection since \
                \(watcher.status.updated.formatted(date: .omitted, time: .shortened)). \
                Turn on the tunnel below, or press Sign in now.
                """
        }

        switch watcher.status.connection {
        case .signedIn:
            return watcher.isWatching
                ? "Keeping this open holds the session. Leaving the app stops the watcher."
                : "Signed in. Turn the watcher on to sign in again when it runs out."
        case .signingIn:
            return "Asking the router for a page, the portal for an account, then signing in."
        case .wrongNetwork(let why):
            return why
        case .starting:
            return "Nothing has happened yet."
        case .stopped, .stopping:
            return "The watcher is off."
        case .onFallback(let name):
            return "On \(name)."
        }
    }

    // MARK: - Facts

    @ViewBuilder
    private var facts: some View {
        LabeledContent("Router", value: address)
        LabeledContent("Internet over WiFi", value: watcher.status.hasInternet ? "Yes" : "No")
        // How long is left is deliberately not repeated here. It is in the
        // headline, which ticks; a second copy of it out here does not, so the
        // screen showed 4m 30s and 4m 34s at the same time for the same session.
    }

    private var address: String {
        watcher.status.routerIP.isEmpty ? "not found yet" : watcher.status.routerIP
    }

    // MARK: - Buttons

    @ViewBuilder
    private var controls: some View {
        Button {
            Task { await watcher.signInNow() }
        } label: {
            if watcher.isSigningIn {
                HStack { ProgressView(); Text("Signing in") }
            } else {
                Text("Sign in now")
            }
        }
        .disabled(watcher.isSigningIn)

        Toggle(
            "Keep the session alive",
            isOn: Binding(
                get: { watcher.isWatching },
                set: { $0 ? watcher.startWatching() : watcher.stopWatching() }
            )
        )

        Text(
            """
            Holds the session while this app is on the screen. iOS stops it the moment you leave, \
            so it puts a notification at the time the session ends instead. Turn on the tunnel \
            below to have it held in your pocket.
            """
        )
        .font(.footnote)
        .foregroundStyle(.secondary)
    }

    // MARK: - The tunnel

    @ViewBuilder
    private var background: some View {
        Toggle(
            "Hold the session with the app closed",
            isOn: Binding(
                get: { watcher.tunnel.isRunning },
                set: { on in Task { await watcher.setTunnel(on: on) } }
            )
        )

        LabeledContent("Tunnel", value: watcher.tunnel.plainStatus)

        if let problem = watcher.tunnel.problem {
            Text(problem)
                .font(.footnote)
                .foregroundStyle(.red)
        }

        Text(
            """
            This runs the same watcher in a separate process that iOS does not suspend, so the \
            session is held with the app closed and the phone locked. It moves no traffic and \
            is not a VPN; iOS shows the VPN badge because that is the only way an app is \
            allowed to keep running. Remove it any time in Settings, under VPN.
            """
        )
        .font(.footnote)
        .foregroundStyle(.secondary)
    }

    // MARK: - Log

    @ViewBuilder
    private var logLines: some View {
        if watcher.entries.isEmpty {
            Text("Nothing yet.").foregroundStyle(.secondary)
        } else {
            ForEach(watcher.entries.suffix(60).reversed()) { entry in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Circle()
                        .fill(colour(of: entry.level))
                        .frame(width: 7, height: 7)
                    Text(entry.formatted)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                }
            }
        }
    }

    private func colour(of level: LogEntry.Level) -> Color {
        switch level {
        case .info: .secondary
        case .success: .green
        case .warning: .orange
        case .failure: .red
        }
    }
}

// MARK: - Settings

struct SettingsView: View {

    @Binding var settings: Settings
    var tunnel: TunnelController?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Portal address", text: $settings.portalFallbackURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("Portal")
                } footer: {
                    Text(
                        """
                        Used only when the router's login page names no portal of its own. \
                        The two networks here use different ones, so if signing in stops \
                        working after moving building, suspect this first.
                        """
                    )
                }

                Section {
                    TextField("Router address", text: $settings.gatewayIP)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.numbersAndPunctuation)
                } header: {
                    Text("Router")
                } footer: {
                    Text(
                        """
                        Leave this empty. Empty means work it out from the address this network \
                        gave the phone, which is what makes one setting work on both of the \
                        hotspot's networks. Fill it in only if the app says nothing answered.
                        """
                    )
                }

                Section {
                    Stepper(
                        "Session length: \(settings.interval) min",
                        value: $settings.interval,
                        in: 1...120
                    )
                } footer: {
                    Text(
                        """
                        What the router allows, not a preference. It is only used for the line \
                        at the top; the countdown comes from the router itself.
                        """
                    )
                }

                if let tunnel, tunnel.isInstalled {
                    Section {
                        Button("Remove the tunnel", role: .destructive) {
                            Task { await tunnel.forget() }
                        }
                    } footer: {
                        Text(
                            """
                            Deletes the VPN entry this app added. The same thing as deleting it \
                            in Settings, under VPN. Nothing else about the app changes.
                            """
                        )
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                Button("Done") { dismiss() }
            }
        }
    }
}
