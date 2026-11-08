package vn.io.nhannht.dormwifihack.engine;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The engine, checked on a plain JVM with no phone attached.
 *
 * <p>This is the same idea as the Linux build's {@code python3 -m unittest
 * discover}: every rule that decides whether a sign-in works is a pure function of
 * bytes and strings, none of it needs Android, and none of it needs the hotspot. So
 * the golden values here come from the Python engine, which is the port's source of
 * truth, and a disagreement means one of the two ports has drifted.
 *
 * <p>Run it with {@code ./check.sh}. Failure prints the name of the check and the two
 * values that disagree, and the exit status is 1.
 */
public final class EngineChecks {

    private static int passed;
    private static final List<String> failures = new ArrayList<String>();

    /** The status page this hotspot actually serves, trimmed to its table. */
    private static final String STATUS_PAGE =
            "<html><head><title>Status - INET</title>"
            + "<meta http-equiv=\"refresh\" content=\"60\"></head>"
            + "<body><div class=\"status-card\"><table>"
            + "<tr><td>\u0110\u1ecba ch\u1ec9 IP</td><td>192.168.0.42</td></tr>"
            + "<tr><td>\u0110\u1ecba ch\u1ec9 MAC</td><td>00:00:5E:00:53:2A</td></tr>"
            + "<tr><td>Th\u1eddi gian k\u1ebft n\u1ed1i</td><td>2m35s</td></tr>"
            + "<tr><td>Th\u1eddi gian c\u00f2n l\u1ea1i</td><td>12m25s</td></tr>"
            + "</table></div></body></html>";

    private static final String STATUS_URL = "http://192.168.200.1/status";

    private static final String LOGIN_PAGE =
            "<form action=\"alogin.html\"><input name=\"username\" value=\"u\">"
            + "<input name=\"dst\" value=\"http://captive.apple.com/hotspot-detect.html\">"
            + "<input name=\"chap-id\" value=\"\\061\">"
            + "<input name=\"chap-challenge\" value=\"\\100\\277\"></form>";

    public static void main(String[] args) {
        chapterChap();
        sessions();
        pacing();
        pages();
        urls();
        settings();
        building();
        json();
        discovery();
        wording();
        status();

        System.out.println();
        System.out.println(passed + " passed, " + failures.size() + " failed");
        for (String failure : failures) {
            System.out.println("  FAIL " + failure);
        }
        if (!failures.isEmpty()) {
            System.exit(1);
        }
    }

    // MARK: - The CHAP hash, against the same values the Python build produces

    private static void chapterChap() {
        equal("plain md5 of password", "5f4dcc3b5aa765d61d8327deb882cf99", Chap.plainMd5("password"));
        equal("octal escape", "68d1", hex(Chap.decode("\\150\\321")));
        equal("percent-encoded backslash", "6c", hex(Chap.decode("%5C154")));
        equal("hash with octal challenge", "299ea2123a50524883a217971f216031",
                Chap.hashPassword("secret", "abc", "\\061\\062"));
        equal("hash with percent-encoded challenge", "d7f5abba7791127fa0e5c488d7d45851",
                Chap.hashPassword("demo", "%5C061%5C143", "%5C100%5C277"));

        // A backslash that is not an escape is a literal one.
        equal("a backslash that is not an escape stays literal", "5c317832",
                hex(Chap.decode("\\1x2")));
        // ... but three octal digits that cannot be a byte are an error, not a literal.
        expectParseError("octal escape out of range", "\\777");
    }

    private static void expectParseError(String name, String text) {
        try {
            Chap.decode(text);
            failures.add(name + ": expected a ParseError, got none");
        } catch (Chap.ParseError error) {
            equal(name + " digits", text.substring(1), error.digits);
        }
    }

    // MARK: - Reading the status page

