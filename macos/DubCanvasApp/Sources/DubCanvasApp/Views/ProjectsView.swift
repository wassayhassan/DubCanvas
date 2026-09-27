import SwiftUI

struct ProjectsView: View {
    @EnvironmentObject private var state: AppState
    @State private var search = ""

    private var visibleProjects: [ProjectSummary] {
        guard !search.isEmpty else { return state.projects }
        return state.projects.filter {
            $0.displayName.localizedCaseInsensitiveContains(search) ||
            $0.source.localizedCaseInsensitiveContains(search)
        }
    }

    var body: some View {
        Group {
            if state.projects.isEmpty {
                ContentUnavailableView {
                    Label("No Projects Yet", systemImage: "square.stack.3d.up")
                } description: {
                    Text("Add a source video to a project, then create as many dub versions as you need.")
                } actions: {
                    Button("New Project") { state.newProject() }
                        .buttonStyle(.borderedProminent)
                }
            } else {
                VStack(spacing: 0) {
                    Table(visibleProjects, selection: $state.selectedProjectID) {
                        TableColumn("Project") { project in
                            HStack(spacing: 12) {
                                Image(systemName: "folder.fill").foregroundStyle(.tint)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(project.displayName).fontWeight(.medium)
                                    Text(project.source).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                }
                            }.padding(.vertical, 6)
                        }
                        TableColumn("Dubs") { project in
                            Text("\(project.dubs.count)").monospacedDigit()
                        }.width(65)
                        TableColumn("Subtitles") { project in
                            Text("\(project.subtitles.count)").monospacedDigit()
                        }.width(90)
                        TableColumn("Status") { project in
                            Label(project.statusLabel, systemImage: project.status == "completed" ? "checkmark.circle" : project.status == "running" ? "hourglass" : "circle")
                        }.width(110)
                    }
                    HStack {
                        Text("Open a project to see its source analysis, subtitles and dub versions.")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button("Open Project") {
                            if let project = state.currentProject { state.openProject(project) }
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(state.currentProject == nil)
                    }.padding(16)
                }
            }
        }
        .navigationTitle("Projects")
        .searchable(text: $search, prompt: "Search projects by name or source")
        .toolbar {
            Button { state.newProject() } label: {
                Label("New Project", systemImage: "folder.badge.plus")
            }
            Button { state.refreshProjects() } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
        }
        .task { state.refreshProjects() }
    }
}
