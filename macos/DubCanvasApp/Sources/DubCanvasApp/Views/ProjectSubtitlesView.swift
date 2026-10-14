import AppKit
import AVKit
import SwiftUI

private struct SubtitleCue: Identifiable {
    let id: Int
    let start: Double
    let end: Double
    let time: String
    let text: String
}

struct ProjectSubtitlesView: View {
    @EnvironmentObject private var state: AppState
    @State private var selectedSetID = ""
    @State private var cues: [SubtitleCue] = []
    @State private var search = ""
    @State private var player: AVPlayer?
    @State private var fileIssue: String?

    private var project: ProjectSummary? { state.currentProject }
    private var selectedSet: SubtitleSummary? {
        project?.subtitles.first(where: { $0.id == selectedSetID })
    }
    private var subtitlePath: String? {
        guard let set = selectedSet else { return nil }
        return set.artifacts["translated_srt"] ?? set.artifacts["source_srt"]
            ?? set.artifacts["chinese_srt"] ?? set.artifacts["english_srt"]
            ?? set.artifacts["partial_translated_srt"] ?? set.artifacts["source_provisional_srt"]
    }
    private var visibleCues: [SubtitleCue] {
        search.isEmpty ? cues : cues.filter {
            $0.text.localizedCaseInsensitiveContains(search) || $0.time.contains(search)
        }
    }

    var body: some View {
        Group {
            if let project {
                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        VStack(alignment: .leading) {
                            Text("Subtitles").font(.largeTitle.bold())
                            Text("\(project.displayName) · source and versioned translations")
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Generate Subtitles", systemImage: "text.badge.plus") {
                            state.selection = .newSubtitles
                        }
                    }
                    if project.subtitles.isEmpty {
                        ContentUnavailableView("No Subtitles Yet", systemImage: "captions.bubble",
                            description: Text("Source subtitles appear after transcription. Translated subtitles appear before voice rendering finishes."))
                    } else {
                        HStack {
                            Picker("Subtitle set", selection: $selectedSetID) {
                                ForEach(project.subtitles) { set in
                                    Text(setTitle(set, project: project)).tag(set.id)
                                }
                            }
                            .frame(maxWidth: 420)
                            if let path = subtitlePath, FileManager.default.fileExists(atPath: path) {
                                Button("Save SRT…") { exportFile(path) }
                            }
                            if let vtt = selectedSet?.artifacts["translated_vtt"]
                                ?? selectedSet?.artifacts["source_vtt"]
                                ?? selectedSet?.artifacts["chinese_vtt"],
                                FileManager.default.fileExists(atPath: vtt) {
                                Button("Save VTT…") { exportFile(vtt) }
                            }
                        }
                        if let player {
                            NativeDubPlayer(player: player)
                                .frame(minHeight: 210, maxHeight: 320)
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                        } else {
                            Label("Source video is not available for preview. Subtitles can still be inspected and exported.", systemImage: "film")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                        if let fileIssue {
                            Label(fileIssue, systemImage: "exclamationmark.triangle")
                                .foregroundStyle(.orange)
                        }
                        if selectedSet?.artifacts["translated_srt"] == nil,
                           selectedSet?.artifacts["partial_translated_srt"] != nil {
                            Label("Partial translation — completed lines are saved; review must finish before dubbing.", systemImage: "exclamationmark.triangle")
                                .font(.caption).foregroundStyle(.orange)
                        }
                        if selectedSet?.artifacts["source_srt"] == nil,
                           selectedSet?.artifacts["chinese_srt"] == nil,
                           selectedSet?.artifacts["source_provisional_srt"] != nil {
                            Label("Provisional source transcript — recognition and alignment checks are still pending.", systemImage: "clock")
                                .font(.caption).foregroundStyle(.orange)
                        }
                        TextField("Search text or timestamp", text: $search)
                            .textFieldStyle(.roundedBorder)
                        Text("\(visibleCues.count) cues · Select a line to play its source moment")
                            .font(.caption).foregroundStyle(.secondary)
                        List(visibleCues) { cue in
                            Button {
                                player?.seek(to: CMTime(seconds: max(0, cue.start - 0.25), preferredTimescale: 600)) { _ in
                                    player?.play()
                                }
                            } label: {
                                HStack(alignment: .top, spacing: 12) {
                                    Text(cue.time).monospacedDigit().foregroundStyle(.secondary)
                                        .frame(width: 105, alignment: .leading)
                                    Text(cue.text).frame(maxWidth: .infinity, alignment: .leading)
                                }.padding(.vertical, 4).contentShape(Rectangle())
                            }.buttonStyle(.plain)
                        }
                        .listStyle(.inset)
                    }
                }
                .padding(24)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .onAppear { selectDefaultSet(project); loadSource(project) }
                .onChange(of: selectedSetID) { _, _ in loadCues() }
                .onChange(of: project.subtitles.count) { _, _ in
                    selectDefaultSet(project)
                    loadCues()
                }
                .onDisappear { player?.pause() }
            } else {
                ContentUnavailableView("Open a Project", systemImage: "folder", description: Text("Choose a project to view subtitles."))
            }
        }
        .navigationTitle("Subtitles")
    }

    private func setTitle(_ set: SubtitleSummary, project: ProjectSummary) -> String {
        if set.artifacts.keys.contains(where: { $0 == "source_srt" || $0 == "chinese_srt" }) {
            return "Source · \(set.language.uppercased())"
        }
        let versionID = set.id.split(separator: ":").first.map(String.init) ?? ""
        if let dub = project.dubs.first(where: { $0.id == versionID }) {
            return "\(dub.title) · \(set.language.uppercased())"
        }
        return "\(set.language.uppercased()) · \(versionID == set.language ? "Source or standalone" : "Subtitle version")"
    }

    private func selectDefaultSet(_ project: ProjectSummary) {
        if !project.subtitles.contains(where: { $0.id == selectedSetID }) {
            selectedSetID = project.subtitles.first?.id ?? ""
        }
        loadCues()
    }

    private func loadSource(_ project: ProjectSummary) {
        let path = project.artifacts["source_video"] ?? project.source
        player = FileManager.default.fileExists(atPath: path) ? AVPlayer(url: URL(fileURLWithPath: path)) : nil
    }

    private func loadCues() {
        cues = []
        fileIssue = nil
        guard let subtitlePath else { return }
        do {
            let text = try String(contentsOfFile: subtitlePath, encoding: .utf8)
            let blocks = text.replacingOccurrences(of: "\r\n", with: "\n")
                .components(separatedBy: "\n\n")
            cues = blocks.enumerated().compactMap { index, block in
                let lines = block.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
                guard let timingIndex = lines.firstIndex(where: { $0.contains(" --> ") }),
                      timingIndex + 1 < lines.count else { return nil }
                let timestamps = lines[timingIndex].components(separatedBy: " --> ")
                guard timestamps.count == 2, let start = seconds(timestamps[0]),
                      let end = seconds(timestamps[1]) else { return nil }
                return SubtitleCue(id: index + 1, start: start, end: end,
                                   time: timestamps[0].trimmingCharacters(in: .whitespaces),
                                   text: lines[(timingIndex + 1)...].joined(separator: "\n"))
            }
        } catch {
            fileIssue = "Subtitle file is unavailable at its saved location."
        }
    }

    private func seconds(_ time: String) -> Double? {
        let parts = time.trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: ",", with: ".").split(separator: ":")
        guard parts.count == 3, let hours = Double(parts[0]), let minutes = Double(parts[1]),
              let seconds = Double(parts[2]) else { return nil }
        return hours * 3600 + minutes * 60 + seconds
    }
}
