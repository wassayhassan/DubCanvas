import SwiftUI

struct NewProjectView: View {
    @EnvironmentObject private var state: AppState
    @State private var detailsExpanded = false

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
                nameSection
                languageSection
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
                    Button("Save Project Only") { state.createProject() }
                        .disabled(!hasSource)
                        .help("Save the source without starting analysis or dubbing")
                    Spacer()
                    Button("Create Project & Dub") { state.quickStart() }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                        .disabled(!state.canQuickStart || state.quickStartProblem != nil)
                }
                .padding(.top, 6)

                Text("The first dub runs automatically. You can add more languages and voice versions from the project later.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: 720, alignment: .leading)
            .padding(28)
            .frame(maxWidth: .infinity, alignment: .top)
        }
        .navigationTitle("New Project")
    }

    private var sourceSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle("1", "Source video")
            GroupBox {
                VStack(alignment: .leading, spacing: 14) {
                    if hasLocalFile {
                        HStack(spacing: 12) {
                            Image(systemName: "film")
                                .font(.title2)
                                .foregroundStyle(.tint)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(URL(fileURLWithPath: state.source).lastPathComponent)
                                    .fontWeight(.medium)
                                    .lineLimit(1)
                                Text(state.source)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                    .textSelection(.enabled)
                            }
                            Spacer(minLength: 8)
                            Button("Use Link Instead") { state.source = "" }
                            Button("Change File…") { state.chooseSourceFile() }
                        }
                    } else {
                        HStack(spacing: 12) {
                            Button { state.chooseSourceFile() } label: {
                                Label("Choose Video File", systemImage: "plus")
                            }
                            Text("or drop a video here")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                        HStack(spacing: 10) {
                            Image(systemName: "link")
                                .foregroundStyle(.secondary)
                            TextField("Or paste a video page URL", text: $state.source)
                                .textFieldStyle(.roundedBorder)
                        }
                    }
                    Text("Use the video's page link rather than a temporary playback URL.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .padding(8)
            }
            .onDrop(of: ["public.file-url"], isTargeted: nil) { providers in
                guard let provider = providers.first else { return false }
                _ = provider.loadObject(ofClass: URL.self) { url, _ in
                    if let url, url.isFileURL {
                        DispatchQueue.main.async { state.source = url.path }
                    }
                }
                return true
            }
        }
    }

    private var nameSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle("2", "Project name")
            GroupBox {
                VStack(alignment: .leading, spacing: 8) {
                    TextField("For example, Episode 1", text: $state.projectName)
                        .textFieldStyle(.roundedBorder)
                    Text("Give this video a name you'll recognize when you return to your projects.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
            }
        }
    }

    private var languageSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            sectionTitle("3", "First dub")
            GroupBox {
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Dub language").fontWeight(.medium)
                        Text("The source language is detected automatically.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Picker("Dub language", selection: $state.targetLanguage) {
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
                    .labelsHidden()
                    .frame(width: 165)
                }
                .padding(8)
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
