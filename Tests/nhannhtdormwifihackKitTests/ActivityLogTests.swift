import Foundation
import Testing

@testable import nhannhtdormwifihackKit

@Suite("The log file")
struct ActivityLogTests {

    private func scratchFile() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("log-\(UUID().uuidString).log")
    }

    @Test("Keeping history does not wipe what was already there")
    func historySurvivesARestart() async throws {
        // The window is meant to be opened hours after something went wrong.
        // Wiping the file at every launch threw away exactly the history worth
        // reading.
        let url = scratchFile()
        defer { try? FileManager.default.removeItem(at: url) }

        let first = ActivityLog(fileURL: url, truncateFile: false)
        await first.warning("something went wrong earlier")

        let second = ActivityLog(fileURL: url, truncateFile: false)
        await second.info("a later run")

        let text = try String(contentsOf: url, encoding: .utf8)
        #expect(text.contains("something went wrong earlier"))
        #expect(text.contains("a later run"))
    }

    @Test("Starting fresh still wipes the file when asked")
    func truncationStillWorks() async throws {
        let url = scratchFile()
        defer { try? FileManager.default.removeItem(at: url) }

        let first = ActivityLog(fileURL: url, truncateFile: false)
        await first.warning("from an old run")

        _ = ActivityLog(fileURL: url, truncateFile: true)

        let text = try String(contentsOf: url, encoding: .utf8)
        #expect(!text.contains("from an old run"))
    }

    @Test("A file kept across runs cannot grow without bound")
    func longFileIsTrimmed() async throws {
        // Keeping history means the file grows forever unless something stops
        // it. One long-running install would otherwise fill the disk a line at
        // a time.
        let url = scratchFile()
        defer { try? FileManager.default.removeItem(at: url) }

        let oversized = String(repeating: "an old line that no longer matters\n", count: 40_000)
        try Data(oversized.utf8).write(to: url)
        let before = try FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int ?? 0
        #expect(before > 1_000_000)

        _ = ActivityLog(fileURL: url, truncateFile: false)

        let after = try FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int ?? 0
        #expect(after < before)
        #expect(after < 600 * 1024)

        // Trimmed at a line boundary, so the file never opens mid-sentence.
        let text = try String(contentsOf: url, encoding: .utf8)
        #expect(text.hasPrefix("an old line"))
    }

    @Test("A subscriber hears entries logged after it arrives")
    func subscriberHearsNewEntries() async {
        // The log window is built on this feed.
        let log = ActivityLog()
        let stream = await log.subscribe()

        await log.success("it worked")

        var heard: LogEntry?
        for await entry in stream {
            heard = entry
            break
        }

        #expect(heard?.message == "it worked")
        #expect(heard?.level == .success)
    }
}
