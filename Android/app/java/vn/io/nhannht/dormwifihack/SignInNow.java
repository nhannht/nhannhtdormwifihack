package vn.io.nhannht.dormwifihack;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import vn.io.nhannht.dormwifihack.engine.ActivityLog;
import vn.io.nhannht.dormwifihack.engine.ErrorText;
import vn.io.nhannht.dormwifihack.engine.LoginEngine;
import vn.io.nhannht.dormwifihack.engine.SessionProbe;
import vn.io.nhannht.dormwifihack.engine.Settings;
import vn.io.nhannht.dormwifihack.engine.WatcherStatus;

/**
 * One sign-in, on a thread of its own, with something to say at the end.
 *
 * <p>The screen and the service both need this: the screen because a button has to
 * come back with an answer, and the service because the notification offers the same
 * button when nothing is on screen.
 *
 * <p>When no watcher is running, this also reports what it found, so the screen ends
 * up showing the session that was just opened rather than the "Starting" it began
 * with. Otherwise a sign-in that plainly worked in the log would leave the card above
 * it claiming nothing had happened and the router row empty.
 */
final class SignInNow {

    interface Callback {
        void finished(LoginEngine.LoginResult result);
    }

    private SignInNow() {
    }

    static void run(final Context context, final Callback callback) {
        final Context app = context.getApplicationContext();
        new Thread(new Runnable() {
            @Override
            public void run() {
                ActivityLog log = App.log(app);
                AndroidNetwork network = new AndroidNetwork(app);
                LoginEngine.LoginResult result;

                try {
                    String gateway = network.gatewayWaiting(10);
                    if (gateway == null) {
                        log.warning("No router address: this phone is not on a Wi-Fi network.");
                        result = new LoginEngine.LoginResult(
                                LoginEngine.FAILED, "No Wi-Fi network to sign in on");
                    } else {
                        Settings settings = App.settings(app).withGatewayIp(gateway);
                        log.info("Signing in through " + gateway + "...");
                        result = network.loginEngine(settings, log).run(network.portalClient());

                        if (result.isConnected()) {
                            Settings saved = App.settings(app);
                            saved.touchLastRun();
                            App.saveSettings(app, saved);
                        }
                        log.log(result.isConnected() ? ActivityLog.SUCCESS : ActivityLog.WARNING,
                                result.summary());

                        if (!App.isWatching()) {
                            reportOnce(network, gateway, App.settings(app));
                        }
                    }
                } catch (RuntimeException error) {
                    // The button is enabled only by the callback below, so a sign-in
                    // that raised must still come back with an answer.
                    String reason = ErrorText.shortText(error);
                    log.failure("Sign-in stopped unexpectedly: " + reason);
                    result = new LoginEngine.LoginResult(LoginEngine.FAILED, reason);
                }

                final LoginEngine.LoginResult finished = result;
                if (callback == null) {
                    return;
                }
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    @Override
                    public void run() {
                        callback.finished(finished);
                    }
                });
            }
        }, "sign-in").start();
    }

    /** Put what the router says into the screen's state, after a one-off sign-in. */
    private static void reportOnce(AndroidNetwork network, String gateway, Settings settings) {
        WatcherStatus status = new WatcherStatus();
        status.network = settings.wifiName;
        status.routerIp = gateway;
        status.hasInternet = network.hasInternet(1.5);

        SessionProbe.Status probe = SessionProbe.status(gateway, network.portalClient());
        if (probe.isActive()) {
            status.connection = WatcherStatus.SIGNED_IN;
            if (probe.info != null && probe.info.remaining != null) {
                status.expiresAt = Long.valueOf(
                        System.currentTimeMillis() + probe.info.remaining * 1000);
            }
        } else if (SessionProbe.UNREACHABLE.equals(probe.kind)) {
            status.connection = WatcherStatus.WRONG_NETWORK;
            status.hasInternet = false;
        } else {
            // Nothing is watching, and the router does not show a session, so the
            // honest state is the same one Pause leaves behind.
            status.connection = WatcherStatus.STOPPED;
        }
        App.board().publish(status);
    }
}
