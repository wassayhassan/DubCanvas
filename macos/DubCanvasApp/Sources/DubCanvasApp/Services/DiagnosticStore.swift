import AppKit
import Foundation

@MainActor
final class DiagnosticStore {
    let sessionURL: URL
    var jobPaths: [String: String] = [:]

    init() {
        let directory = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/DubCanvas", isDirectory: true)
        sessionURL = directory.appendingPathComponent("session-\(UUID().uuidString).log")
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let revision = Bundle.main.object(forInfoDictionaryKey: "DubCanvasSourceRevision") as? String ?? "development"
        try? "DubCanvas diagnostics\nBuild: \(revision)\nSystem: \(ProcessInfo.processInfo.operatingSystemVersionString)\nStarted: \(Date().ISO8601Format())\n\n"
            .write(to: sessionURL, atomically: true, encoding: .utf8)
    }
    func append(_ text: String) {
        guard let handle = try? FileHandle(forWritingTo: sessionURL) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: Data(text.utf8))
    }
    func fullReport(extraPaths: [String]) -> String {
        var report = (try? String(contentsOf: sessionURL, encoding: .utf8)) ?? ""
        for path in Set(Array(jobPaths.values) + extraPaths).sorted() {
            if let text = try? String(contentsOfFile: path, encoding: .utf8) {
                report += "\n\n=== SAVED JOB LOG: \(URL(fileURLWithPath: path).lastPathComponent) ===\n" + text
            }
        }
        return report
    }
    func stageReport(stage: String, jobID: String) -> String? {
        guard let path = jobPaths[jobID],
              let content = try? String(contentsOf: URL(fileURLWithPath: path).deletingPathExtension().appendingPathExtension("jsonl"), encoding: .utf8) else { return nil }
        return content.split(separator: "\n").compactMap { line -> String? in
            guard let data = String(line).data(using: .utf8),
                  let item = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  item["stage"] as? String == stage else { return nil }
            let details = item["details"] as? [String: Any] ?? [:]
            let detailText = (try? JSONSerialization.data(withJSONObject: details, options: [.prettyPrinted, .sortedKeys]))
                .flatMap { String(data: $0, encoding: .utf8) } ?? ""
            return "\(item["timestamp"] ?? "") [\(jobID)] [\(stage)] [\(item["level"] ?? "info")] \(item["message"] ?? "")\n\(details.isEmpty ? "" : detailText)"
        }.joined(separator: "\n")
    }
    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}
