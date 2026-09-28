import SwiftUI

struct RootView: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        NavigationSplitView {
            List(selection: $state.selection) {
                Section("Library") {
                    Button { openWindow(id: "welcome") } label: {
                        Label("All Projects", systemImage: "square.stack.3d.up")
                    }
                    .buttonStyle(.plain)
                    Button {
                        openWindow(id: "workspace", value: WorkspaceRequest.newProject(in: state.outputFolder))
                    } label: {
                        Label("New Project", systemImage: "folder.badge.plus")
                    }
                    .buttonStyle(.plain)
                }

                if let project = state.currentProject {
                    Section(project.displayName) {
                        sidebarRow(.overview)
                        sidebarRow(.media)
                        sidebarRow(.subtitles)
                        sidebarRow(.characters)
                        sidebarRow(.dubs)
                        sidebarRow(.newDub)
                        ForEach(project.dubs) { dub in
                            Button { state.selectDub(dub) } label: {
                                Label(dub.title, systemImage: dub.status == "completed" ? "checkmark.circle" : "waveform.circle")
                                    .lineLimit(1)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .contentShape(Rectangle())
                                    .padding(.leading, 12)
                            }
                            .buttonStyle(.plain)
                            .tag(SidebarDestination.dub(dub.id))
                        }
                    }
                }

                Section("Support") {
                    sidebarRow(.activity)
                    sidebarRow(.settings)
                }
            }
            .listStyle(.sidebar)
            .navigationTitle("DubCanvas")
            .navigationSplitViewColumnWidth(min: 205, ideal: 235, max: 290)
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
        }
    }

    private func sidebarRow(_ destination: SidebarDestination) -> some View {
        Button { state.selection = destination } label: {
            Label(destination.title, systemImage: destination.symbol)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .tag(destination)
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
