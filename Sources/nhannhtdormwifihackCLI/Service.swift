import ArgumentParser
import Foundation
import nhannhtdormwifihackKit

/// Registering the watcher with launchd, so it starts at login and is restarted
/// if it ever dies. This is what the Windows build did with a registry key under
/// CurrentVersion\Run.
enum LaunchAgent {
    static let label = "io.vn.nhannht.nhannhtdormwifihack"

    static var plistURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(label).plist")
    }

    /// The agent description launchd reads.
    ///
    /// Built from a dictionary and serialised, not interpolated into XML by
    /// hand. A binary or settings path holding an ampersand or an angle bracket
    /// used to produce a malformed plist that launchctl rejected with a status
    /// number naming nothing useful, and a path could inject keys of its own.
    static func plistData(executable: String, settingsPath: String?, verbose: Bool) throws -> Data {
        var arguments = [executable, "run"]
        if verbose {
            arguments.append("--verbose")
        }
        if let settingsPath {
            arguments.append("--settings")
            arguments.append(settingsPath)
        }

        let logDirectory = Settings.supportDirectory.path

        let agent: [String: Any] = [
            "Label": label,
            "ProgramArguments": arguments,
            "RunAtLoad": true,
            "KeepAlive": true,
            "ProcessType": "Background",
            "StandardOutPath": "\(logDirectory)/launchd.out.log",
            "StandardErrorPath": "\(logDirectory)/launchd.err.log",
        ]

        return try PropertyListSerialization.data(
            fromPropertyList: agent,
            format: .xml,
            options: 0
        )
    }

    @discardableResult
    static func launchctl(_ arguments: [String]) throws -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        try process.run()
        process.waitUntilExit()
        return process.terminationStatus
    }
}

struct Install: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Start the watcher at login, and keep it running.",
        discussion: """
            Writes a launchd agent to ~/Library/LaunchAgents and loads it. Build \
            with `swift build -c release` and pass --executable pointing at the \
            release binary, or copy the binary somewhere permanent first. A path \
            inside .build will stop working the moment you clean the project.
            """
    )

    @OptionGroup var options: GlobalOptions

    @Option(help: "Path to the binary launchd should run.")
    var executable: String?

    mutating func run() async throws {
        let binary =
            executable.map { ($0 as NSString).expandingTildeInPath }
            ?? URL(fileURLWithPath: CommandLine.arguments[0])
                .resolvingSymlinksInPath()
                .standardizedFileURL
                .path

        guard FileManager.default.isExecutableFile(atPath: binary) else {
            throw ValidationError("Not an executable file: \(binary)")
        }

        if binary.contains("/.build/") {
            Console.line(
                """
                Warning: \(binary) is inside the build directory. It will stop \
                working when you clean the project. Copy it somewhere permanent \
                and rerun with --executable.
                """
            )
        }

        let url = LaunchAgent.plistURL
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        // Replacing an agent that is already loaded needs it unloaded first.
        _ = try? LaunchAgent.launchctl(["bootout", "gui/\(getuid())/\(LaunchAgent.label)"])

        let contents = try LaunchAgent.plistData(
            executable: binary,
            settingsPath: options.settingsPath,
            verbose: options.verbose
        )
        try contents.write(to: url, options: .atomic)

        let status = try LaunchAgent.launchctl(["bootstrap", "gui/\(getuid())", url.path])
        guard status == 0 else {
            throw ValidationError(
                "Wrote \(url.path) but launchctl bootstrap failed with status \(status)."
            )
        }

        Console.line("Installed. The watcher will start at login and is running now.")
        Console.line("Agent:  \(url.path)")
        Console.line("Logs:   \(Settings.supportDirectory.path)/launchd.out.log")
        if !options.verbose {
            Console.line(
                "The agent logs warnings and failures only. Reinstall with --verbose to have "
                    + "it log every step."
            )
        }
    }
}

struct Uninstall: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Stop the watcher and remove it from login."
    )

    mutating func run() async throws {
        let url = LaunchAgent.plistURL

        _ = try? LaunchAgent.launchctl(["bootout", "gui/\(getuid())/\(LaunchAgent.label)"])

        if FileManager.default.fileExists(atPath: url.path) {
            try FileManager.default.removeItem(at: url)
            Console.line("Removed \(url.path).")
        } else {
            Console.line("There was no agent installed.")
        }
    }
}
