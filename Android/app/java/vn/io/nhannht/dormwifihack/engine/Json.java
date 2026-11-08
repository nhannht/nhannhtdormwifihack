package vn.io.nhannht.dormwifihack.engine;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The smallest JSON reader and writer that will do.
 *
 * <p>The settings file is the same {@code settings.json} the macOS, Windows and
 * Linux builds read, so this has to write exactly that shape: an object, a
 * handful of strings and numbers, and one nested object for the portal's
 * endpoints. Android ships {@code org.json}, but that class lives in the
 * framework and not on a plain JVM, and the point of the engine is that every
 * rule in it can be run under a test without a phone attached. So the codec
 * lives here, in about as much code as a stub would have taken.
 *
 * <p>Parsing produces {@link LinkedHashMap} for objects, {@link ArrayList} for
 * arrays, {@link String}, {@link Long} for whole numbers and {@link Double} for
 * the rest, {@link Boolean}, or null.
 */
public final class Json {

    private Json() {
    }

    // MARK: - Reading

    public static Object parse(String text) {
        Parser parser = new Parser(text);
        Object value = parser.readValue();
        parser.skipWhitespace();
        if (!parser.atEnd()) {
            throw new IllegalArgumentException(
                    "Trailing content after the JSON value at offset " + parser.index);
        }
        return value;
    }

