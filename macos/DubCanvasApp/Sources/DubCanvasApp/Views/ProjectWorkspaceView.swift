import AppKit
import AVKit
import SwiftUI

struct ProjectWorkspaceView: View {
    @EnvironmentObject private var state: AppState
    @State private var showingComparison = false
    @State private var showingDeleteConfirmation = false
    @State private var showingResultExport = false
    @State private var sourcePlayer: AVPlayer?
    @State private var resultPlayer: AVPlayer?
    @SceneStorage("overviewScrollTarget") private var overviewScrollTarget: String?
    @SceneStorage("sourceScrollTarget") private var sourceScrollTarget: String?
    @SceneStorage("dubsScrollTarget") private var dubsScrollTarget: String?
    @SceneStorage("overviewScrollProjectID") private var overviewScrollProjectID = ""

    private var scrollTarget: Binding<String?> {
        switch state.selection {
        case .some(.media): $sourceScrollTarget
        case .some(.dubs): $dubsScrollTarget
        default: $overviewScrollTarget
        }
    }

    var body: some View {
        ScrollView {
            if let project = state.currentProject {
                VStack(alignment: .leading, spacing: 20) {
                    header(project).id("projectHeader")
                    switch state.selection ?? .overview {
                    case .media: media(project)
                    case .dubs: dubs(project)
                    default: overview(project)
                    }
                }
                .scrollTargetLayout()
                .frame(maxWidth: 900, alignment: .leading)
                .padding(28)
                .frame(maxWidth: .infinity, alignment: .topLeading)
            } else {
                ContentUnavailableView("Open a Project", systemImage: "folder", description: Text("Choose a project in the library."))
            }
        }
        .scrollPosition(id: scrollTarget)
        .navigationTitle(state.currentProject?.displayName ?? "Project")
        .sheet(isPresented: $showingComparison) {
            if let project = state.currentProject {
                DubComparisonView(project: project).frame(minWidth: 880, minHeight: 640)
            }
        }
        .sheet(isPresented: $showingResultExport) {
            if let project = state.currentProject,
               let dub = project.dubs.first(where: { $0.status == "completed" && $0.artifacts["dubbed_video"] != nil }) {
                DubExportView(projectName: project.displayName, dub: dub).frame(minWidth: 520)
            }
        }
        .confirmationDialog("Delete \(state.currentProject?.displayName ?? "project")?", isPresented: $showingDeleteConfirmation) {
            if let project = state.currentProject {
                Button("Delete Project and Generated Files", role: .destructive) {
                    state.deleteProject(project)
                }
            }
        } message: {
            Text("Subtitles, dubs, cached media, and processing history will be removed. Your original video file stays on your Mac. This cannot be undone.")
        }
        .onAppear {
            if let project = state.currentProject {
                if overviewScrollProjectID != project.id {
                    overviewScrollProjectID = project.id
                    overviewScrollTarget = "projectHeader"
                    sourceScrollTarget = "projectHeader"
                    dubsScrollTarget = "projectHeader"
                }
                loadSource(project)
                loadResult(project)
            }
        }
        .onChange(of: state.selectedProjectID) { _, _ in
            sourcePlayer?.pause()
            resultPlayer?.pause()
            if let projectID = state.selectedProjectID, projectID != overviewScrollProjectID {
                overviewScrollProjectID = projectID
                overviewScrollTarget = "projectHeader"
                sourceScrollTarget = "projectHeader"
                dubsScrollTarget = "projectHeader"
            }
            if let project = state.currentProject { loadSource(project); loadResult(project) }
        }
        .onChange(of: state.currentProject?.source) { _, _ in
            if let project = state.currentProject { loadSource(project) }
        }
        .onChange(of: state.currentProject?.artifacts["source_video"]) { _, _ in
            if let project = state.currentProject { loadSource(project) }
        }
        .onChange(of: state.currentProject?.dubs.first(where: { $0.status == "completed" })?.artifacts["dubbed_video"]) { _, _ in
            if let project = state.currentProject { loadResult(project) }
        }
        .onDisappear { sourcePlayer?.pause(); resultPlayer?.pause() }
    }

