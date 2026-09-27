import AppKit
import AVKit
import SwiftUI

struct ProjectWorkspaceView: View {
    @EnvironmentObject private var state: AppState
    @State private var showingComparison = false
    @State private var sourcePlayer: AVPlayer?

    var body: some View {
        ScrollView {
            if let project = state.currentProject {
                VStack(alignment: .leading, spacing: 20) {
                    header(project)
                    switch state.selection ?? .overview {
                    case .media: media(project)
                    case .dubs: dubs(project)
                    default: overview(project)
                    }
                }
                .frame(maxWidth: 900, alignment: .leading)
                .padding(28)
                .frame(maxWidth: .infinity, alignment: .topLeading)
            } else {
                ContentUnavailableView("Open a Project", systemImage: "folder", description: Text("Choose a project in the library."))
            }
        }
        .navigationTitle(state.currentProject?.displayName ?? "Project")
        .sheet(isPresented: $showingComparison) {
            if let project = state.currentProject {
                DubComparisonView(project: project).frame(minWidth: 880, minHeight: 640)
            }
        }
        .onAppear { if let project = state.currentProject { loadSource(project) } }
        .onChange(of: state.selectedProjectID) { _, _ in
            sourcePlayer?.pause()
            if let project = state.currentProject { loadSource(project) }
        }
        .onChange(of: state.currentProject?.source) { _, _ in
            if let project = state.currentProject { loadSource(project) }
        }
        .onChange(of: state.currentProject?.artifacts["source_video"]) { _, _ in
            if let project = state.currentProject { loadSource(project) }
        }
        .onDisappear { sourcePlayer?.pause() }
    }

