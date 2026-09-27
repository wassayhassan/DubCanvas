import SwiftUI

struct ProjectsView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.openWindow) private var openWindow
    @Environment(\.scenePhase) private var scenePhase
    @State private var search = ""

    private var recentProjects: [ProjectSummary] {
        let sorted = state.projects.sorted { $0.updatedAt > $1.updatedAt }
        guard !search.isEmpty else { return sorted }
        return sorted.filter {
            $0.displayName.localizedCaseInsensitiveContains(search) ||
            $0.source.localizedCaseInsensitiveContains(search)
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                VStack(alignment: .leading, spacing: 7) {
                    Text("Welcome to DubCanvas")
                        .font(.largeTitle.bold())
                    Text("Keep one source and its analysis together. Create as many dub versions as you need inside the project.")
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                HStack(alignment: .top, spacing: 24) {
                    newProjectCard
                        .frame(minWidth: 250, maxWidth: 330)
                    recentProjectsCard
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                }
            }
            .frame(maxWidth: 1050, alignment: .leading)
            .padding(28)
            .frame(maxWidth: .infinity, alignment: .top)
        }
        .navigationTitle("Projects")
        .toolbar {
            Button { state.refreshProjects() } label: {
                Label("Refresh Projects", systemImage: "arrow.clockwise")
            }
        }
        .task { state.refreshProjects() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { state.refreshProjects() }
        }
        .sheet(isPresented: $state.showingSystemCheck) {
            SystemCheckSheet().environmentObject(state)
        }
    }

    private var newProjectCard: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 16) {
                Image(systemName: "folder.badge.plus")
                    .font(.system(size: 36))
                    .foregroundStyle(.tint)
                    .accessibilityHidden(true)
                Text("Start a new project")
                    .font(.title2.bold())
                Text("Add a video once. Its transcript, speakers and source subtitles stay together for every dub.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Create New Project", systemImage: "plus") {
                    openWindow(id: "workspace", value: WorkspaceRequest.newProject(in: state.outputFolder))
                }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
        }
    }

    private var recentProjectsCard: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Text("Recent projects")
                        .font(.title2.bold())
                    Spacer()
                    Text("\(state.projects.count)")
                        .font(.callout).foregroundStyle(.secondary)
                        .monospacedDigit()
                }
                TextField("Search by project name or source", text: $search)
                    .textFieldStyle(.roundedBorder)

                if recentProjects.isEmpty {
                    ContentUnavailableView {
                        Label(search.isEmpty ? "No projects yet" : "No matching projects", systemImage: "folder")
                    } description: {
                        Text(search.isEmpty ? "Create a project to keep your video and dub versions together." : "Try a different name or source.")
                    }
                    .frame(minHeight: 230)
                } else {
                    LazyVStack(spacing: 8) {
                        ForEach(recentProjects) { project in
                            Button {
                                openWindow(id: "workspace", value: WorkspaceRequest.existing(project))
                            } label: {
                                HStack(spacing: 12) {
                                    Image(systemName: "folder.fill")
                                        .font(.title3).foregroundStyle(.tint)
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(project.displayName).fontWeight(.semibold)
                                            .foregroundStyle(.primary)
                                        Text("\(project.dubs.count) dubs · \(project.statusLabel) · \(project.updatedAt.prefix(10))")
                                            .font(.caption).foregroundStyle(.secondary)
                                        Text(project.source)
                                            .font(.caption).foregroundStyle(.secondary)
                                            .lineLimit(1).truncationMode(.middle)
                                    }
                                    Spacer(minLength: 8)
                                    Image(systemName: "chevron.right")
                                        .foregroundStyle(.tertiary)
                                }
                                .padding(12)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Open \(project.displayName)")
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(12)
        }
    }
}
