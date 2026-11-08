package vn.io.nhannht.dormwifihack.engine;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;

/**
 * Is there real internet, right now?
 *
 * <p>This opens a TCP connection to a public DNS server rather than fetching a web
 * page. A captive portal answers HTTP requests with its own page, so an HTTP check
 * can come back looking healthy while the connection is still locked. A TCP
 * handshake on port 53 either completes or it does not, and it settles in a
 * fraction of the time a page fetch takes.
 */
public interface InternetProbe {

    boolean hasInternet(double timeoutSeconds);

    /** Connects over whatever this process's default network is. */
    final class DirectSocketProbe implements InternetProbe {

        /** Google first, Cloudflare second, so one operator being unreachable does not read as the whole connection being down. */
        private static final String[][] TARGETS = {{"8.8.8.8", "53"}, {"1.1.1.1", "53"}};

        @Override
        public boolean hasInternet(double timeoutSeconds) {
            for (String[] target : TARGETS) {
                if (canConnect(target[0], Integer.parseInt(target[1]), timeoutSeconds)) {
                    return true;
                }
            }
            return false;
        }

        static boolean canConnect(String host, int port, double timeoutSeconds) {
            Socket socket = new Socket();
            try {
                socket.connect(new InetSocketAddress(host, port),
                        (int) Math.round(Math.max(timeoutSeconds, 0.1) * 1000));
                return true;
            } catch (IOException error) {
                return false;
            } finally {
                try {
                    socket.close();
                } catch (IOException ignored) {
                    // Nothing useful to do about it.
                }
            }
        }
    }
}
