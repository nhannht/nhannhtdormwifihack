package vn.io.nhannht.dormwifihack;

import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import java.util.List;

import vn.io.nhannht.dormwifihack.engine.SessionScheduler;
import vn.io.nhannht.dormwifihack.engine.WatcherStatus;

/**
 * The watcher, in a foreground service.
 *
 * <p>On Linux this is a systemd user unit and a thread. Here it has to be a
 * foreground service, because that is the only kind of Android process allowed to
 * keep working with the screen off, and a watcher that Android may freeze cannot
 * renew a fifteen-minute session on time — which is the whole job.
 *
 * <p>Stopping never blocks. {@code onStartCommand} runs on the main thread, and a
 * watcher caught in a request to the router can take seconds to notice, so the
 * thread reports its own end and the next start is asked for rather than raced:
 * two watchers would both post to {@code /login} the moment a session lapses, and
 * the router rejects the second one.
 */
public class WatcherService extends Service implements WatcherStatus.Listener {

    public static final String ACTION_START = "vn.io.nhannht.dormwifihack.START";
    public static final String ACTION_STOP = "vn.io.nhannht.dormwifihack.STOP";
    public static final String ACTION_SIGN_IN = "vn.io.nhannht.dormwifihack.SIGN_IN";
    public static final String ACTION_RELOAD = "vn.io.nhannht.dormwifihack.RELOAD";

    private Notifier notifier;
    private AndroidNetwork network;
    private PowerManager.WakeLock wakeLock;
    private SessionScheduler scheduler;
    private Thread worker;
    private Runnable statusSubscription;

    /** Set when a start arrives while a stop is still finishing. */
    private boolean restartWhenStopped;

    @Override
    public void onCreate() {
        super.onCreate();
        notifier = new Notifier(this);
        network = new AndroidNetwork(this);
        App.log(this);
        statusSubscription = App.board().subscribe(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();

        // Before anything else, and every time: startForegroundService gives a
        // service a few seconds to post its notification, and a Stop pressed from the
        // notification arrives through here too.
        startForeground(Notifier.WATCH_ID, notifier.build(App.board().snapshot()));

        if (ACTION_STOP.equals(action)) {
            stopWatching();
            stopSelf();
            return START_NOT_STICKY;
        }

        if (ACTION_SIGN_IN.equals(action)) {
            signInOnce();
            return App.isWatching() ? START_STICKY : START_NOT_STICKY;
        }

        if (ACTION_RELOAD.equals(action)) {
            restartWatching();
            return START_STICKY;
        }

        startWatching();
        return START_STICKY;
    }

    // MARK: - Watching

    private synchronized void startWatching() {
        if (worker != null && worker.isAlive()) {
            // A stop is still finishing. Ask for the restart rather than starting a
            // second watcher.
            restartWhenStopped = true;
            return;
        }

        final SessionScheduler running = new SessionScheduler(
                App.settings(this), App.log(this), platform(), App.board());

        scheduler = running;
        worker = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    running.run();
                } finally {
                    finished(running);
                }
            }
        }, "watcher");

        // The scheduler stops by interrupting the thread it runs on, so it has to be
        // handed the one it was given. Without this a Pause waits out the whole nap.
        running.attach(worker);
        App.setWatching(true);
        worker.start();
        notifier.show(App.board().snapshot());
    }

    /** The watcher's thread has ended, however it ended. */
    private synchronized void finished(SessionScheduler ended) {
        if (scheduler == ended) {
            scheduler = null;
            worker = null;
        }
        App.setWatching(false);
        holdCpu(false);

        if (restartWhenStopped) {
            restartWhenStopped = false;
            startWatching();
        }
    }

    private void stopWatching() {
        SessionScheduler running = scheduler;
        if (running != null) {
            running.stop();
        }
    }

    /** Re-read the settings and start again. */
    private synchronized void restartWatching() {
        boolean wasWatching = App.isWatching() || (worker != null && worker.isAlive());
        if (!wasWatching) {
            startWatching();
            return;
        }
        restartWhenStopped = true;
        stopWatching();
    }

    private void signInOnce() {
        SignInNow.run(this, new SignInNow.Callback() {
            @Override
            public void finished(vn.io.nhannht.dormwifihack.engine.LoginEngine.LoginResult result) {
                if (!result.isConnected()) {
                    notifier.alert("Could not sign in", result.summary());
                }
                notifier.show(App.board().snapshot());
                if (!App.isWatching()) {
                    stopSelf();
                }
            }
        });
    }

    // MARK: - What the scheduler needs from this phone

    private SessionScheduler.Platform platform() {
        return new SessionScheduler.Platform() {
            @Override
            public String gateway(int waitSeconds) {
                return waitSeconds > 0 ? network.gatewayWaiting(waitSeconds) : network.gateway();
            }

            @Override
            public boolean hasInternet(double timeoutSeconds) {
                return network.hasInternet(timeoutSeconds);
            }

            @Override
            public boolean joinNetwork(String ssid) {
                return network.joinNetwork(ssid);
            }

            @Override
            public List<String> savedNetworks() {
                return network.savedNetworks();
            }

            @Override
            public void keepAwake(boolean keepAwake) {
                holdCpu(keepAwake);
            }
        };
    }

    // MARK: - Keeping the CPU awake

    /**
     * Hold the CPU awake while watching.
     *
     * <p>This is the Android tax on a job like this one. Between Doze and app
     * standby, a sleeping phone may not run a thread's timer for fifteen minutes at
     * a time, and a session that lapses in the meantime stays lapsed until somebody
     * unlocks the phone. A partial wake lock is what turns "usually works" into
     * "works", and it is why the screen offers Pause and says what the lock costs.
     */
    private void holdCpu(boolean keepAwake) {
        PowerManager power = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (power == null) {
            return;
        }
        if (wakeLock == null) {
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "dormwifi:watcher");
            wakeLock.setReferenceCounted(false);
        }
        try {
            if (keepAwake && !wakeLock.isHeld()) {
                wakeLock.acquire();
            } else if (!keepAwake && wakeLock.isHeld()) {
                wakeLock.release();
            }
        } catch (RuntimeException ignored) {
            // A refused wake lock is a reason for a longer gap, not a crash.
        }
    }

    // MARK: - The status the notification shows

    @Override
    public void onStatus(WatcherStatus status) {
        if (status == null) {
            return;
        }
        notifier.show(status);
    }

    @Override
    public void onDestroy() {
        if (statusSubscription != null) {
            statusSubscription.run();
            statusSubscription = null;
        }
        stopWatching();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE);
        } else {
            stopForeground(true);
        }
        notifier.cancelWatch();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