    private func header(_ project: ProjectSummary) -> some View {
        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
            Text(project.displayName).font(.largeTitle.bold())
            Text(project.source).font(.callout).foregroundStyle(.secondary)
                .lineLimit(1).truncationMode(.middle).textSelection(.enabled)
            HStack {
                Label("\(project.dubs.count) dubs", systemImage: "waveform")
                Label("\(project.subtitles.count) subtitle sets", systemImage: "captions.bubble")
                if let language = project.sourceLanguage {
                    Label("Source: \(language.uppercased())", systemImage: "globe")
                }
                if project.analysisRevision > 0 {
                    Label("Analysis revision \(project.analysisRevision)", systemImage: "square.stack.3d.up")
                }
                if project.status == "running" {
                    Label(project.stageTitle, systemImage: "hourglass")
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            Button("New Dub", systemImage: "plus") {
                state.outputMode = .dub
                state.dubName = ""
                state.selection = .newDub
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
        }
    }

    private func overview(_ project: ProjectSummary) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            if !state.jobIssue.isEmpty {
                GroupBox("Needs attention") {
                    VStack(alignment: .leading, spacing: 10) {
                        Label(state.jobIssue, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                        Button("System Check") { state.runSystemCheck() }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if project.status == "running" || (state.jobStartPending && project.id == state.selectedProjectID) {
                GroupBox("Project activity") {
                    VStack(alignment: .leading, spacing: 10) {
                        Label(state.stageDetail.isEmpty ? project.stageTitle : state.stageDetail, systemImage: "hourglass")
                        if let fraction = state.progressFraction ?? project.progress {
                            ProgressView(value: fraction)
                            Text("Current step: \(fraction, format: .percent.precision(.fractionLength(0)))")
                                .font(.caption).foregroundStyle(.secondary)
                        } else { ProgressView() }
                        if !state.downloadDetail.isEmpty { Text(state.downloadDetail).font(.caption) }
                        Text("Completed subtitles are available below while voices and video continue processing.")
                            .font(.caption).foregroundStyle(.secondary)
                        if state.activeJobID != nil {
                            Button("Pause and keep completed work") { state.cancelActiveJob() }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 16) {
                    sourcePreview.frame(minWidth: 280)
                    sharedAnalysis(project).frame(minWidth: 340)
                }
                VStack(alignment: .leading, spacing: 16) {
                    sourcePreview
                    sharedAnalysis(project)
                }
            }
            HStack {
                Button("View Subtitles") { state.selection = .subtitles }
                Button("View Dubs") { state.selection = .dubs }
            }
            if project.status == "failed", let error = project.lastError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(.orange)
            }
            subtitles(project)
            dubs(project)
            DisclosureGroup("Project files and settings") {
                media(project)
                Button("Project Settings") { state.selection = .projectSettings }
                Button("Speakers & Voices") { state.selection = .characters }
            }
        }
    }

    private var sourcePreview: some View {
        GroupBox("Source preview") {
            VStack(alignment: .leading, spacing: 8) {
                if let sourcePlayer {
                    NativeDubPlayer(player: sourcePlayer)
                        .frame(height: 220)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                } else {
                    ContentUnavailableView("Source video unavailable", systemImage: "film",
                        description: Text("The saved video path is not available on this Mac. Check whether the file was moved or deleted."))
                        .frame(height: 220)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func sharedAnalysis(_ project: ProjectSummary) -> some View {
        GroupBox("Shared source analysis") {
            VStack(alignment: .leading, spacing: 12) {
                LabeledContent("Transcript", value: project.artifacts["source_srt"] != nil || project.artifacts["chinese_srt"] != nil ? "Ready" : "Pending")
                LabeledContent("Speakers", value: project.artifacts["character_map"] != nil ? "Ready" : "Pending")
                LabeledContent("Source subtitles", value: project.artifacts["source_srt"] != nil || project.artifacts["chinese_srt"] != nil ? "Ready" : "Pending")
                LabeledContent("Source language", value: project.sourceLanguage?.uppercased() ?? "Detecting")
                if project.analysisRevision > 0 {
                    LabeledContent("Analysis revision", value: "\(project.analysisRevision)")
                }
                Text("These source details are reused by dub versions in this project.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Subtitles") { state.selection = .subtitles }
                    Button("Speakers") { state.selection = .characters }
                    if project.artifacts["source_srt"] == nil && project.artifacts["chinese_srt"] == nil {
                        Button("Analyze Source") { state.startJob(analysis: true) }
                            .disabled(!state.canStartJob)
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func loadSource(_ project: ProjectSummary) {
        let path = project.artifacts["source_video"] ?? project.source
        sourcePlayer = FileManager.default.fileExists(atPath: path)
            ? AVPlayer(url: URL(fileURLWithPath: path)) : nil
    }

    private func media(_ project: ProjectSummary) -> some View {
        GroupBox("Source / Media") {
            VStack(alignment: .leading, spacing: 14) {
                LabeledContent("Original source", value: project.source)
                LabeledContent("Output folder", value: project.outputDir)
                if let path = project.artifacts["source_video"] {
                    ArtifactLink(title: "Cached original video", path: path)
                } else {
                    Text("The original video will be downloaded or resolved when processing starts.")
                        .foregroundStyle(.secondary)
                }
                Button("Reveal Project Files", systemImage: "folder") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: project.outputDir))
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func subtitles(_ project: ProjectSummary) -> some View {
        GroupBox("Subtitles and Transcripts") {
            VStack(alignment: .leading, spacing: 14) {
                if project.subtitles.isEmpty {
                    Text("No subtitles yet. Generate subtitles on their own or create a dub. Files appear here as soon as translation finishes.")
                        .foregroundStyle(.secondary)
                }
                ForEach(project.subtitles) { set in
                    VStack(alignment: .leading, spacing: 7) {
                        Label("\(set.language.uppercased()) · \(set.createdAt)", systemImage: "captions.bubble")
                            .font(.headline)
                        ForEach(set.artifacts.keys.sorted(), id: \.self) { kind in
                            if let path = set.artifacts[kind] {
                                ArtifactLink(title: kind == "review_report" ? "Subtitle Review Report" : kind.replacingOccurrences(of: "_", with: " ").capitalized, path: path)
                            }
                        }
                    }
                    Divider()
                }
                HStack {
                    Spacer()
                    Button("Generate Subtitles", systemImage: "text.badge.plus") {
                        state.selection = .newSubtitles
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func dubs(_ project: ProjectSummary) -> some View {
        GroupBox("Dub Versions") {
            VStack(alignment: .leading, spacing: 12) {
            if project.dubs.isEmpty {
                    Text("No dub versions yet. A new dub will keep its own video, subtitles and settings.")
                        .foregroundStyle(.secondary)
                }
            ForEach(project.dubs) { dub in
                    Button {
                        state.selectDub(dub)
                    } label: {
                        HStack {
                            Image(systemName: dub.status == "completed" ? "checkmark.circle.fill" : "waveform.circle")
                                .foregroundStyle(dub.status == "completed" ? Color.green : Color.secondary)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(dub.title).fontWeight(.medium)
                                Text("\(dub.language.uppercased()) · \(dub.config["tts_engine"] as? String ?? "Voice pending") · analysis rev \(dub.analysisRevision) · \(dub.createdAt)")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(dub.status.capitalized).font(.caption).foregroundStyle(.secondary)
                            Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                        }.contentShape(Rectangle())
                    }.buttonStyle(.plain)
                    Divider()
            }
            HStack {
                Button("Compare Versions", systemImage: "rectangle.split.2x1") { showingComparison = true }
                    .disabled(project.dubs.filter { $0.status == "completed" && $0.artifacts["dubbed_video"] != nil }.count < 2)
                Spacer()
                    Button("New Dub", systemImage: "waveform.badge.plus") {
                        state.outputMode = .dub
                        state.dubName = ""
                        state.selection = .newDub
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

struct ArtifactLink: View {
    let title: String
    let path: String
    @State private var showsPath = false

    private var fileExists: Bool { FileManager.default.fileExists(atPath: path) }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: path.hasSuffix(".srt") || path.hasSuffix(".vtt") ? "captions.bubble" : "doc")
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).fontWeight(.medium)
                if showsPath {
                    Text(path).font(.caption.monospaced()).foregroundStyle(.secondary)
                        .lineLimit(2).textSelection(.enabled)
                }
                if !fileExists {
                    Text("File missing from its saved location")
                        .font(.caption).foregroundStyle(.orange)
                }
            }
            Spacer()
            Button("Open") { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                .disabled(!fileExists)
            Button("Save…") { exportFile(path) }
                .disabled(!fileExists)
            Button { showsPath.toggle() } label: {
                Image(systemName: "info.circle")
            }.help("Show file location")
        }
    }
}

func exportFile(_ path: String) {
    let source = URL(fileURLWithPath: path)
    guard FileManager.default.fileExists(atPath: source.path) else { return }
    let panel = NSSavePanel()
    panel.nameFieldStringValue = source.lastPathComponent
    guard panel.runModal() == .OK, let destination = panel.url else { return }
    do {
        if FileManager.default.fileExists(atPath: destination.path) {
            try FileManager.default.removeItem(at: destination)
        }
        try FileManager.default.copyItem(at: source, to: destination)
    } catch {
        NSAlert(error: error).runModal()
    }
}
