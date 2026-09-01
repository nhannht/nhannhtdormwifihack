import Foundation

// macOS only. iOS has no way to launch a child process at all: `Process` is
// unavailable there, and a sandboxed app has nothing to launch even if it
// were. The one answer this file was used for on a phone, which router the
// device is using, comes from `DefaultRoute` instead, and that reads the
// kernel's routing table on both platforms.
#if os(macOS)

/// Running a system command and reading what it printed.
///
/// macOS keeps several answers this app needs behind command-line tools rather
/// than behind an API a sandboxed process can call, so there is no way around
/// launching one. Everything here is read-only or joins a network the user has
/// already saved the password for.
public enum Shell {

    /// Standard output of `path arguments`, or nil if it could not be run.
    ///
    /// The child is killed if it outlives `timeout`. A watcher that runs for
    /// weeks cannot afford to inherit a command that hangs.
    public static func run(
        _ path: String,
        _ arguments: [String],
        timeout: TimeInterval = 15
    ) -> String? {
        guard FileManager.default.isExecutableFile(atPath: path) else { return nil }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: path)
        process.arguments = arguments

        let output = Pipe()
        process.standardOutput = output
        process.standardError = Pipe()

        do {
            try process.run()
        } catch {
            return nil
        }

        // Read on another thread. Reading to end of file blocks until the child
        // closes the pipe, so doing it here would make the timeout unreachable
        // for the exact case it exists to handle.
        let finished = DispatchSemaphore(value: 0)
        let collected = Collected()

        DispatchQueue.global(qos: .utility).async {
            collected.data = output.fileHandleForReading.readDataToEndOfFile()
            finished.signal()
        }

        if finished.wait(timeout: .now() + timeout) == .timedOut {
            process.terminate()
            _ = finished.wait(timeout: .now() + 2)
        }
        process.waitUntilExit()

        return String(decoding: collected.data, as: UTF8.self)
    }

    /// A box, so the reading thread has somewhere to put the bytes.
    private final class Collected: @unchecked Sendable {
        var data = Data()
    }
}

#endif
