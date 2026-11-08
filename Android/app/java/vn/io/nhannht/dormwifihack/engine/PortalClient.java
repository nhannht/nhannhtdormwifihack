package vn.io.nhannht.dormwifihack.engine;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UnsupportedEncodingException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.security.cert.X509Certificate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

/**
 * The HTTP side of talking to the router and the portal.
 *
 * <p>One instance is one browsing session: it keeps its own cookie jar and forgets
 * everything when it goes away. Each login attempt builds a fresh one, so a stale
 * cookie from a failed attempt can never leak into the next.
 *
 * <p>Redirects are followed here rather than by the platform, because the login
 * sequence needs to know where it ended up: landing on the connectivity check page
 * is how a sign-in is confirmed, and {@code HttpURLConnection} hides that unless
 * redirects are handled by hand.
 */
public final class PortalClient {

    /** Sent on every request. The portal serves different content to clients it does not recognise. */
    public static final String USER_AGENT =
            "Mozilla/5.0 (Linux; Android 14; K) AppleWebKit/537.36 "
                    + "(KHTML, like Gecko) Chrome/143.0.0.0 Mobile Safari/537.36";

    private static final int MAX_REDIRECTS = 5;

    /** A request that could not be made at all. */
    public static final class ClientError extends IOException {

        private static final long serialVersionUID = 1L;

        public ClientError(String message) {
            super(message);
        }
    }

    /** What came back from one request. */
    public static final class Response {

        public final int statusCode;
        public final String finalUrl;
        public final byte[] data;

        Response(int statusCode, String finalUrl, byte[] data) {
            this.statusCode = statusCode;
            this.finalUrl = finalUrl;
            this.data = data;
        }

        /**
         * The body as text.
         *
         * <p>Replacement rather than strict decoding, because the router's own pages
         * are not always valid UTF-8 and a decoding error here would hide the page
         * that explains the problem.
         */
        public String body() {
            try {
                return new String(data, "UTF-8");
            } catch (UnsupportedEncodingException error) {
                return new String(data);
            }
        }

        public boolean isSuccess() {
            return statusCode >= 200 && statusCode < 300;
        }

        public Object json() {
            return Json.parse(body());
        }

        @SuppressWarnings("unchecked")
        public Map<String, Object> jsonObject() {
            Object value = json();
            return value instanceof Map ? (Map<String, Object>) value : new LinkedHashMap<String, Object>();
        }
    }

    /**
     * How a connection is opened.
     *
     * <p>The JVM and a phone want different things here: on Android the request has
     * to be bound to the Wi-Fi network, or it goes out over mobile data and the
     * router's answer becomes somebody else's. Handing that decision to the caller
     * is what keeps this class free of {@code android.*} and runnable under a test.
     */
    public interface Transport {
        HttpURLConnection open(URL url) throws IOException;
    }

    /** Opens connections over whatever this process's default network is. */
    public static final class DirectTransport implements Transport {
        @Override
        public HttpURLConnection open(URL url) throws IOException {
            return (HttpURLConnection) url.openConnection();
        }
    }

    private final double defaultTimeoutSeconds;
    private final Transport transport;

    /** Host, then cookie name, then the {@code name=value} pair as it goes out. */
    private final Map<String, Map<String, String>> cookies =
            new LinkedHashMap<String, Map<String, String>>();

    public PortalClient() {
        this(20.0, new DirectTransport());
    }

    public PortalClient(double timeoutSeconds, Transport transport) {
        this.defaultTimeoutSeconds = timeoutSeconds;
        this.transport = transport;
    }

    // MARK: - Requests

    public Response get(String url) throws IOException {
        return get(url, null, null);
    }

    public Response get(String url, Map<String, String> headers, Double timeoutSeconds)
            throws IOException {
        Map<String, String> all = new LinkedHashMap<String, String>();
        all.put("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8");
        all.put("Upgrade-Insecure-Requests", "1");
        if (headers != null) {
            all.putAll(headers);
        }
        return send("GET", url, null, all, timeoutSeconds);
    }

    public Response postJson(String url, Object body, String referer, String origin)
            throws IOException {
        return postJson(url, body, referer, origin, null);
    }

    public Response postJson(
            String url,
            Object body,
            String referer,
            String origin,
            Double timeoutSeconds) throws IOException {

        byte[] payload = Json.write(body).trim().getBytes("UTF-8");
        Map<String, String> headers = new LinkedHashMap<String, String>();
        headers.put("Content-Type", "application/json; charset=UTF-8");
        headers.put("Accept", "*/*");
        headers.put("X-Requested-With", "XMLHttpRequest");
        if (referer != null) {
            headers.put("Referer", referer);
        }
        if (origin != null) {
            headers.put("Origin", origin);
        }
        return send("POST", url, payload, headers, timeoutSeconds);
    }

