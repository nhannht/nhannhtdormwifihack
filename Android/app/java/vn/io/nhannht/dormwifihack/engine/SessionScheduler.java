package vn.io.nhannht.dormwifihack.engine;

import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Keeps the connection up, indefinitely.
 *
 * <p>The router hands out a fixed session, fifteen minutes on this hotspot, and
 * there is no way to extend one. Signing in again while a session is open is the
 * obvious thing to try and it does not work: the router accepts the connection to
 * {@code /login} and then never answers until the session has actually gone. So the
 * only strategy available is to know exactly when the session ends and sign in
 * again the moment it does.
 *
 * <p>The router publishes the countdown itself on {@code /status}, which answers in
 * about 15ms whether signed in or out. That number is better than any clock kept
 * here: it survives suspend, a closed lid and clock drift, and it comes from the
 * device that actually enforces the limit. So this asks {@code /status} on every
 * pass and does what the answer says. There is no local timer and no state carried
 * between passes to get out of step with the router.
 *
 * <p>The cost is a gap of a few seconds once per session, while the old session
 * lapses and the new sign-in goes through. That gap is inherent to a router that
 * refuses early renewal; it cannot be closed from here.
 */
public final class SessionScheduler implements Runnable {

    /**
     * What the scheduler needs from the machine it is running on.
     *
     * <p>Everything platform-specific lives behind this: the router's address, the
     * internet probe, and whether a network can be joined at all. On a phone the
     * last one is a flat no, and that is a fact about Android rather than a gap in
     * this code.
     */
    public interface Platform {

        /** The router this device is using right now, waiting up to {@code waitSeconds}. */
        String gateway(int waitSeconds);

        boolean hasInternet(double timeoutSeconds);

        /** Rejoin a saved network. False on a platform that will not allow it. */
        boolean joinNetwork(String ssid);

        /**
         * The networks this machine has saved, or null when the platform cannot say.
         *
         * <p>Null is not the same as empty: empty would read as "you have saved
         * nothing", which on Android is both true and useless.
         */
        List<String> savedNetworks();

        /** Hold the CPU awake, or let it sleep. */
        void keepAwake(boolean keepAwake);
    }

    /** How long the internet may be gone before the safety net fires, in seconds. */
    public static final int INTERNET_GRACE_SECONDS = 15;

    // How long to leave the hotspot alone after falling back, in seconds, and the
    // ceiling that wait grows to.
    //
    // Without this the two networks flap: on the phone hotspot the dorm router is
    // unreachable, so we join the dorm hotspot, which fails, so the safety net puts
    // us back on the phone, where the dorm router is unreachable again. Two joins a
    // cycle, forever, each dropping the link for half a minute. Doubling the wait
    // after every failure and only clearing it on a session that actually works is
    // what stops that.
    public static final int FIRST_BACKOFF_SECONDS = 120;
    public static final int LONGEST_BACKOFF_SECONDS = 1800;

    // How many times in a row the router has to ignore us before we conclude this
    // device is on the wrong network.
    //
    // Not one. A single timeout means nothing: the router gets busy, the radio
    // hiccups, and a phone roams between access points. Rejoining on one missed
    // reply would drop a working link for no reason.
    public static final int UNREACHABLE_BEFORE_REJOINING = 3;

    /** How long one sleep lasts at most, so a stop is noticed quickly. */
    private static final long NAP_SLICE_MILLIS = 500;

    /** What {@code /status} said, reduced to what the loop needs. */
    private static final class Outcome {

        final String kind;
        final SessionProbe.SessionInfo info;
        final String why;

        Outcome(String kind, SessionProbe.SessionInfo info, String why) {
            this.kind = kind;
            this.info = info;
            this.why = why == null ? "" : why;
        }
    }

    private final Settings settings;
    private final ActivityLog log;
    private final Platform platform;
    private final WatcherStatus.Board board;

