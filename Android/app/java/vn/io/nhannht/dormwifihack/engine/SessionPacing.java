package vn.io.nhannht.dormwifihack.engine;

/**
 * When to look at the router, and for how long to look away.
 *
 * <p>These numbers are facts about the router, not about the machine watching it,
 * so every build reads them from here. They were measured on the hotspot: a
 * session cannot be renewed early, {@code /login} starts answering the instant
 * the old one lapses, and {@code /status} answers in about 15ms whether signed in
 * or not.
 */
public final class SessionPacing {

    /** How often to retry while the router is refusing, in seconds. */
    public static final int RETRY_EVERY_SECONDS = 2;

    /** Stop sleeping and start watching closely once the session has this many seconds left. */
    public static final int POUNCE_SECONDS = 5;

    /** How long to keep trying after a lapse before giving up on this pass. */
    public static final int POUNCE_RETRY_SECONDS = 90;

    /** Never sleep longer than this, so a session that vanishes early is noticed. */
    public static final int MAXIMUM_SLEEP = 60;

    /** What to assume is left when the router reports an open session without saying how long. */
    public static final int BLIND_SLEEP_SECONDS = 30;

    private SessionPacing() {
    }

    /**
     * How long to sleep while a session is open with {@code secondsLeft} to run.
     *
     * <p>Long naps while there is time, then short ones on the boundary. Never
     * past the moment the session ends, because the sign-in has to go in within a
     * couple of seconds of the lapse to keep the gap short.
     */
    public static int napWhileActive(int secondsLeft) {
        if (secondsLeft <= POUNCE_SECONDS) {
            return RETRY_EVERY_SECONDS;
        }
        return Math.max(Math.min(secondsLeft - POUNCE_SECONDS, MAXIMUM_SLEEP), 1);
    }
}
