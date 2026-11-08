package vn.io.nhannht.dormwifihack;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

import vn.io.nhannht.dormwifihack.engine.Settings;

/**
 * Starts the watcher again after a reboot, when the setting says so.
 *
 * <p>Off by default, and the screen says why: an app that starts itself on every boot
 * is a surprise, and one that holds the CPU awake while it does it is a surprise
 * somebody should have chosen.
 */
public class StartOnBoot extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent == null ? null : intent.getAction();
        if (action == null) {
            return;
        }
        if (!Intent.ACTION_BOOT_COMPLETED.equals(action)
                && !Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            return;
        }

        Settings settings = App.settings(context);
        if (!settings.autoStart) {
            return;
        }

        App.log(context).info("Starting again after " + action + ".");
        context.startForegroundService(new Intent(context, WatcherService.class)
                .setAction(WatcherService.ACTION_START));
    }
}
