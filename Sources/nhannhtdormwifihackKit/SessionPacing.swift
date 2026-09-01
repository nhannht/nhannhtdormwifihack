import Foundation

/// When to look at the router, and for how long to look away.
///
/// These numbers are facts about the router, not about the Mac or the phone, so
/// both watchers read them from here. They were measured on the hotspot: a
/// session cannot be renewed early, `/login` starts answering the instant the
/// old one lapses, and `/status` answers in about 15ms whether signed in or not.
public enum SessionPacing {

    /// How often to retry while the router is refusing, in seconds. Deliberately
    /// impatient: `/login` starts answering the instant the old session goes,
    /// and every attempt before that is free.
    public static let retryEverySeconds = 2

    /// Stop sleeping and start watching closely once the session has this many
    /// seconds left, so the sign-in goes in as soon as the old one lapses.
    public static let pounceSeconds = 5

    /// How long to keep trying after a lapse before giving up on this pass.
    public static let pounceRetrySeconds = 90

    /// Never sleep longer than this, so a session that vanishes early is
    /// noticed and stopping stays responsive.
    public static let maximumSleep = 60

    /// What to assume is left when the router reports an open session without
    /// saying how long. Short on purpose: guessing high means missing the lapse.
    public static let blindSleepSeconds = 30

    /// How long to sleep while a session is open with `left` seconds to run.
    ///
    /// Long naps while there is time, then short ones on the boundary. Never
    /// past the moment the session ends, because the sign-in has to go in
    /// within a couple of seconds of the lapse to keep the gap short.
    public static func napWhileActive(secondsLeft left: Int) -> Int {
        guard left > pounceSeconds else { return retryEverySeconds }
        return max(min(left - pounceSeconds, maximumSleep), 1)
    }
}
