package vn.io.nhannht.dormwifihack;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.DhcpInfo;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.RouteInfo;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.net.URLConnection;
import java.util.List;

import vn.io.nhannht.dormwifihack.engine.ActivityLog;
import vn.io.nhannht.dormwifihack.engine.InternetProbe;
import vn.io.nhannht.dormwifihack.engine.LoginEngine;
import vn.io.nhannht.dormwifihack.engine.PortalClient;
import vn.io.nhannht.dormwifihack.engine.Settings;

/**
 * The Linux build's {@code default_route}, {@code network_probe} and {@code wifi}
 * modules, in one place.
 *
 * <p>Almost none of it survives the move unchanged, and the reason is worth
 * writing down. On Linux the router's address is a line in {@code /proc/net/route},
 * the internet check is a bare socket, and rejoining a network is one
 * {@code nmcli} call. Android gives none of those: routes come from
 * {@link LinkProperties}, a bare socket goes out over mobile data unless it is
 * bound to the Wi-Fi network first, and no app may join a network at all. So the
 * shape of the port is "ask Android the same questions, and say plainly which one
 * has no answer".
 */
public final class AndroidNetwork implements InternetProbe, PortalClient.Transport {

    private final Context context;
    private final ConnectivityManager connectivity;
    private final WifiManager wifi;

    public AndroidNetwork(Context context) {
        this.context = context.getApplicationContext();
        this.connectivity = (ConnectivityManager) this.context.getSystemService(Context.CONNECTIVITY_SERVICE);
        this.wifi = (WifiManager) this.context.getApplicationContext()
                .getSystemService(Context.WIFI_SERVICE);
    }

    // MARK: - The network everything here is about

