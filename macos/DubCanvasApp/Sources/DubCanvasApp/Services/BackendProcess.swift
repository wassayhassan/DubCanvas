import Foundation

enum BackendProcessError: LocalizedError {
    case backendNotFound
    case runtimeNotFound(String)
    case processNotRunning
    case invalidRequest

    var errorDescription: String? {
        switch self {
        case .backendNotFound:
            "Could not locate the DubCanvas Python backend."
        case .runtimeNotFound(let detail):
            "Could not locate a Python runtime for DubCanvas. \(detail)"
        case .processNotRunning:
            "The Python backend is not running."
        case .invalidRequest:
            "The backend request could not be encoded."
        }
    }
}

final class BackendProcess {
    private var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?
    private var stdoutBuffer = Data()
    private let writeLock = NSLock()

    private var onMessage: (([String: Any]) -> Void)?
    private var onDiagnostic: ((String) -> Void)?
    private var onTermination: ((Int32) -> Void)?

    func start(
        onMessage: @escaping ([String: Any]) -> Void,
        onDiagnostic: @escaping (String) -> Void,
        onTermination: @escaping (Int32) -> Void
    ) throws {
        if process?.isRunning == true { return }

        self.onMessage = onMessage
        self.onDiagnostic = onDiagnostic
        self.onTermination = onTermination

        guard let backendRoot = locateBackendRoot() else {
            throw BackendProcessError.backendNotFound
        }

        let python = try locatePython(for: backendRoot)

        let process = Process()
        let input = Pipe()
        let output = Pipe()
        let error = Pipe()

        process.executableURL = python.url
        process.arguments = python.arguments + ["-m", "anime_dubber.transport.stdio_server"]
        process.currentDirectoryURL = backendRoot

        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONPATH"] = backendRoot.path
        var searchPaths = (environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")
            .split(separator: ":").map(String.init)
        for directory in ["/usr/local/bin", "/opt/homebrew/bin"] {
            if FileManager.default.fileExists(atPath: directory), !searchPaths.contains(directory) {
                searchPaths.insert(directory, at: 0)
            }
        }
        environment["PATH"] = searchPaths.joined(separator: ":")
        process.environment = environment

        process.standardInput = input
        process.standardOutput = output
        process.standardError = error

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.consumeStdout(data)
        }

        error.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            self?.onDiagnostic?(text)
        }

        process.terminationHandler = { [weak self] process in
            self?.onTermination?(process.terminationStatus)
        }

        try process.run()

