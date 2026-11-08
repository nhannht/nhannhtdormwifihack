package vn.io.nhannht.dormwifihack.engine;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Everything the app needs to know, stored as JSON.
 *
 * <p>The key names match the {@code settings.json} the macOS, Windows and Linux
 * builds already write, so an existing file loads unchanged. Keys an older file
 * does not have fall back to the defaults here, and keys this build does not model
 * are carried through untouched — a settings file that has been through the
 * Windows build keeps what that build put in it.
 *
 * <p>On Android the JSON lives in one string in the app's preferences rather than
 * as a file. Same bytes, so a file can still be moved between the two by copying
 * its contents.
 */
public final class Settings {

    /** The key names this build writes for itself. */
    public static final Set<String> DECLARED_KEYS;

    static {
        Set<String> keys = new TreeSet<String>();
        keys.add("wifi_name");
        keys.add("gateway_ip");
        keys.add("interval");
        keys.add("check_interval_secs");
        keys.add("fallback_wifi_name");
        keys.add("portal_fallback_url");
        keys.add("portal_endpoints");
        keys.add("destination_url");
        keys.add("logout_url");
        keys.add("auto_connect");
        keys.add("auto_start");
        keys.add("check_wifi_every_time");
        keys.add("last_run_timestamp");
        keys.add("username");
        keys.add("password");
        DECLARED_KEYS = java.util.Collections.unmodifiableSet(keys);
    }

    /** The portal's API paths. Defaults match the portal this was built against. */
    public static final class PortalEndpoints {

        public String verifyUrl = "/Home/VerifyUrl";
        public String getCustomer = "/Content/GetCustomer";
        public String getCampaignHtml = "/Content/GetCampaignHtml";
        public String analytic = "/Analytic/Send";

        static PortalEndpoints fromJson(Object payload) {
            PortalEndpoints endpoints = new PortalEndpoints();
            if (!(payload instanceof Map)) {
                return endpoints;
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> source = (Map<String, Object>) payload;
            endpoints.verifyUrl = Json.string(source, "verifyURL", endpoints.verifyUrl);
            endpoints.getCustomer = Json.string(source, "getCustomer", endpoints.getCustomer);
            endpoints.getCampaignHtml =
                    Json.string(source, "getCampaignHTML", endpoints.getCampaignHtml);
            endpoints.analytic = Json.string(source, "analytic", endpoints.analytic);
            return endpoints;
        }

        Map<String, Object> toJson() {
            Map<String, Object> payload = new LinkedHashMap<String, Object>();
            payload.put("verifyURL", verifyUrl);
            payload.put("getCustomer", getCustomer);
            payload.put("getCampaignHTML", getCampaignHtml);
            payload.put("analytic", analytic);
            return payload;
        }

        PortalEndpoints copy() {
            PortalEndpoints duplicate = new PortalEndpoints();
            duplicate.verifyUrl = verifyUrl;
            duplicate.getCustomer = getCustomer;
            duplicate.getCampaignHtml = getCampaignHtml;
            duplicate.analytic = analytic;
            return duplicate;
        }
    }

    /** The SSID to look for. Only used to say which network is being watched. */
    public String wifiName = "";

    /**
     * The router's address. Empty, which is the default, means ask this device which
     * router it is currently using. Every network has a different one, so a fixed
     * address stops working the moment you join a different hotspot.
     */
    public String gatewayIp = "";

    /** How long a session lasts, in minutes. The router decides this; this has to match. */
    public int interval = 15;

    /** How often to check that the connection is still up, in seconds. */
    public int checkIntervalSecs = 2;

    /**
     * A network to fall back to when the internet has been gone for a while.
     *
     * <p>On Android the app cannot join a network by itself, so this is a label for
     * the notification rather than an action. Empty turns the safety net off.
     */
    public String fallbackWifiName = "";

    /**
     * An account to sign in with, straight at the router.
     *
     * <p>Set these and the portal is never contacted. That matters on hotspots where
     * the portal cannot be reached until after signing in — the router intercepts
     * every plain request and resets TLS — so a portal-issued account is impossible
     * to obtain from the captive side.
     */
    public String username = "";
    public String password = "";

    /** Where to send the login when the router's page carries no form action. */
    public String portalFallbackUrl = "";

    /** The portal's JSON endpoints, relative to whichever host is in play. */
    public PortalEndpoints portalEndpoints = new PortalEndpoints();

    /** Where to ask the router to send us once it lets us through. */
    public String destinationUrl = "http://captive.apple.com/hotspot-detect.html";

    /** Hitting this logs the current session out. Optional. */
    public String logoutUrl = "";

    /** Join the hotspot automatically when it is not the current network. Written, unused here. */
    public boolean autoConnect = true;

    /** Start the watcher when the phone boots. Read by nobody in this build. */
    public boolean autoStart = false;

    /** Check the network before every login rather than trusting the last check. */
    public boolean checkWifiEveryTime = false;

    /** When the last successful login happened, as a Unix timestamp. */
    public double lastRunTimestamp = 0.0;

    /** Keys found in the JSON that this build does not model. Written back out verbatim. */
    public Map<String, Object> unknownKeys = new LinkedHashMap<String, Object>();

