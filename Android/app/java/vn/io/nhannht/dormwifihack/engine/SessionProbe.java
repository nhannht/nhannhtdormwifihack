package vn.io.nhannht.dormwifihack.engine;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Asks the router whether we are signed in, and for how much longer.
 *
 * <p>This has to run before anything else. On this hotspot {@code /login} does not
 * answer at all while a session is open: it accepts the connection and then hangs
 * until the client gives up. Only {@code /status} is safe to ask first, and it
 * replies in about 15ms.
 */
public final class SessionProbe {

    /** The largest value worth believing. A page that says "999999999999s" is not a duration. */
    private static final long MAX_DURATION = Long.MAX_VALUE / 4;

    // Both the Vietnamese page this was built against and the stock MikroTik
    // English one, so a firmware or language change does not blind it.
    private static final String[] IP_LABELS = {"\u0111\u1ecba ch\u1ec9 ip", "ip address", "ip"};
    private static final String[] MAC_LABELS = {"\u0111\u1ecba ch\u1ec9 mac", "mac address", "mac"};
    private static final String[] CONNECTED_LABELS = {
        "th\u1eddi gian k\u1ebft n\u1ed1i", "connected", "uptime"
    };
    private static final String[] REMAINING_LABELS = {
        "th\u1eddi gian c\u00f2n l\u1ea1i", "time left", "session time left"
    };

    private static final Pattern SCRIPT_OR_STYLE =
            Pattern.compile("<(script|style)\\b[^>]*>[\\s\\S]*?</\\1>", Pattern.CASE_INSENSITIVE);
    private static final Pattern ANY_TAG = Pattern.compile("<[^>]+>");

    public static final String ACTIVE = "active";
    public static final String EXPIRED = "expired";
    public static final String UNKNOWN = "unknown";
    public static final String UNREACHABLE = "unreachable";

    private SessionProbe() {
    }

    /** What the router says about the current session. */
    public static final class SessionInfo {

        public final String ipAddress;
        public final String macAddress;
        /** How long this session has been up, in seconds. */
        public final Long connectedFor;
        /**
         * How long it has left, in seconds. The router publishes this, so there is
         * no need to guess from a local timer.
         */
        public final Long remaining;

        SessionInfo(String ipAddress, String macAddress, Long connectedFor, Long remaining) {
            this.ipAddress = ipAddress;
            this.macAddress = macAddress;
            this.connectedFor = connectedFor;
            this.remaining = remaining;
        }
    }

    /** What one look at {@code /status} found. */
    public static final class Status {

        public final String kind;
        public final SessionInfo info;
        public final String why;

        Status(String kind, SessionInfo info, String why) {
            this.kind = kind;
            this.info = info;
            this.why = why == null ? "" : why;
        }

        public boolean isActive() {
            return ACTIVE.equals(kind);
        }

        public boolean isExpired() {
            return EXPIRED.equals(kind);
        }
    }

    public static Status status(String gatewayIp, PortalClient client) {
        return status(gatewayIp, client, 5.0);
    }

    public static Status status(String gatewayIp, PortalClient client, double timeoutSeconds) {
        if (gatewayIp == null || gatewayIp.isEmpty()) {
            return new Status(UNREACHABLE, null, "Cannot build a status URL for an empty address");
        }

        PortalClient.Response response;
        try {
            response = client.get("http://" + gatewayIp + "/status", null, timeoutSeconds);
        } catch (Exception error) {
            // A transport failure is the router not being there, which is a
            // different problem from a page that carries no session.
            return new Status(UNREACHABLE, null, ErrorText.shortText(error));
        }

        if (!response.isSuccess()) {
            return new Status(UNREACHABLE, null, "Status page answered HTTP " + response.statusCode);
        }
        return interpret(response.body(), response.finalUrl);
    }

