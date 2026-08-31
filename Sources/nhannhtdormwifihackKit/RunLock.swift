import Foundation

/// Guarantees only one watcher runs at a time.
///
/// Two watchers is not a harmless duplicate. While a session is open both just
/// read `/status` and agree, but at the moment one lapses both see "signed out"
/// and both post to `/login`, and the router answers the second with its
/// too-many-sessions rejection. Nothing stopped that before: starting the app
/// and then running the command line tool gave you two.
///
/// This uses `flock`, so the lock belongs to the process and the kernel drops it
/// however the process ends, crash included. A file holding a process id would
/// go stale on a crash and lock the user out of their own tool.
public final class RunLock: @unchecked Sendable {

    private let descriptor: Int32

    private init(descriptor: Int32) {
        self.descriptor = descriptor
    }

    public static var defaultURL: URL {
        Settings.supportDirectory.appendingPathComponent("watcher.lock")
    }

    /// Take the lock, or return nil when another watcher already holds it.
    public static func acquire(at url: URL = defaultURL) -> RunLock? {
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        let descriptor = open(url.path, O_CREAT | O_RDWR, 0o600)
        guard descriptor >= 0 else { return nil }

        guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            close(descriptor)
            return nil
        }

        return RunLock(descriptor: descriptor)
    }

    deinit {
        flock(descriptor, LOCK_UN)
        close(descriptor)
    }
}