    private static void sessions() {
        SessionProbe.Status active = SessionProbe.interpret(STATUS_PAGE, STATUS_URL);
        equal("status page is active", SessionProbe.ACTIVE, active.kind);
        equal("ip address", "192.168.0.42", active.info.ipAddress);
        equal("mac address", "00:00:5E:00:53:2A", active.info.macAddress);
        equal("connected for", Long.valueOf(155), active.info.connectedFor);
        equal("remaining", Long.valueOf(745), active.info.remaining);
        // 2m35s used plus 12m25s left is exactly the 15 minute session.
        equal("used plus left", Long.valueOf(900),
                Long.valueOf(active.info.connectedFor + active.info.remaining));

        SessionProbe.Status english = SessionProbe.interpret(
                "<table><tr><td>Uptime</td><td>1h2m3s</td></tr></table>", STATUS_URL);
        equal("stock english page", SessionProbe.ACTIVE, english.kind);
        equal("uptime", Long.valueOf(3723), english.info.connectedFor);

        equal("redirect to login is signed out", SessionProbe.EXPIRED,
                SessionProbe.interpret("<html></html>", "http://192.168.200.1/login?dst=x").kind);

        equal("fresh challenge is signed out", SessionProbe.EXPIRED,
                SessionProbe.interpret("<form><input id=\"chap-challenge\" value=\"\\044\"></form>",
                        STATUS_URL).kind);

        // Captured at a real session boundary: the router answers, but the session
        // rows are simply gone. Reading that as an unreachable router made the
        // watcher rejoin the wireless network at every boundary.
        String lapse = "<html><head><title>Status - INET</title></head>"
                + "<body><div class=\"status-card\"><table></table></div></body></html>";
        equal("page served at a lapse is unknown", SessionProbe.UNKNOWN,
                SessionProbe.interpret(lapse, STATUS_URL).kind);

        // A status page whose JavaScript happens to name the challenge is still an
        // open session. Getting this wrong reported a live session as signed out on
        // every pass and sent the watcher into a login loop.
        String scripted = "<html><script>var s = 'chap-challenge';</script><body><table>"
                + "<tr><td>Uptime</td><td>2m35s</td></tr></table></body></html>";
        equal("script bodies do not count", SessionProbe.ACTIVE,
                SessionProbe.interpret(scripted, STATUS_URL).kind);

        equal("duration 12m25s", Long.valueOf(745), SessionProbe.duration("12m25s"));
        equal("duration 1h2m3s", Long.valueOf(3723), SessionProbe.duration("1h2m3s"));
        equal("duration 45s", Long.valueOf(45), SessionProbe.duration("45s"));
        equal("duration 1d2h3m4s", Long.valueOf(93784), SessionProbe.duration("1d2h3m4s"));
        equal("duration 0s", Long.valueOf(0), SessionProbe.duration("0s"));
        equal("duration refuses twenty digits", null,
                SessionProbe.duration("999999999999999999999s"));
        equal("duration refuses words", null, SessionProbe.duration("abc"));
        equal("duration refuses empty", null, SessionProbe.duration(""));
    }

    private static void pacing() {
        equal("a long session naps at the ceiling", 60, SessionPacing.napWhileActive(745));
        equal("the boundary naps short", 2, SessionPacing.napWhileActive(5));
        equal("inside the boundary naps short", 2, SessionPacing.napWhileActive(4));
        equal("one second left still naps", 2, SessionPacing.napWhileActive(1));
        equal("sixty seconds left naps", 55, SessionPacing.napWhileActive(60));
    }

    // MARK: - The HTML scraper

    private static void pages() {
        equal("chap-id", "\\061", HtmlScraper.valueIdentifiedBy("chap-id", LOGIN_PAGE));
        equal("chap-challenge", "\\100\\277",
                HtmlScraper.valueIdentifiedBy("chap-challenge", LOGIN_PAGE));
        equal("username", "u", HtmlScraper.valueNamed("username", LOGIN_PAGE));
        equal("form action", "alogin.html", HtmlScraper.formAction(LOGIN_PAGE));

        List<String[]> items = HtmlScraper.queryItems(LOGIN_PAGE);
        equal("four fields with values", 4, items.size());
        equal("first field key", "username", items.get(0)[0]);

        // A key that appears twice keeps its first position and its last value.
        List<String[]> repeated = HtmlScraper.queryItems(
                "<input name=\"a\" value=\"1\"><input name=\"b\" value=\"2\">"
                + "<input name=\"a\" value=\"3\">");
        equal("repeated key keeps one entry", 2, repeated.size());
        equal("repeated key keeps the last value", "3", repeated.get(0)[1]);

        // An input with no name is sent under its id, which is the rule the router's
        // own page relies on.
        List<String[]> byId = HtmlScraper.queryItems("<input id=\"only-id\" value=\"v\">");
        equal("unnamed input falls back to its id", "only-id", byId.get(0)[0]);

        equal("a page with no form has no action", null,
                HtmlScraper.formAction("<html><body>nothing</body></html>"));
        equal("single-quoted values are read", "x",
                HtmlScraper.valueNamed("n", "<input name='n' value='x'>"));
        equal("a missing value is null", null,
                HtmlScraper.valueNamed("n", "<input name=\"n\">"));
    }

