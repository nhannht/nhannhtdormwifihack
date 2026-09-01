import SwiftUI
import WidgetKit
import nhannhtdormwifihackKit

/// The session, on the Lock Screen and the Home Screen.
///
/// ## Why this never says "right now"
///
/// A widget is not allowed to keep looking. WidgetKit gives each one a small
/// number of refreshes a day and drops the rest, so anything written here in the
/// present tense becomes a lie the moment a refresh is skipped, and there is no
/// way to know from in here whether that has happened.
///
/// So nothing here is present tense. It shows two things that stay true however
/// long ago they were written: the moment the router said this session ends,
/// which is a deadline and does not decay, and the time of the reading itself.
/// "Signed in, checked 4:07pm" is still honest at midnight. "Signed in" alone is
/// not.
///
/// The countdown is drawn with `Text(timerInterval:)`, which iOS ticks on its
/// own with no refresh at all. When it reaches zero the widget stops claiming
/// anything, which is the truth: either the watcher renewed the session and a
/// refresh will bring the new deadline, or it did not and the internet has
/// stopped. Both of those want the same thing from the user, which is a look.
struct SessionWidget: Widget {

    static let kind = "vn.io.nhannht.dormwifihack.session"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: Self.kind, provider: SessionProvider()) { entry in
            SessionWidgetView(entry: entry)
        }
        .configurationDisplayName("Dorm WiFi")
        .description("How long this hotspot session has left, and when it was last checked.")
        .supportedFamilies([
            .systemSmall,
            .accessoryRectangular,
            .accessoryCircular,
            .accessoryInline,
        ])
    }
}

// MARK: - What it shows

struct SessionEntry: TimelineEntry {
    let date: Date
    let status: WatcherStatus?
    let sessionLength: Int

    /// The deadline, only while it is still ahead of us.
    var endsAt: Date? {
        guard let ends = status?.expiresAt, ends > date else { return nil }
        return ends
    }

    var checked: String? {
        guard let updated = status?.updated else { return nil }
        return updated.formatted(date: .omitted, time: .shortened)
    }

    /// How much of the session is left, for the round Lock Screen gauge.
    var fractionLeft: Double {
        guard let ends = endsAt, sessionLength > 0 else { return 0 }
        return min(max(ends.timeIntervalSince(date) / Double(sessionLength), 0), 1)
    }
}

struct SessionProvider: TimelineProvider {

    func placeholder(in context: Context) -> SessionEntry { Self.sample }

    func getSnapshot(in context: Context, completion: @escaping (SessionEntry) -> Void) {
        completion(context.isPreview ? Self.sample : read())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<SessionEntry>) -> Void) {
        let entry = read()

        // One refresh, asked for shortly after the reading stops meaning
        // anything. Asking more often would spend a daily allowance measured in
        // tens on a number iOS is already ticking without being asked.
        //
        // Ninety seconds past the end rather than at the end. Right at the end
        // the watcher is in the middle of signing in again, which the log shows
        // taking half a minute of the portal timing out, and a read taken then
        // finds no session and no deadline. Landing after that is the
        // difference between the widget picking up the new session by itself
        // and it sitting on "No session" until something tells it otherwise.
        let next =
            entry.endsAt.map { $0.addingTimeInterval(90) }
            ?? entry.date.addingTimeInterval(Double(max(entry.sessionLength, 60)))

        completion(Timeline(entries: [entry], policy: .after(next)))
    }

    private func read() -> SessionEntry {
        SessionEntry(
            date: Date(),
            status: StatusFile.shared.read(),
            sessionLength: ((try? Settings.load()) ?? Settings()).sessionTimeoutSeconds
        )
    }

    private static var sample: SessionEntry {
        var status = WatcherStatus()
        status.connection = .signedIn
        status.expiresAt = Date().addingTimeInterval(9 * 60 + 14)
        return SessionEntry(date: Date(), status: status, sessionLength: 15 * 60)
    }
}

// MARK: - Drawing it

struct SessionWidgetView: View {

    @Environment(\.widgetFamily) private var family
    let entry: SessionEntry

    var body: some View {
        switch family {
        case .accessoryInline:
            inline
        case .accessoryCircular:
            circular
        case .accessoryRectangular:
            rectangular
        default:
            // Only the Home Screen one gets a background. The Lock Screen
            // families draw on whatever the wallpaper is and paint their own
            // backdrop; giving the inline one a filled container puts a slab
            // next to the clock.
            small.containerBackground(.fill.tertiary, for: .widget)
        }
    }

    // MARK: One line, next to the Lock Screen clock

    private var inline: some View {
        if let ends = entry.endsAt {
            Text("Dorm WiFi \(Text(timerInterval: entry.date...ends, countsDown: true))")
        } else {
            Text(waiting)
        }
    }

    // MARK: The round one

    private var circular: some View {
        Gauge(value: entry.fractionLeft) {
            Image(systemName: "wifi")
        } currentValueLabel: {
            if let ends = entry.endsAt {
                Text(timerInterval: entry.date...ends, countsDown: true)
                    .font(.system(size: 11, design: .rounded))
            } else {
                Image(systemName: "wifi.slash")
            }
        }
        .gaugeStyle(.accessoryCircular)
    }

    // MARK: The wide one

    private var rectangular: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text("Dorm WiFi").font(.headline)

            if let ends = entry.endsAt {
                Text(timerInterval: entry.date...ends, countsDown: true)
                    .font(.title3.monospacedDigit())
            } else {
                Text(waiting).font(.subheadline)
            }

            Text(footnote).font(.caption2)
        }
    }

    // MARK: The Home Screen one

    private var small: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label("Dorm WiFi", systemImage: entry.endsAt == nil ? "wifi.slash" : "wifi")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)

            Spacer(minLength: 0)

            if let ends = entry.endsAt {
                Text(timerInterval: entry.date...ends, countsDown: true)
                    .font(.system(.title, design: .rounded).monospacedDigit())
                    .minimumScaleFactor(0.6)
                Text("left on this session").font(.caption2).foregroundStyle(.secondary)
            } else {
                Text(waiting)
                    .font(.subheadline.weight(.medium))
                    .minimumScaleFactor(0.7)
            }

            Spacer(minLength: 0)
            Text(footnote).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: Words

    /// What to say when there is no deadline ahead of us. Never a claim about
    /// what is happening now, because from in here that cannot be known.
    private var waiting: String {
        guard entry.status != nil else { return "Not set up yet" }
        return "No session"
    }

    private var footnote: String {
        guard let checked = entry.checked else { return "Open the app to start" }
        return "checked \(checked)"
    }
}
