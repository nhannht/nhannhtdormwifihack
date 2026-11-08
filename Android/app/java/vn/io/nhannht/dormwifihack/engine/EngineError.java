package vn.io.nhannht.dormwifihack.engine;

/**
 * A step of the sign-in that could not be done, in plain words.
 *
 * <p>Separate from a transport failure on purpose: an {@code IOException} from
 * the socket means "that did not reach the router", while this means "the router
 * answered and what it said cannot be worked with". The two send a reader to look
 * in completely different places.
 */
public class EngineError extends Exception {

    private static final long serialVersionUID = 1L;

    /** The same sentence as {@link #getMessage()}, under the name the log uses. */
    public final String description;

    public EngineError(String description) {
        super(description);
        this.description = description;
    }

    public static EngineError gatewayUnreachable(int attempts) {
        return new EngineError("The router did not answer after " + attempts + " attempts.");
    }

    public static EngineError gatewayNotALoginPage() {
        return new EngineError("The router answered, but not with a hotspot login page.");
    }

    public static EngineError noPortalUrl() {
        return new EngineError(
                "The router's login page names no portal, and no fallback is set. Set the "
                        + "portal address in Settings \u2192 Portal address.");
    }

    public static EngineError portalRejected(String step, int status) {
        return new EngineError("The portal answered " + step + " with HTTP " + status + ".");
    }

    public static EngineError noLoginForm() {
        return new EngineError("The portal's reply carried no login form.");
    }

    public static EngineError badUrl(String text) {
        return new EngineError("Could not build a URL from: " + text);
    }
}
