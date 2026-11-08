package vn.io.nhannht.dormwifihack;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

import vn.io.nhannht.dormwifihack.engine.WatcherStatus;

/**
 * The one notification that has to exist.
 *
 * <p>A foreground service is only allowed to keep running because it shows a
 * notification, so this is not decoration: it is the app's licence to be alive with
 * the screen off, and it is also the only place the countdown is visible without
 * opening anything.
 */
final class Notifier {

    static final String CHANNEL_WATCH = "watch";
    static final String CHANNEL_ALERTS = "alerts";
    static final int WATCH_ID = 1001;

    private final Context context;
    private final NotificationManager manager;

    Notifier(Context context) {
        this.context = context.getApplicationContext();
        this.manager = (NotificationManager) this.context.getSystemService(Context.NOTIFICATION_SERVICE);
        createChannels();
    }

    private void createChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O || manager == null) {
            return;
        }
        NotificationChannel watch = new NotificationChannel(
                CHANNEL_WATCH, "Watching", NotificationManager.IMPORTANCE_LOW);
        watch.setDescription("The countdown, and whether the sign-in is working.");
        // The countdown changes every second; a sound for that would be absurd.
        watch.setShowBadge(false);
        manager.createNotificationChannel(watch);

        NotificationChannel alerts = new NotificationChannel(
                CHANNEL_ALERTS, "Sign-in trouble", NotificationManager.IMPORTANCE_DEFAULT);
        alerts.setDescription("Only when a sign-in fails, so silence means it is working.");
        manager.createNotificationChannel(alerts);
    }

    /** The ongoing notification, built from the watcher's own state. */
    Notification build(WatcherStatus status) {
        String headline = status == null ? "Starting" : status.headline();
        String text = describe(status);

        Intent open = new Intent(context, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent contentIntent = PendingIntent.getActivity(
                context, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);

        Notification.Builder builder = newBuilder()
                .setSmallIcon(R.drawable.ic_stat_wifi)
                .setContentTitle(headline)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(contentIntent)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setShowWhen(false)
                .setCategory(Notification.CATEGORY_SERVICE);

        // Two actions, both of which work. There used to be a third, Log out, and it
        // is not here for the same reason the Linux window lost it: this router stops
        // answering /login the moment a session is ended, so the button could only
        // strand the phone.
        builder.addAction(new Notification.Action.Builder(
                null,
                App.isWatching() ? "Pause" : "Start",
                serviceIntent(App.isWatching() ? WatcherService.ACTION_STOP : WatcherService.ACTION_START))
                .build());
        builder.addAction(new Notification.Action.Builder(
                null,
                "Sign in now",
                serviceIntent(WatcherService.ACTION_SIGN_IN))
                .build());

        return builder.build();
    }

    private String describe(WatcherStatus status) {
        if (status == null) {
            return "";
        }
        switch (status.connection) {
            case WatcherStatus.SIGNED_IN:
                return joined(status.routerIp, status.hasInternet);
            case WatcherStatus.SIGNING_IN:
                return joined(status.routerIp, false) + " Renewing the session.";
            case WatcherStatus.WRONG_NETWORK:
                return "The router did not answer" + (status.routerIp.isEmpty() ? "" : " at " + status.routerIp)
                        + ". This phone is probably on another network.";
            case WatcherStatus.ON_FALLBACK:
                return "The internet had been down too long. Switch to " + status.detail + ".";
            case WatcherStatus.STOPPED:
                return "Not signing in. Tap to open, or press Start in the notification.";
            default:
                return joined(status.routerIp, status.hasInternet);
        }
    }

    private static String joined(String router, boolean internet) {
        StringBuilder text = new StringBuilder();
        if (router == null || router.isEmpty()) {
            text.append("Looking for the router");
        } else {
            text.append("Router ").append(router);
        }
        text.append(internet ? " \u00b7 internet up" : " \u00b7 no internet yet");
        return text.toString();
    }

    private PendingIntent serviceIntent(String action) {
        Intent intent = new Intent(context, WatcherService.class);
        intent.setAction(action);
        return PendingIntent.getService(
                context, action.hashCode(), intent,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private Notification.Builder newBuilder() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            return new Notification.Builder(context, CHANNEL_WATCH);
        }
        return new Notification.Builder(context);
    }

    void show(WatcherStatus status) {
        if (manager == null) {
            return;
        }
        try {
            manager.notify(WATCH_ID, build(status));
        } catch (SecurityException ignored) {
            // Without POST_NOTIFICATIONS on Android 13 the notification is simply not
            // shown. The service keeps running, and the screen says so.
        }
    }

    /** A one-off notification for something that went wrong, or right, and should be heard. */
    void alert(String title, String text) {
        if (manager == null) {
            return;
        }
        Intent open = new Intent(context, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent contentIntent = PendingIntent.getActivity(
                context, 1, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);

        Notification.Builder builder = (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(context, CHANNEL_ALERTS)
                : new Notification.Builder(context))
                .setSmallIcon(R.drawable.ic_stat_wifi)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(contentIntent)
                .setAutoCancel(true);

        try {
            manager.notify(WATCH_ID + 1, builder.build());
        } catch (SecurityException ignored) {
            // Same as above: no permission, no notification, everything else works.
        }
    }

    /** Called from the service when it is about to stop showing the ongoing one. */
    void cancelWatch() {
        if (manager != null) {
            manager.cancel(WATCH_ID);
        }
    }
}