    // MARK: - Building the URLs the sequence fetches

    private static void urls() {
        Settings settings = new Settings();
        settings.gatewayIp = "192.168.200.1";
        LoginEngine engine = new LoginEngine(settings, new ActivityLog(null));

        equal("a form action resolves against the router's login page",
                "http://192.168.200.1/alogin.html?username=u"
                        + "&dst=http%3A%2F%2Fcaptive.apple.com%2Fhotspot-detect.html"
                        + "&chap-id=%5C061&chap-challenge=%5C100%5C277",
                callPortalUrl(engine, LOGIN_PAGE));

        Settings fallback = new Settings();
        fallback.gatewayIp = "192.168.200.1";
        fallback.portalFallbackUrl = "http://v1.awingconnect.vn";
        LoginEngine fallbackEngine = new LoginEngine(fallback, new ActivityLog(null));
        equal("the fallback address carries the hidden fields",
                "http://v1.awingconnect.vn?mac=aa%3Abb",
                callPortalUrl(fallbackEngine, "<html><input name=\"mac\" value=\"aa:bb\"></html>"));

        equal("a rooted action keeps the router", "http://192.168.200.1/x/y",
                callResolve(engine, "/x/y"));
        equal("a scheme-less host action is http", "http://h/p", callResolve(engine, "//h/p"));
        equal("an absolute action is the portal naming itself", "http://o/z",
                callResolve(engine, "http://o/z"));
        equal("a bare file name joins the login page's directory",
                "http://192.168.200.1/alogin.html", callResolve(engine, "alogin.html"));

        portalUrlExpectError("no portal and no fallback says so",
                new Settings().withGatewayIp("192.168.200.1"), "<html></html>");

        // The form encoding the router expects: a space is a `+`, and `*` is not
        // escaped, because an over-escaped value is a different value.
        List<String[]> fields = new ArrayList<String[]>();
        fields.add(new String[] {"dst", "http://captive.apple.com/hotspot-detect.html"});
        fields.add(new String[] {"popup", "false"});
        equal("form body", "dst=http%3A%2F%2Fcaptive.apple.com%2Fhotspot-detect.html&popup=false",
                PortalClient.formEncoded(fields));

        List<String[]> query = new ArrayList<String[]>();
        query.add(new String[] {"q", "a b*c~"});
        equal("query uses %20 and leaves * and ~", "q=a%20b*c~",
                PortalClient.percentEncoded(query));
    }

    private static void portalUrlExpectError(String name, Settings settings, String html) {
        LoginEngine engine = new LoginEngine(settings, new ActivityLog(null));
        try {
            engine.portalUrlFrom(html);
            failures.add(name + ": expected an EngineError, got none");
        } catch (EngineError error) {
            check(name, error.description.contains("names no portal"));
        }
    }

    private static String callPortalUrl(LoginEngine engine, String html) {
        try {
            return engine.portalUrlFrom(html);
        } catch (EngineError error) {
            throw new IllegalStateException(error.description, error);
        }
    }

    private static String callResolve(LoginEngine engine, String action) {
        try {
            return engine.resolveAction(action);
        } catch (EngineError error) {
            throw new IllegalStateException(error.description, error);
        }
    }

    // MARK: - Settings

