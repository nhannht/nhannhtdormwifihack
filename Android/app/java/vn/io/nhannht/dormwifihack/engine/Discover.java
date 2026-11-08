package vn.io.nhannht.dormwifihack.engine;

import java.net.MalformedURLException;
import java.net.URL;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Reading the router's login page for the one thing it may not say outright.
 *
 * <p>Some MikroTik templates put the portal's address in the login form's
 * {@code action}, and then the sign-in needs no configuration at all. Others put it
 * nowhere: the form has no action, and the page expects a browser to be carried
 * there by a script or by the user clicking something. On those the three step
 * login has no address to go to, and the portal address has to be set by hand.
 *
 * <p>This reads a page the app already fetched and says what it holds. It prints
 * candidates; it never sets one.
 */
public final class Discover {

    static final Pattern URL_PATTERN =
            Pattern.compile("https?://[^\\s\"'<>()\\\\]+", Pattern.CASE_INSENSITIVE);

    // Hosts that answer "is there internet?" and are never the portal. The router
    // puts one of these in the login page as the place it will send you afterwards.
    private static final String[] PROBE_HOSTS = {
        "msftconnecttest.com", "msftncsi.com", "captive.apple.com",
        "connectivitycheck.gstatic.com", "connectivitycheck.android.com", "gstatic.com",
        "detectportal.firefox.com", "connectivity-check.ubuntu.com", "generate_204",
        "nmcheck.gnome.org",
    };

    // Extensions that are never a portal, and are noise in a list of candidates.
    private static final String[] ASSET_SUFFIXES = {
        ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2",
        ".ttf",
    };

    private Discover() {
    }

    /** What one hotspot login page turned out to hold. */
    public static final class Finding {

        public boolean looksLikeLoginPage;
        public boolean hasChapId;
        public Integer challengeBytes;
        public String formAction;
        public List<String[]> urls = new ArrayList<String[]>();
        /** URLs on a host other than the gateway, reduced to {@code scheme://host}. */
        public List<String> candidates = new ArrayList<String>();
    }

    /** Read one login page. Pure: no network, no settings. */
    public static Finding inspect(String page, String gatewayIp) {
        Finding finding = new Finding();
        String lower = page == null ? "" : page.toLowerCase(Locale.US);
        finding.looksLikeLoginPage = lower.contains("chap-id") || lower.contains("username");

        finding.hasChapId = HtmlScraper.valueIdentifiedBy("chap-id", page) != null;
        String challenge = HtmlScraper.valueIdentifiedBy("chap-challenge", page);
        if (challenge != null) {
            try {
                finding.challengeBytes = Integer.valueOf(Chap.decode(challenge).length);
            } catch (Chap.ParseError error) {
                finding.challengeBytes = null;
            }
        }

        finding.formAction = HtmlScraper.formAction(page);

        Set<String> seen = new LinkedHashSet<String>();
        List<String[]> found = new ArrayList<String[]>();
        for (HtmlScraper.HTMLInput candidate : HtmlScraper.inputsIn(page)) {
            if (candidate.value == null || candidate.value.isEmpty()) {
                continue;
            }
            Matcher matcher = URL_PATTERN.matcher(candidate.value);
            while (matcher.find()) {
                String url = tidy(matcher.group());
                if (seen.add(url)) {
                    found.add(new String[] {url, "field"});
                }
            }
        }
        Matcher matcher = URL_PATTERN.matcher(page == null ? "" : page);
        while (matcher.find()) {
            String url = tidy(matcher.group());
            if (seen.add(url)) {
                found.add(new String[] {url, "page"});
            }
        }

        // Everything found, in the order it appeared, then the ranking.
        finding.urls = found;
        finding.candidates = rankedCandidates(found, gatewayIp);
        return finding;
    }

    /**
     * Which URLs on the page could be the portal, best first.
     *
     * <p>Two things make a URL unlikely, and both come from what these templates
     * actually do. The router always names itself — the login form it posts to, the
     * script it loads — so its own host is not the answer. And the value of a hidden
     * field is usually where the router will send you afterwards, one of the
     * browsers' connectivity checks; the portal is what the page's own script or
     * link points at. Hence: URLs found in the markup before URLs found in field
     * values.
     */
    static List<String> rankedCandidates(List<String[]> urls, String gatewayIp) {
        List<String> fromMarkup = new ArrayList<String>();
        List<String> fromFields = new ArrayList<String>();

        for (String[] entry : urls) {
            String url = entry[0];
            if (isAsset(url) || host(url).equals(gatewayIp) || isProbeHost(url)) {
                continue;
            }
            String root = root(url);
            if (root == null) {
                continue;
            }
            List<String> bucket = "field".equals(entry[1]) ? fromFields : fromMarkup;
            if (!bucket.contains(root)) {
                bucket.add(root);
            }
        }

        List<String> ranked = new ArrayList<String>(fromMarkup);
        for (String root : fromFields) {
            if (!ranked.contains(root)) {
                ranked.add(root);
            }
        }
        return ranked;
    }

    /** The report a person reads, line by line. */
    public static List<String> render(Finding finding, String gatewayIp) {
        List<String> lines = new ArrayList<String>();
        lines.add("Router: " + (gatewayIp == null || gatewayIp.isEmpty() ? "(none)" : gatewayIp));

        if (!finding.looksLikeLoginPage) {
            lines.add("Login page: no. This does not look like a hotspot login page.");
            lines.add("Either this device is on the wrong network, or this network has no sign-in page at all.");
            return lines;
        }

        lines.add("Login page: yes");
        lines.add("Challenge: " + (finding.hasChapId
                ? "present, " + (finding.challengeBytes == null ? "unreadable" : finding.challengeBytes + " bytes")
                : "missing"));
        lines.add("Form posts: " + (finding.formAction == null
                ? "nowhere. The page names no portal, so one has to be set by hand."
                : finding.formAction));

        if (!finding.candidates.isEmpty()) {
            lines.add("The portal is probably one of these:");
            for (String candidate : finding.candidates) {
                lines.add("  " + candidate);
            }
        } else {
            lines.add("No portal address appears anywhere on the page. Open the login page in a "
                    + "browser and copy the address it is served from.");
        }
        return lines;
    }

    static String tidy(String url) {
        int end = url.length();
        while (end > 0 && ".,;:)]}\\".indexOf(url.charAt(end - 1)) >= 0) {
            end--;
        }
        return url.substring(0, end);
    }

    static String host(String url) {
        try {
            String host = new URL(url).getHost();
            return host == null ? "" : host;
        } catch (MalformedURLException error) {
            return "";
        }
    }

    static String root(String url) {
        try {
            URL parsed = new URL(url);
            int port = parsed.getPort();
            return parsed.getProtocol() + "://" + parsed.getHost() + (port >= 0 ? ":" + port : "");
        } catch (MalformedURLException error) {
            return null;
        }
    }

    private static boolean isProbeHost(String url) {
        String host = host(url).toLowerCase(Locale.US);
        String path = "";
        try {
            path = new URL(url).getPath().toLowerCase(Locale.US);
        } catch (MalformedURLException error) {
            return false;
        }
        for (String probe : PROBE_HOSTS) {
            if (host.equals(probe) || host.endsWith("." + probe) || path.contains(probe)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isAsset(String url) {
        String path = "";
        try {
            path = new URL(url).getPath().toLowerCase(Locale.US);
        } catch (MalformedURLException error) {
            return false;
        }
        for (String suffix : ASSET_SUFFIXES) {
            if (path.endsWith(suffix)) {
                return true;
            }
        }
        return false;
    }
}