    /** Read one status page. */
    public static Status interpret(String body, String finalUrl) {
        // Being sent to the login page settles it immediately.
        if (pathOf(finalUrl).toLowerCase(Locale.US).contains("login")) {
            return new Status(EXPIRED, null, "");
        }

        // Script and style bodies go before anything is read out of the page, so a
        // status page whose JavaScript happens to name the challenge is not read as
        // a login page. Getting that wrong reported an open session as signed out
        // on every pass, which sent the watcher into a login loop.
        String visible = withoutScriptsAndStyles(body == null ? "" : body);

        String lower = visible.toLowerCase(Locale.US);
        if (lower.contains("chap-challenge") || lower.contains("chap-id")) {
            return new Status(EXPIRED, null, "");
        }

        List<String> fields = textFields(visible);
        SessionInfo info = new SessionInfo(
                valueFor(IP_LABELS, fields),
                valueFor(MAC_LABELS, fields),
                duration(valueFor(CONNECTED_LABELS, fields)),
                duration(valueFor(REMAINING_LABELS, fields)));

        // A session page names at least one of these. A login page names none.
        if (info.remaining != null || info.connectedFor != null || info.macAddress != null) {
            return new Status(ACTIVE, info, "");
        }

        if (lower.contains("uptime") || lower.contains("bytes in")) {
            return new Status(ACTIVE, info, "");
        }
        if (lower.contains("<form") && lower.contains("password")) {
            return new Status(EXPIRED, null, "");
        }

        return new Status(UNKNOWN, null, "Status page did not identify itself");
    }

    private static String pathOf(String url) {
        if (url == null) {
            return "";
        }
        try {
            String path = new java.net.URL(url).getPath();
            return path == null ? "" : path;
        } catch (java.net.MalformedURLException error) {
            return url;
        }
    }

    /** Everything between a {@code script} or {@code style} tag and its close, removed. */
    public static String withoutScriptsAndStyles(String html) {
        return SCRIPT_OR_STYLE.matcher(html).replaceAll(" ");
    }

    /** The page is a two column table, so the value is the cell after the label. */
    public static List<String> textFields(String html) {
        String text = ANY_TAG.matcher(withoutScriptsAndStyles(html)).replaceAll("\u0001");
        List<String> fields = new ArrayList<String>();
        for (String field : text.split("\u0001", -1)) {
            String trimmed = field.trim();
            if (!trimmed.isEmpty()) {
                fields.add(trimmed);
            }
        }
        return fields;
    }

    /**
     * {@code 12m25s}, {@code 1h2m3s}, {@code 45s}, {@code 1d2h3m4s} into seconds.
     *
     * <p>Every step is bounds-checked and a number too large to hold is rejected.
     * This text arrives from the router over plain HTTP, so a run of twenty digits
     * is something the page can genuinely say, and an unchecked answer would make
     * the watcher sleep forever.
     */
    public static Long duration(String text) {
        if (text == null || text.isEmpty()) {
            return null;
        }

        long total = 0;
        long number = 0;
        boolean sawAnyUnit = false;

        String lower = text.toLowerCase(Locale.US);
        for (int index = 0; index < lower.length(); index++) {
            char character = lower.charAt(index);
            if (character >= '0' && character <= '9') {
                number = number * 10 + (character - '0');
                if (number > MAX_DURATION) {
                    return null;
                }
                continue;
            }
            long unit;
            switch (character) {
                case 'd':
                    unit = 86400;
                    break;
                case 'h':
                    unit = 3600;
                    break;
                case 'm':
                    unit = 60;
                    break;
                case 's':
                    unit = 1;
                    break;
                default:
                    if (Character.isWhitespace(character)) {
                        continue;
                    }
                    return null;
            }
            if (number > MAX_DURATION / unit) {
                return null;
            }
            total += number * unit;
            if (total > MAX_DURATION) {
                return null;
            }
            number = 0;
            sawAnyUnit = true;
        }

        return sawAnyUnit ? Long.valueOf(total) : null;
    }

    private static String valueFor(String[] labels, List<String> fields) {
        for (int index = 0; index < fields.size(); index++) {
            String normalized = strip(fields.get(index).toLowerCase(Locale.US));
            for (String label : labels) {
                if (normalized.equals(label) && index + 1 < fields.size()) {
                    return fields.get(index + 1);
                }
            }
        }
        return null;
    }

    /** Lowercase and drop the punctuation a label carries but a comparison does not. */
    private static String strip(String text) {
        int start = 0;
        int end = text.length();
        while (start < end && trimCharacter(text.charAt(start))) {
            start++;
        }
        while (end > start && trimCharacter(text.charAt(end - 1))) {
            end--;
        }
        return text.substring(start, end);
    }

    private static boolean trimCharacter(char character) {
        return character == ':' || character == ' ' || character == '\t' || character == '\u00a0';
    }
}
