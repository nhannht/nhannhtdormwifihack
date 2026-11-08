package vn.io.nhannht.dormwifihack.engine;

import java.io.IOException;
import java.io.UnsupportedEncodingException;
import java.net.MalformedURLException;
import java.net.URL;
import java.net.URLDecoder;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;


/**
 * Logs into the hotspot.
 *
 * <p>Three steps. The router's login page carries a CHAP challenge; the portal in
 * front of it hands out the account to use; the router accepts that account hashed
 * against the challenge. The portal's own web page does all this in JavaScript
 * after showing an advert, and this does the same calls directly.
 */
public final class LoginEngine {

    // `/login` takes 1.37s to first byte even when it is working, while every other
    // endpoint on this router answers in about 12ms. A tight timeout turns a
    // working sign-in into a failure.
    static final double GATEWAY_TIMEOUT = 8.0;
    static final int GATEWAY_ATTEMPTS = 3;

    // MARK: - The result of one attempt

    public static final String LOGGED_IN = "loggedIn";
    public static final String ALREADY_ACTIVE = "alreadyActive";
    public static final String INVALID_PASSWORD = "invalidPassword";
    public static final String SESSION_LIMIT = "sessionLimit";
    public static final String FAILED = "failed";

    /** How a login attempt ended. */
    public static final class LoginResult {

        public final String kind;
        public final String reason;

        public LoginResult(String kind) {
            this(kind, "");
        }

        public LoginResult(String kind, String reason) {
            this.kind = kind;
            this.reason = reason == null ? "" : reason;
        }

        public boolean isConnected() {
            return LOGGED_IN.equals(kind) || ALREADY_ACTIVE.equals(kind);
        }

        public String summary() {
            if (LOGGED_IN.equals(kind)) {
                return "Logged in.";
            }
            if (ALREADY_ACTIVE.equals(kind)) {
                return "Already connected, session still open.";
            }
            if (INVALID_PASSWORD.equals(kind)) {
                return "The router rejected the password.";
            }
            if (SESSION_LIMIT.equals(kind)) {
                return "Too many sessions open on this account.";
            }
            return "Failed: " + reason;
        }
    }

    // MARK: - What the login page holds

    static final class GatewayPage {

        final String chapId;
        final String chapChallenge;
        final String portalUrl;

        GatewayPage(String chapId, String chapChallenge, String portalUrl) {
            this.chapId = chapId;
            this.chapChallenge = chapChallenge;
            this.portalUrl = portalUrl;
        }
    }

    static final class Credentials {

        final String username;
        final String password;
        final String dst;
        final String popup;

        Credentials(String username, String password, String dst, String popup) {
            this.username = username;
            this.password = password;
            this.dst = dst;
            this.popup = popup;
        }
    }

    private final Settings settings;
    private final ActivityLog log;
    private final InternetProbe probe;

    public LoginEngine(Settings settings, ActivityLog log) {
        this(settings, log, new InternetProbe.DirectSocketProbe());
    }

    public LoginEngine(Settings settings, ActivityLog log, InternetProbe probe) {
        this.settings = settings;
        this.log = log;
        this.probe = probe;
    }

    // MARK: - The whole sequence

    /**
     * Sign in, unless a session is already open.
     *
     * <p>Asking {@code /status} first is not an optimisation, it is the only safe
     * order. While a session is open this router accepts the connection to
     * {@code /login} and then never answers, so a sign-in attempt in that state
     * cannot succeed: it can only stall until the timeout and be reported as a
     * failure. The caller then treats a perfectly healthy connection as broken and
     * starts tearing it down.
     *
     * <p>Never sign out here either. Ending the session puts the router in the same
     * non-answering state with no way back in.
     */
    public LoginResult run() {
        return run(new PortalClient());
    }

