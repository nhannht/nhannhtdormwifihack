package vn.io.nhannht.dormwifihack.engine;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * One line of the running commentary, and the file it is mirrored to.
 *
 * <p>Safe to write from the watcher's thread while the window reads it: every
 * public method takes the lock, and subscriber callbacks are invoked after it is
 * released.
 */
public final class ActivityLog {

    /** How much history to keep on disk, in bytes. */
    private static final int LONGEST_KEPT_FILE = 512 * 1024;

    public static final String INFO = "info";
    public static final String SUCCESS = "success";
    public static final String WARNING = "warning";
    public static final String FAILURE = "failure";

    /** One thing that happened. */
    public static final class Entry {

        public final long millis;
        public final String level;
        public final String message;

        Entry(long millis, String level, String message) {
            this.millis = millis;
            this.level = level;
            this.message = message;
        }

        /** {@code [16:57:55] Gateway answered with a login page} */
        public String formatted() {
            return "[" + clock() + "] " + message;
        }

        /** The same line as written to disk: {@code s [16:57:55] Logged in.} */
        public String forFile() {
            return level.charAt(0) + " " + formatted();
        }

        private String clock() {
            return new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date(millis));
        }
    }

    private final File file;
    private final int limit;
    private final List<Entry> entries = new ArrayList<Entry>();
    private final Map<Integer, Listener> subscribers = new LinkedHashMap<Integer, Listener>();
    private int nextSubscriber;
    private final Object lock = new Object();

    /** A live feed of new entries. */
    public interface Listener {
        void onEntry(Entry entry);
    }

    public ActivityLog(File file, int limit) {
        this.file = file;
        this.limit = limit;
        if (this.file != null) {
            File parent = this.file.getParentFile();
            if (parent != null && !parent.exists()) {
                parent.mkdirs();
            }
            trim(this.file, LONGEST_KEPT_FILE);
        }
    }

    public ActivityLog(File file) {
        this(file, 2000);
    }

    // MARK: - Writing

    public void log(String level, String message) {
        Entry entry = new Entry(System.currentTimeMillis(), level, message);
        List<Listener> listeners;

        synchronized (lock) {
            entries.add(entry);
            if (entries.size() > limit) {
                entries.subList(0, entries.size() - limit).clear();
            }
            listeners = new ArrayList<Listener>(subscribers.values());
        }

        appendToFile(entry);

        for (Listener listener : listeners) {
            try {
                listener.onEntry(entry);
            } catch (RuntimeException ignored) {
                // A broken listener is not the log's problem.
            }
        }
    }

    public void info(String message) {
        log(INFO, message);
    }

    public void success(String message) {
        log(SUCCESS, message);
    }

    public void warning(String message) {
        log(WARNING, message);
    }

    public void failure(String message) {
        log(FAILURE, message);
    }

    // MARK: - Reading

    public List<Entry> all() {
        synchronized (lock) {
            return new ArrayList<Entry>(entries);
        }
    }

    public List<Entry> recent(int count) {
        synchronized (lock) {
            int from = Math.max(entries.size() - count, 0);
            return new ArrayList<Entry>(entries.subList(from, entries.size()));
        }
    }

    /** Nothing to show yet? */
    public boolean isEmpty() {
        synchronized (lock) {
            return entries.isEmpty();
        }
    }

    public Runnable subscribe(Listener listener) {
        final int identifier;
        synchronized (lock) {
            identifier = nextSubscriber++;
            subscribers.put(identifier, listener);
        }
        return new Runnable() {
            @Override
            public void run() {
                synchronized (lock) {
                    subscribers.remove(identifier);
                }
            }
        };
    }

    public File file() {
        return file;
    }

    private void appendToFile(Entry entry) {
        if (file == null) {
            return;
        }
        FileOutputStream stream = null;
        try {
            stream = new FileOutputStream(file, true);
            stream.write((entry.forFile() + "\n").getBytes("UTF-8"));
        } catch (IOException ignored) {
            // A log that cannot be written is not a reason to stop watching.
        } finally {
            if (stream != null) {
                try {
                    stream.close();
                } catch (IOException ignored) {
                    // Nothing useful to do about it.
                }
            }
        }
    }

    /**
     * Keep only the last {@code keepBytes} of the file, starting at a line
     * boundary, so one long-running install cannot fill the disk a line at a time.
     */
    private static void trim(File file, int keepBytes) {
        if (!file.exists() || file.length() <= keepBytes) {
            return;
        }
        RandomAccessFile handle = null;
        try {
            handle = new RandomAccessFile(file, "rw");
            long start = handle.length() - keepBytes;
            handle.seek(start);
            byte[] tail = new byte[(int) (handle.length() - start)];
            handle.readFully(tail);

            int newline = -1;
            for (int index = 0; index < tail.length; index++) {
                if (tail[index] == '\n') {
                    newline = index;
                    break;
                }
            }
            int from = newline + 1;

            handle.seek(0);
            handle.setLength(0);
            handle.write(tail, from, tail.length - from);
        } catch (IOException ignored) {
            // A log that cannot be trimmed is not worth failing over.
        } finally {
            if (handle != null) {
                try {
                    handle.close();
                } catch (IOException ignored) {
                    // Nothing useful to do about it.
                }
            }
        }
    }

    /** The last {@code count} entries of a log file that is already on disk. */
    public static List<Entry> entriesInFile(File file, int count) {
        List<Entry> found = new ArrayList<Entry>();
        if (file == null || !file.exists()) {
            return found;
        }
        try {
            byte[] data = new byte[(int) Math.min(file.length(), 256 * 1024)];
            RandomAccessFile handle = new RandomAccessFile(file, "r");
            try {
                handle.seek(Math.max(file.length() - data.length, 0));
                handle.readFully(data);
            } finally {
                handle.close();
            }
            String[] lines = new String(data, "UTF-8").split("\n");
            List<String> tail = new ArrayList<String>();
            for (String line : lines) {
                if (!line.isEmpty()) {
                    tail.add(line);
                }
            }
            if (tail.size() > count) {
                tail = tail.subList(tail.size() - count, tail.size());
            }
            for (String line : tail) {
                Entry parsed = parseFileLine(line);
                if (parsed != null) {
                    found.add(parsed);
                }
            }
        } catch (IOException ignored) {
            return Collections.emptyList();
        }
        return found;
    }

    /**
     * Read a line back, in either the current shape or the older one. Returns null
     * for anything that is not a log line, such as the session banner.
     */
    static Entry parseFileLine(String line) {
        String rest = line;
        String level = INFO;

        if (rest.length() > 2 && rest.charAt(1) == ' ') {
            switch (rest.charAt(0)) {
                case 'i':
                    level = INFO;
                    rest = rest.substring(2);
                    break;
                case 's':
                    level = SUCCESS;
                    rest = rest.substring(2);
                    break;
                case 'w':
                    level = WARNING;
                    rest = rest.substring(2);
                    break;
                case 'f':
                    level = FAILURE;
                    rest = rest.substring(2);
                    break;
                default:
                    break;
            }
        }

        if (!rest.startsWith("[") || rest.indexOf(']') < 0) {
            return null;
        }

        String clock = rest.substring(1, rest.indexOf(']'));
        long millis;
        try {
            millis = new SimpleDateFormat("HH:mm:ss", Locale.US).parse(clock).getTime();
        } catch (java.text.ParseException error) {
            return null;
        }

        String message = rest.substring(rest.indexOf(']') + 1).trim();
        if (message.isEmpty()) {
            return null;
        }
        return new Entry(millis, level, message);
    }
}
