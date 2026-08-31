import Foundation
import Testing

@testable import nhannhtdormwifihackKit

@Suite("Only one watcher at a time")
struct RunLockTests {

    private func scratchLock() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("watcher-\(UUID().uuidString).lock")
    }

    @Test("A second watcher cannot start while the first holds the lock")
    func secondIsRefused() {
        // Two watchers is not a harmless duplicate. At the moment a session
        // lapses both see "signed out" and both post to /login, and the router
        // rejects the second for having too many sessions open.
        let url = scratchLock()
        defer { try? FileManager.default.removeItem(at: url) }

        let first = RunLock.acquire(at: url)
        #expect(first != nil)

        let second = RunLock.acquire(at: url)
        #expect(second == nil, "A second watcher was allowed to start")

        _ = first
    }

    @Test("The lock is released when the holder goes away")
    func releasedOnDeinit() {
        // The lock belongs to the process, so the kernel drops it however the
        // process ends. A file holding a process id would go stale after a
        // crash and lock the user out of their own tool.
        let url = scratchLock()
        defer { try? FileManager.default.removeItem(at: url) }

        do {
            let holder = RunLock.acquire(at: url)
            #expect(holder != nil)
        }

        let afterwards = RunLock.acquire(at: url)
        #expect(afterwards != nil, "The lock outlived its holder")
    }

    @Test("Separate lock files do not block each other")
    func differentFilesAreIndependent() {
        let a = scratchLock()
        let b = scratchLock()
        defer {
            try? FileManager.default.removeItem(at: a)
            try? FileManager.default.removeItem(at: b)
        }

        let first = RunLock.acquire(at: a)
        let second = RunLock.acquire(at: b)

        #expect(first != nil)
        #expect(second != nil)
        _ = (first, second)
    }
}