    public LoginResult run(PortalClient client) {
        SessionProbe.Status answer = SessionProbe.status(settings.gatewayIp, client);
        if (SessionProbe.ACTIVE.equals(answer.kind)) {
            String left = answer.info != null && answer.info.remaining != null
                    ? ", " + ErrorText.duration(answer.info.remaining) + " left"
                    : "";
            log.info("Already signed in" + left + ".");
            return new LoginResult(ALREADY_ACTIVE);
        }

        if (SessionProbe.UNREACHABLE.equals(answer.kind)) {
            // Nothing is listening on this address. Asking it for a login page three
            // times over cannot change that, and the answer the caller needs is "you
            // are not on that network", not "the login failed".
            log.failure("The router at " + settings.gatewayIp + " did not answer: " + answer.why);
            return new LoginResult(FAILED, "No hotspot router at " + settings.gatewayIp);
        }

        try {
            log.info("Step 1 of 3: asking the router for its login page");
            PortalClient.Response page = fetchLoginPage(client);

            GatewayPage gateway;
            Credentials credentials;
            if (!settings.username.isEmpty()) {
                // The account is in the settings, so there is no portal to ask and
                // nothing to approve. The portal's address is not resolved at all
                // here: on a hotspot that intercepts every request and resets TLS
                // while captive, the portal cannot be reached from the side that
                // needs it, and demanding its address would make a known account
                // useless.
                String[] challenge = readChallenge(page);
                gateway = new GatewayPage(challenge[0], challenge[1], "");
                log.info("Step 2 of 3: using the account from the settings");
                credentials = new Credentials(settings.username, settings.password,
                        settings.destinationUrl, "false");
            } else {
                gateway = gatewayPage(page);
                log.info("Step 2 of 3: collecting an account from the portal");
                credentials = fetchCredentials(gateway.portalUrl, client);
                log.info("Portal issued the account " + credentials.username);
            }

            log.info("Step 3 of 3: signing in to the router");
            return signIn(gateway, credentials, client);
        } catch (EngineError error) {
            log.failure(error.description);
            return new LoginResult(FAILED, error.description);
        } catch (Exception error) {
            String reason = ErrorText.shortText(error);
            log.failure(reason);
            return new LoginResult(FAILED, reason);
        }
    }

    /** End the current session, so a fresh one can be started. */
    public void signOut() {
        PortalClient client = new PortalClient();
        String target = settings.logoutUrl.isEmpty()
                ? "http://" + settings.gatewayIp + "/logout"
                : settings.logoutUrl;
        try {
            client.get(target, null, 5.0);
        } catch (Exception ignored) {
            // Logging out is best effort.
        }
    }

    // MARK: - Step 1

    /**
     * The router's login page, and nothing read out of it yet.
     *
     * <p>Separate from {@link #gatewayPage} because the two things that can go wrong
     * here are worth telling apart. A page that answers but names no portal is the
     * page a person needs to look at, which is what the Discover action does.
     */
    public PortalClient.Response fetchLoginPage(PortalClient client) throws EngineError {
        if (settings.gatewayIp == null || settings.gatewayIp.isEmpty()) {
            throw EngineError.badUrl(settings.gatewayIp == null ? "" : settings.gatewayIp);
        }

        List<String[]> query = new ArrayList<String[]>();
        query.add(new String[] {"dst", settings.destinationUrl});
        String url = "http://" + settings.gatewayIp + "/login?" + PortalClient.percentEncoded(query);

        PortalClient.Response page = null;
        boolean answeredWithAnotherPage = false;

        for (int attempt = 1; attempt <= GATEWAY_ATTEMPTS; attempt++) {
            try {
                PortalClient.Response response = client.get(url, null, GATEWAY_TIMEOUT);
                if (response.isSuccess() && looksLikeLoginPage(response.body())) {
                    page = response;
                    break;
                }
                if (response.isSuccess()) {
                    answeredWithAnotherPage = true;
                    log.warning("Router answered attempt " + attempt
                            + " with a page that is not a hotspot login, retrying");
                } else {
                    log.warning("Router answered attempt " + attempt + " with HTTP "
                            + response.statusCode + ", retrying");
                }
            } catch (Exception error) {
                log.warning("Router did not answer attempt " + attempt + ": "
                        + ErrorText.shortText(error));
            }
            if (attempt < GATEWAY_ATTEMPTS) {
                sleep(1000);
            }
        }

        if (page == null) {
            throw answeredWithAnotherPage
                    ? EngineError.gatewayNotALoginPage()
                    : EngineError.gatewayUnreachable(GATEWAY_ATTEMPTS);
        }
        return page;
    }