    /**
     * The Wi-Fi network this phone is on, or null when it is on none.
     *
     * <p>Deliberately not {@code getActiveNetwork()}. That returns the default
     * network, which on a phone with mobile data is usually the mobile one —
     * including, and this is the whole problem, while Wi-Fi sits captive with no
     * internet. Every request this app makes has to leave over Wi-Fi or it is
     * talking to the wrong network.
     */
    public Network wifiNetwork() {
        if (connectivity == null) {
            return null;
        }
        for (Network network : connectivity.getAllNetworks()) {
            NetworkCapabilities capabilities = connectivity.getNetworkCapabilities(network);
            if (capabilities == null) {
                continue;
            }
            if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) {
                return network;
            }
        }
        return null;
    }

    // MARK: - The router

    /**
     * The router this device sends its traffic through on Wi-Fi, or null.
     *
     * <p>The captive portal always sits on it, and its address is different on every
     * network. Asking the system each time is what lets one settings entry work on
     * all of them without being edited in between, which a fixed address cannot do.
     */
    public String gateway() {
        Network network = wifiNetwork();
        if (network == null || connectivity == null) {
            return null;
        }

        LinkProperties properties = connectivity.getLinkProperties(network);
        if (properties != null) {
            List<RouteInfo> routes = properties.getRoutes();
            for (RouteInfo route : routes) {
                if (!route.isDefaultRoute()) {
                    continue;
                }
                InetAddress address = route.getGateway();
                if (address instanceof Inet4Address) {
                    return address.getHostAddress();
                }
            }
        }

        return dhcpGateway();
    }

    /**
     * The router as the DHCP lease named it, for the rare case where no default
     * route is installed yet.
     *
     * <p>The address comes back as an {@code int} holding the four bytes in network
     * order, so it is reassembled a byte at a time rather than trusted as a number.
     */
    private String dhcpGateway() {
        if (wifi == null) {
            return null;
        }
        try {
            DhcpInfo info = wifi.getDhcpInfo();
            if (info == null || info.gateway == 0) {
                return null;
            }
            int value = info.gateway;
            return (value & 0xFF) + "." + ((value >> 8) & 0xFF) + "."
                    + ((value >> 16) & 0xFF) + "." + ((value >> 24) & 0xFF);
        } catch (RuntimeException error) {
            return null;
        }
    }

    /** The same answer, waiting up to {@code seconds} for a network that is still coming up. */
    public String gatewayWaiting(int seconds) {
        String found = gateway();
        if (found != null || seconds <= 0) {
            return found;
        }
        for (int attempt = 0; attempt < seconds; attempt++) {
            try {
                Thread.sleep(1000);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                return gateway();
            }
            found = gateway();
            if (found != null) {
                return found;
            }
        }
        return gateway();
    }

    // MARK: - Requests, over Wi-Fi only

    /**
     * Open a connection bound to the Wi-Fi network.
     *
     * <p>Fails rather than falling back to the default network. A portal request that
     * went out over mobile data would either fail in a confusing way or, worse,
     * succeed — the portal's host is on the public internet — and the sign-in would
     * then be posted from an address the router has never heard of.
     */
    @Override
    public HttpURLConnection open(URL url) throws IOException {
        Network network = wifiNetwork();
        if (network == null) {
            throw new IOException("This phone is not on Wi-Fi");
        }
        URLConnection connection = network.openConnection(url);
        if (!(connection instanceof HttpURLConnection)) {
            throw new IOException("Not an HTTP connection: " + url);
        }
        return (HttpURLConnection) connection;
    }

    /**
     * Is there real internet, over Wi-Fi, right now?
     *
     * <p>A TCP handshake on port 53, bound to the Wi-Fi network. A captive portal
     * answers HTTP requests with its own page, so an HTTP check can come back looking
     * healthy while the connection is still locked; and an unbound socket would
     * measure mobile data instead of the network under test.
     */
    @Override
    public boolean hasInternet(double timeoutSeconds) {
        Network network = wifiNetwork();
        if (network == null) {
            return false;
        }
        String[][] targets = {{"8.8.8.8", "53"}, {"1.1.1.1", "53"}};
        int milliseconds = (int) Math.round(Math.max(timeoutSeconds, 0.1) * 1000);

        for (String[] target : targets) {
            Socket socket = new Socket();
            try {
                network.bindSocket(socket);
                socket.connect(new InetSocketAddress(target[0], Integer.parseInt(target[1])), milliseconds);
                return true;
            } catch (IOException error) {
                // Try the next one; one operator being unreachable is not the whole
                // connection being down.
            } finally {
                try {
                    socket.close();
                } catch (IOException ignored) {
                    // Nothing useful to do about it.
                }
            }
        }
        return false;
    }

    // MARK: - Joining a network

    /**
     * Never. Android has refused to let an app join a Wi-Fi network since API 29.
     *
     * <p>This is a limit of the platform, and the honest thing to do with it is to
     * return false and let the caller say so, rather than to pretend and leave
     * someone waiting for a switch that will not happen.
     */
    public boolean joinNetwork(String ssid) {
        return false;
    }

    /**
     * Null, not empty.
     *
     * <p>Empty would read as "you have saved nothing", which is both true of what
     * this app can see and completely useless as an answer: the phone has saved
     * networks, it just will not show them to a third-party app.
     */
    public List<String> savedNetworks() {
        return null;
    }

    // MARK: - What the screen shows

    /**
     * The SSID, when Android will say, and null when it will not.
     *
     * <p>Only ever a label. The router's address is what the sign-in actually uses,
     * and this is here so the screen can name the network instead of showing an
     * address. Reading it needs the location permission, which this app does not ask
     * for, so on most phones it will answer {@code <unknown ssid>} and this returns
     * null.
     */
    public String currentSsid() {
        if (wifi == null) {
            return null;
        }
        try {
            WifiInfo info = wifi.getConnectionInfo();
            if (info == null) {
                return null;
            }
            String ssid = info.getSSID();
            if (ssid == null) {
                return null;
            }
            if (ssid.length() > 1 && ssid.charAt(0) == '"' && ssid.endsWith("\"")) {
                ssid = ssid.substring(1, ssid.length() - 1);
            }
            if (ssid.isEmpty() || ssid.contains("unknown") || ssid.equals("0x")) {
                return null;
            }
            return ssid;
        } catch (RuntimeException error) {
            return null;
        }
    }

    /** A login engine that talks over Wi-Fi, for the Discover and Sign-in actions. */
    public PortalClient portalClient() {
        return new PortalClient(20.0, this);
    }

    public LoginEngine loginEngine(Settings settings, ActivityLog log) {
        return new LoginEngine(settings, log, this);
    }

    static SharedPreferences preferences(Context context) {
        return context.getApplicationContext()
                .getSharedPreferences("dormwifi", Context.MODE_PRIVATE);
    }
}
