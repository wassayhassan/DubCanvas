import AppKit
import CryptoKit
import Foundation
import SwiftUI

struct AppUpdateManifest: Decodable, Sendable {
    let schema: Int
    let revision: String
    let sourceTimestamp: Int
    let version: String
    let sha256: String
    var downloadURL: URL { URL(string: "https://github.com/wassayhassan/DubCanvas/releases/download/app-updates/DubCanvas-update-\(revision).zip")! }
    func validate() throws {
        guard schema == 1, revision.range(of: "^[a-f0-9]{40}$", options: .regularExpression) != nil,
              sha256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil, sourceTimestamp > 0 else {
            throw UpdateFailure("The update manifest is invalid. The installed app was not changed.")
        }
    }
}
struct UpdateFailure: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

@MainActor
final class AppUpdater: ObservableObject {
    static let shared = AppUpdater()
    let diagnostics = DiagnosticStore()
    @Published var busy = false
    @Published var status = "" {
        didSet { diagnostics.append("\(Date().ISO8601Format()) [update] \(status)\n") }
    }
    @Published var offeredUpdate: AppUpdateManifest?
    @Published var error: String? {
        didSet { if let error { diagnostics.append("\(Date().ISO8601Format()) [update] [error] \(error)\n") } }
    }

    func check() async {
        guard !busy else { return }
        busy = true; error = nil; offeredUpdate = nil; status = "Checking for updates…"
        defer { busy = false }
        do {
            let url = URL(string: "https://github.com/wassayhassan/DubCanvas/releases/download/app-updates/latest-update.json")!
            var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 30)
            request.setValue("DubCanvas-Updater", forHTTPHeaderField: "User-Agent")
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let response = response as? HTTPURLResponse else { throw UpdateFailure("The update server returned no response.") }
            if response.statusCode == 404 { status = "No automatic update has been published yet."; return }
            guard response.statusCode == 200 else { throw UpdateFailure("Update check failed (HTTP \(response.statusCode)). Try again later.") }
            let manifest = try JSONDecoder().decode(AppUpdateManifest.self, from: data)
            try manifest.validate()
            let current = Bundle.main.object(forInfoDictionaryKey: "DubCanvasSourceRevision") as? String ?? ""
            let timestamp = Int(Bundle.main.object(forInfoDictionaryKey: "DubCanvasSourceTimestamp") as? String ?? "0") ?? 0
            if manifest.revision == current || (timestamp > 0 && manifest.sourceTimestamp <= timestamp) {
                status = "DubCanvas is up to date."
            } else {
                offeredUpdate = manifest
                status = "A new DubCanvas build is available (\(manifest.revision.prefix(8)))."
            }
        } catch { self.error = error.localizedDescription; status = "Could not check for updates." }
    }

    func installApprovedUpdate(closeUpdateWindow: () -> Void) async {
        guard !busy, let manifest = offeredUpdate else { return }
        guard !AppState.hasRunningJobs else { error = "Finish or pause all running jobs before updating."; return }
        busy = true; error = nil
        var work: URL?
        var staged: URL?
        defer { busy = false }
        do {
            let fm = FileManager.default
            let target = Bundle.main.bundleURL
            guard target.pathExtension == "app", fm.isWritableFile(atPath: target.deletingLastPathComponent().path) else {
                throw UpdateFailure("Move DubCanvas to a writable Applications folder before updating.")
            }
            guard fm.fileExists(atPath: target.appendingPathComponent("Contents/Resources/backend/.venv/bin/python").path) else {
                throw UpdateFailure("Automatic updates require an app built with its Python environment. Rebuild once using macos/package_app.sh.")
            }
            let directory = fm.temporaryDirectory.appendingPathComponent("DubCanvas-update-\(UUID().uuidString)")
            work = directory
            try fm.createDirectory(at: directory, withIntermediateDirectories: true)
            status = "Downloading update…"
            let (download, response) = try await URLSession.shared.download(from: manifest.downloadURL)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw UpdateFailure("The update download failed.") }
            let archive = directory.appendingPathComponent("update.zip")
            try fm.moveItem(at: download, to: archive)
            status = "Verifying update and preparing the installed environment…"
            let destination = target.deletingLastPathComponent().appendingPathComponent(".DubCanvas-update-\(UUID().uuidString).app")
            staged = destination
            let logURL = fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/DubCanvas/update-\(UUID().uuidString).log")
            try fm.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            fm.createFile(atPath: logURL.path, contents: nil)
            diagnostics.jobPaths["update"] = logURL.path
            let helper = try await Task.detached {
                try Self.prepare(archive: archive, work: directory, staged: destination, current: target, manifest: manifest, logURL: logURL)
            }.value
            guard !AppState.hasRunningJobs else { throw UpdateFailure("A job started while the update was downloading. Pause it and try again.") }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/zsh")
            process.arguments = [helper.path, String(ProcessInfo.processInfo.processIdentifier), target.path, destination.path, logURL.path]
            try process.run()
            status = "Restarting DubCanvas…"
            closeUpdateWindow()
            Self.finishRestart(shutdown: { AppState.shutdownAllForUpdate() },
                               terminate: { NSApplication.shared.terminate(nil) })
        } catch {
            self.error = error.localizedDescription; status = "Update stopped. The installed app is unchanged."
            if let staged { try? FileManager.default.removeItem(at: staged) }
            if let work { try? FileManager.default.removeItem(at: work) }
        }
    }

    static func finishRestart(
        shutdown: @escaping @MainActor () -> Void,
        terminate: @escaping @MainActor () -> Void
    ) {
        // Let the update window close before AppKit evaluates termination.
        DispatchQueue.main.async {
            shutdown()
            terminate()
        }
    }

    nonisolated static func prepare(archive: URL, work: URL, staged: URL, current: URL,
                                    manifest: AppUpdateManifest, logURL: URL) throws -> URL {
        let fm = FileManager.default
        let file = try FileHandle(forReadingFrom: archive)
        defer { try? file.close() }
        var hash = SHA256()
        while let chunk = try file.read(upToCount: 1_048_576), !chunk.isEmpty { hash.update(data: chunk) }
        guard hash.finalize().map({ String(format: "%02x", $0) }).joined() == manifest.sha256 else {
            throw UpdateFailure("Update checksum verification failed. The installed app was not changed.")
        }
        let unpacked = work.appendingPathComponent("unpacked")
        try run("/usr/bin/ditto", ["-x", "-k", archive.path, unpacked.path], logURL: logURL)
        let app = unpacked.appendingPathComponent("DubCanvas.app")
        guard let info = try PropertyListSerialization.propertyList(from: Data(contentsOf: app.appendingPathComponent("Contents/Info.plist")), format: nil) as? [String: Any],
              info["CFBundleIdentifier"] as? String == "com.animedubber.app",
              info["DubCanvasSourceRevision"] as? String == manifest.revision,
              fm.isExecutableFile(atPath: app.appendingPathComponent("Contents/MacOS/DubCanvas").path) else {
            throw UpdateFailure("The downloaded app does not match this update.")
        }
        try run("/usr/bin/codesign", ["--verify", "--deep", "--strict", app.path], logURL: logURL)
        var requirementsChanged = false
        let requirementNames = ["requirements.txt", "requirements-cross-platform.txt", "requirements-premium-voices.txt"]
        for name in requirementNames + ["constraints.txt"] {
            let relative = "Contents/Resources/backend/\(name)"
            if (try? Data(contentsOf: current.appendingPathComponent(relative))) != (try Data(contentsOf: app.appendingPathComponent(relative))) { requirementsChanged = true }
        }
        do {
            try run("/usr/bin/ditto", [app.path, staged.path], logURL: logURL)
            try run("/usr/bin/ditto", [current.appendingPathComponent("Contents/Resources/backend/.venv").path,
                                      staged.appendingPathComponent("Contents/Resources/backend/.venv").path], logURL: logURL)
            let backend = staged.appendingPathComponent("Contents/Resources/backend")
            let python = backend.appendingPathComponent(".venv/bin/python").path
            if requirementsChanged {
                try run(python, ["-m", "pip", "install"] + requirementNames.flatMap { ["-r", backend.appendingPathComponent($0).path] }, logURL: logURL, directory: backend)
            }
            try run(python, ["-m", "pip", "check"], logURL: logURL)
            try run(python, ["-m", "dubcanvas", "--help"], logURL: logURL, directory: backend)
            try run("/usr/bin/codesign", ["--force", "--deep", "--sign", "-", staged.path], logURL: logURL)
            let helper = work.appendingPathComponent("install_update.sh")
            try fm.copyItem(at: app.appendingPathComponent("Contents/Resources/install_update.sh"), to: helper)
            return helper
        } catch { try? fm.removeItem(at: staged); throw error }
    }

    nonisolated static func run(_ executable: String, _ arguments: [String], logURL: URL, directory: URL? = nil) throws {
        let process = Process()
        let output = try FileHandle(forWritingTo: logURL)
        defer { try? output.close() }
        _ = try output.seekToEnd()
        try output.write(contentsOf: Data(("\(Date().ISO8601Format()) \(executable) \(arguments.joined(separator: " "))\n").utf8))
        process.executableURL = URL(fileURLWithPath: executable); process.arguments = arguments
        if let directory {
            process.currentDirectoryURL = directory
            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONPATH"] = directory.path; process.environment = environment
        }
        process.standardOutput = output; process.standardError = output
        try process.run(); process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw UpdateFailure("Update preparation failed (exit \(process.terminationStatus)). See \(logURL.path).") }
    }
}

struct AppUpdateView: View {
    @ObservedObject private var updater = AppUpdater.shared
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("DubCanvas Updates").font(.title2.bold())
            Text(updater.status)
            if updater.busy { ProgressView() }
            if updater.offeredUpdate != nil {
                Text("Update and Restart installs the new app, preserves your projects and models, and restarts DubCanvas. Finish or pause all jobs first.")
                    .foregroundStyle(.secondary)
            }
            if let error = updater.error {
                Text(error).foregroundStyle(.red).textSelection(.enabled)
            }
            HStack {
                Spacer()
                Button(updater.offeredUpdate == nil ? "Close" : "Later") { dismiss() }
                    .disabled(updater.busy)
                if updater.offeredUpdate != nil {
                    Button("Update and Restart") {
                        Task { await updater.installApprovedUpdate(closeUpdateWindow: { dismiss() }) }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(updater.busy)
                }
            }
        }
        .padding(24)
        .frame(width: 500)
    }
}