    // MARK: - Derived

    /**
     * How long a session lasts, in seconds, clamped to one day.
     *
     * <p>{@code interval} comes out of a file that can say anything.
     */
    public int sessionTimeoutSeconds() {
        return Math.min(Math.max(interval, 1), 1440) * 60;
    }

    /** The same settings, with the router named rather than asked for. */
    public Settings withGatewayIp(String address) {
        Settings duplicate = copy();
        duplicate.gatewayIp = address == null ? "" : address;
        return duplicate;
    }

    public Settings copy() {
        Settings duplicate = new Settings();
        duplicate.wifiName = wifiName;
        duplicate.gatewayIp = gatewayIp;
        duplicate.interval = interval;
        duplicate.checkIntervalSecs = checkIntervalSecs;
        duplicate.fallbackWifiName = fallbackWifiName;
        duplicate.username = username;
        duplicate.password = password;
        duplicate.portalFallbackUrl = portalFallbackUrl;
        duplicate.portalEndpoints = portalEndpoints.copy();
        duplicate.destinationUrl = destinationUrl;
        duplicate.logoutUrl = logoutUrl;
        duplicate.autoConnect = autoConnect;
        duplicate.autoStart = autoStart;
        duplicate.checkWifiEveryTime = checkWifiEveryTime;
        duplicate.lastRunTimestamp = lastRunTimestamp;
        duplicate.unknownKeys = new LinkedHashMap<String, Object>(unknownKeys);
        return duplicate;
    }

    // MARK: - Storage

    /** Load from JSON text, or hand back defaults when there is nothing there yet. */
    public static Settings fromJson(String text) {
        Settings defaults = new Settings();
        if (text == null || text.trim().isEmpty()) {
            return defaults;
        }

        Map<String, Object> payload = Json.parseObject(text);

        Settings settings = new Settings();
        settings.wifiName = Json.string(payload, "wifi_name", defaults.wifiName);
        settings.gatewayIp = Json.string(payload, "gateway_ip", defaults.gatewayIp);
        settings.interval = Json.integer(payload, "interval", defaults.interval);
        settings.checkIntervalSecs =
                Json.integer(payload, "check_interval_secs", defaults.checkIntervalSecs);
        settings.fallbackWifiName =
                Json.string(payload, "fallback_wifi_name", defaults.fallbackWifiName);
        settings.portalFallbackUrl =
                Json.string(payload, "portal_fallback_url", defaults.portalFallbackUrl);
        settings.username = Json.string(payload, "username", defaults.username);
        settings.password = Json.string(payload, "password", defaults.password);
        settings.portalEndpoints = PortalEndpoints.fromJson(payload.get("portal_endpoints"));
        settings.destinationUrl = Json.string(payload, "destination_url", defaults.destinationUrl);
        settings.logoutUrl = Json.string(payload, "logout_url", defaults.logoutUrl);
        settings.autoConnect = Json.bool(payload, "auto_connect", defaults.autoConnect);
        settings.autoStart = Json.bool(payload, "auto_start", defaults.autoStart);
        settings.checkWifiEveryTime =
                Json.bool(payload, "check_wifi_every_time", defaults.checkWifiEveryTime);
        settings.lastRunTimestamp =
                Json.number(payload, "last_run_timestamp", defaults.lastRunTimestamp);

        /* Iterate rather than filter, so a key can never be dropped by omission. */
        for (Map.Entry<String, Object> entry : payload.entrySet()) {
            if (!DECLARED_KEYS.contains(entry.getKey())) {
                settings.unknownKeys.put(entry.getKey(), entry.getValue());
            }
        }
        return settings;
    }

    /** The JSON text this build writes, unknown keys included. */
    public Map<String, Object> toJson() {
        Map<String, Object> payload = new LinkedHashMap<String, Object>();
        payload.put("wifi_name", wifiName);
        payload.put("gateway_ip", gatewayIp);
        payload.put("interval", interval);
        payload.put("check_interval_secs", checkIntervalSecs);
        payload.put("fallback_wifi_name", fallbackWifiName);
        payload.put("portal_fallback_url", portalFallbackUrl);
        payload.put("username", username);
        payload.put("password", password);
        payload.put("portal_endpoints", portalEndpoints.toJson());
        payload.put("destination_url", destinationUrl);
        payload.put("logout_url", logoutUrl);
        payload.put("auto_connect", autoConnect);
        payload.put("auto_start", autoStart);
        payload.put("check_wifi_every_time", checkWifiEveryTime);
        payload.put("last_run_timestamp", lastRunTimestamp);

        // Then whatever else was in the file. A declared key always wins, so a
        // stale entry here can never shadow a real setting.
        for (Map.Entry<String, Object> entry : unknownKeys.entrySet()) {
            if (!DECLARED_KEYS.contains(entry.getKey())) {
                payload.put(entry.getKey(), entry.getValue());
            }
        }
        return payload;
    }

    public String toJsonText() {
        return Json.write(toJson());
    }

    public void touchLastRun() {
        lastRunTimestamp = System.currentTimeMillis() / 1000.0;
    }
}