    public Response postForm(
            String url,
            List<String[]> fields,
            String referer,
            String origin,
            Double timeoutSeconds) throws IOException {

        byte[] payload = formEncoded(fields).getBytes("UTF-8");
        Map<String, String> headers = new LinkedHashMap<String, String>();
        headers.put("Content-Type", "application/x-www-form-urlencoded");
        headers.put("Upgrade-Insecure-Requests", "1");
        if (referer != null) {
            headers.put("Referer", referer);
        }
        if (origin != null) {
            headers.put("Origin", origin);
        }
        return send("POST", url, payload, headers, timeoutSeconds);
    }

    // MARK: - The wire

    private Response send(
            String method,
            String url,
            byte[] body,
            Map<String, String> headers,
            Double timeoutSeconds) throws IOException {

        int milliseconds = (int) Math.round((timeoutSeconds == null ? defaultTimeoutSeconds : timeoutSeconds) * 1000);
        String current = url;

        for (int hop = 0; hop <= MAX_REDIRECTS; hop++) {
            URL target;
            try {
                target = new URL(current);
            } catch (IOException error) {
                throw new ClientError("Not a usable URL: " + current);
            }

            HttpURLConnection connection = transport.open(target);
            if (connection == null) {
                throw new ClientError("Could not open a connection to " + target.getHost());
            }

            try {
                connection.setConnectTimeout(milliseconds);
                connection.setReadTimeout(milliseconds);
                connection.setInstanceFollowRedirects(false);
                connection.setUseCaches(false);
                connection.setRequestMethod(method);

                connection.setRequestProperty("User-Agent", USER_AGENT);
                connection.setRequestProperty("Accept-Language", "en-US,en;q=0.9,vi;q=0.8");
                for (Map.Entry<String, String> header : headers.entrySet()) {
                    connection.setRequestProperty(header.getKey(), header.getValue());
                }
                String cookie = cookieHeader(target.getHost());
                if (!cookie.isEmpty()) {
                    connection.setRequestProperty("Cookie", cookie);
                }

                trustPrivateHost(connection, target);

                if (body != null) {
                    connection.setDoOutput(true);
                    connection.setFixedLengthStreamingMode(body.length);
                    OutputStream stream = connection.getOutputStream();
                    try {
                        stream.write(body);
                        stream.flush();
                    } finally {
                        stream.close();
                    }
                }

                int status = connection.getResponseCode();
                storeCookies(target.getHost(), connection);

                String location = connection.getHeaderField("Location");
                if (isRedirect(status) && location != null && !location.isEmpty()) {
                    current = resolve(target, location);
                    // A browser turns a redirected POST into a GET unless the
                    // server said the method has to be kept.
                    if (status != 307 && status != 308 && !"GET".equals(method)) {
                        method = "GET";
                        body = null;
                        headers.remove("Content-Type");
                        headers.remove("X-Requested-With");
                    }
                    continue;
                }

                return new Response(status, current, read(connection, status));
            } finally {
                connection.disconnect();
            }
        }

        throw new ClientError("Too many redirects, starting at " + url);
    }

    private static boolean isRedirect(int status) {
        return status == 301 || status == 302 || status == 303 || status == 307 || status == 308;
    }

    private static byte[] read(HttpURLConnection connection, int status) throws IOException {
        InputStream stream = status >= 400
                ? connection.getErrorStream()
                : connection.getInputStream();
        if (stream == null) {
            return new byte[0];
        }
        try {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int read;
            while ((read = stream.read(chunk)) > 0) {
                buffer.write(chunk, 0, read);
            }
            return buffer.toByteArray();
        } finally {
            stream.close();
        }
    }

    // MARK: - Cookies

    private String cookieHeader(String host) {
        Map<String, String> jar = cookies.get(hostKey(host));
        if (jar == null || jar.isEmpty()) {
            return "";
        }
        StringBuilder out = new StringBuilder();
        for (String pair : jar.values()) {
            if (out.length() > 0) {
                out.append("; ");
            }
            out.append(pair);
        }
        return out.toString();
    }

    private void storeCookies(String host, HttpURLConnection connection) {
        Map<String, List<String>> fields = connection.getHeaderFields();
        List<String> values = null;
        for (Map.Entry<String, List<String>> entry : fields.entrySet()) {
            if (entry.getKey() != null && entry.getKey().equalsIgnoreCase("Set-Cookie")) {
                values = entry.getValue();
                break;
            }
        }
        if (values == null) {
            return;
        }
        String key = hostKey(host);
        Map<String, String> jar = cookies.get(key);
        if (jar == null) {
            jar = new LinkedHashMap<String, String>();
            cookies.put(key, jar);
        }
        for (String value : values) {
            int semicolon = value.indexOf(';');
            String pair = (semicolon >= 0 ? value.substring(0, semicolon) : value).trim();
            if (pair.isEmpty() || pair.indexOf('=') <= 0) {
                continue;
            }
            String name = pair.substring(0, pair.indexOf('=')).trim();
            // A name set twice replaces itself, so one request never sends two
            // values for the same cookie.
            jar.put(name, pair);
        }
    }

    private static String hostKey(String host) {
        return host == null ? "" : host.toLowerCase();
    }

    // MARK: - TLS

    private static SSLSocketFactory trustEverything;

