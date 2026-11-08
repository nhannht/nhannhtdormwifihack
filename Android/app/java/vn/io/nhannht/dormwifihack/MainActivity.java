package vn.io.nhannht.dormwifihack;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.view.View;
import android.view.inputmethod.InputMethodManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.List;

import vn.io.nhannht.dormwifihack.engine.ActivityLog;
import vn.io.nhannht.dormwifihack.engine.Discover;
import vn.io.nhannht.dormwifihack.engine.ErrorText;
import vn.io.nhannht.dormwifihack.engine.LoginEngine;
import vn.io.nhannht.dormwifihack.engine.PortalClient;
import vn.io.nhannht.dormwifihack.engine.SessionProbe;
import vn.io.nhannht.dormwifihack.engine.Settings;
import vn.io.nhannht.dormwifihack.engine.WatcherStatus;

/**
 * The one screen: the state at the top, the two actions under it, then the settings
 * and the log.
 *
 * <p>It is laid out flat rather than in a menu because the Linux window is laid out
 * flat, and because the state is the reason anyone opens this. The log is on its own
 * tab: on a phone, a page that scrolls through two hundred lines of commentary makes
 * the three facts above it hard to reach.
 */
public class MainActivity extends Activity implements WatcherStatus.Listener, ActivityLog.Listener {

    /**
     * What this hotspot is, so a first run has something to show rather than six empty
     * fields. These are read off the dorm network this app was written for; anything
     * else is typed over them and only becomes real when Save is pressed.
     */
    private static final String DEFAULT_HOTSPOT = "INET - Free WiFi";
    private static final String DEFAULT_PORTAL = "http://v1.awingconnect.vn";

    /** How many log lines the screen keeps. The file keeps far more. */
    private static final int VISIBLE_LOG_LINES = 300;

    private TextView headline;
    private TextView detailLine;
    private TextView networkValue;
    private TextView routerValue;
    private TextView internetValue;
    private TextView safetyValue;
    private TextView message;
    private TextView actionHint;
    private TextView settingsHeader;
    private TextView settingsFoot;
    private TextView logCount;
    private LinearLayout settingsBody;
    private LinearLayout logView;
    private LinearLayout batteryRow;
    private ScrollView pageStatus;
    private ScrollView pageLog;
    private Button tabStatus;
    private Button tabLog;
    private Button toggleButton;
    private Button signInButton;
    private Button saveSettings;
    private Button findPortal;
    private Button copyLog;
    private Button batteryButton;
    private EditText fieldWifi;
    private EditText fieldPortal;
    private EditText fieldInterval;
    private EditText fieldFallback;
    private EditText fieldUsername;
    private EditText fieldPassword;
    private CheckBox fieldAutoStart;

    private Runnable statusSubscription;
    private Runnable logSubscription;
    private int visibleLogLines;
    private boolean settingsOpen;
    private boolean signingIn;
    /**
     * A pause has been asked for and the watcher has not finished yet.
     *
     * <p>Stopping is not instant: the loop may be inside a request to the router. The
     * button says so and goes insensitive rather than inviting a second press, which
     * would be a start racing a stop.
     */
    private boolean stopping;

    private final Handler ticker = new Handler(Looper.getMainLooper());

    /** How long an informational message stays before it stops being news. */
    private static final int INFO_MESSAGE_MILLIS = 5000;