        self.process = process
        self.stdinPipe = input
        self.stdoutPipe = output
        self.stderrPipe = error
    }

    @discardableResult
    func send(method: String, params: [String: Any] = [:], id: String = UUID().uuidString) throws -> String {
        guard process?.isRunning == true, let input = stdinPipe else {
            throw BackendProcessError.processNotRunning
        }

        let request: [String: Any] = [
            "type": "request",
            "id": id,
            "method": method,
            "params": params,
        ]

        guard JSONSerialization.isValidJSONObject(request),
              var data = try? JSONSerialization.data(withJSONObject: request) else {
            throw BackendProcessError.invalidRequest
        }
        data.append(0x0A)

        writeLock.lock()
        defer { writeLock.unlock() }
        try input.fileHandleForWriting.write(contentsOf: data)
        return id
    }

    func stop() {
        guard let process, process.isRunning else { return }
        Self.requestShutdown(process: process, input: stdinPipe?.fileHandleForWriting, writeLock: writeLock)
    }

    static func requestShutdown(process: Process, input: FileHandle?, writeLock: NSLock) {
        // A stalled backend may stop reading stdin. Neither a full pipe nor an
        // in-flight writer may block the UI thread or delay application quit.
        DispatchQueue.global().async {
            writeLock.lock()
            defer { writeLock.unlock() }
            try? input?.write(contentsOf: Data("{\"type\":\"request\",\"id\":\"shutdown\",\"method\":\"shutdown\",\"params\":{}}\n".utf8))
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) {
            // Capture this process, so a later backend start cannot be stopped.
            if process.isRunning { process.terminate() }
        }
    }

    private func consumeStdout(_ data: Data) {
        stdoutBuffer.append(data)

        while let newline = stdoutBuffer.firstIndex(of: 0x0A) {
            let lineData = stdoutBuffer.prefix(upTo: newline)
            stdoutBuffer.removeSubrange(...newline)

            guard !lineData.isEmpty,
                  let object = try? JSONSerialization.jsonObject(with: Data(lineData)),
                  let dictionary = object as? [String: Any] else {
                if let text = String(data: lineData, encoding: .utf8), !text.isEmpty {
                    onDiagnostic?(text + "\n")
                }
                continue
            }
            onMessage?(dictionary)
        }
    }

    private func locateBackendRoot() -> URL? {
        let fileManager = FileManager.default
        let env = ProcessInfo.processInfo.environment

        if let explicit = env["ANIMEDUBBER_REPO_ROOT"], !explicit.isEmpty {
            let url = URL(fileURLWithPath: explicit, isDirectory: true)
            if isBackendRoot(url) { return url }
        }

        if let resourceURL = Bundle.main.resourceURL {
            let bundled = resourceURL.appendingPathComponent("backend", isDirectory: true)
            if isBackendRoot(bundled) { return bundled }
        }

        if let saved = UserDefaults.standard.string(forKey: "AnimeDubberBackendRoot"), !saved.isEmpty {
            let url = URL(fileURLWithPath: saved, isDirectory: true)
            if isBackendRoot(url) { return url }
        }

        var candidate = URL(fileURLWithPath: fileManager.currentDirectoryPath, isDirectory: true)
        for _ in 0..<10 {
            if isBackendRoot(candidate) {
                UserDefaults.standard.set(candidate.path, forKey: "AnimeDubberBackendRoot")
                return candidate
            }
            let parent = candidate.deletingLastPathComponent()
            if parent.path == candidate.path { break }
            candidate = parent
        }

        return nil
    }

    private func isBackendRoot(_ url: URL) -> Bool {
        FileManager.default.fileExists(
            atPath: url.appendingPathComponent("anime_dubber/__init__.py").path
        )
    }

    private struct PythonLaunch {
        let url: URL
        let arguments: [String]
    }

    private func locatePython(for backendRoot: URL) throws -> PythonLaunch {
        let fileManager = FileManager.default
        let embeddedCandidates = [
            backendRoot.appendingPathComponent(".venv/bin/python"),
            backendRoot.appendingPathComponent(".venv/bin/python3"),
        ]
        for candidate in embeddedCandidates where fileManager.isExecutableFile(atPath: candidate.path) {
            return PythonLaunch(url: candidate, arguments: [])
        }

        if let resources = Bundle.main.resourceURL {
            let futureRuntime = resources.appendingPathComponent("python/bin/python3")
            if fileManager.isExecutableFile(atPath: futureRuntime.path) {
                return PythonLaunch(url: futureRuntime, arguments: [])
            }
        }

        let bundleBackend = Bundle.main.resourceURL?
            .appendingPathComponent("backend", isDirectory: true)
            .standardizedFileURL
        if bundleBackend?.path == backendRoot.standardizedFileURL.path {
            throw BackendProcessError.runtimeNotFound(
                "This app bundle was built without the embedded backend environment. Rebuild it with macos/package_app.sh after running setup.sh."
            )
        }

        let candidates = [
            "/opt/homebrew/bin/python3.11",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ]
        for path in candidates where fileManager.isExecutableFile(atPath: path) {
            return PythonLaunch(url: URL(fileURLWithPath: path), arguments: [])
        }

        return PythonLaunch(
            url: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["python3"]
        )
    }
}