    /**
     * Accept whatever certificate the gateway presents.
     *
     * <p>A captive portal's router serves a self-signed certificate for an IP
     * address, which no trust chain will ever validate. Refusing it would mean never
     * being able to log in. This is limited to hosts on a private network, so a
     * certificate problem out on the internet still fails the way it should.
     */
    private static void trustPrivateHost(HttpURLConnection connection, URL target) {
        if (!(connection instanceof HttpsURLConnection) || !isPrivateHost(target.getHost())) {
            return;
        }
        SSLSocketFactory factory = trustedFactory();
        if (factory == null) {
            // The context could not even be built. Leaving the platform's own
            // verification in place fails closed, which is the right way to fail.
            return;
        }
        HttpsURLConnection secure = (HttpsURLConnection) connection;
        secure.setSSLSocketFactory(factory);
        secure.setHostnameVerifier(new javax.net.ssl.HostnameVerifier() {
            @Override
            public boolean verify(String hostname, javax.net.ssl.SSLSession session) {
                return true;
            }
        });
    }

    private static synchronized SSLSocketFactory trustedFactory() {
        if (trustEverything == null) {
            try {
                SSLContext context = SSLContext.getInstance("TLS");
                context.init(null, new TrustManager[] {new X509TrustManager() {
                    @Override
                    public void checkClientTrusted(X509Certificate[] chain, String type) {
                    }

                    @Override
                    public void checkServerTrusted(X509Certificate[] chain, String type) {
                    }

                    @Override
                    public X509Certificate[] getAcceptedIssuers() {
                        return new X509Certificate[0];
                    }
                }}, null);
                trustEverything = context.getSocketFactory();
            } catch (Exception error) {
                // Leaving it null means the platform's own verification stands,
                // which fails closed rather than open.
                return null;
            }
        }
        return trustEverything;
    }

    /**
     * Private address ranges, plus loopback. Anything else is a public host.
     *
     * <p>Every label has to be a plain decimal octet, and there have to be exactly
     * four of them. Dropping the labels that were not numbers before counting made
     * {@code 10.0.0.1.evil.com} count as four octets, match the 10.x range, and have
     * any certificate it presented accepted. The portal host is read from the
     * router's login page, which an attacker on the captive network controls, so
     * that name is reachable.
     */
    public static boolean isPrivateHost(String host) {
        if (host == null) {
            return false;
        }
        if (host.equals("localhost") || host.equals("::1") || host.equals("[::1]")) {
            return true;
        }

        String[] labels = host.split("\\.", -1);
        if (labels.length != 4) {
            return false;
        }

        int[] octets = new int[4];
        for (int index = 0; index < 4; index++) {
            String label = labels[index];
            if (label.isEmpty() || label.length() > 3) {
                return false;
            }
            for (int position = 0; position < label.length(); position++) {
                char character = label.charAt(position);
                if (character < '0' || character > '9') {
                    return false;
                }
            }
            int value = Integer.parseInt(label);
            if (value > 255) {
                return false;
            }
            octets[index] = value;
        }

        int first = octets[0];
        int second = octets[1];
        if (first == 10 || first == 127) {
            return true;
        }
        if (first == 192 && second == 168) {
            return true;
        }
        if (first == 172 && second >= 16 && second <= 31) {
            return true;
        }
        return first == 169 && second == 254;
    }

    // MARK: - Encoding

    /** Form bodies use {@code +} for a space, which plain quoting will not do. */
    public static String formEncoded(List<String[]> fields) {
        StringBuilder out = new StringBuilder();
        for (String[] field : fields) {
            if (out.length() > 0) {
                out.append('&');
            }
            out.append(encode(field[0], true)).append('=').append(encode(field[1], true));
        }
        return out.toString();
    }

    /** Percent-encoding with a space as {@code %20}, the way the router's own links do it. */
    public static String percentEncoded(List<String[]> items) {
        StringBuilder out = new StringBuilder();
        for (String[] item : items) {
            if (out.length() > 0) {
                out.append('&');
            }
            out.append(encode(item[0], false)).append('=').append(encode(item[1], false));
        }
        return out.toString();
    }

    static String encode(String text, boolean spaceAsPlus) {
        try {
            String encoded = URLEncoder.encode(text, "UTF-8");
            if (spaceAsPlus) {
                return encoded;
            }
            // URLEncoder also turns `*` and `~` into escapes; the router's links
            // leave them alone, and an over-escaped `dst` is a different value.
            return encoded.replace("+", "%20").replace("%2A", "*").replace("%7E", "~");
        } catch (UnsupportedEncodingException error) {
            throw new IllegalStateException("This runtime has no UTF-8.", error);
        }
    }

    /**
     * The absolute form of a {@code Location} header.
     *
     * <p>This is {@code urljoin} as a browser does it: a header that carries its own
     * scheme is the server naming itself, one beginning with a slash joins that
     * origin, and anything else joins the directory of the request.
     */
    static String resolve(URL base, String location) throws IOException {
        try {
            URL resolved = new URL(base, location);
            return resolved.toString();
        } catch (IOException error) {
            throw new ClientError("Could not resolve " + location);
        }
    }
}