    private final Handler messageTimer = new Handler(Looper.getMainLooper());
    private final Runnable hideMessage = new Runnable() {
        @Override
        public void run() {
            message.setVisibility(View.GONE);
        }
    };
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            // Only the countdown moves. The rest of the state changes when the watcher
            // says it does, and a screen that invented its own state would eventually
            // disagree with the notification about the same connection.
            WatcherStatus status = App.board().snapshot();
            headline.setText(status.headline());
            ticker.postDelayed(this, 1000);
        }
    };

    // MARK: - Lifecycle

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        headline = findViewById(R.id.headline);
        detailLine = findViewById(R.id.detailLine);
        networkValue = findViewById(R.id.networkValue);
        routerValue = findViewById(R.id.routerValue);
        internetValue = findViewById(R.id.internetValue);
        safetyValue = findViewById(R.id.safetyValue);
        message = findViewById(R.id.message);
        actionHint = findViewById(R.id.actionHint);
        settingsHeader = findViewById(R.id.settingsHeader);
        settingsFoot = findViewById(R.id.settingsFoot);
        logCount = findViewById(R.id.logCount);
        settingsBody = findViewById(R.id.settingsBody);
        logView = findViewById(R.id.logView);
        batteryRow = findViewById(R.id.batteryRow);
        pageStatus = findViewById(R.id.pageStatus);
        pageLog = findViewById(R.id.pageLog);
        tabStatus = findViewById(R.id.tabStatus);
        tabLog = findViewById(R.id.tabLog);
        toggleButton = findViewById(R.id.toggleButton);
        signInButton = findViewById(R.id.signInButton);
        saveSettings = findViewById(R.id.saveSettings);
        findPortal = findViewById(R.id.findPortal);
        copyLog = findViewById(R.id.copyLog);
        batteryButton = findViewById(R.id.batteryButton);
        fieldWifi = findViewById(R.id.fieldWifi);
        fieldPortal = findViewById(R.id.fieldPortal);
        fieldInterval = findViewById(R.id.fieldInterval);
        fieldFallback = findViewById(R.id.fieldFallback);
        fieldUsername = findViewById(R.id.fieldUsername);
        fieldPassword = findViewById(R.id.fieldPassword);

        fieldAutoStart = new CheckBox(this);
        fieldAutoStart.setText("Start watching when the phone reboots");
        fieldAutoStart.setTextColor(getColor(R.color.text));
        fieldAutoStart.setTextSize(13f);
        LinearLayout.LayoutParams autoStartParams =
                new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                        LinearLayout.LayoutParams.WRAP_CONTENT);
        autoStartParams.topMargin = dp(14);
        settingsBody.addView(fieldAutoStart, autoStartParams);

        settingsFoot.setText("The file keeps more than this screen does: files/activity.log in the "
                + "app's own storage. Settings are stored as the same settings.json the macOS, "
                + "Windows and Linux builds write.");

        wireActions();
        loadSettingsIntoForm();
        refreshBatteryRow();
        askForNotifications();
        showStatusPage();
    }

    @Override
    protected void onStart() {
        super.onStart();
        statusSubscription = App.board().subscribe(this);
        logSubscription = App.log(this).subscribe(this);
        fillLogFromMemory();
        refreshToggle();
        ticker.post(tick);
    }

    @Override
    protected void onStop() {
        ticker.removeCallbacks(tick);
        if (statusSubscription != null) {
            statusSubscription.run();
            statusSubscription = null;
        }
        if (logSubscription != null) {
            logSubscription.run();
            logSubscription = null;
        }
        super.onStop();
    }

    // MARK: - Wiring

    private void wireActions() {
        tabStatus.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                showStatusPage();
            }
        });
        tabLog.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                showLogPage();
            }
        });

        settingsHeader.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                settingsOpen = !settingsOpen;
                settingsBody.setVisibility(settingsOpen ? View.VISIBLE : View.GONE);
                renderSettingsHeader();
            }
        });

        toggleButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                if (App.isWatching()) {
                    stopping = true;
                    sendService(WatcherService.ACTION_STOP);
                    say("Pausing. The session already open stays open until it lapses.",
                            Kind.INFO);
                    refreshToggle();
                    // If the status that says it stopped never arrives, do not leave a
                    // dead button behind: give the watcher a while and then trust the
                    // question again.
                    toggleButton.postDelayed(new Runnable() {
                        @Override
                        public void run() {
                            stopping = false;
                            refreshToggle();
                        }
                    }, 20000);
                } else {
                    Settings settings = App.settings(MainActivity.this);
                    if (!App.hasSavedSettings(MainActivity.this) || settings.wifiName.isEmpty()) {
                        say("Starting. No hotspot name is set, so this will only sign in when the "
                                + "phone is already on the hotspot. Set it under Settings.", Kind.INFO);
                    } else {
                        say("Starting. It signs in when the session lapses, not before.", Kind.OK);
                    }
                    startForegroundService(new Intent(MainActivity.this, WatcherService.class)
                            .setAction(WatcherService.ACTION_START));
                }
                toggleButton.postDelayed(new Runnable() {
                    @Override
                    public void run() {
                        refreshToggle();
                    }
                }, 600);
            }
        });

        signInButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                signInNow();
            }
        });

        saveSettings.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                saveForm();
            }
        });

        findPortal.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                findPortal();
            }
        });

        copyLog.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                copyLogToClipboard();
            }
        });

        batteryButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                allowBackgroundUse();
            }
        });
    }

    private void showStatusPage() {
        pageStatus.setVisibility(View.VISIBLE);
        pageLog.setVisibility(View.GONE);
        tabStatus.setTextColor(getColor(R.color.accent));
        tabLog.setTextColor(getColor(R.color.text));
    }

    private void showLogPage() {
        pageStatus.setVisibility(View.GONE);
        pageLog.setVisibility(View.VISIBLE);
        tabStatus.setTextColor(getColor(R.color.text));
        tabLog.setTextColor(getColor(R.color.accent));
    }

    // MARK: - The state

    @Override
    public void onStatus(final WatcherStatus status) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                render(status);
            }
        });
    }

    private void render(WatcherStatus status) {
        if (WatcherStatus.STOPPED.equals(status.connection)) {
            stopping = false;
        }
        headline.setText(status.headline());
        headline.setTextColor(colorFor(status));

        String network = status.network == null || status.network.isEmpty()
                ? App.settings(this).wifiName
                : status.network;
        if (network == null || network.isEmpty()) {
            network = "(no hotspot name set)";
        }
        String ssid = new AndroidNetwork(this).currentSsid();
        networkValue.setText(ssid == null ? network : ssid);
        networkValue.setTextColor(getColor(R.color.text));

        routerValue.setText(status.routerIp.isEmpty() ? "\u2014" : status.routerIp);
        internetValue.setText(status.hasInternet ? "up" : "down");
        internetValue.setTextColor(getColor(status.hasInternet ? R.color.ok : R.color.warn));

        String fallback = App.settings(this).fallbackWifiName;
        safetyValue.setText(fallback.isEmpty() ? "off \u00b7 no fallback network set" : fallback);
        safetyValue.setTextColor(getColor(fallback.isEmpty() ? R.color.text_dim : R.color.text));

        detailLine.setText(detailText(status));
        refreshToggle();
    }

    private String detailText(WatcherStatus status) {
        if (status.isStale() && !App.isWatching()) {
            return "Nothing is watching right now, so this is the last thing it saw.";
        }
        switch (status.connection) {
            case WatcherStatus.SIGNED_IN:
                return "The router says the session is open. It cannot be renewed early, so the next "
                        + "sign-in goes in the moment this one ends.";
            case WatcherStatus.SIGNING_IN:
                return "The session has lapsed and the sign-in is going in.";
            case WatcherStatus.WRONG_NETWORK:
                return "The router did not answer. This phone is probably on another network.";
            case WatcherStatus.ON_FALLBACK:
                return "The internet had been down too long.";
            case WatcherStatus.STOPPED:
                return "Not signing in.";
            default:
                return "Starting up.";
        }
    }

    private int colorFor(WatcherStatus status) {
        if (status.isHealthy()) {
            return getColor(R.color.ok);
        }
        if (WatcherStatus.WRONG_NETWORK.equals(status.connection)) {
            return getColor(R.color.warn);
        }
        if (WatcherStatus.STOPPED.equals(status.connection)) {
            return getColor(R.color.text_dim);
        }
        return getColor(R.color.text);
    }

    private void refreshToggle() {
        if (stopping) {
            toggleButton.setEnabled(false);
            toggleButton.setText("Stopping\u2026");
            actionHint.setText("Waiting for the watcher to finish what it is doing.");
            return;
        }
        toggleButton.setEnabled(true);
        boolean watching = App.isWatching();
        toggleButton.setText(watching ? "Pause" : "Start");
        actionHint.setText(watching
                ? "Pause stops the automatic sign-in. Sign in now tries once, immediately."
                : "Start watches for a lapsed session and signs in again. Sign in now tries once, "
                        + "immediately.");
    }

    // MARK: - Sign in now

    private void signInNow() {
        if (signingIn) {
            return;
        }
        signingIn = true;
        signInButton.setEnabled(false);
        signInButton.setText("Signing in\u2026");
        say("Signing in through the portal\u2026", Kind.INFO);

        SignInNow.run(this, new SignInNow.Callback() {
            @Override
            public void finished(LoginEngine.LoginResult result) {
                // The button is enabled only here, so a failure that raised still leaves
                // a button that works.
                signingIn = false;
                signInButton.setEnabled(true);
                signInButton.setText("Sign in now");
                say(result.summary(), result.isConnected() ? Kind.OK : Kind.ERROR);
                hideKeyboard();
            }
        });
    }

    // MARK: - Settings

    private void loadSettingsIntoForm() {
        if (!App.hasSavedSettings(this)) {
            // Nothing has ever been saved. Show this hotspot's own values rather than
            // six empty fields, and say out loud that nothing is applied until Save.
            fieldWifi.setText(DEFAULT_HOTSPOT);
            fieldPortal.setText(DEFAULT_PORTAL);
            fieldInterval.setText("15");
            say("Nothing is set up yet. This dorm's values are filled in below. Check them and "
                    + "press Save.", Kind.INFO);
            settingsOpen = true;
            settingsBody.setVisibility(View.VISIBLE);
            renderSettingsHeader();
            return;
        }

        Settings settings = App.settings(this);
        fieldWifi.setText(settings.wifiName);
        fieldPortal.setText(settings.portalFallbackUrl);
        fieldInterval.setText(String.valueOf(settings.interval));
        fieldFallback.setText(settings.fallbackWifiName);
        fieldUsername.setText(settings.username);
        fieldPassword.setText(settings.password);
        fieldAutoStart.setChecked(settings.autoStart);
        renderSettingsHeader();
    }

    private void renderSettingsHeader() {
        settingsHeader.setText("Settings  " + (settingsOpen ? "\u25be" : "\u25b8"));
    }

    private void saveForm() {
        Settings settings = App.settings(this);
        settings.wifiName = text(fieldWifi);
        settings.portalFallbackUrl = text(fieldPortal);
        settings.fallbackWifiName = text(fieldFallback);
        settings.username = text(fieldUsername);
        settings.password = text(fieldPassword);
        settings.autoStart = fieldAutoStart.isChecked();

        Integer minutes = parse(fieldInterval);
        if (minutes == null || minutes < 1 || minutes > 1440) {
            say("Session length has to be a whole number of minutes between 1 and 1440.",
                    Kind.ERROR);
            fieldInterval.requestFocus();
            return;
        }
        settings.interval = minutes;

        try {
            App.saveSettings(this, settings);
        } catch (RuntimeException error) {
            say("Could not save: " + ErrorText.shortText(error), Kind.ERROR);
            return;
        }

        hideKeyboard();
        render(App.board().snapshot());

        StringBuilder saved = new StringBuilder("Saved.");
        if (settings.wifiName.isEmpty()) {
            saved.append(" No hotspot name is set, so nothing will be joined.");
        }
        if (settings.portalFallbackUrl.isEmpty() && settings.username.isEmpty()) {
            saved.append(" No portal address is set, so a portal with no address on its login page "
                    + "cannot be followed.");
        }
        if (App.isWatching()) {
            saved.append(" The watcher is restarting with the new settings.");
            sendService(WatcherService.ACTION_RELOAD);
        }
        say(saved.toString(), Kind.OK);
    }

    /**
     * Read the router's login page and look for a portal address on it.
     *
     * <p>The same reading the Linux build's {@code dormwifi discover} does. It fills the
     * field and stops there: an address that was guessed and silently saved is worse
     * than one somebody looked at.
     */
    private void findPortal() {
        findPortal.setEnabled(false);
        findPortal.setText("Looking\u2026");
        say("Reading the router's login page\u2026", Kind.INFO);

        new Thread(new Runnable() {
            @Override
            public void run() {
                final AndroidNetwork network = new AndroidNetwork(MainActivity.this);
                final String gateway = network.gatewayWaiting(5);

                String found = null;
                String description;
                Kind kind;

                if (gateway == null) {
                    kind = Kind.ERROR;
                    description = "No router address: this phone is not on a Wi-Fi network.";
                } else {
                    try {
                        PortalClient client = network.portalClient();
                        // While a session is open this router accepts the connection to
                        // /login and then never answers. Asking anyway turns a working
                        // hotspot into eight seconds of nothing followed by "timed out",
                        // which sends the reader looking in the wrong place.
                        if (SessionProbe.status(gateway, client).isActive()) {
                            kind = Kind.ERROR;
                            description = "A session is already open, and this router stops "
                                    + "answering its login page while one is. Try again once it "
                                    + "lapses.";
                        } else {
                            String url = "http://" + gateway
                                    + "/login?dst=http%3A%2F%2Fcaptive.apple.com%2Fhotspot-detect.html";
                            PortalClient.Response page = client.get(url, null, 8.0);
                            Discover.Finding finding = Discover.inspect(page.body(), gateway);
                            if (!finding.candidates.isEmpty()) {
                                found = finding.candidates.get(0);
                                kind = Kind.OK;
                                description = "Found " + found + " on the login page at "
                                        + gateway + ". Press Save to use it.";
                            } else {
                                kind = Kind.ERROR;
                                description = join(Discover.render(finding, gateway));
                            }
                        }
                    } catch (Exception error) {
                        kind = Kind.ERROR;
                        description = "Could not read the login page at " + gateway + ": "
                                + ErrorText.shortText(error);
                    }
                }

                final String address = found;
                final String text = description;
                final Kind messageKind = kind;
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        findPortal.setEnabled(true);
                        findPortal.setText("Find portal");
                        if (address != null) {
                            fieldPortal.setText(address);
                        }
                        say(text, messageKind);
                    }
                });
            }
        }, "find-portal").start();
    }

    private static String join(List<String> lines) {
        StringBuilder text = new StringBuilder();
        for (String line : lines) {
            if (text.length() > 0) {
                text.append('\n');
            }
            text.append(line);
        }
        return text.toString();
    }

    // MARK: - Battery

    private void refreshBatteryRow() {
        PowerManager power = (PowerManager) getSystemService(Context.POWER_SERVICE);
        boolean allowed = power == null || power.isIgnoringBatteryOptimizations(getPackageName());
        batteryRow.setVisibility(allowed ? View.GONE : View.VISIBLE);
    }

    private void allowBackgroundUse() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            return;
        }
        try {
            Intent intent = new Intent(
                    android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS);
            intent.setData(Uri.parse("package:" + getPackageName()));
            startActivity(intent);
            say("Allow this app in the dialog. Without it Android may hold the sign-in back until "
                    + "the phone is unlocked.", Kind.INFO);
        } catch (RuntimeException error) {
            // Some builds refuse the direct request. The list of apps is one tap away
            // and does the same thing, so say which one instead of leaving a button
            // that did nothing.
            try {
                startActivity(new Intent(
                        android.provider.Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
                say("Pick Dorm WiFi from the list and allow it.", Kind.INFO);
            } catch (RuntimeException again) {
                say("This phone will not open its battery settings. The watcher still works, but "
                        + "Android may delay a sign-in while the screen has been off a long time.",
                        Kind.ERROR);
            }
        }
        batteryButton.postDelayed(new Runnable() {
            @Override
            public void run() {
                refreshBatteryRow();
            }
        }, 1500);
    }

    // MARK: - The log

    @Override
    public void onEntry(final ActivityLog.Entry entry) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                appendLog(entry);
            }
        });
    }

    private void fillLogFromMemory() {
        logView.removeAllViews();
        visibleLogLines = 0;
        ActivityLog activityLog = App.log(this);
        List<ActivityLog.Entry> entries = activityLog.all();
        if (entries.isEmpty() && activityLog.file() != null) {
            // The file outlives the process. Without this, opening the app after
            // Android has reclaimed it shows an empty log while the file is full,
            // which reads as "it has done nothing".
            entries = ActivityLog.entriesInFile(activityLog.file(), VISIBLE_LOG_LINES);
        }
        int from = Math.max(entries.size() - VISIBLE_LOG_LINES, 0);
        for (ActivityLog.Entry entry : entries.subList(from, entries.size())) {
            appendLog(entry);
        }
        updateLogCount();
    }

    private void appendLog(ActivityLog.Entry entry) {
        TextView line = new TextView(this);
        line.setTextSize(12f);
        line.setTypeface(android.graphics.Typeface.MONOSPACE);
        line.setText(entry.formatted());
        line.setTextColor(getColor(colorForLevel(entry.level)));
        line.setPadding(0, dp(1), 0, dp(1));
        logView.addView(line);
        visibleLogLines++;

        // The screen keeps the last few hundred. The file keeps the last half megabyte,
        // so nothing is lost by dropping the top of this list.
        while (visibleLogLines > VISIBLE_LOG_LINES && logView.getChildCount() > 0) {
            logView.removeViewAt(0);
            visibleLogLines--;
        }
        updateLogCount();

        if (pageLog.getVisibility() == View.VISIBLE) {
            pageLog.post(new Runnable() {
                @Override
                public void run() {
                    pageLog.fullScroll(View.FOCUS_DOWN);
                }
            });
        }
    }

    private void updateLogCount() {
        if (visibleLogLines == 0) {
            logCount.setText("No lines yet.");
        } else {
            logCount.setText(visibleLogLines + (visibleLogLines == 1 ? " line" : " lines")
                    + " \u00b7 newest last");
        }
    }

    private int colorForLevel(String level) {
        if (ActivityLog.SUCCESS.equals(level)) {
            return R.color.ok;
        }
        if (ActivityLog.WARNING.equals(level)) {
            return R.color.warn;
        }
        if (ActivityLog.FAILURE.equals(level)) {
            return R.color.error;
        }
        return R.color.text_dim;
    }

    private void copyLogToClipboard() {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        List<ActivityLog.Entry> entries = App.log(this).all();
        if (entries.isEmpty()) {
            say("There is nothing in the log to copy yet.", Kind.ERROR);
            return;
        }
        StringBuilder text = new StringBuilder();
        for (ActivityLog.Entry entry : entries) {
            text.append(entry.formatted()).append('\n');
        }
        if (clipboard == null) {
            say("This phone has no clipboard available.", Kind.ERROR);
            return;
        }
        clipboard.setPrimaryClip(ClipData.newPlainText("Dorm WiFi log", text.toString()));
        say("Copied " + entries.size() + (entries.size() == 1 ? " line." : " lines."), Kind.OK);
    }

    // MARK: - Feedback

    private enum Kind {
        OK,
        ERROR,
        INFO
    }

    /**
     * Something every action answers with.
     *
     * <p>No interaction in this screen is allowed to be a bare press with nothing to
     * show for it, and a failure has to say why. Errors stay until the next action
     * replaces them, because a message that vanished before it was read is the same as
     * no message.
     */
    private void say(String text, Kind kind) {
        // The banner sits above the fold, and two of the buttons that answer into it
        // are a screen below it: Save and Find portal. Without this, pressing Save
        // halfway down the settings would look like nothing at all happened.
        if (kind != Kind.INFO && pageStatus != null && pageStatus.getVisibility() == View.VISIBLE) {
            pageStatus.post(new Runnable() {
                @Override
                public void run() {
                    pageStatus.smoothScrollTo(0, 0);
                }
            });
        }
        message.setVisibility(View.VISIBLE);
        // An answer to a press stays; a note about what is happening does not. A
        // banner that says "Starting" five minutes after the start reads as a bug.
        messageTimer.removeCallbacks(hideMessage);
        if (kind == Kind.INFO) {
            messageTimer.postDelayed(hideMessage, INFO_MESSAGE_MILLIS);
        }
        switch (kind) {
            case OK:
                message.setText("\u2713  " + text);
                message.setTextColor(getColor(R.color.ok));
                break;
            case ERROR:
                message.setText("\u26a0  " + text);
                message.setTextColor(getColor(R.color.error));
                break;
            default:
                message.setText(text);
                message.setTextColor(getColor(R.color.accent));
                break;
        }
    }

    // MARK: - Permissions

    private void askForNotifications() {
        if (Build.VERSION.SDK_INT < 33) {
            return;
        }
        if (checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                == PackageManager.PERMISSION_GRANTED) {
            return;
        }
        requestPermissions(new String[] {"android.permission.POST_NOTIFICATIONS"}, 1);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != 1) {
            return;
        }
        boolean granted = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
        say(granted
                ? "Notifications allowed. The countdown will be in the status bar."
                : "Notifications are off, so the countdown will not show in the status bar. The "
                        + "watcher still runs.", granted ? Kind.OK : Kind.ERROR);
    }

    // MARK: - Small helpers

    private void sendService(String action) {
        Intent intent = new Intent(this, WatcherService.class);
        intent.setAction(action);
        startForegroundService(intent);
    }

    private static String text(EditText field) {
        return field.getText().toString().trim();
    }

    private static Integer parse(EditText field) {
        try {
            return Integer.valueOf(Integer.parseInt(field.getText().toString().trim()));
        } catch (NumberFormatException error) {
            return null;
        }
    }

    private void hideKeyboard() {
        View focus = getCurrentFocus();
        if (focus == null) {
            return;
        }
        InputMethodManager keyboard =
                (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
        if (keyboard != null) {
            keyboard.hideSoftInputFromWindow(focus.getWindowToken(), 0);
        }
        focus.clearFocus();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
