package vn.io.nhannht.dormwifihack;

import android.content.Context;

import java.io.File;

import vn.io.nhannht.dormwifihack.engine.ActivityLog;
import vn.io.nhannht.dormwifihack.engine.ErrorText;
import vn.io.nhannht.dormwifihack.engine.Settings;
import vn.io.nhannht.dormwifihack.engine.WatcherStatus;

/**
 * The pieces the screen and the service both need, in one place.
 *
 * <p>They are the same process, so a handful of statics does what a binder would do
 * more ceremoniously: one log, one board to publish the watcher's state on, and one
 * answer to "is it watching". The alternative — the screen reading a status file the
 * service writes — is what the Linux build does, and it does it because a window and
 * a systemd unit are genuinely different processes there. Here they are not.
 */
public final class App {

    /** The preference file the settings JSON lives in. */
    static final String PREFERENCES = "dormwifi";
    static final String SETTINGS_JSON = "settings_json";

    private static volatile ActivityLog log;
    private static final WatcherStatus.Board BOARD = new WatcherStatus.Board();
    private static volatile boolean watching;

    private App() {
    }

    public static synchronized ActivityLog log(Context context) {
        if (log == null) {
            File file = new File(context.getApplicationContext().getFilesDir(), "activity.log");
            log = new ActivityLog(file);
        }
        return log;
    }

    public static WatcherStatus.Board board() {
        return BOARD;
    }

    public static boolean isWatching() {
        return watching;
    }

    static void setWatching(boolean value) {
        watching = value;
    }

    /** The saved settings, or defaults when there is nothing saved yet. */
    public static Settings settings(Context context) {
        String text = AndroidNetwork.preferences(context).getString(SETTINGS_JSON, "");
        try {
            return Settings.fromJson(text);
        } catch (RuntimeException error) {
            log(context).failure("The saved settings could not be read: "
                    + ErrorText.shortText(error) + ". Starting from defaults.");
            return new Settings();
        }
    }

    /** True when nothing has ever been saved, which is what makes the form prefill. */
    public static boolean hasSavedSettings(Context context) {
        return AndroidNetwork.preferences(context).contains(SETTINGS_JSON);
    }

    public static void saveSettings(Context context, Settings settings) {
        AndroidNetwork.preferences(context).edit()
                .putString(SETTINGS_JSON, settings.toJsonText())
                .apply();
    }
}
