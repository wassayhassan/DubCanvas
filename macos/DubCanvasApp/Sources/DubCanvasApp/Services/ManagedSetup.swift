import Foundation
import CryptoKit

@MainActor
final class ManagedSetup: ObservableObject {
    static let shared = ManagedSetup()
    nonisolated static var directory: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/DubCanvas/Runtime")
    }
    nonisolated static var backend: URL? { Bundle.main.resourceURL?.appendingPathComponent("backend") }
    nonisolated static func fingerprint(at backend: URL) -> String? {
        guard let requirements = try? Data(contentsOf: backend.appendingPathComponent("requirements.txt")),
              let constraints = try? Data(contentsOf: backend.appendingPathComponent("constraints.txt")),
              let managed = try? Data(contentsOf: backend.appendingPathComponent("requirements-managed-core.txt")) else { return nil }
        return SHA256.hash(data: requirements + constraints + managed).map { String(format: "%02x", $0) }.joined()
    }
    nonisolated static func coreReady(at backend: URL) -> Bool {
        guard FileManager.default.isExecutableFile(atPath: directory.appendingPathComponent("core/bin/python").path),
              let data = try? Data(contentsOf: directory.appendingPathComponent("core-ready.json")),
              let marker = try? JSONSerialization.jsonObject(with: data) as? [String: String],
              let expected = fingerprint(at: backend) else { return false }
        return marker["fingerprint"] == expected
    }
    @Published var needsSetup: Bool
    @Published var busy = false
    @Published var status = "Download the app's tools and models to get started."
    @Published var log = ""
    @Published var error: String?
    @Published var logURL: URL?
    private var process: Process?
    private var output: Pipe?
    private var logHandle: FileHandle?

    private init() {
        let fm = FileManager.default
        if let backend = Self.backend, fm.fileExists(atPath: backend.appendingPathComponent("anime_dubber").path) {
            needsSetup = !fm.isExecutableFile(atPath: backend.appendingPathComponent(".venv/bin/python").path) && !Self.coreReady(at: backend)
        } else { needsSetup = false } // Development checkout keeps its existing workflow.
    }

    func start(quality: Bool, token: String) {
        guard !busy else { return }
        guard !AppState.hasRunningJobs else { error = "Finish or pause running jobs before setup."; return }
        if quality && token.isEmpty {
            error = "Accept the speaker model conditions and enter your Hugging Face read token before starting quality setup."; return
        }
        if quality && ProcessInfo.processInfo.physicalMemory < 44_000_000_000 {
            error = "This quality model configuration requires at least 48 GB memory. Install standard processing on this Mac."; return
        }
        guard let resources = Bundle.main.resourceURL, let backend = Self.backend else { error = "Setup files are missing."; return }
        let uv = resources.appendingPathComponent("tools/uv")
        guard FileManager.default.isExecutableFile(atPath: uv.path) else { error = "Download the complete DubCanvas app to use automatic setup."; return }
        busy = true; error = nil; log = ""; status = "Preparing downloads…"
        do {
            try FileManager.default.createDirectory(at: Self.directory, withIntermediateDirectories: true)
            let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/DubCanvas")
            try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
            let url = logs.appendingPathComponent("setup-\(UUID().uuidString).log")
            FileManager.default.createFile(atPath: url.path, contents: nil)
            logURL = url; logHandle = try FileHandle(forWritingTo: url)
            let task = Process(), pipe = Pipe()
            task.executableURL = uv
            task.arguments = ["run", "--no-project", "--managed-python", "--python", "3.13", "python",
                              backend.appendingPathComponent("tools/managed_setup.py").path,
                              "--directory", Self.directory.path, "--uv", uv.path] + (quality ? ["--quality"] : [])
            task.currentDirectoryURL = backend
            var environment = ProcessInfo.processInfo.environment
            environment["UV_PYTHON_INSTALL_DIR"] = Self.directory.appendingPathComponent("python").path
            environment["UV_CACHE_DIR"] = Self.directory.appendingPathComponent("cache").path
            environment["PYTHONUNBUFFERED"] = "1"
            environment["PATH"] = resources.appendingPathComponent("tools").path + ":" + (environment["PATH"] ?? "/usr/bin:/bin")
            if !token.isEmpty { environment["HF_TOKEN"] = token }
            task.environment = environment
            task.standardOutput = pipe; task.standardError = pipe
            pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                guard !data.isEmpty else { return }
                Task { @MainActor in
                    guard let self else { return }
                    // Hub never prints the token; keep credentials out of diagnostics defensively.
                    let text = String(decoding: data, as: UTF8.self).replacingOccurrences(of: token.isEmpty ? "\u{0}" : token, with: "[redacted]")
                    try? self.logHandle?.write(contentsOf: Data(text.utf8))
                    self.log = String((self.log + text).suffix(24_000))
                    if let last = text.split(whereSeparator: \.isNewline).last { self.status = String(last.prefix(240)) }
                }
            }
            task.terminationHandler = { [weak self] finished in
                Task { @MainActor in
                    guard let self else { return }
                    self.output?.fileHandleForReading.readabilityHandler = nil
                    try? self.logHandle?.close(); self.logHandle = nil
                    self.busy = false; self.process = nil; self.output = nil
                    if finished.terminationStatus == 0 {
                        self.needsSetup = false
                        self.status = quality ? "Quality processing is installed. Start with a short video to test your Mac." : "DubCanvas is ready."
                        AppState.completeManagedSetup(quality: quality)
                    } else {
                        self.error = "Setup stopped. Check the log, then retry; completed environments and cached models are kept."
                    }
                }
            }
            process = task; output = pipe
            try task.run()
        } catch {
            busy = false; self.error = error.localizedDescription
            try? logHandle?.close(); logHandle = nil; process = nil; output = nil
        }
    }
}