    private volatile boolean cancelled;
    private Thread thread;
    private final WatcherStatus status = new WatcherStatus();

    /** When the internet went away, or -1 while it is working. */
    private long offlineSince = -1;
    /** Don't touch the hotspot until this moment. */
    private long leaveHotspotAloneUntil = -1;
    private int backoffSeconds = FIRST_BACKOFF_SECONDS;

    public SessionScheduler(Settings settings, ActivityLog log, Platform platform, WatcherStatus.Board board) {
        this.settings = settings;
        this.log = log;
        this.platform = platform;
        this.board = board;
        this.status.network = settings.wifiName;
    }

    // MARK: - Control

    /** Ask the loop to finish what it is doing and return. */
    public void stop() {
        cancelled = true;
        Thread running = thread;
        if (running != null) {
            running.interrupt();
        }
    }

    public boolean isStopped() {
        return cancelled;
    }

    /** Hands the loop its own thread, so stopping can interrupt a nap. */
    public void attach(Thread running) {
        this.thread = running;
    }

    private String hotspotName() {
        // An install that has not been told which network to use would otherwise log
        // "it will simply have no internet" and "probably not on .", which read as a
        // bug in the sentence rather than as a missing setting.
        return settings.wifiName == null || settings.wifiName.isEmpty()
                ? "the configured network"
                : settings.wifiName;
    }

    // MARK: - The loop

    @Override
    public void run() {
        platform.keepAwake(true);
        log.info("Watching. This router allows one "
                + ErrorText.duration(settings.sessionTimeoutSeconds())
                + " session at a time and will not renew one early, so expect a gap of a few "
                + "seconds once per session.");

        announceHotspot();
        announceSafetyNet();
        report(WatcherStatus.STARTING, "", null);

        int unreachableInARow = 0;

        try {
            while (!cancelled) {
                // The safety net goes first, before anything is asked of the hotspot.
                // Its whole job is to make sure a failed experiment costs fifteen
                // seconds of internet rather than an evening of it.
                if (safetyNetFired()) {
                    unreachableInARow = 0;
                    continue;
                }

                Outcome outcome = currentSession();

                if (SessionProbe.ACTIVE.equals(outcome.kind)) {
                    unreachableInARow = 0;

                    // A session that actually works clears the debt from every earlier
                    // failure, so the next problem starts from a short wait again
                    // rather than a long one.
                    leaveHotspotAloneUntil = -1;
                    backoffSeconds = FIRST_BACKOFF_SECONDS;

                    status.hasInternet = true;
                    status.expiresAt = outcome.info != null && outcome.info.remaining != null
                            ? Long.valueOf(System.currentTimeMillis() + outcome.info.remaining * 1000)
                            : null;
                    report(WatcherStatus.SIGNED_IN, "", routerNow());

                    // The router's own number, not a clock kept here.
                    int left = outcome.info != null && outcome.info.remaining != null
                            ? outcome.info.remaining.intValue()
                            : SessionPacing.BLIND_SLEEP_SECONDS;
                    int nap = SessionPacing.napWhileActive(left);
                    if (left > SessionPacing.POUNCE_SECONDS) {
                        log.info("Signed in, " + ErrorText.duration(left) + " left. Next check in "
                                + nap + "s.");
                    }
                    // Past the boundary the nap is short on its own, so the loop keeps
                    // looking closely until the router says the session ended.
                    sleepSeconds(nap);

                } else if ("signedOut".equals(outcome.kind)) {
                    unreachableInARow = 0;
                    report(WatcherStatus.SIGNING_IN, "", null);
                    signInAndWait();

                } else {
                    unreachableInARow++;

                    if (unreachableInARow < UNREACHABLE_BEFORE_REJOINING) {
                        log.info("Router did not answer (" + outcome.why + "). Looking again, "
                                + unreachableInARow + " of " + UNREACHABLE_BEFORE_REJOINING + ".");
                        sleepSeconds(checkDelay());
                        continue;
                    }

                    // Persistently silent. The hotspot router always answers when we are
                    // on the hotspot, so this means we are somewhere else: a phone
                    // hotspot, mobile data, the wrong wireless network. Whether that
                    // other network has working internet is beside the point, because
                    // being online is not the same as being where we are meant to be.
                    //
                    // Unless we are serving out a wait after a failed attempt. Then
                    // sitting still is the entire point: the last try cost fifteen
                    // seconds offline and repeating it immediately would cost that
                    // again, on a loop.
                    if (leaveHotspotAloneUntil >= 0 && System.currentTimeMillis() < leaveHotspotAloneUntil) {
                        long left = Math.max(leaveHotspotAloneUntil - System.currentTimeMillis(), 0) / 1000;
                        log.info("Not on " + hotspotName() + ", but the last attempt failed, so "
                                + "leaving it alone for another " + ErrorText.duration(left) + ".");
                        unreachableInARow = 0;
                        sleepSeconds((int) Math.min(left, SessionPacing.MAXIMUM_SLEEP));
                        continue;
                    }

                    log.warning("The router has ignored " + unreachableInARow + " checks in a row, so "
                            + "this device is probably not on " + hotspotName() + ".");
                    report(WatcherStatus.WRONG_NETWORK, outcome.why, null);

                    if (!platform.joinNetwork(settings.wifiName) && settings.wifiName != null
                            && !settings.wifiName.isEmpty()) {
                        log.warning("Android does not let an app join a network. Join "
                                + hotspotName() + " from the Wi-Fi list and this picks up again.");
                    }
                    offlineSince = -1;
                    unreachableInARow = 0;
                }
            }
        } catch (InterruptedException interrupted) {
            // Stopping is not an error: fall through to the same exit every other
            // way out of the loop takes.
            Thread.currentThread().interrupt();
        } finally {
            cancelled = true;
            platform.keepAwake(false);
            report(WatcherStatus.STOPPED, "", null);
            log.info("Stopped watching.");
        }
    }