    private static void settings() {
        String json = "{\n"
                + "  \"wifi_name\": \"INET - Free WiFi\",\n"
                + "  \"interval\": 15,\n"
                + "  \"auto_connect\": true,\n"
                + "  \"portal_fallback_url\": \"http://v1.awingconnect.vn\",\n"
                + "  \"last_run_timestamp\": 1757300000.5,\n"
                + "  \"selected_version\": \"script-2\",\n"
                + "  \"gemini_api_key\": \"kept\",\n"
                + "  \"portal_endpoints\": {\"verifyURL\": \"/Home/VerifyUrl\"}\n"
                + "}";

        Settings loaded = Settings.fromJson(json);
        equal("wifi name", "INET - Free WiFi", loaded.wifiName);
        equal("interval", 15, loaded.interval);
        equal("auto connect", true, loaded.autoConnect);
        equal("fallback address", "http://v1.awingconnect.vn", loaded.portalFallbackUrl);
        equal("timestamp", 1757300000.5, loaded.lastRunTimestamp);
        equal("session timeout in seconds", 900, loaded.sessionTimeoutSeconds());

        Settings reread = Settings.fromJson(loaded.toJsonText());
        equal("a key this build does not model is carried through", "script-2",
                String.valueOf(reread.unknownKeys.get("selected_version")));
        equal("and another one", "kept", String.valueOf(reread.unknownKeys.get("gemini_api_key")));
        equal("the portal endpoint survives", "/Home/VerifyUrl",
                reread.portalEndpoints.verifyUrl);
        equal("the empty endpoint falls back", "/Content/GetCustomer",
                reread.portalEndpoints.getCustomer);

        Settings absurd = Settings.fromJson("{\"interval\": 5000}");
        equal("an absurd interval is clamped to a day", 86400, absurd.sessionTimeoutSeconds());
        Settings zero = Settings.fromJson("{\"interval\": 0}");
        equal("a zero interval is clamped to a minute", 60, zero.sessionTimeoutSeconds());

        Settings defaults = Settings.fromJson("");
        equal("an empty file is defaults", 15, defaults.interval);
        equal("and the destination default", "http://captive.apple.com/hotspot-detect.html",
                defaults.destinationUrl);

        try {
            Settings.fromJson("[1,2,3]");
            failures.add("an array should not load as settings");
        } catch (IllegalArgumentException expected) {
            passed++;
        }
    }

    private static void building() {
        // The portal nests its login form at one of three depths; all have to work.
        Map<String, Object> deep = new LinkedHashMap<String, Object>();
        deep.put("contentAuthenForm", "<input name=\"username\" value=\"u\">");
        Map<String, Object> context = new LinkedHashMap<String, Object>();
        context.put("captiveContext", deep);
        Map<String, Object> root = new LinkedHashMap<String, Object>();
        root.put("captiveContextDTO", context);
        equal("a three-deep form is found", "<input name=\"username\" value=\"u\">",
                LoginEngine.authenticationForm(root));

        Map<String, Object> shallow = new LinkedHashMap<String, Object>();
        shallow.put("contentAuthenForm", "<input name=\"username\" value=\"v\">");
        equal("a one-deep form is found", "<input name=\"username\" value=\"v\">",
                LoginEngine.authenticationForm(shallow));

        equal("nothing is not a form", null, LoginEngine.authenticationForm(root.get("nope")));

        equal("private range 10", true, PortalClient.isPrivateHost("10.0.0.1"));
        equal("private range 192.168", true, PortalClient.isPrivateHost("192.168.200.1"));
        equal("private range 172.16", true, PortalClient.isPrivateHost("172.16.0.1"));
        equal("link local", true, PortalClient.isPrivateHost("169.254.1.1"));
        equal("a public address", false, PortalClient.isPrivateHost("8.8.8.8"));
        // A name that merely contains a private range is not a private host. Reading
        // it as one would accept any certificate an attacker on the captive network
        // presented.
        equal("a hostname dressed up as an address", false,
                PortalClient.isPrivateHost("10.0.0.1.evil.com"));
        equal("five labels", false, PortalClient.isPrivateHost("1.2.3.4.5"));
        equal("a hex octet", false, PortalClient.isPrivateHost("0x0a.0.0.1"));
    }