    /** The router's CHAP fields, and a line about whether it gave any. */
    String[] readChallenge(PortalClient.Response page) {
        String chapId = HtmlScraper.valueIdentifiedBy("chap-id", page.body());
        String chapChallenge = HtmlScraper.valueIdentifiedBy("chap-challenge", page.body());

        if (chapId != null && chapChallenge != null) {
            log.info("Router issued a CHAP challenge");
        } else {
            log.warning("Router issued no CHAP challenge; will send a plain hash");
        }
        return new String[] {chapId, chapChallenge};
    }

    /**
     * Read the challenge and the portal's address out of that page.
     *
     * <p>Only for the portal path. With an account in the settings the portal is
     * never contacted, so its address is not required — and demanding one anyway is
     * what made a hotspot with no portal on its login page impossible to use even
     * when the account was known.
     */
    GatewayPage gatewayPage(PortalClient.Response page) throws EngineError {
        String[] challenge = readChallenge(page);
        String portalUrl = portalUrlFrom(page.body());
        log.info("Portal is at " + hostOf(portalUrl));

        // Say out loud where that address came from. When the router names its own
        // portal we are reading the truth off the page. When it does not we are using
        // a guess from the settings. If the next step fails, this line is the
        // difference between suspecting the portal and suspecting the address we
        // invented for it.
        if (HtmlScraper.formAction(page.body()) == null) {
            log.warning("The router at " + settings.gatewayIp + " named no portal, so this is "
                    + "falling back to the portal address in the settings. That address has only "
                    + "been confirmed on one router of this hotspot, so if the next step fails, "
                    + "suspect it first.");
        }

        return new GatewayPage(challenge[0], challenge[1], portalUrl);
    }

    /** Where to send the browser next, with every hidden field carried along. */
    String portalUrlFrom(String html) throws EngineError {
        String action = HtmlScraper.formAction(html);
        List<String[]> items = HtmlScraper.queryItems(html);

        String base;
        if (action != null) {
            base = resolveAction(action);
        } else if (settings.portalFallbackUrl != null && !settings.portalFallbackUrl.isEmpty()) {
            base = settings.portalFallbackUrl;
            if (schemeOf(base) == null) {
                throw EngineError.badUrl(base);
            }
        } else {
            throw EngineError.noPortalUrl();
        }

        List<String[]> merged = new ArrayList<String[]>(queryFrom(base));
        merged.addAll(items);
        return rebuild(base, PortalClient.percentEncoded(merged));
    }

    /**
     * Turn the login form's {@code action} into a URL that can actually be fetched.
     *
     * <p>Only an action that carries its own scheme is the portal naming itself.
     * Everything else belongs to the router and is resolved against its login page,
     * a bare {@code alogin.html} included. Treating every action that did not begin
     * with a slash as absolute produced a URL with no scheme at all, which failed
     * several steps later as an unexplained "unsupported URL".
     */
    String resolveAction(String action) throws EngineError {
        // `//host/path` means "whatever scheme this page used", which is HTTP.
        if (action.startsWith("//")) {
            return "http:" + action;
        }
        if (schemeOf(action) != null) {
            return action;
        }
        try {
            return new URL(new URL("http://" + settings.gatewayIp + "/login"), action).toString();
        } catch (IOException error) {
            throw EngineError.badUrl(action);
        }
    }

    // MARK: - Step 2

