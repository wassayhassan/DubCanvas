import AppKit
import SwiftUI

struct DubExportView: View {
    let projectName: String
    let dub: DubSummary
    @Environment(\.dismiss) private var dismiss

    private var exports: [(String, String, String)] {
        var items: [(String, String, String)] = []
        if let path = dub.artifacts["dubbed_video"] {
            items.append(("Dubbed video", "MP4", path))
        }
        if let path = dub.artifacts["translated_srt"] ?? dub.artifacts["english_srt"] {
            items.append(("Target subtitles", "SRT", path))
        }
        if let path = dub.artifacts["translated_vtt"] ?? dub.artifacts["english_vtt"] {
            items.append(("Target subtitles", "VTT", path))
        }
        if let path = dub.artifacts["dub_audio"] ?? dub.artifacts["dubbed_audio"] {
            items.append(("Dubbed audio", URL(fileURLWithPath: path).pathExtension.uppercased(), path))
        }
        return items
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("Export This Version").font(.title2.bold())
                Spacer()
                Button("Done") { dismiss() }
            }
            Text("\(projectName) / \(dub.title) · \(dub.language.uppercased())")
                .foregroundStyle(.secondary)
            if !dub.warnings.isEmpty {
                Label("\(dub.warnings.count) review note(s). Preview the video before sharing.", systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
            }
            ForEach(exports.indices, id: \.self) { index in
                let item = exports[index]
                GroupBox {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(item.0).fontWeight(.semibold)
                            Text("\(item.1) · \(URL(fileURLWithPath: item.2).lastPathComponent)")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Save…") { exportFile(item.2) }
                            .disabled(!FileManager.default.fileExists(atPath: item.2))
                    }
                }
            }
            if exports.isEmpty {
                ContentUnavailableView("No Files Ready", systemImage: "square.and.arrow.up",
                    description: Text("Subtitles become available after translation; the video appears after rendering."))
            }
            Text("Source subtitles are shared by the project and can be exported from its Subtitles screen.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(22)
    }
}
