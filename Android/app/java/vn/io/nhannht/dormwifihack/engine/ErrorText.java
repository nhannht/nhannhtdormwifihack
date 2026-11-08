package vn.io.nhannht.dormwifihack.engine;

import java.io.IOException;
import java.net.ConnectException;
import java.net.NoRouteToHostException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import java.net.UnknownServiceException;

import javax.net.ssl.SSLException;

/**
 * Turns networking errors into something worth reading.
 *
 * <p>A stack trace wraps the one word that matters in a chain of exception types.
 * In a log that scrolls past every two seconds, that buries it.
 */
public final class ErrorText {

    private ErrorText() {
    }

    /** A few words for {@code error}, for a log line. */
    public static String shortText(Throwable error) {
        if (error == null) {
            return "unknown error";
        }

        // Our own errors already say what went wrong in plain words.
        if (error instanceof EngineError) {
            return ((EngineError) error).description;
        }

        if (error instanceof SocketTimeoutException) {
            return "timed out";
        }
        if (error instanceof UnknownHostException) {
            // On a phone this is almost never a bad name. Android's Private DNS
            // resolves over TLS, a captive network blocks that port, and then nothing
            // resolves at all — including the portal the sign-in needs. Saying so
            // here is the difference between a dead end and a fix.
            return "that name does not resolve. A captive network blocks Private DNS, so "
                    + "turn Private DNS off (Settings \u2192 Network \u2192 Private DNS) and try "
                    + "again, or set an account in the app's Settings.";
        }
        if (error instanceof NoRouteToHostException) {
            return "this machine has no network";
        }
        if (error instanceof ConnectException) {
            return "nothing is listening there";
        }
        if (error instanceof SSLException) {
            return "the secure connection failed";
        }
        if (error instanceof UnknownServiceException) {
            // What Android throws when cleartext was refused. Never happens with
            // this app's manifest, but the sentence is there if it does.
            return "the request was blocked before it went out";
        }
        if (error instanceof IOException) {
            String message = error.getMessage();
            if (message != null && message.toLowerCase().contains("closed")) {
                return "the connection dropped";
            }
        }

        String message = error.getMessage();
        if (message != null && !message.isEmpty()) {
            return message;
        }
        return error.getClass().getSimpleName();
    }

    /** {@code 77 days}, {@code 4h 20m}, {@code 45s}. For telling someone how long. */
    public static String duration(long seconds) {
        seconds = Math.max(seconds, 0);

        if (seconds < 60) {
            return seconds + "s";
        }
        if (seconds < 3600) {
            return (seconds / 60) + "m " + (seconds % 60) + "s";
        }
        if (seconds < 86400) {
            return (seconds / 3600) + "h " + ((seconds % 3600) / 60) + "m";
        }

        long days = seconds / 86400;
        return days == 1 ? "1 day" : days + " days";
    }
}