    // MARK: - The safety net

    private void announceHotspot() {
        if (settings.wifiName != null && !settings.wifiName.isEmpty()) {
            log.info("Watching for " + settings.wifiName + ".");
            return;
        }
        log.warning("No hotspot name is set, so this will never join a network: it will only sign "
                + "in when this device is already on the hotspot. Set it in Settings.");
    }

    private void announceSafetyNet() {
        if (settings.fallbackWifiName == null || settings.fallbackWifiName.isEmpty()) {
            log.warning("No fallback network is set, so if this device cannot get onto "
                    + hotspotName() + " it will simply have no internet. Set the fallback to a "
                    + "saved network, your phone's other hotspot for instance, to put a floor "
                    + "under that.");
            return;
        }

        List<String> saved = platform.savedNetworks();
        if (saved == null) {
            log.success("Safety net on: if the internet stays down for " + INTERNET_GRACE_SECONDS
                    + "s, this tells you to switch to " + settings.fallbackWifiName
                    + ". It cannot switch by itself: Android does not let an app join a network.");
            return;
        }

        // A network this machine has not saved cannot be rejoined, so a net naming one
        // is decorative and worse than none: it would be trusted while not being there.
        if (!saved.contains(settings.fallbackWifiName)) {
            log.failure(settings.fallbackWifiName + " is not one of this device's saved networks, "
                    + "so it cannot be joined automatically and the safety net will not work. Join "
                    + "it by hand once, then start this again.");
            return;
        }

        log.success("Safety net on: if the internet stays down for " + INTERNET_GRACE_SECONDS
                + "s, this joins " + settings.fallbackWifiName + " instead.");
    }

