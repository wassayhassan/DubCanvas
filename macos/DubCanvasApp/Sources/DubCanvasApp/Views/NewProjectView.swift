import AppKit
import AVFoundation
import SwiftUI

struct NewProjectView: View {
    @EnvironmentObject private var state: AppState
    @State private var detailsExpanded = false
    @State private var showingLinkField = false
    @State private var thumbnail: NSImage?
    @State private var dropTargeted = false
    @State private var proposedNameFromSource = ""
    @FocusState private var linkFocused: Bool

    private var hasLocalFile: Bool {
        state.source.hasPrefix("/")
    }

    private var hasSource: Bool {
        !state.source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 8) {
                    Label("New Project", systemImage: "folder.badge.plus")
                        .font(.largeTitle.bold())
                    Text("Start with one video. DubCanvas keeps its transcript, speakers and subtitles together so every dub version stays consistent.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                sourceSection
                identitySection
                expectations
                detailsSection

                if !state.jobIssue.isEmpty {
                    issueRow(state.jobIssue, symbol: "exclamationmark.triangle", color: .orange)
                } else if let issue = state.quickStartProblem, hasSource {
                    issueRow(issue, symbol: "info.circle", color: .secondary)
                }

                if let issue = state.quickStartProblem,
                   issue.contains("Settings") || issue.contains("translation provider") || issue.contains("model file") {
                    Button("Open Processing Settings") {
                        state.settingsShowProviders = true
                        state.selection = .settings
                    }
                }
                if !state.canQuickStart && !state.startPending && !state.jobStartPending && state.activeJobID == nil {
                    Button("Reconnect Processing Service") { state.connectBackend() }
                }

                HStack(alignment: .center, spacing: 16) {
                    Button("Create Project and Start \(languageName) Dub") { state.quickStart() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                        .disabled(!state.canQuickStart || state.quickStartProblem != nil || state.sourceValidationPending)
                    if state.sourceValidationPending { ProgressView().controlSize(.small) }
                }
                .padding(.top, 6)
                Button("Create Without Dub") { state.createProject() }
                    .buttonStyle(.link)
                    .disabled(!hasSource || state.projectNameProblem() != nil || state.sourceValidationPending)
                Text("Saves the project now. You can generate subtitles or create a dub later.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: 720, alignment: .leading)
            .padding(28)
            .frame(maxWidth: .infinity, alignment: .top)
        }
        .navigationTitle("New Project")
        .onChange(of: state.source) { _, source in
            state.resetSourceInspection()
            thumbnail = nil
            if !proposedNameFromSource.isEmpty && state.projectName == proposedNameFromSource {
                state.projectName = ""
            }
            guard state.projectName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  !source.isEmpty else {
                if source.hasPrefix("/") { state.inspectSource() }
                return
            }
            if source.hasPrefix("/") {
                state.projectName = URL(fileURLWithPath: source).deletingPathExtension().lastPathComponent
                    .replacingOccurrences(of: "_", with: " ")
                proposedNameFromSource = state.projectName
                state.inspectSource()
            }
        }
        .onChange(of: state.sourceInspection?.source) { _, _ in
            if let inspection = state.sourceInspection, inspection.kind == "url",
               state.projectName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                state.projectName = inspection.title
                proposedNameFromSource = inspection.title
            }
            loadThumbnail()
        }
    }

    private var languageName: String {
        ["en": "English", "es": "Spanish", "fr": "French", "de": "German", "ja": "Japanese",
         "ko": "Korean", "zh": "Chinese", "pt": "Portuguese", "it": "Italian", "hi": "Hindi",
         "ar": "Arabic"][state.targetLanguage] ?? state.targetLanguage.uppercased()
    }

    private var durationLabel: String {
        guard let duration = state.sourceInspection?.duration, duration > 0 else { return "Duration unavailable" }
        let seconds = Int(duration)
        return seconds >= 3600 ? String(format: "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
            : String(format: "%d:%02d", seconds / 60, seconds % 60)
    }

    private var sourceSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle("1", "Source video")
            HStack(spacing: 18) {
                if let thumbnail {
                    Image(nsImage: thumbnail).resizable().scaledToFill()
                        .frame(width: 190, height: 112).clipped()
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                        .accessibilityLabel("Selected video thumbnail")
                } else if let address = state.sourceInspection?.thumbnailURL,
                          let url = URL(string: address), url.scheme == "https" {
                    AsyncImage(url: url) { image in
                        image.resizable().scaledToFill()
                    } placeholder: {
                        Image(systemName: "film.fill").font(.largeTitle).foregroundStyle(.tint)
                    }
                    .frame(width: 190, height: 112).clipped()
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                } else {
                    Image(systemName: hasSource ? "film.fill" : "plus.viewfinder")
                        .font(.system(size: 42))
                        .foregroundStyle(.tint)
                        .frame(width: 112, height: 112)
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text(state.sourceInspection?.title ?? (hasSource ? (hasLocalFile ? URL(fileURLWithPath: state.source).lastPathComponent : "Video link") : "Choose a video"))
                        .font(.headline).lineLimit(2)
                    Text(hasSource ? durationLabel : "Drop a video here or choose a file from your Mac.")
                        .foregroundStyle(.secondary)
                    if let size = state.sourceInspection?.sizeBytes {
                        Text(ByteCountFormatter.string(fromByteCount: size, countStyle: .file))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if state.sourceValidationPending {
                        Label("Checking video and audio…", systemImage: "hourglass")
                            .font(.caption).foregroundStyle(.secondary)
                    } else if state.sourceInspection != nil {
                        Label("Video and audio available", systemImage: "checkmark.circle.fill")
                            .font(.caption).foregroundStyle(.green)
                    }
                    Button(hasLocalFile ? "Change Video…" : "Choose Video…") { state.chooseSourceFile() }
                        .buttonStyle(.borderedProminent)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, minHeight: 165, alignment: .leading)
            .padding(18)
            .background(dropTargeted ? Color.accentColor.opacity(0.13) : Color.accentColor.opacity(0.045),
                        in: RoundedRectangle(cornerRadius: 16))
            .overlay(RoundedRectangle(cornerRadius: 16)
                .strokeBorder(Color.accentColor.opacity(dropTargeted ? 0.7 : 0.3), style: StrokeStyle(lineWidth: 1.5, dash: [7])))
            .onDrop(of: ["public.file-url"], isTargeted: $dropTargeted) { providers in
                guard let provider = providers.first else { return false }
                _ = provider.loadObject(ofClass: URL.self) { url, _ in
                    if let url, url.isFileURL {
                        DispatchQueue.main.async { state.source = url.path }
                    }
                }
                return true
            }
            HStack(spacing: 10) {
                Button("Paste Link", systemImage: "link") {
                    showingLinkField = true
                    if hasLocalFile { state.source = "" }
                    if let copied = NSPasteboard.general.string(forType: .string)?.trimmingCharacters(in: .whitespacesAndNewlines),
                       copied.hasPrefix("https://") || copied.hasPrefix("http://") {
                        state.source = copied
                    }
                    linkFocused = true
                }
                    .buttonStyle(.link)
                if hasLocalFile { Text("or use a video page link").font(.caption).foregroundStyle(.secondary) }
            }
            if showingLinkField || (hasSource && !hasLocalFile) {
                HStack {
                    TextField("Paste video page URL", text: $state.source)
                        .textFieldStyle(.roundedBorder)
                        .focused($linkFocused)
                    Button("Check Link") { state.inspectSource() }
                        .disabled(!hasSource || state.sourceValidationPending || hasLocalFile)
                }
                Text("Use a video page URL, not a temporary playback link.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let issue = state.sourceValidationIssue {
                issueRow(issue, symbol: "exclamationmark.triangle", color: .orange)
                HStack {
                    Button("Choose Another Video…") { state.chooseSourceFile() }
                    Button("Check Again") { state.inspectSource() }
                    Button("System Check") { state.runSystemCheck() }
                }
            }
        }
    }

    private var identitySection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle("2", "Name and target")
            GroupBox {
                VStack(alignment: .leading, spacing: 14) {
                    Text("Project name").fontWeight(.medium)
                    TextField("For example, Episode 1", text: $state.projectName)
                        .textFieldStyle(.roundedBorder)
                    if let issue = state.projectNameProblem() {
                        Label(issue, systemImage: "exclamationmark.circle")
                            .font(.caption).foregroundStyle(.orange)
                        if let suggested = state.suggestedProjectName {
                            Button("Use \(suggested)") { state.projectName = suggested }
                                .buttonStyle(.link)
                        }
                    }
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("First dub language").fontWeight(.medium)
                            Text("The source language is detected automatically.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Picker("First dub language", selection: $state.targetLanguage) {
                            Text("English").tag("en")
                            Text("Spanish").tag("es")
                            Text("French").tag("fr")
                            Text("German").tag("de")
                            Text("Japanese").tag("ja")
                            Text("Korean").tag("ko")
                            Text("Chinese").tag("zh")
                            Text("Portuguese").tag("pt")
                            Text("Italian").tag("it")
                            Text("Hindi").tag("hi")
                            Text("Arabic").tag("ar")
                        }
                        .labelsHidden().frame(width: 165)
                    }
                    if !state.outputFolder.isEmpty {
                        HStack {
                            Label("Saved in \((state.outputFolder as NSString).expandingTildeInPath)", systemImage: "folder")
                            Button("Change…") { state.chooseOutputFolder() }
                                .buttonStyle(.link)
                        }
                            .font(.caption).foregroundStyle(.secondary)
                            .lineLimit(1).truncationMode(.middle)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
            }
        }
    }

    private var expectations: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("When you start the first dub").font(.headline)
            Label("Source language and speakers detected automatically", systemImage: "waveform.badge.magnifyingglass")
            Label("Source and translated subtitles saved before voice generation", systemImage: "captions.bubble")
            Label("Final output: dubbed video and subtitle files", systemImage: "film.stack")
            if let seconds = state.sourceInspection?.duration, seconds > 0 {
                let audioBytes = Int64(seconds * 44100 * 4 * 3)
                Text("Temporary audio for this video needs at least \(ByteCountFormatter.string(fromByteCount: audioBytes, countStyle: .file)). Video output and models need more space.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("The first run may download several GB of speech, translation, and voice models. Exact download size depends on the installed models.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
    }

    private func loadThumbnail() {
        thumbnail = nil
        guard state.sourceInspection?.kind == "file", let path = state.sourceInspection?.source else { return }
        DispatchQueue.global(qos: .userInitiated).async {
            let generator = AVAssetImageGenerator(asset: AVURLAsset(url: URL(fileURLWithPath: path)))
            generator.appliesPreferredTrackTransform = true
            let image = try? generator.copyCGImage(at: CMTime(seconds: 0.5, preferredTimescale: 600), actualTime: nil)
            DispatchQueue.main.async {
                guard state.source == path, let image else { return }
                thumbnail = NSImage(cgImage: image, size: NSSize(width: image.width, height: image.height))
            }
        }
    }

    private var detailsSection: some View {
        DisclosureGroup("Advanced project settings", isExpanded: $detailsExpanded) {
            VStack(alignment: .leading, spacing: 12) {
                labeledField("Series identifier", placeholder: "Optional", text: $state.seriesID)
                HStack(alignment: .bottom, spacing: 10) {
                    labeledField("Output folder", placeholder: "Choose a folder", text: $state.outputFolder)
                    Button("Choose…") { state.chooseOutputFolder() }
                }
            }
            .padding(.top, 12)
        }
        .padding(16)
        .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 12))
    }

    private func sectionTitle(_ number: String, _ title: String) -> some View {
        HStack(spacing: 9) {
            Text(number)
                .font(.caption.bold())
                .foregroundStyle(.white)
                .frame(width: 23, height: 23)
                .background(.tint, in: Circle())
            Text(title).font(.headline)
        }
    }

    private func labeledField(_ label: String, placeholder: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label).font(.callout).foregroundStyle(.secondary)
            TextField(placeholder, text: text)
                .textFieldStyle(.roundedBorder)
        }
    }

    private func issueRow(_ text: String, symbol: String, color: Color) -> some View {
        Label(text, systemImage: symbol)
            .font(.callout)
            .foregroundStyle(color)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
            .background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
    }
}
