package vn.io.nhannht.dormwifihack.engine;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * What the watcher is doing right now, in the words the screen shows.
 *
 * <p>Whatever is displaying the state reads this and never asks the router itself.
 * Two probes running side by side eventually disagree, and then the screen and the
 * notification are telling the user different stories about one connection.
 */
public final class WatcherStatus {

    public static final String STARTING = "starting";
    public static final String SIGNED_IN = "signedIn";
    public static final String SIGNING_IN = "signingIn";
    public static final String WRONG_NETWORK = "wrongNetwork";
    public static final String ON_FALLBACK = "onFallback";
    public static final String STOPPED = "stopped";
    public static final String STOPPING = "stopping";

    public String connection = STARTING;

    /** The payload some states carry: why the router went quiet, or the fallback's name. */
    public String detail = "";

    /**
     * When the router said this session ends, as a Unix timestamp in milliseconds,
     * or null when it did not say.
     *
     * <p>A moment, not a duration. The watcher can sleep a full minute between
     * checks, so a stored "745 seconds left" is wrong by up to a minute by the time
     * anyone reads it. A deadline stays true however long ago it was set.
     */
    public Long expiresAt;

    public String routerIp = "";
    public String network = "";
    public boolean hasInternet = false;
    public String lastMessage = "";
    public long updated = System.currentTimeMillis();

    /**
     * How long this session has left, worked out now rather than when asked.
     *
     * <p>Rounded, not truncated: truncating turns 744.99 seconds into 744 and the
     * screen reads one second short of the truth from the moment it is set.
     */
    public Integer remainingNow() {
        if (expiresAt == null) {
            return null;
        }
        long seconds = Math.round((expiresAt - System.currentTimeMillis()) / 1000.0);
        return Integer.valueOf((int) Math.max(seconds, 0));
    }

    /** The one line at the top of the screen. */
    public String headline() {
        if (STARTING.equals(connection)) {
            return "Starting";
        }
        if (SIGNED_IN.equals(connection)) {
            Integer left = remainingNow();
            if (left == null) {
                return "Signed in";
            }
            return "Signed in, " + ErrorText.duration(left) + " left";
        }
        if (SIGNING_IN.equals(connection)) {
            return "Signing in";
        }
        if (WRONG_NETWORK.equals(connection)) {
            return network == null || network.isEmpty() ? "Not on the hotspot" : "Not on " + network;
        }
        if (ON_FALLBACK.equals(connection)) {
            return "On " + detail;
        }
        if (STOPPED.equals(connection)) {
            return "Paused";
        }
        if (STOPPING.equals(connection)) {
            return "Stopping";
        }
        return "Starting";
    }

    /**
     * Too old to believe.
     *
     * <p>A status is a snapshot, and the only thing that keeps it true is a loop
     * writing a new one. When nothing is watching, {@code expiresAt} keeps counting
     * down all the same, so a screen that trusts an abandoned status ends up
     * insisting "Signed in, 0s left" forever. The watcher writes at least once a
     * minute, so nothing older than two is worth showing as current.
     */
    public boolean isStale() {
        return System.currentTimeMillis() - updated > 120 * 1000;
    }

    /** Whether the connection is currently good, for the screen's indicator. */
    public boolean isHealthy() {
        if (SIGNED_IN.equals(connection)) {
            return true;
        }
        if (ON_FALLBACK.equals(connection)) {
            return hasInternet;
        }
        return false;
    }

    public WatcherStatus copy() {
        WatcherStatus duplicate = new WatcherStatus();
        duplicate.connection = connection;
        duplicate.detail = detail;
        duplicate.expiresAt = expiresAt;
        duplicate.routerIp = routerIp;
        duplicate.network = network;
        duplicate.hasInternet = hasInternet;
        duplicate.lastMessage = lastMessage;
        duplicate.updated = updated;
        return duplicate;
    }

    // MARK: - Broadcasting

    /** A live feed of the watcher's state. */
    public interface Listener {
        void onStatus(WatcherStatus status);
    }

    /**
     * Broadcasts the watcher's state to whoever is displaying it.
     *
     * <p>Writers publish, readers subscribe, and a late reader can ask for the
     * current value rather than waiting for the next change. A screen that is opened
     * between two updates needs exactly that.
     */
    public static final class Board {

        private final Map<Integer, Listener> listeners = new LinkedHashMap<Integer, Listener>();
        private int nextIdentifier;
        private WatcherStatus current = new WatcherStatus();
        private final Object lock = new Object();

        public WatcherStatus publish(WatcherStatus status) {
            WatcherStatus stamped = status.copy();
            stamped.updated = System.currentTimeMillis();

            List<Listener> snapshot;
            synchronized (lock) {
                current = stamped;
                snapshot = new ArrayList<Listener>(listeners.values());
            }
            for (Listener listener : snapshot) {
                try {
                    listener.onStatus(stamped);
                } catch (RuntimeException ignored) {
                    // A broken listener is not the board's problem.
                }
            }
            return stamped;
        }

        public WatcherStatus snapshot() {
            synchronized (lock) {
                return current.copy();
            }
        }

        public Runnable subscribe(final Listener listener) {
            final int identifier;
            synchronized (lock) {
                identifier = nextIdentifier++;
                listeners.put(Integer.valueOf(identifier), listener);
                final WatcherStatus value = current.copy();

                // Handed the current value straight away, because a screen that
                // opened between two updates would otherwise show nothing until the
                // next one. Guarded like every other delivery.
                try {
                    listener.onStatus(value);
                } catch (RuntimeException ignored) {
                    // A listener that throws is that listener's problem.
                }
            }
            return new Runnable() {
                @Override
                public void run() {
                    synchronized (lock) {
                        listeners.remove(Integer.valueOf(identifier));
                    }
                }
            };
        }
    }
}
