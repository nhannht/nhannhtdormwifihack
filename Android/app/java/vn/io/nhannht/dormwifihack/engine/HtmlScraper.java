package vn.io.nhannht.dormwifihack.engine;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Pulls hidden form fields out of the two pages the login sequence reads: the
 * router's hotspot login page, and the HTML fragment the portal returns.
 *
 * <p>This deliberately does not parse HTML in general. Both pages are known, and
 * the only thing wanted from either is the attributes of their {@code <input>}
 * tags, which is a job a pattern match does correctly.
 */
public final class HtmlScraper {

    private static final Pattern INPUT_TAG = Pattern.compile("<input\\b[^>]*>", Pattern.CASE_INSENSITIVE);
    private static final Pattern FORM_TAG = Pattern.compile("<form\\b[^>]*>", Pattern.CASE_INSENSITIVE);
    private static final Pattern ATTRIBUTE = Pattern.compile(
            "([A-Za-z_:][-A-Za-z0-9_:.]*)\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\s\"'>=`]+))");

    private static final String[][] ENTITIES = {
        {"&lt;", "<"},
        {"&gt;", ">"},
        {"&quot;", "\""},
        {"&#39;", "'"},
        {"&apos;", "'"},
        {"&nbsp;", " "},
    };

    private HtmlScraper() {
    }

    /** One {@code <input>} tag lifted off a page. */
    public static final class HTMLInput {

        public final String name;
        public final String id;
        public final String value;

        HTMLInput(String name, String id, String value) {
            this.name = name;
            this.id = id;
            this.value = value;
        }

        /**
         * The key to send this field under: its name, or its id when unnamed.
         *
         * <p>This is the rule the router's own page relies on.
         */
        public String key() {
            if (name != null && !name.isEmpty()) {
                return name;
            }
            if (id != null && !id.isEmpty()) {
                return id;
            }
            return null;
        }
    }

    /** Every {@code <input>} on the page, in the order it appears. */
    public static List<HTMLInput> inputsIn(String html) {
        List<HTMLInput> found = new ArrayList<HTMLInput>();
        if (html == null) {
            return found;
        }
        Matcher matcher = INPUT_TAG.matcher(html);
        while (matcher.find()) {
            Map<String, String> attributes = attributesIn(matcher.group());
            found.add(new HTMLInput(attributes.get("name"), attributes.get("id"), attributes.get("value")));
        }
        return found;
    }

    /** The value of the input with this {@code name}. */
    public static String valueNamed(String name, String html) {
        for (HTMLInput candidate : inputsIn(html)) {
            if (name.equals(candidate.name)) {
                return candidate.value;
            }
        }
        return null;
    }

    /**
     * The value of the input with this id, falling back to its name.
     *
     * <p>The router labels the two CHAP fields inconsistently across firmware
     * versions, so both spellings have to be tried.
     */
    public static String valueIdentifiedBy(String identifier, String html) {
        List<HTMLInput> found = inputsIn(html);
        for (HTMLInput candidate : found) {
            if (identifier.equals(candidate.id)) {
                return candidate.value;
            }
        }
        for (HTMLInput candidate : found) {
            if (identifier.equals(candidate.name)) {
                return candidate.value;
            }
        }
        return null;
    }

    /**
     * Every field that has both a key and a value, ready to hang off a URL.
     *
     * <p>A key that appears more than once keeps the position of its first
     * appearance and the value of its last.
     */
    public static List<String[]> queryItems(String html) {
        List<String> order = new ArrayList<String>();
        Map<String, String> values = new LinkedHashMap<String, String>();

        for (HTMLInput candidate : inputsIn(html)) {
            String key = candidate.key();
            String value = candidate.value;
            if (key == null || value == null || value.isEmpty()) {
                continue;
            }
            if (!values.containsKey(key)) {
                order.add(key);
            }
            values.put(key, value);
        }

        List<String[]> result = new ArrayList<String[]>(order.size());
        for (String key : order) {
            result.add(new String[] {key, values.get(key)});
        }
        return result;
    }

    /** Where the page's first form posts to, if it says. */
    public static String formAction(String html) {
        if (html == null) {
            return null;
        }
        Matcher matcher = FORM_TAG.matcher(html);
        if (!matcher.find()) {
            return null;
        }
        String action = attributesIn(matcher.group()).get("action");
        return action == null || action.isEmpty() ? null : action;
    }

    private static Map<String, String> attributesIn(String tag) {
        Map<String, String> result = new LinkedHashMap<String, String>();
        Matcher matcher = ATTRIBUTE.matcher(tag);
        while (matcher.find()) {
            String quoted = matcher.group(2);
            if (quoted == null) {
                quoted = matcher.group(3);
            }
            if (quoted == null) {
                quoted = matcher.group(4);
            }
            result.put(matcher.group(1).toLowerCase(), decodeEntities(quoted == null ? "" : quoted));
        }
        return result;
    }

    /** The handful of entities that actually turn up in these values. */
    private static String decodeEntities(String text) {
        if (text.indexOf('&') < 0) {
            return text;
        }
        for (String[] entity : ENTITIES) {
            text = text.replace(entity[0], entity[1]);
        }
        // Ampersand goes last, so `&amp;lt;` ends up as `&lt;` and not as `<`.
        return text.replace("&amp;", "&");
    }
}
