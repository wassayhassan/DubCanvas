import SwiftUI

struct RootView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.openWindow) private var openWindow
    @SceneStorage("workspaceProjectID") private var savedProjectID = ""
    @SceneStorage("workspaceDestination") private var savedDestination = "overview"

    var body: some View {
        NavigationSplitView {
            List(selection: $state.selection) {
                if let project = state.currentProject {
                    Section(project.displayName) {
                        sidebarRow(.overview)
                        sidebarRow(.media)
                        sidebarRow(.subtitles)
                        sidebarRow(.characters)
                        sidebarRow(.dubs)
                        ForEach(project.dubs) { dub in
                            NavigationLink(value: SidebarDestination.dub(dub.id)) {
                                HStack(spacing: 8) {
                                    if dub.status == "running" {
                                        ProgressView().controlSize(.mini)
                                    } else {
                                        Image(systemName: dub.status == "completed" ?
                                              (dub.warnings.isEmpty ? "checkmark.circle.fill" : "exclamationmark.circle.fill") :
                                              dub.status == "failed" ? "xmark.octagon.fill" : "pause.circle")
                                            .foregroundStyle(dub.status == "completed" ?
                                                (dub.warnings.isEmpty ? Color.green : Color.orange) :
                                                dub.status == "failed" ? Color.red : Color.secondary)
                                    }
                                    Text(dub.title).lineLimit(1)
                                    Spacer(minLength: 0)
                                }
                                .padding(.leading, 14)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .contentShape(Rectangle())
                            }
                            .help("\(dub.title) · \(dub.status.capitalized) · \(dub.language.uppercased())")
                        }
                        Button {
                            state.outputMode = .dub
                            state.dubName = ""
                            state.selection = .newDub
                        } label: {
                            Label("New Dub", systemImage: "plus.circle.fill")
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(project.status == "running" || state.activeJobID != nil)
                    }
                }
                Section("App") {
                    Button { openWindow(id: "welcome") } label: {
                        Label("All Projects", systemImage: "square.stack.3d.up")
                    }.buttonStyle(.plain)
                    Button {
                        openWindow(id: "workspace", value: WorkspaceRequest.newProject(in: state.outputFolder))
                    } label: {
                        Label("New Project", systemImage: "folder.badge.plus")
                    }.buttonStyle(.plain)
                    sidebarRow(.activity)
                    sidebarRow(.settings)
                }
            }
            .listStyle(.sidebar)
            .navigationTitle(state.currentProject?.displayName ?? "DubCanvas")
            .navigationSplitViewColumnWidth(min: 230, ideal: 255, max: 320)
        } detail: {
            detail
                .toolbar {
                    ToolbarItemGroup(placement: .primaryAction) {
                        backendBadge
                        if state.activeJobID != nil || state.startPending || state.jobStartPending {
                            Button { state.selection = state.currentProject == nil ? .projects : .overview } label: {
                                Label(state.statusText, systemImage: "hourglass")
                            }.help("Show project progress")
                        }
                        Button { state.runSystemCheck() } label: {
                            Label("System Check", systemImage: "stethoscope")
                        }
                    }
                }
                .safeAreaInset(edge: .bottom, spacing: 0) { StatusBarView() }
        }
        .sheet(isPresented: $state.showingSystemCheck) {
            SystemCheckSheet().environmentObject(state)
        }
        .onChange(of: state.settingsSnapshot) { _, _ in state.savePreferences() }
        .onChange(of: state.selection) { _, destination in
            if destination == .newDub { state.outputMode = .dub }
            if destination == .newSubtitles { state.outputMode = .subtitles }
            if state.currentProject != nil, let destination {
                savedDestination = destination.storageKey
            }
        }
        .onChange(of: state.currentProject?.id) { _, projectID in
            guard let projectID else { return }
            if savedProjectID == projectID {
                if let destination = SidebarDestination(storageKey: savedDestination) {
                    switch destination {
                    case .dub(let id), .review(let id):
                        state.selection = state.currentProject?.dubs.contains(where: { $0.id == id }) == true
                            ? destination : .overview
                    default:
                        state.selection = destination
                    }
                }
            } else {
                savedProjectID = projectID
                savedDestination = "overview"
            }
        }
    }

    private func sidebarRow(_ destination: SidebarDestination) -> some View {
        NavigationLink(value: destination) {
            Label(destination.title, systemImage: destination.symbol)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
        }
    }

    @ViewBuilder private var detail: some View {
        switch state.selection ?? .projects {
        case .projects:
            ContentUnavailableView {
                Label("Choose a Project", systemImage: "folder")
            } description: {
                Text("Open a project from the welcome window.")
            } actions: {
                Button("Show Projects") { openWindow(id: "welcome") }
            }
        case .newProject: NewProjectView()
        case .processing: ProjectWorkspaceView()
        case .overview, .media, .dubs: ProjectWorkspaceView()
        case .subtitles: ProjectSubtitlesView()
        case .dub(let id): DubDetailsView(dubID: id)
        case .review(let id): DubReviewView(dubID: id)
        case .newDub, .newSubtitles: NewDubView()
        case .characters: CharactersView()
        case .projectSettings: ProjectSettingsView()
        case .activity: ActivityView()
        case .settings: SettingsView()
        }
    }

    private var backendBadge: some View {
        HStack(spacing: 6) {
            Circle().fill(state.backendState.color).frame(width: 8, height: 8)
            Text(state.backendState.label).font(.caption).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(.quaternary, in: Capsule())
    }
}

struct SystemCheckSheet: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("System Check").font(.title2.bold())
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            List(state.systemCheckItems) { item in
                Label {
                    VStack(alignment: .leading) {
                        Text(item.name + (item.optional && !item.ok ? " (optional)" : ""))
                            .fontWeight(.medium)
                        Text(item.detail).font(.caption).foregroundStyle(.secondary)
                    }
                } icon: {
                    Image(systemName: item.ok ? "checkmark.circle.fill" : item.optional ? "minus.circle" : "xmark.circle.fill")
                        .foregroundStyle(item.ok ? .green : item.optional ? .secondary : .red)
                }
            }
            if state.systemCheckItems.contains(where: { !$0.ok && !$0.optional }) {
                Text("Setup is incomplete. Run the project's setup.sh from Terminal, then reopen DubCanvas and run System Check again. This installs video tools and Python dependencies.")
                    .font(.callout)
            }
        }
        .padding(22).frame(width: 560, height: 430)
    }
}