    Credentials fetchCredentials(String portalUrl, PortalClient client) throws IOException, EngineError {
        // The first visit sets the cookies every later call depends on, and can
        // redirect to whichever host actually serves the portal.
        //
        // Five seconds, not ten. This request goes out at the exact instant the old
        // session lapsed, which is the worst moment to ask the router to pass traffic
        // to a host out on the internet, and it does sometimes hang. Retrying is free
        // and works; waiting is what costs the user.
        PortalClient.Response landing = client.get(portalUrl, null, 5.0);
        String finalUrl = landing.finalUrl;
        String baseUrl = originOf(finalUrl);

        Settings.PortalEndpoints endpoints = settings.portalEndpoints;

        PortalClient.Response verify = client.postJson(
                urljoin(baseUrl + "/", endpoints.verifyUrl),
                new LinkedHashMap<String, Object>(),
                finalUrl,
                baseUrl,
                10.0);
        if (!verify.isSuccess()) {
            throw EngineError.portalRejected("VerifyUrl", verify.statusCode);
        }
        Object context = verify.json();

        PortalClient.Response customerResponse = client.postJson(
                urljoin(baseUrl + "/", endpoints.getCustomer), context, finalUrl, baseUrl, 10.0);
        if (customerResponse.isSuccess()) {
            context = customerResponse.json();
        }

        Object customer = dig(context, "captiveContext", "customer");
        if (customer == null) {
            customer = new LinkedHashMap<String, Object>();
        }

        Map<String, Object> campaignBody = new LinkedHashMap<String, Object>();
        campaignBody.put("captiveContextDTO", context);
        campaignBody.put("updateCustomerFlag", Boolean.FALSE);
        campaignBody.put("customer", customer);

        PortalClient.Response campaign = client.postJson(
                urljoin(baseUrl + "/", endpoints.getCampaignHtml),
                campaignBody,
                finalUrl,
                baseUrl,
                10.0);
        if (!campaign.isSuccess()) {
            throw EngineError.portalRejected("GetCampaignHtml", campaign.statusCode);
        }

        String form = authenticationForm(campaign.json());
        if (form == null) {
            throw EngineError.noLoginForm();
        }

        String username = HtmlScraper.valueNamed("username", form);
        String password = HtmlScraper.valueNamed("password", form);
        if (username == null || password == null) {
            throw EngineError.noLoginForm();
        }

        // Telling the portal its advert was seen. It was not, but the portal holds
        // the account open based on this, so it is part of the flow.
        sendAnalytics(context, baseUrl, client);

        String dst = HtmlScraper.valueNamed("dst", form);
        String popup = HtmlScraper.valueNamed("popup", form);
        return new Credentials(
                username,
                password,
                dst == null || dst.isEmpty() ? settings.destinationUrl : dst,
                popup == null || popup.isEmpty() ? "false" : popup);
    }

    /** The portal nests the login form at one of three depths, so all are tried. */
    static String authenticationForm(Object payload) {
        String[][] paths = {
            {"captiveContextDTO", "captiveContext", "contentAuthenForm"},
            {"captiveContext", "contentAuthenForm"},
            {"contentAuthenForm"},
        };
        for (String[] path : paths) {
            Object found = dig(payload, path);
            if (found instanceof String && !((String) found).isEmpty()) {
                return (String) found;
            }
        }
        return null;
    }

    private void sendAnalytics(Object context, String baseUrl, PortalClient client) {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("captiveContextDTO", context);
        body.put("analyticType", "View");
        body.put("viewIndex", 1);
        try {
            client.postJson(
                    urljoin(baseUrl + "/", settings.portalEndpoints.analytic), body, null, baseUrl, 5.0);
        } catch (Exception ignored) {
            // The advert was not really watched anyway.
        }
    }

    // MARK: - Step 3

    LoginResult signIn(GatewayPage gateway, Credentials credentials, PortalClient client)
            throws EngineError, IOException {

        String url = "http://" + settings.gatewayIp + "/login";
        String password;

        if (gateway.chapId != null && gateway.chapChallenge != null) {
            try {
                password = Chap.hashPassword(credentials.password, gateway.chapId, gateway.chapChallenge);
            } catch (Chap.ParseError error) {
                // Quietly falling back to a plain hash here makes the router always
                // reject, and reports "invalid password" for a challenge it could
                // not read.
                log.failure("Could not read the router's challenge: " + error.description);
                return new LoginResult(FAILED, "Unreadable CHAP challenge: " + error.description);
            }
        } else {
            password = Chap.plainMd5(credentials.password);
        }

        List<String[]> fields = new ArrayList<String[]>();
        fields.add(new String[] {"username", credentials.username});
        fields.add(new String[] {"password", password});
        fields.add(new String[] {"dst", credentials.dst});
        // What the portal's own form asked for, not a fixed guess.
        fields.add(new String[] {"popup", credentials.popup});

        PortalClient.Response response = client.postForm(url, fields, null, null, 15.0);
        return interpretSignIn(response, client);
    }