    /**
     * Did the net have to catch us? True means the loop should start over.
     *
     * <p>The clock measures unexplained downtime. Joining a network takes the link
     * away for about half a minute all by itself, so the two join paths restart the
     * count rather than being punished for the gap they just caused. A sign-in does
     * not restart it: a sign-in that is working restores the connection in a couple
     * of seconds, so if fifteen of them pass with no internet, signing in is not
     * working.
     */
    private boolean safetyNetFired() throws InterruptedException {
        if (settings.fallbackWifiName == null || settings.fallbackWifiName.isEmpty()) {
            return false;
        }

        if (stillOnline()) {
            offlineSince = -1;
            status.hasInternet = true;
            return false;
        }

        status.hasInternet = false;
        if (offlineSince < 0) {
            offlineSince = System.currentTimeMillis();
        }

        long down = (System.currentTimeMillis() - offlineSince) / 1000;
        if (down < INTERNET_GRACE_SECONDS) {
            return false;
        }

        log.warning("No internet for " + down + "s. Falling back to " + settings.fallbackWifiName
                + " so this device is not left offline.");

        boolean joined = platform.joinNetwork(settings.fallbackWifiName);
        offlineSince = -1;

        if (joined) {
            log.success("On " + settings.fallbackWifiName + ". You have internet again.");
            status.hasInternet = true;
            report(WatcherStatus.ON_FALLBACK, settings.fallbackWifiName, null);
        } else {
            log.failure("Could not get onto " + settings.fallbackWifiName + " either.");
            status.hasInternet = false;
            report(WatcherStatus.ON_FALLBACK, settings.fallbackWifiName, null);
        }

        // Either way, stop hammering the hotspot that just failed us.
        leaveHotspotAloneUntil = System.currentTimeMillis() + backoffSeconds * 1000L;
        log.info("Leaving " + hotspotName() + " alone for " + ErrorText.duration(backoffSeconds) + ".");
        backoffSeconds = Math.min(backoffSeconds * 2, LONGEST_BACKOFF_SECONDS);

        return true;
    }

    /**
     * Is the connection actually working?
     *
     * <p>Asked twice, because the probe gives false negatives often enough to cause
     * pointless network changes.
     */
    private boolean stillOnline() throws InterruptedException {
        if (platform.hasInternet(1.5)) {
            return true;
        }
        sleepSeconds(0.5);
        return platform.hasInternet(2.0);
    }

    // MARK: - Signing in

    /**
     * Sign in, and keep trying briefly while the router is still refusing.
     *
     * <p>{@code /login} only starts answering once the old session has actually gone,
     * so the first attempt on a boundary can still be refused. Retrying for a short
     * window costs nothing and closes the gap to a few seconds.
     *
     * <p>This deliberately reports nothing back to the loop. The loop asks the router
     * what the truth is on its next pass rather than believing a return value from
     * here, so there is no way for the two to get out of step.
     */
    private void signInAndWait() throws InterruptedException {
        long deadline = System.currentTimeMillis() + SessionPacing.POUNCE_RETRY_SECONDS * 1000L;
        long started = System.currentTimeMillis();

        while (!cancelled) {
            if (signIn().isConnected()) {
                return;
            }

            if (System.currentTimeMillis() >= deadline) {
                log.failure("Could not sign in within " + SessionPacing.POUNCE_RETRY_SECONDS
                        + "s. Still trying.");
                return;
            }

            // Hand control back once the safety net's patience is up. Retrying in here
            // for a minute and a half while the net sits unused outside the loop would
            // defeat the point of having one: the whole promise is fifteen seconds
            // offline, not ninety.
            long elapsed = (System.currentTimeMillis() - started) / 1000;
            if (settings.fallbackWifiName != null && !settings.fallbackWifiName.isEmpty()
                    && elapsed >= INTERNET_GRACE_SECONDS) {
                log.warning("Still not signed in after " + elapsed + "s. Handing over.");
                return;
            }

            sleepSeconds(SessionPacing.RETRY_EVERY_SECONDS);
        }
    }