    private static void json() {
        Map<String, Object> source = new LinkedHashMap<String, Object>();
        source.put("a", "quote \" and backslash \\ and newline \n");
        source.put("b", Long.valueOf(15));
        source.put("c", Double.valueOf(1757300000.5));
        source.put("d", Boolean.TRUE);
        List<Object> list = new ArrayList<Object>();
        list.add("x");
        list.add(Long.valueOf(2));
        source.put("e", list);
        source.put("f", null);

        Map<String, Object> reread = Json.parseObject(Json.write(source));
        equal("a string with escapes", source.get("a"), reread.get("a"));
        equal("an integer stays an integer", Long.valueOf(15), reread.get("b"));
        equal("a fraction stays a fraction", Double.valueOf(1757300000.5), reread.get("c"));
        equal("a boolean", Boolean.TRUE, reread.get("d"));
        equal("a list", 2, ((List<?>) reread.get("e")).size());
        equal("a null", null, reread.get("f"));
        equal("an empty object", 0, Json.parseObject("{}").size());

        try {
            Json.parse("{");
            failures.add("an unclosed object should not parse");
        } catch (IllegalArgumentException expected) {
            passed++;
        }
    }

    private static void discovery() {
        List<String[]> urls = new ArrayList<String[]>();
        urls.add(new String[] {"http://192.168.200.1/alogin.html", "page"});
        urls.add(new String[] {"http://captive.apple.com/hotspot-detect.html", "field"});
        urls.add(new String[] {"http://v1.awingconnect.vn/login?serial=1", "page"});
        urls.add(new String[] {"http://v1.awingconnect.vn/app.js", "page"});

        List<String> candidates = Discover.rankedCandidates(urls, "192.168.200.1");
        equal("only another host can be the portal", 1, candidates.size());
        equal("and it is the portal", "http://v1.awingconnect.vn", candidates.get(0));

        Discover.Finding finding = Discover.inspect(LOGIN_PAGE, "192.168.200.1");
        equal("the page is a login page", true, finding.looksLikeLoginPage);
        equal("with a challenge", Boolean.TRUE, Boolean.valueOf(finding.hasChapId));
        equal("of two bytes", Integer.valueOf(2), finding.challengeBytes);
        equal("and a form action", "alogin.html", finding.formAction);
    }

    private static void wording() {
        equal("seconds", "45s", ErrorText.duration(45));
        equal("minutes", "12m 25s", ErrorText.duration(745));
        equal("hours", "1h 2m", ErrorText.duration(3723));
        equal("a day", "1 day", ErrorText.duration(86400));
        equal("days", "3 days", ErrorText.duration(3 * 86400));
        equal("nothing is zero seconds", "0s", ErrorText.duration(-5));
    }

    private static void status() {
        WatcherStatus signedIn = new WatcherStatus();
        signedIn.connection = WatcherStatus.SIGNED_IN;
        signedIn.expiresAt = Long.valueOf(System.currentTimeMillis() + 745_000L);
        check("a signed-in headline counts down", signedIn.headline().startsWith("Signed in, 12m"));
        check("a signed-in status is healthy", signedIn.isHealthy());

        WatcherStatus stale = new WatcherStatus();
        stale.connection = WatcherStatus.SIGNED_IN;
        stale.updated = System.currentTimeMillis() - 200_000L;
        check("two hundred seconds old is stale", stale.isStale());

        WatcherStatus paused = new WatcherStatus();
        paused.connection = WatcherStatus.STOPPED;
        equal("paused says so", "Paused", paused.headline());

        WatcherStatus wrong = new WatcherStatus();
        wrong.connection = WatcherStatus.WRONG_NETWORK;
        wrong.network = "INET - Free WiFi";
        equal("wrong network names the network", "Not on INET - Free WiFi", wrong.headline());

        WatcherStatus noExpiry = new WatcherStatus();
        noExpiry.connection = WatcherStatus.SIGNED_IN;
        equal("no reported deadline says signed in", "Signed in", noExpiry.headline());
    }

    // MARK: - The harness

    private static void check(String name, boolean condition) {
        if (condition) {
            passed++;
        } else {
            failures.add(name);
        }
    }

    private static void equal(String name, Object expected, Object actual) {
        boolean same = expected == null ? actual == null : expected.equals(actual);
        if (same) {
            passed++;
        } else {
            failures.add(name + "\n         expected: " + expected + "\n         actual:   " + actual);
        }
    }

    private static String hex(byte[] data) {
        StringBuilder out = new StringBuilder();
        for (byte value : data) {
            out.append(Character.forDigit((value >> 4) & 0xF, 16));
            out.append(Character.forDigit(value & 0xF, 16));
        }
        return out.toString();
    }
}
