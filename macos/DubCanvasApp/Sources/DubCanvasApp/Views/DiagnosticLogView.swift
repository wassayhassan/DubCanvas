import AppKit
import SwiftUI

struct DiagnosticLogView: View {
    @EnvironmentObject private var state: AppState
    @State private var showDetails = false
    @State private var search = ""
    private var groups: [[ActivityEntry]] {
        var sections: [[ActivityEntry]] = []
        var indices: [String: Int] = [:]
        for entry in state.activity {
            let key = entry.jobID + ":" + entry.stage
            if let index = indices[key] { sections[index].append(entry) }
            else { indices[key] = sections.count; sections.append([entry]) }
        }
        return sections
    }
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Button { DiagnosticStore.copy(state.diagnosticReport) } label: { Label("Copy All Logs", systemImage: "doc.on.doc") }
                Button { state.exportDiagnostics() } label: { Label("Save Logs…", systemImage: "square.and.arrow.down") }
                Button { NSWorkspace.shared.activateFileViewerSelecting([state.diagnosticStore.sessionURL]) } label: { Label("Show Log File", systemImage: "folder") }
                Spacer()
                Toggle("Technical details", isOn: $showDetails).toggleStyle(.checkbox)
            }.padding(10)
            TextField("Search logs", text: $search).textFieldStyle(.roundedBorder).padding(.horizontal, 10)
            Text("Logs are saved automatically. Copy a stage for a focused issue, or save all logs for a complete report.")
                .font(.caption).foregroundStyle(.secondary).padding(10)
            Divider()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    ForEach(groups.indices, id: \.self) { index in stageSection(groups[index]) }
                    if state.activity.isEmpty {
                        Text(state.diagnosticReport).font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }.padding(12)
            }
        }
    }
    @ViewBuilder private func stageSection(_ entries: [ActivityEntry]) -> some View {
        if let first = entries.first {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    VStack(alignment: .leading) {
                        Text(first.stage.replacingOccurrences(of: "_", with: " ").capitalized).font(.headline)
                        if !first.jobID.isEmpty { Text(first.jobID).font(.caption).foregroundStyle(.secondary) }
                    }
                    Spacer()
                    Button("Copy Stage") { DiagnosticStore.copy(state.diagnosticStore.stageReport(stage: first.stage, jobID: first.jobID)
                        ?? entries.map(\.formatted).joined(separator: "\n")) }
                }
                ForEach(entries.filter { (showDetails || !$0.isDebug) &&
                    (search.isEmpty || $0.message.localizedCaseInsensitiveContains(search)) }.suffix(200)) { entry in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: entry.symbol).foregroundStyle(entry.tint)
                        Text(entry.date, style: .time).foregroundStyle(.secondary).frame(width: 78, alignment: .leading)
                        Text(entry.message).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                        Button { DiagnosticStore.copy(entry.formatted) } label: { Image(systemName: "doc.on.doc") }
                            .buttonStyle(.borderless).help("Copy this log entry")
                    }.font(.system(.caption, design: .monospaced))
                }
            }.padding(12).background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
        }
    }
}