    private Outcome currentSession() {
        // Resolved every time. Switching networks changes which router the portal is
        // on, and one settings entry has to work on all of them.
        String gateway = platform.gateway(0);
        if (gateway == null || gateway.isEmpty()) {
            return new Outcome(SessionProbe.UNREACHABLE, null, "this device has no network at all");
        }

        status.routerIp = gateway;
        SessionProbe.Status answer = SessionProbe.status(gateway, new PortalClient());
        if (SessionProbe.ACTIVE.equals(answer.kind)) {
            return new Outcome(SessionProbe.ACTIVE, answer.info, "");
        }
        if (SessionProbe.EXPIRED.equals(answer.kind)) {
            return new Outcome("signedOut", null, "");
        }
        if (SessionProbe.UNKNOWN.equals(answer.kind)) {
            // The router answered but named no session. That is what it serves at the
            // moment a session lapses, so this is a sign-out, not a lost router.
            // Reading it as unreachable meant rejoining the wireless network at every
            // session boundary, which costs about eight seconds and a dropped link for
            // no reason at all.
            return new Outcome("signedOut", null, "");
        }
        return new Outcome(SessionProbe.UNREACHABLE, null, answer.why);
    }

    private LoginEngine.LoginResult signIn() {
        String gateway = platform.gateway(10);

        if (gateway == null || gateway.isEmpty()) {
            log.warning("This device has no network to sign in on.");
            return new LoginEngine.LoginResult(LoginEngine.FAILED, "No network");
        }

        // Always say which router is being tried. Without it, "nothing is listening
        // there" names no address and cannot be acted on: the usual cause is being on
        // a different network altogether, and the address is the only thing that
        // reveals that.
        log.info("Router is " + gateway + ".");

        Settings active = settings.withGatewayIp(gateway);
        long started = System.currentTimeMillis();
        LoginEngine.LoginResult result = new LoginEngine(active, log, new LoginEngineProbe(platform)).run();
        double took = (System.currentTimeMillis() - started) / 1000.0;
        log.info(result.summary() + " (" + String.format(Locale.US, "%.1f", took) + "s)");

        if (result.isConnected()) {
            settings.touchLastRun();
        }
        return result;
    }

    /** The engine's internet probe, answered by whatever network this device is on. */
    private static final class LoginEngineProbe implements InternetProbe {

        private final Platform platform;

        LoginEngineProbe(Platform platform) {
            this.platform = platform;
        }

        @Override
        public boolean hasInternet(double timeoutSeconds) {
            return platform.hasInternet(timeoutSeconds);
        }
    }

    // MARK: - Timing

    private int checkDelay() {
        return Math.min(Math.max(settings.checkIntervalSecs, 1), 60);
    }

    private void sleepSeconds(double seconds) throws InterruptedException {
        if (cancelled) {
            return;
        }
        // Measured against the clock rather than trusting one sleep, so a wake-up
        // that arrives early does not shorten the nap and a spurious interrupt does
        // not end it.
        long until = System.currentTimeMillis() + Math.round(Math.max(seconds, 0) * 1000);
        while (!cancelled) {
            long left = until - System.currentTimeMillis();
            if (left <= 0) {
                return;
            }
            // Sliced rather than one long sleep: an interrupt is not the only way to
            // be told to stop, and a nap is up to a minute long.
            Thread.sleep(Math.min(left, NAP_SLICE_MILLIS));
        }
    }

    private String routerNow() {
        String gateway = platform.gateway(0);
        return gateway == null ? "" : gateway;
    }

    /** Tell whoever is displaying the state what just changed. */
    private void report(String connection, String detail, String router) {
        status.connection = connection;
        if (!WatcherStatus.ON_FALLBACK.equals(connection) && !WatcherStatus.WRONG_NETWORK.equals(connection)) {
            status.detail = "";
        } else if (detail != null && !detail.isEmpty()) {
            status.detail = detail;
        }
        if (router != null && !router.isEmpty()) {
            status.routerIp = router;
        }
        if (board != null) {
            board.publish(status);
        }
    }
}