    private func header(_ project: ProjectSummary) -> some View {
        HStack(alignment: .top, spacing: 18) {
            VStack(alignment: .leading, spacing: 6) {
            Text(project.displayName).font(.largeTitle.bold())
            Text(project.sourceTitle).font(.callout).foregroundStyle(.secondary).lineLimit(1)
            HStack {
                if let language = project.sourceLanguage {
                    Label("Source: \(Locale.current.localizedString(forLanguageCode: language) ?? language.uppercased())", systemImage: "globe")
                }
                Label("\(project.dubs.count) versions", systemImage: "waveform")
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
            .disabled(project.status == "running" || state.activeJobID != nil)
            Menu {
                Button("Project Settings") { state.selection = .projectSettings }
                Divider()
                Button("Delete Project…", role: .destructive) {
                    showingDeleteConfirmation = true
                }
                .disabled(project.status == "running" || state.deletingProjectID != nil)
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
            .help("Project actions")
        }
    }

    private func overview(_ project: ProjectSummary) -> some View {
        VStack(alignment: .leading, spacing: 20) {
            nextStep(project).id("projectAction")
            if !state.jobIssue.isEmpty {
                GroupBox("Needs attention") {
                    VStack(alignment: .leading, spacing: 10) {
                        Label(state.jobIssue, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                        Button("System Check") { state.runSystemCheck() }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if let error = state.projectDeletionError {
                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
            }
            if project.dubs.contains(where: { $0.status == "completed" && $0.artifacts["dubbed_video"] != nil }) {
                latestResult(project).id("projectResult")
            }
            timeline(project).id("projectTimeline")
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 16) {
                    if !project.dubs.isEmpty || !project.subtitles.isEmpty {
                        sourcePreview.frame(minWidth: 280)
                    }
                    sharedAnalysis(project).frame(minWidth: 340)
                }
                VStack(alignment: .leading, spacing: 16) {
                    if !project.dubs.isEmpty || !project.subtitles.isEmpty { sourcePreview }
                    sharedAnalysis(project)
                }
            }.id("projectAnalysis")
            overviewPreviews(project).id("projectVersions")
        }
        .scrollTargetLayout()
    }

    private func latestResult(_ project: ProjectSummary) -> some View {
        let dub = project.dubs.first { $0.status == "completed" && $0.artifacts["dubbed_video"] != nil }
        return GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                if let dub {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Latest finished result").font(.title2.bold())
                            Text("\(dub.title) · \(dub.language.uppercased())")
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Export…", systemImage: "square.and.arrow.up") { showingResultExport = true }
                            .buttonStyle(.borderedProminent)
                    }
                    if let resultPlayer {
                        NativeDubPlayer(player: resultPlayer)
                            .frame(height: 330)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                    } else {
                        Label("The saved video is no longer available at its original location.", systemImage: "film")
                            .foregroundStyle(.orange)
                    }
                    Button("Open Version Details") { state.selectDub(dub) }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
        }
    }

    private func loadResult(_ project: ProjectSummary) {
        guard let path = project.dubs.first(where: { $0.status == "completed" && $0.artifacts["dubbed_video"] != nil })?
            .artifacts["dubbed_video"], FileManager.default.fileExists(atPath: path) else {
            resultPlayer = nil
            return
        }
        if (resultPlayer?.currentItem?.asset as? AVURLAsset)?.url.path != path {
            resultPlayer = AVPlayer(url: URL(fileURLWithPath: path))
        }
    }

    private func timeline(_ project: ProjectSummary) -> some View {
        let sourceReady = project.artifacts["source_video"] != nil ||
            project.source.hasPrefix("http://") || project.source.hasPrefix("https://") ||
            FileManager.default.fileExists(atPath: project.source)
        let speechReady = (project.artifacts["source_srt"] != nil || project.artifacts["chinese_srt"] != nil) &&
            project.artifacts["character_map"] != nil
        let subtitleReady = project.subtitles.contains { $0.artifacts["translated_srt"] != nil }
        let dubReady = project.dubs.contains { $0.status == "completed" && $0.artifacts["dubbed_video"] != nil }
        let stages: [(String, Bool)] = [("Source ready", sourceReady), ("Speech and speakers", speechReady),
                                        ("Subtitles", subtitleReady), ("Dub and render", dubReady)]
        let activeIndex = stages.firstIndex { !$0.1 }
        return GroupBox("Project timeline") {
            HStack(alignment: .top, spacing: 10) {
                ForEach(stages.indices, id: \.self) { index in
                    let stage = stages[index]
                    let blocked = index == activeIndex && ["failed", "paused", "cancelled"].contains(project.status)
                    let active = index == activeIndex && project.status == "running"
                    VStack(alignment: .leading, spacing: 7) {
                        Image(systemName: stage.1 ? "checkmark.circle.fill" : blocked ? "exclamationmark.circle.fill" : active ? "circle.dotted.circle.fill" : "circle")
                            .foregroundStyle(stage.1 ? Color.green : blocked ? Color.orange : active ? Color.accentColor : Color.secondary)
                        Text(stage.0).font(.callout.weight(index == activeIndex ? .semibold : .regular))
                        Text(stage.1 ? "Ready" : blocked ? "Needs attention" : active ? "In progress" : "Next")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityElement(children: .combine)
                }
            }
            .padding(.vertical, 8)
            Text("Speech can be ready while speaker matching continues; this stage finishes when both are available.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func overviewPreviews(_ project: ProjectSummary) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            GroupBox("Subtitles") {
                HStack {
                    Label(project.subtitles.isEmpty ? "No subtitles yet" : "\(project.subtitles.count) subtitle sets available",
                          systemImage: "captions.bubble")
                    Spacer()
                    Button("View All") { state.selection = .subtitles }
                }.padding(.vertical, 7)
            }
            GroupBox("Dub versions") {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(project.dubs.prefix(2)) { dub in
                        Button { state.selectDub(dub) } label: {
                            Label("\(dub.title) · \(dub.language.uppercased()) · \(dub.status.capitalized)",
                                  systemImage: dub.status == "completed" ? "checkmark.circle.fill" : "waveform")
                        }.buttonStyle(.plain)
                    }
                    if project.dubs.isEmpty { Text("No versions yet").foregroundStyle(.secondary) }
                    Button("View All Versions") { state.selection = .dubs }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 7)
            }
        }
    }

    private func nextStep(_ project: ProjectSummary) -> some View {
        let latest = project.dubs.first
        let hasSubtitles = project.subtitles.contains { set in
            set.artifacts["translated_srt"] != nil || set.artifacts["source_srt"] != nil || set.artifacts["chinese_srt"] != nil
        }
        return GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                if project.status == "running" || state.jobStartPending {
                    Label("Your project is processing", systemImage: "hourglass")
                        .font(.title2.bold())
                    Text(project.stageTitle.isEmpty ? "Preparing your video…" : project.stageTitle)
                        .foregroundStyle(.secondary)
                    if let started = startTime(project) {
                        TimelineView(.periodic(from: .now, by: 1)) { context in
                            Text("Elapsed: \(Int(max(0, context.date.timeIntervalSince(started))) / 60) min · Next: \(nextStage(after: project.stage))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if let fraction = state.progressFraction ?? project.progress {
                        ProgressView(value: fraction)
                        Text("Current step: \(fraction, format: .percent.precision(.fractionLength(0)))")
                            .font(.caption).foregroundStyle(.secondary)
                    } else { ProgressView() }
                    Button("View Progress") { overviewScrollTarget = "projectTimeline" }
                        .buttonStyle(.borderedProminent)
                    if hasSubtitles {
                        Label("Subtitles are ready. You can view or save them while the dub continues.", systemImage: "captions.bubble.fill")
                        Button("View Subtitles") { state.selection = .subtitles }
                    } else {
                        Text("Source subtitles appear after speech recognition; translated subtitles appear before voices are generated.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    if state.activeJobID != nil {
                        Button("Pause and keep completed work") { state.cancelActiveJob() }
                    }
                } else if let latest, latest.status == "failed" || latest.status == "paused" || latest.status == "cancelled" {
                    Label("This dub needs attention", systemImage: "exclamationmark.triangle.fill")
                        .font(.title2.bold()).foregroundStyle(.orange)
                    Text(latest.error ?? "Processing stopped. Your completed subtitles are still available.")
                        .fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Button("Retry Failed Step") { state.resumeDub(latest) }
                            .buttonStyle(.borderedProminent)
                            .disabled(!state.canStartJob)
                        Button("Details") { state.selectDub(latest) }
                        if hasSubtitles { Button("View Available Subtitles") { state.selection = .subtitles } }
                    }
                } else if let latest, latest.status == "completed",
                          !["failed", "paused", "cancelled"].contains(project.status),
                          let path = latest.artifacts["dubbed_video"], FileManager.default.fileExists(atPath: path) {
                    Label("Your dub is ready", systemImage: "checkmark.circle.fill")
                        .font(.title2.bold()).foregroundStyle(.green)
                    Text("\(latest.title) · \(latest.language.uppercased()). Watch the video below and export it when ready.")
                        .foregroundStyle(.secondary)
                    HStack {
                        Button("Watch Result") { overviewScrollTarget = "projectResult" }
                            .buttonStyle(.borderedProminent)
                        Button("Export…") { showingResultExport = true }
                    }
                } else if project.status == "failed" || project.status == "paused" {
                    Label(project.lastRunMode == "subtitles" ? "Subtitle generation needs attention" : "Source analysis needs attention",
                          systemImage: "exclamationmark.triangle.fill")
                        .font(.title2.bold()).foregroundStyle(.orange)
                    Text(project.lastError ?? "Processing stopped. You can retry without losing completed work.")
                    if project.lastRunMode == "subtitles" {
                        Button("Retry Subtitle Generation") {
                            state.outputMode = .subtitles
                            state.selection = .newSubtitles
                        }.buttonStyle(.borderedProminent)
                    } else {
                        Button("Retry Source Analysis") { state.startJob(analysis: true) }
                            .buttonStyle(.borderedProminent).disabled(!state.canStartJob)
                    }
                    if hasSubtitles { Button("View Available Subtitles") { state.selection = .subtitles } }
                } else if hasSubtitles {
                    Label("Subtitles are ready", systemImage: "captions.bubble.fill")
                        .font(.title2.bold())
                    Text("You can save the subtitles now or make a dub from this project.")
                        .foregroundStyle(.secondary)
                    HStack {
                        Button("View and Export Subtitles") { state.selection = .subtitles }
                            .buttonStyle(.borderedProminent)
                        Button("Create a Dub") { state.outputMode = .dub; state.selection = .newDub }
                    }
                } else {
                    Label("Ready to analyze", systemImage: "play.circle.fill")
                        .font(.title2.bold())
                    Text("Create a dub to detect the source language, make subtitles, and render a video. You can also make subtitles on their own.")
                        .foregroundStyle(.secondary)
                    HStack {
                        Button("Create First Dub") { state.outputMode = .dub; state.selection = .newDub }
                            .buttonStyle(.borderedProminent)
                        Button("Subtitles Only") { state.selection = .newSubtitles }
                    }
                    sourcePreview
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
        }
    }

    private func nextStage(after stage: String) -> String {
        switch stage {
        case "downloading", "extracting_audio", "preparing": "Speech and speakers"
        case "transcribing": "Subtitles"
        default: "Dub and render"
        }
    }

    private func startTime(_ project: ProjectSummary) -> Date? {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return parser.date(from: project.startedAt)
            ?? ISO8601DateFormatter().date(from: project.startedAt)
    }

    private var sourcePreview: some View {
        GroupBox("Source preview") {
            VStack(alignment: .leading, spacing: 8) {
                if let sourcePlayer {
                    NativeDubPlayer(player: sourcePlayer)
                        .frame(height: 220)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                } else {
                    ContentUnavailableView(projectSourceIsRemote ? "Video ready to download" : "Source video unavailable",
                        systemImage: "film",
                        description: Text(projectSourceIsRemote
                            ? "The source preview appears after processing downloads this video."
                            : "The saved video path is not available on this Mac. Check whether the file was moved or deleted."))
                        .frame(height: 220)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var projectSourceIsRemote: Bool {
        guard let source = state.currentProject?.source else { return false }
        return source.hasPrefix("https://") || source.hasPrefix("http://")
    }

    private func sharedAnalysis(_ project: ProjectSummary) -> some View {
        GroupBox("Reusable source analysis") {
            VStack(alignment: .leading, spacing: 12) {
                Label(project.artifacts["source_srt"] != nil || project.artifacts["chinese_srt"] != nil ?
                      "Transcript ready" : "Transcript pending", systemImage: "text.bubble")
                Label(project.artifacts["character_map"] != nil ? "Speakers identified" : "Speaker matching pending",
                      systemImage: "person.2")
                Text("Transcript and speaker identities are reused for new dubs, keeping versions consistent.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Source Details") { state.selection = .media }
                    Button("Speakers") { state.selection = .characters }
                    if ((project.artifacts["source_srt"] == nil && project.artifacts["chinese_srt"] == nil) ||
                        project.artifacts["character_map"] == nil) && project.status != "running" {
                        Button(project.status == "paused" || project.status == "failed" ? "Retry Source Analysis" : "Analyze Source") {
                            state.startJob(analysis: true)
                        }
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
                if project.analysisRevision > 0 {
                    LabeledContent("Analysis revision", value: "\(project.analysisRevision)")
                }
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
                                Text("\(dub.language.uppercased()) · \(dub.status.capitalized) · \(dub.createdAt)")
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