    /** The object at {@code text}, or an empty one when it does not hold an object. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        Object value = parse(text);
        if (!(value instanceof Map)) {
            throw new IllegalArgumentException("That JSON is not an object.");
        }
        return (Map<String, Object>) value;
    }

    // MARK: - Writing

    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        writeValue(value, out, 0);
        out.append('\n');
        return out.toString();
    }

    private static void writeValue(Object value, StringBuilder out, int depth) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String) {
            writeString((String) value, out);
        } else if (value instanceof Boolean) {
            out.append(value.toString());
        } else if (value instanceof Double || value instanceof Float) {
            double number = ((Number) value).doubleValue();
            if (Double.isNaN(number) || Double.isInfinite(number)) {
                out.append("null");
            } else if (number == Math.rint(number) && Math.abs(number) < 1e15) {
                // Python writes 15.0 as "15.0", and a settings file that both
                // builds can read is worth the two extra characters.
                out.append((long) number).append(".0");
            } else {
                // Not Double.toString: it writes 1794119825.5 as "1.7941198255E9",
                // which is legal JSON and unreadable in a file somebody may open.
                out.append(java.math.BigDecimal.valueOf(number).toPlainString());
            }
        } else if (value instanceof Number) {
            out.append(((Number) value).longValue());
        } else if (value instanceof Map) {
            writeObject(asMap(value), out, depth);
        } else if (value instanceof Iterable) {
            writeArray((Iterable<?>) value, out, depth);
        } else {
            writeString(value.toString(), out);
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object value) {
        return (Map<String, Object>) value;
    }

    private static void writeObject(Map<String, Object> value, StringBuilder out, int depth) {
        if (value.isEmpty()) {
            out.append("{}");
            return;
        }
        out.append("{\n");
        int index = 0;
        for (Map.Entry<String, Object> entry : value.entrySet()) {
            indent(out, depth + 1);
            writeString(entry.getKey(), out);
            out.append(": ");
            writeValue(entry.getValue(), out, depth + 1);
            if (++index < value.size()) {
                out.append(',');
            }
            out.append('\n');
        }
        indent(out, depth);
        out.append('}');
    }

    private static void writeArray(Iterable<?> value, StringBuilder out, int depth) {
        boolean any = false;
        out.append("[\n");
        for (Object element : value) {
            indent(out, depth + 1);
            writeValue(element, out, depth + 1);
            out.append(",\n");
            any = true;
        }
        if (any) {
            // Trailing commas are not JSON, so drop the one just written.
            out.setLength(out.length() - 2);
            out.append('\n');
        }
        indent(out, depth);
        out.append(']');
    }

    private static void indent(StringBuilder out, int depth) {
        for (int level = 0; level < depth; level++) {
            out.append("  ");
        }
    }

    private static void writeString(String text, StringBuilder out) {
        out.append('"');
        for (int index = 0; index < text.length(); index++) {
            char character = text.charAt(index);
            switch (character) {
                case '"':
                    out.append("\\\"");
                    break;
                case '\\':
                    out.append("\\\\");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                case '\b':
                    out.append("\\b");
                    break;
                case '\f':
                    out.append("\\f");
                    break;
                default:
                    if (character < 0x20) {
                        out.append(String.format("\\u%04x", (int) character));
                    } else {
                        out.append(character);
                    }
            }
        }
        out.append('"');
    }

    // MARK: - Typed reads

    public static String string(Map<String, Object> source, String key, String fallback) {
        Object value = source.get(key);
        return value instanceof String ? (String) value : fallback;
    }

    public static int integer(Map<String, Object> source, String key, int fallback) {
        Object value = source.get(key);
        if (value instanceof Boolean || !(value instanceof Number)) {
            return fallback;
        }
        return ((Number) value).intValue();
    }

    public static double number(Map<String, Object> source, String key, double fallback) {
        Object value = source.get(key);
        if (value instanceof Boolean || !(value instanceof Number)) {
            return fallback;
        }
        return ((Number) value).doubleValue();
    }

    public static boolean bool(Map<String, Object> source, String key, boolean fallback) {
        Object value = source.get(key);
        return value instanceof Boolean ? (Boolean) value : fallback;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> object(
            Map<String, Object> source, String key, Map<String, Object> fallback) {
        Object value = source.get(key);
        return value instanceof Map ? (Map<String, Object>) value : fallback;
    }

    // MARK: - The parser

    private static final class Parser {

        private final String text;
        private int index;

        Parser(String text) {
            this.text = text == null ? "" : text;
        }

        boolean atEnd() {
            return index >= text.length();
        }

        void skipWhitespace() {
            while (index < text.length() && Character.isWhitespace(text.charAt(index))) {
                index++;
            }
        }

        Object readValue() {
            skipWhitespace();
            if (atEnd()) {
                throw error("The file ends where a value should start.");
            }
            char character = text.charAt(index);
            switch (character) {
                case '{':
                    return readObject();
                case '[':
                    return readArray();
                case '"':
                    return readString();
                case 't':
                    expect("true");
                    return Boolean.TRUE;
                case 'f':
                    expect("false");
                    return Boolean.FALSE;
                case 'n':
                    expect("null");
                    return null;
                default:
                    return readNumber();
            }
        }

        private Map<String, Object> readObject() {
            Map<String, Object> result = new LinkedHashMap<String, Object>();
            index++; // the brace
            skipWhitespace();
            if (!atEnd() && text.charAt(index) == '}') {
                index++;
                return result;
            }
            while (true) {
                skipWhitespace();
                if (atEnd() || text.charAt(index) != '"') {
                    throw error("A key has to be a string.");
                }
                String key = readString();
                skipWhitespace();
                if (atEnd() || text.charAt(index) != ':') {
                    throw error("A key has to be followed by ':'.");
                }
                index++;
                result.put(key, readValue());
                skipWhitespace();
                if (atEnd()) {
                    throw error("The object is not closed.");
                }
                char character = text.charAt(index);
                if (character == ',') {
                    index++;
                    continue;
                }
                if (character == '}') {
                    index++;
                    return result;
                }
                throw error("Expected ',' or '}' in an object.");
            }
        }

        private List<Object> readArray() {
            List<Object> result = new ArrayList<Object>();
            index++; // the bracket
            skipWhitespace();
            if (!atEnd() && text.charAt(index) == ']') {
                index++;
                return result;
            }
            while (true) {
                result.add(readValue());
                skipWhitespace();
                if (atEnd()) {
                    throw error("The array is not closed.");
                }
                char character = text.charAt(index);
                if (character == ',') {
                    index++;
                    continue;
                }
                if (character == ']') {
                    index++;
                    return result;
                }
                throw error("Expected ',' or ']' in an array.");
            }
        }

        private String readString() {
            StringBuilder out = new StringBuilder();
            index++; // the opening quote
            while (true) {
                if (atEnd()) {
                    throw error("The string is not closed.");
                }
                char character = text.charAt(index++);
                if (character == '"') {
                    return out.toString();
                }
                if (character != '\\') {
                    out.append(character);
                    continue;
                }
                if (atEnd()) {
                    throw error("The string ends in the middle of an escape.");
                }
                char escape = text.charAt(index++);
                switch (escape) {
                    case '"':
                        out.append('"');
                        break;
                    case '\\':
                        out.append('\\');
                        break;
                    case '/':
                        out.append('/');
                        break;
                    case 'b':
                        out.append('\b');
                        break;
                    case 'f':
                        out.append('\f');
                        break;
                    case 'n':
                        out.append('\n');
                        break;
                    case 'r':
                        out.append('\r');
                        break;
                    case 't':
                        out.append('\t');
                        break;
                    case 'u':
                        if (index + 4 > text.length()) {
                            throw error("A \\u escape needs four hex digits.");
                        }
                        String digits = text.substring(index, index + 4);
                        try {
                            out.append((char) Integer.parseInt(digits, 16));
                        } catch (NumberFormatException error) {
                            throw error("Not a \\u escape: " + digits);
                        }
                        index += 4;
                        break;
                    default:
                        throw error("Unknown escape \\" + escape);
                }
            }
        }

        private Object readNumber() {
            int start = index;
            if (!atEnd() && (text.charAt(index) == '-' || text.charAt(index) == '+')) {
                index++;
            }
            boolean fractional = false;
            while (!atEnd()) {
                char character = text.charAt(index);
                if (character >= '0' && character <= '9') {
                    index++;
                } else if (character == '.' || character == 'e' || character == 'E'
                        || character == '+' || character == '-') {
                    fractional = fractional || character == '.' || character == 'e' || character == 'E';
                    index++;
                } else {
                    break;
                }
            }
            String literal = text.substring(start, index);
            if (literal.isEmpty()) {
                throw error("Expected a value.");
            }
            try {
                if (fractional) {
                    return Double.valueOf(literal);
                }
                return Long.valueOf(literal);
            } catch (NumberFormatException error) {
                throw error("Not a number: " + literal);
            }
        }

        private void expect(String literal) {
            if (!text.startsWith(literal, index)) {
                throw error("Expected " + literal + ".");
            }
            index += literal.length();
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + " (offset " + index + ")");
        }
    }
}