    LoginResult interpretSignIn(PortalClient.Response response, PortalClient client) {
        String body = response.body().toLowerCase(Locale.US);

        if (body.contains("invalid password") || body.contains("invalid username")) {
            return new LoginResult(INVALID_PASSWORD);
        }
        if (body.contains("session limit") || body.contains("too many")) {
            return new LoginResult(SESSION_LIMIT);
        }

        // Landing on the connectivity check page means the router let us out.
        if (response.finalUrl.contains("captive.apple.com") || body.contains("<title>success</title>")) {
            return new LoginResult(LOGGED_IN);
        }

        // Otherwise ask the router directly rather than guessing from the page.
        SessionProbe.Status answer = SessionProbe.status(settings.gatewayIp, client);
        if (SessionProbe.ACTIVE.equals(answer.kind)) {
            return new LoginResult(LOGGED_IN);
        }
        if (SessionProbe.EXPIRED.equals(answer.kind)) {
            return new LoginResult(FAILED, "The router still shows us as signed out.");
        }

        // Last resort: is there actually internet?
        if (probe.hasInternet(2.0)) {
            return new LoginResult(LOGGED_IN);
        }
        return new LoginResult(FAILED, "Signed in with no confirmation, and there is still no internet.");
    }

    // MARK: - Small helpers

    static boolean looksLikeLoginPage(String body) {
        return body != null && (body.contains("chap-id") || body.toLowerCase(Locale.US).contains("username"));
    }

    static Object dig(Object payload, String... path) {
        Object current = payload;
        for (String key : path) {
            if (!(current instanceof Map)) {
                return null;
            }
            current = ((Map<?, ?>) current).get(key);
        }
        return current;
    }

    static String schemeOf(String url) {
        int colon = url.indexOf(':');
        if (colon <= 0) {
            return null;
        }
        for (int index = 0; index < colon; index++) {
            char character = url.charAt(index);
            boolean letter = (character >= 'a' && character <= 'z')
                    || (character >= 'A' && character <= 'Z');
            boolean other = character == '+' || character == '-' || character == '.'
                    || (index > 0 && character >= '0' && character <= '9');
            if (!letter && !other) {
                return null;
            }
        }
        return url.substring(0, colon).toLowerCase(Locale.US);
    }

    static String hostOf(String url) {
        try {
            String host = new URL(url).getHost();
            return host == null || host.isEmpty() ? url : host;
        } catch (MalformedURLException error) {
            return url;
        }
    }

    /** {@code http://host}, from any absolute URL. */
    static String originOf(String url) throws IOException {
        URL parsed = new URL(url);
        int port = parsed.getPort();
        StringBuilder origin = new StringBuilder();
        origin.append(parsed.getProtocol()).append("://").append(parsed.getHost());
        if (port >= 0) {
            origin.append(':').append(port);
        }
        return origin.toString();
    }

    /** {@code urljoin}, for a base that always ends in a slash. */
    static String urljoin(String base, String path) {
        if (path == null) {
            return base;
        }
        try {
            return new URL(new URL(base), path).toString();
        } catch (IOException error) {
            return base;
        }
    }

    /** The query pairs of a URL, kept even when a value is empty. */
    static List<String[]> queryFrom(String url) {
        List<String[]> items = new ArrayList<String[]>();
        String query = url;
        int mark = url.indexOf('?');
        if (mark < 0) {
            return items;
        }
        query = url.substring(mark + 1);
        int hash = query.indexOf('#');
        if (hash >= 0) {
            query = query.substring(0, hash);
        }
        for (String pair : query.split("&", -1)) {
            if (pair.isEmpty()) {
                continue;
            }
            int equals = pair.indexOf('=');
            String name = equals < 0 ? pair : pair.substring(0, equals);
            String value = equals < 0 ? "" : pair.substring(equals + 1);
            items.add(new String[] {decode(name), decode(value)});
        }
        return items;
    }

    /** {@code base} with its query replaced by {@code query}. */
    static String rebuild(String base, String query) throws EngineError {
        String withoutFragment = base;
        String fragment = "";
        int hash = base.indexOf('#');
        if (hash >= 0) {
            withoutFragment = base.substring(0, hash);
            fragment = base.substring(hash);
        }
        int question = withoutFragment.indexOf('?');
        String path = question < 0 ? withoutFragment : withoutFragment.substring(0, question);
        return path + (query.isEmpty() ? "" : "?" + query) + fragment;
    }

    private static String decode(String text) {
        try {
            return URLDecoder.decode(text, "UTF-8");
        } catch (UnsupportedEncodingException error) {
            return text;
        } catch (IllegalArgumentException error) {
            // A malformed escape in the router's own link is not worth failing over.
            return text;
        }
    }

    private static void sleep(long milliseconds) {
        try {
            Thread.sleep(milliseconds);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
        }
    }
}
