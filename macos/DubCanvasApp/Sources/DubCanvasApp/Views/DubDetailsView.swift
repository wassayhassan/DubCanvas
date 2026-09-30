import AppKit
import AVKit
import SwiftUI

struct DubDetailsView: View {
    @EnvironmentObject private var state: AppState
    let dubID: String
    @State private var player: AVPlayer?
    @State private var sourceCuePlayer: AVPlayer?
    @State private var confirmDelete = false
    @State private var showingExport = false
    @State private var advancedExpanded = false
    @State private var configExpanded = false
    @State private var reviewExpanded = false
    @State private var filesExpanded = false
    @State private var voiceAssignments: [String] = []
    @State private var reviewCues: [ReviewCue] = []
    @State private var reviewDraft: [Int: String] = [:]
    @State private var reviewMessage = ""

    private struct ReviewCue: Identifiable {
        let id: Int
        let start: Double
        let end: Double
        let source: String
        let translation: String
        let suggestion: String
        let alternate: String
        let alternateEnglish: String
        let reasons: [String]
        let duration: Double?
        let available: Double?
        let error: String
    }

    private var dub: DubSummary? { state.currentProject?.dubs.first { $0.id == dubID } }

    var body: some View {
        ScrollView {
            if let dub {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 8) {
                        Label(dub.title, systemImage: "waveform.circle.fill")
                            .font(.largeTitle.bold())
                        HStack {
                            Label(dub.status.capitalized, systemImage: dub.status == "completed" ? "checkmark.circle.fill" : "clock")
                            Text("· \(dub.language.uppercased())")
                            Text("· Created \(dub.createdAt)")
                        }.font(.callout).foregroundStyle(.secondary)
                    }

                    if dub.status == "failed" || (dub.status == "paused" && dub.error != "Subtitle review required before voice generation") {
                        GroupBox("Needs attention") {
                            VStack(alignment: .leading, spacing: 12) {
                                Text(friendlyError(dub.error ?? "Processing stopped."))
                                HStack {
                                    Button("Retry This Dub", systemImage: "arrow.clockwise") { state.resumeDub(dub) }
                                        .buttonStyle(.borderedProminent)
                                        .disabled(state.activeJobID != nil || state.jobStartPending)
                                    if (dub.error ?? "").contains("403") {
                                        Button("Choose a Video File") { state.newProject() }
                                    }
                                    Button("System Check") { state.runSystemCheck() }
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }

                    if let player {
                        NativeDubPlayer(player: player)
                            .frame(height: 340)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                        if let path = dub.artifacts["dubbed_video"] {
                            HStack {
                                Button("Export…", systemImage: "square.and.arrow.up") { showingExport = true }
                                    .buttonStyle(.borderedProminent)
                                Button("Show in Finder", systemImage: "folder") {
                                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
                                }
                            }
                        }
                    } else if dub.status == "completed" {
                        GroupBox("Video unavailable") {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("The finished video is missing from its saved location.")
                                    .foregroundStyle(.orange)
                                Button("Show Project Folder") {
                                    if let project = state.currentProject {
                                        NSWorkspace.shared.open(URL(fileURLWithPath: project.outputDir))
                                    }
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }

                    if dub.artifacts["translated_srt"] != nil || dub.artifacts["english_srt"] != nil,
                       player == nil {
                        Button("Export Available Subtitles…", systemImage: "captions.bubble") { showingExport = true }
                    }

                    if dub.status == "completed" && player != nil {
                        GroupBox("Result") {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Ready to watch · \(dub.language.uppercased())")
                                    .font(.headline)
                                Text(dub.warnings.isEmpty ? "Processing finished. Watch the video to check the result." :
                                    "Processing finished with \(dub.warnings.count) note(s), including \(dub.warnings.filter { $0.localizedCaseInsensitiveContains("overlap") || $0.localizedCaseInsensitiveContains("timing") }.count) about timing. Review them below if the video needs adjustment.")
                                    .foregroundStyle(.secondary)
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    if dub.artifacts["review_report"] != nil {
                        Button("Review Subtitle and Timing Notes", systemImage: "text.magnifyingglass") {
                            state.selection = .review(dub.id)
                        }
                    }
                    DisclosureGroup("Processing and voice details", isExpanded: $advancedExpanded) {
                    GroupBox("Overview") {
                        detailGrid([
                            ("Target language", dub.language.uppercased()),
                            ("Source language", dub.sourceLanguage.uppercased()),
                            ("Translation model", value(dub.config, "translation") + modelSuffix(dub.config)),
                            ("Speech engine", value(dub.config, "tts_engine")),
                            ("Voice", value(dub.config, "voice")),
                            ("Duration", dub.duration.map { String(format: "%.1f s", $0) } ?? "Pending"),
                            ("Processing stage", dub.stage.capitalized),
                            ("Updated", dub.updatedAt),
                        ])
                    }
                    GroupBox("Timing and voices") {
                        VStack(alignment: .leading, spacing: 10) {
                            Label(dub.sync, systemImage: "waveform.path")
                            Text("Timing follows the source speech windows. Review the rendered video for any line that needs manual adjustment.")
                                .font(.caption).foregroundStyle(.secondary)
                            if let path = dub.artifacts["character_map"] {
                                ArtifactLink(title: "Character voice assignments", path: path)
                                ForEach(voiceAssignments, id: \.self) { assignment in
                                    Label(assignment, systemImage: "person.wave.2")
                                        .font(.caption)
                                }
                                Button("View Project Speakers") {
                                    state.selection = .characters
                                }
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                    }
                    if dub.status == "paused" && dub.error == "Subtitle review required before voice generation" {
                        GroupBox("Continue Paused Dub") {
                            VStack(alignment: .leading, spacing: 14) {
                                Text("The updated app can resolve subtitle review and timing automatically. Continue this version without editing source subtitles.")
                                    .foregroundStyle(.secondary)
                                Button("Continue Automatically", systemImage: "play.fill") {
                                    state.resumeDub(dub)
                                }
                                .buttonStyle(.borderedProminent)
                                .disabled(state.activeJobID != nil || state.jobStartPending)
                                if !reviewMessage.isEmpty { Text(reviewMessage).foregroundStyle(.orange) }
                                DisclosureGroup("Inspect subtitles (advanced)", isExpanded: $reviewExpanded) {
                                ForEach(reviewCues) { cue in
                                    VStack(alignment: .leading, spacing: 6) {
                                        Text("Cue \(cue.id) · \(cue.reasons.joined(separator: ", ").replacingOccurrences(of: "_", with: " "))")
                                            .font(.caption).foregroundStyle(.secondary)
                                        if let duration = cue.duration, let available = cue.available {
                                            Text(String(format: "Voice takes %.2f seconds; %.2f seconds available before the next line.", duration, available))
                                                .font(.caption).foregroundStyle(.orange)
                                        }
                                        Text(cue.source).textSelection(.enabled)
                                        Text("Current: \(cue.translation)").font(.callout)
                                        if cue.end > cue.start,
                                           let source = dub.artifacts["source_video"],
                                           FileManager.default.fileExists(atPath: source) {
                                            Button("Play original cue", systemImage: "play.circle") {
                                                let item = AVPlayerItem(url: URL(fileURLWithPath: source))
                                                item.forwardPlaybackEndTime = CMTime(seconds: cue.end + 0.5,
                                                                                     preferredTimescale: 600)
                                                sourceCuePlayer?.pause()
                                                let playback = AVPlayer(playerItem: item)
                                                sourceCuePlayer = playback
                                                playback.seek(to: CMTime(seconds: max(0, cue.start - 0.25),
                                                                         preferredTimescale: 600)) { _ in
                                                    playback.play()
                                                }
                                            }.buttonStyle(.link)
                                        }
                                        if !cue.error.isEmpty { Text(cue.error).font(.caption).foregroundStyle(.orange) }
                                        if cue.reasons.contains("non_chinese_source") || cue.reasons.contains("source_language_mismatch") {
                                            Text("The two speech passes disagree. Compare the translated options and the scene. The alternate is an uncertain guess, especially for a very short cue.")
                                                .font(.caption).foregroundStyle(.orange)
                                            if !cue.alternateEnglish.isEmpty {
                                                Text("Alternate translation: \(cue.alternateEnglish)")
                                                    .font(.callout).foregroundStyle(.orange)
                                                if cue.alternateEnglish != cue.translation {
                                                    Button("Use alternate translation") {
                                                        reviewDraft[cue.id] = cue.alternateEnglish
                                                    }.buttonStyle(.link)
                                                }
                                            } else {
                                                Text("The app could not translate the alternate reliably. Keep the current line only if the scene supports it.")
                                                    .font(.caption).foregroundStyle(.orange)
                                            }
                                        }
                                        if !cue.alternate.isEmpty {
                                            DisclosureGroup("Show raw alternate transcript") {
                                                Text(cue.alternate).textSelection(.enabled)
                                            }.font(.caption)
                                        }
                                        TextField("Approved translation", text: Binding(
                                            get: { reviewDraft[cue.id] ?? cue.translation },
                                            set: { reviewDraft[cue.id] = $0 }
                                        ))
                                        .textFieldStyle(.roundedBorder)
                                        HStack {
                                            if !cue.suggestion.isEmpty {
                                                Text("Suggestion: \(cue.suggestion)").textSelection(.enabled)
                                                Button("Use suggestion") {
                                                    reviewDraft[cue.id] = cue.suggestion
                                                }.buttonStyle(.link)
                                            }
                                            Spacer()
                                            if !cue.reasons.contains("speech_overlap") {
                                                Button("Use original") { reviewDraft[cue.id] = cue.translation }
                                                    .buttonStyle(.link)
                                            }
                                        }.font(.caption)
                                    }
                                    Divider()
                                }
                                Button("Approve and Continue Dub", systemImage: "checkmark.circle") {
                                    let revisions = Dictionary(uniqueKeysWithValues: reviewCues.compactMap { cue -> (String, String)? in
                                        let value = (reviewDraft[cue.id] ?? cue.translation).trimmingCharacters(in: .whitespacesAndNewlines)
                                        return value != cue.translation && !value.isEmpty ? (String(cue.id), value) : nil
                                    })
                                    if reviewCues.contains(where: { (reviewDraft[$0.id] ?? $0.translation).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) {
                                        reviewMessage = "An approved line cannot be empty."
                                    } else if reviewCues.contains(where: { $0.reasons.contains("speech_overlap") &&
                                        (reviewDraft[$0.id] ?? $0.translation).trimmingCharacters(in: .whitespacesAndNewlines) == $0.translation }) {
                                        reviewMessage = "A line overlaps the next voice. Give it shorter wording before continuing."
                                    } else {
                                        state.approveReview(dub, revisions: revisions)
                                    }
                                }
                                .buttonStyle(.borderedProminent)
                                .disabled(state.activeJobID != nil || reviewCues.isEmpty)
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    GroupBox("Additional files") {
                        VStack(alignment: .leading, spacing: 12) {
                            if dub.artifacts.isEmpty {
                                Text("Files appear here as each processing stage finishes.")
                                    .foregroundStyle(.secondary)
                            }
                            ForEach(["english_srt", "target_srt", "chinese_srt", "source_srt", "english_vtt"], id: \.self) { kind in
                                if let path = dub.artifacts[kind] {
                                    ArtifactLink(title: ["chinese_srt", "source_srt"].contains(kind) ? "Original subtitles" : "Translated subtitles", path: path)
                                }
                            }
                            DisclosureGroup("Technical files", isExpanded: $filesExpanded) {
                            ForEach(dub.artifacts.keys.sorted().filter { !["dubbed_video", "english_srt", "target_srt", "chinese_srt", "source_srt", "english_vtt"].contains($0) }, id: \.self) { kind in
                                if let path = dub.artifacts[kind] {
                                    ArtifactLink(title: kind.replacingOccurrences(of: "_", with: " ").capitalized, path: path)
                                }
                            }
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }
                    GroupBox {
                        DisclosureGroup("Full generation settings", isExpanded: $configExpanded) {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(dub.config.keys.sorted(), id: \.self) { key in
                                    if !["source", "output_dir", "elevenlabs_api_key"].contains(key) {
                                        LabeledContent(key.replacingOccurrences(of: "_", with: " ").capitalized,
                                                       value: String(describing: dub.config[key] ?? "—"))
                                            .font(.caption)
                                    }
                                }
                            }.padding(.top, 10).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    if !dub.warnings.isEmpty || dub.error != nil {
                        GroupBox("Warnings and errors") {
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(dub.warnings, id: \.self) { message in
                                    Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(.orange)
                                }
                                if let error = dub.error {
                                    Label(error, systemImage: "xmark.octagon").foregroundStyle(.red)
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    HStack {
                        if (dub.status == "cancelled" &&
                            dub.error != "Subtitle review required before voice generation") ||
                            (dub.status == "running" && state.activeJobID == nil) {
                            Button("Resume This Dub", systemImage: "play.fill") {
                                state.resumeDub(dub)
                            }
                            .disabled(state.activeJobID != nil || state.jobStartPending)
                        }
                        Button("Regenerate as New Version", systemImage: "arrow.clockwise") {
                            state.regenerate(dub)
                        }
                        Spacer()
                        Button("Delete Dub", systemImage: "trash", role: .destructive) {
                            confirmDelete = true
                        }.disabled(dub.status == "running" || dub.id == "legacy")
                    }
                }
                .frame(maxWidth: 900, alignment: .leading)
                .padding(28)
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .confirmationDialog("Delete \(dub.title)?", isPresented: $confirmDelete) {
                    Button("Delete Dub and Its Files", role: .destructive) { state.deleteDub(dub) }
                } message: {
                    Text("This removes this dub's version folder. Shared source media and other dubs stay in the project.")
                }
                .sheet(isPresented: $showingExport) {
                    DubExportView(projectName: state.currentProject?.displayName ?? "Project", dub: dub)
                        .frame(minWidth: 520)
                }
                .onAppear { loadPlayer(dub); loadReview(dub) }
                .onChange(of: dubID) { _, _ in loadPlayer(dub); loadReview(dub) }
                .onChange(of: dub.artifacts["dubbed_video"]) { _, _ in
                    if let updated = self.dub { loadPlayer(updated) }
                }
                .onChange(of: dub.artifacts["character_map"]) { _, _ in
                    if let updated = self.dub { loadVoiceAssignments(updated) }
                }
                .onChange(of: dub.artifacts["review_report"]) { _, _ in
                    if let updated = self.dub { loadReview(updated) }
                }
                .onChange(of: dub.updatedAt) { _, _ in
                    if let updated = self.dub, updated.status == "paused" { loadReview(updated) }
                }
                .onDisappear { player?.pause(); sourceCuePlayer?.pause() }
            } else {
                ContentUnavailableView("Dub Not Found", systemImage: "waveform", description: Text("Select a dub in the sidebar."))
            }
        }
        .navigationTitle(dub?.title ?? "Dub Details")
    }

    private func friendlyError(_ error: String) -> String {
        if error.contains("403") { return "The video site refused the download. Retry using the video's normal page link, or choose a local video file." }
        if error.contains("ffprobe") || error.contains("ffmpeg") { return "Video tools are missing. Open System Check for setup details, then retry this dub." }
        if error.localizedCaseInsensitiveContains("audio prompt") { return "The selected voice clip is too short. Check the character voice or use an automatic fallback, then retry." }
        return error
    }

    private func loadPlayer(_ dub: DubSummary) {
        loadVoiceAssignments(dub)
        guard let path = dub.artifacts["dubbed_video"], FileManager.default.fileExists(atPath: path) else {
            player = nil
            return
        }
        player = AVPlayer(url: URL(fileURLWithPath: path))
    }

    private func loadVoiceAssignments(_ dub: DubSummary) {
        voiceAssignments = []
        guard let path = dub.artifacts["character_map"],
              let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let map = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let characters = map["characters"] as? [[String: Any]] else { return }
        voiceAssignments = characters.map { item in
            let name = item["display_name"] as? String ?? item["id"] as? String ?? "Character"
            let provider = item["tts_provider"] as? String ?? "inherit"
            let voice = item["elevenlabs_voice_id"] as? String ?? item["kokoro_voice"] as? String ?? item["macos_voice"] as? String ?? ""
            let manual = item["reference_audio"] as? String ?? ""
            let suggested = item["suggested_reference_audio"] as? String ?? ""
            let automatic = item["auto_reference_enabled"] as? Bool ?? true
            let reference = manual.isEmpty && automatic ? suggested : manual
            let source = reference.isEmpty ? "" : " · \(URL(fileURLWithPath: reference).lastPathComponent)"
            return "\(name) · \(provider)\(voice.isEmpty ? "" : " · \(voice)")\(source)"
        }
    }

    private func loadReview(_ dub: DubSummary) {
        reviewCues = []
        reviewDraft = [:]
        reviewMessage = ""
        guard let path = dub.artifacts["review_report"],
              let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let report = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let flags = report["flags"] as? [[String: Any]],
              let priority = report["priority_cues"] as? [Int] else {
            if dub.error == "Subtitle review required before voice generation" {
                reviewMessage = "The review report could not be loaded. Open the report in Files to inspect it."
            }
            return
        }
        let timing = report["timing_issues"] as? [String: [String: Any]] ?? [:]
        let selected = Set(priority).union(timing.keys.compactMap(Int.init))
        reviewMessage = report["review_error"] as? String ?? ""
        reviewCues = flags.compactMap { row in
            guard let cue = row["cue"] as? Int, selected.contains(cue) else { return nil }
            let issue = timing[String(cue)] ?? [:]
            return ReviewCue(id: cue, start: row["start"] as? Double ?? 0,
                             end: row["end"] as? Double ?? 0,
                             source: row["source"] as? String ?? "",
                             translation: row["translation"] as? String ?? "",
                             suggestion: row["suggestion"] as? String ?? "",
                             alternate: row["asr_candidate"] as? String ?? "",
                             alternateEnglish: row["asr_translation"] as? String ?? "",
                             reasons: row["reasons"] as? [String] ?? [],
                             duration: issue["duration"] as? Double,
                             available: issue["available"] as? Double,
                             error: row["review_error"] as? String ?? "")
        }
        for cue in reviewCues { reviewDraft[cue.id] = cue.translation }
    }

    private func value(_ config: [String: Any], _ key: String) -> String {
        let text = config[key] as? String ?? ""
        return text.isEmpty ? "Automatic / not specified" : text
    }

    private func modelSuffix(_ config: [String: Any]) -> String {
        let key = config["translation"] as? String == "ollama" ? "ollama_model" : "llm_model"
        guard let model = config[key] as? String else { return "" }
        return " · \(model)"
    }

    private func detailGrid(_ items: [(String, String)]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(items, id: \.0) { item in
                LabeledContent(item.0, value: item.1)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
}
