import SwiftUI

struct WorkspaceRequest: Codable, Hashable {
    let projectID: String?
    let outputFolder: String
    let draftID: UUID?

    static func newProject(in folder: String) -> Self {
        Self(projectID: nil, outputFolder: folder, draftID: UUID())
    }

    static func existing(_ project: ProjectSummary) -> Self {
        Self(projectID: project.id, outputFolder: project.outputDir, draftID: nil)
    }
}

@main
struct DubCanvasApp: App {
    @StateObject private var libraryState = AppState()

    var body: some Scene {
        Window("Welcome to DubCanvas", id: "welcome") {
            ProjectsView()
                .environmentObject(libraryState)
                .frame(minWidth: 760, minHeight: 540)
        }
        .defaultSize(width: 920, height: 640)
        .commands {
            ProjectWindowCommands(libraryState: libraryState)
        }

        WindowGroup("Project Workspace", id: "workspace", for: WorkspaceRequest.self) { request in
            WorkspaceWindow(request: request.wrappedValue)
                .frame(minWidth: 940, minHeight: 650)
        }
        .defaultSize(width: 1180, height: 780)
        .commands {
            ProjectWindowCommands(libraryState: libraryState)
        }

        Settings {
            SettingsView()
                .environmentObject(libraryState)
                .frame(width: 720, height: 620)
        }
    }
}

private struct WorkspaceWindow: View {
    let request: WorkspaceRequest?
    @StateObject private var state = AppState()
    @State private var initialized = false
    @State private var openedProject = false

    var body: some View {
        RootView()
            .environmentObject(state)
            .onAppear {
                guard !initialized else { return }
                initialized = true
                if let request {
                    state.outputFolder = request.outputFolder
                    if let projectID = request.projectID {
                        state.selectedProjectID = projectID
                        state.selection = .overview
                        state.refreshProjects()
                    } else {
                        state.newProject()
                    }
                }
            }
            .onReceive(state.$projects) { projects in
                guard !openedProject, let projectID = request?.projectID,
                      let project = projects.first(where: { $0.id == projectID }) else { return }
                openedProject = true
                state.openProject(project)
            }
    }
}

private struct ProjectWindowCommands: Commands {
    @Environment(\.openWindow) private var openWindow
    @ObservedObject var libraryState: AppState

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("New Project") {
                openWindow(id: "workspace", value: WorkspaceRequest.newProject(in: libraryState.outputFolder))
            }
            .keyboardShortcut("n", modifiers: .command)
            Button("Show Projects") { openWindow(id: "welcome") }
                .keyboardShortcut("o", modifiers: [.command, .shift])
        }
        CommandMenu("DubCanvas") {
            Button("System Check") { libraryState.runSystemCheck() }
                .keyboardShortcut("d", modifiers: [.command, .shift])
        }
    }
}
