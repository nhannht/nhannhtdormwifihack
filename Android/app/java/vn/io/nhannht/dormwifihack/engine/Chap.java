package vn.io.nhannht.dormwifihack.engine;

import java.io.ByteArrayOutputStream;
import java.io.UnsupportedEncodingException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * MikroTik hotspot CHAP authentication.
 *
 * <p>A MikroTik hotspot never accepts a plaintext password. Its login page
 * carries two hidden fields, {@code chap-id} and {@code chap-challenge}, and the
 * password posted back has to be {@code MD5(id + password + challenge)} over the
 * raw bytes of each part, in that order.
 *
 * <p>Both fields arrive as escape strings. Any byte that is not printable ASCII
 * is written as a backslash followed by three octal digits, so {@code \154}
 * means the single byte 108. On top of that the whole value is sometimes
 * percent-encoded, which turns each backslash into {@code %5C}.
 */
public final class Chap {

    private static final int BACKSLASH = 0x5C;
    private static final int PERCENT = 0x25;
    private static final int ZERO = '0';
    private static final int SEVEN = '7';

    private Chap() {
    }

    /** A backslash was followed by three digits that do not form a byte. */
    public static final class ParseError extends IllegalArgumentException {

        private static final long serialVersionUID = 1L;

        /** The three digits that could not be read. */
        public final String digits;
        /** The same thing as a sentence, for a log line. */
        public final String description;

        ParseError(String digits) {
            super(describe(digits));
            this.digits = digits;
            this.description = describe(digits);
        }

        private static String describe(String digits) {
            return "Malformed octal escape \\" + digits + ". Expected three digits in "
                    + "the range 0 to 7 whose value is 255 or less.";
        }
    }

    /** The password to post back: MD5(id + password + challenge), lowercase hex. */
    public static String hashPassword(String password, String chapId, String chapChallenge)
            throws ParseError {
        MessageDigest digest = md5();
        digest.update(decode(chapId));
        digest.update(utf8(password));
        digest.update(decode(chapChallenge));
        return hex(digest.digest());
    }

    /** Plain MD5(password), for a login page that carries no CHAP fields. */
    public static String plainMd5(String password) {
        MessageDigest digest = md5();
        digest.update(utf8(password));
        return hex(digest.digest());
    }

    /**
     * Turn one escape string into the bytes it stands for.
     *
     * <p>A backslash that is not followed by three octal digits is passed through
     * as a literal backslash, because the router escapes a real backslash as
     * {@code \134} and so should never emit a bare one. A backslash followed by
     * three digits that cannot be a byte is an error rather than a literal, since
     * letting it through would produce a wrong hash and the router would answer
     * with nothing more useful than "invalid password".
     */
    public static byte[] decode(String text) throws ParseError {
        byte[] data = percentDecode(utf8(text));

        ByteArrayOutputStream out = new ByteArrayOutputStream(data.length);
        int i = 0;
        int length = data.length;
        while (i < length) {
            if (!(data[i] == BACKSLASH && i + 3 < length)) {
                out.write(data[i]);
                i++;
                continue;
            }

            byte[] digits = {data[i + 1], data[i + 2], data[i + 3]};
            boolean octal = true;
            for (byte digit : digits) {
                if (digit < ZERO || digit > SEVEN) {
                    octal = false;
                    break;
                }
            }
            if (!octal) {
                out.write(data[i]);
                i++;
                continue;
            }

            int value = (digits[0] - ZERO) * 64 + (digits[1] - ZERO) * 8 + (digits[2] - ZERO);
            if (value > 0xFF) {
                throw new ParseError(new String(digits, java.nio.charset.Charset.forName("US-ASCII")));
            }
            out.write(value);
            i += 4;
        }

        return out.toByteArray();
    }

    /**
     * Percent-decode over raw bytes rather than over text.
     *
     * <p>Decoding into text first runs the result through UTF-8 validation. Any
     * byte of the challenge that is not valid UTF-8 gets replaced with U+FFFD and
     * the hash comes out wrong with no error anywhere.
     */
    public static byte[] percentDecode(byte[] data) {
        boolean hasPercent = false;
        for (byte value : data) {
            if (value == PERCENT) {
                hasPercent = true;
                break;
            }
        }
        if (!hasPercent) {
            return data;
        }

        ByteArrayOutputStream out = new ByteArrayOutputStream(data.length);
        int i = 0;
        while (i < data.length) {
            int high = i + 2 < data.length ? hexNibble(data[i + 1]) : -1;
            int low = i + 2 < data.length ? hexNibble(data[i + 2]) : -1;
            if (data[i] == PERCENT && high >= 0 && low >= 0) {
                out.write(high << 4 | low);
                i += 3;
            } else {
                out.write(data[i]);
                i++;
            }
        }
        return out.toByteArray();
    }

    private static int hexNibble(byte value) {
        if (value >= '0' && value <= '9') {
            return value - '0';
        }
        if (value >= 'a' && value <= 'f') {
            return value - 'a' + 10;
        }
        if (value >= 'A' && value <= 'F') {
            return value - 'A' + 10;
        }
        return -1;
    }

    private static MessageDigest md5() {
        try {
            return MessageDigest.getInstance("MD5");
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("This runtime has no MD5.", error);
        }
    }

    static byte[] utf8(String text) {
        try {
            return text.getBytes("UTF-8");
        } catch (UnsupportedEncodingException error) {
            throw new IllegalStateException("This runtime has no UTF-8.", error);
        }
    }

    private static String hex(byte[] data) {
        StringBuilder out = new StringBuilder(data.length * 2);
        for (byte value : data) {
            out.append(Character.forDigit((value >> 4) & 0xF, 16));
            out.append(Character.forDigit(value & 0xF, 16));
        }
        return out.toString();
    }
}
