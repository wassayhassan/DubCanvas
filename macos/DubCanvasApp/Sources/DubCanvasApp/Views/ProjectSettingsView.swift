import SwiftUI

struct ProjectSettingsView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        Form {
            if let project = state.currentProject {
                Section("Project identity") {
                    TextField("Project name", text: $state.projectName)
                    if let issue = state.projectNameProblem(excluding: project.id) {
                        Label(issue, systemImage: "exclamationmark.circle")
                            .foregroundStyle(.orange)
                    }
                    if !state.jobIssue.isEmpty {
                        Label(state.jobIssue, systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                    }
                    TextField("Series identifier", text: $state.seriesID)
                    LabeledContent("Source", value: project.source)
                    LabeledContent("Output folder", value: project.outputDir)
                    HStack {
                        Spacer()
                        Button("Save Project Settings") { state.saveProjectSettings() }
                            .buttonStyle(.borderedProminent)
                            .disabled(state.projectNameProblem(excluding: project.id) != nil)
                    }
                }
                Section("Processing defaults") {
                    Toggle("Resume cached processing", isOn: $state.resumeCachedWork)
                    Toggle("Detect characters", isOn: $state.detectCharacters)
                    Text("Choose language, translation model and voice engine for each new dub. Existing versions retain their own settings.")
                        .foregroundStyle(.secondary)
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Project Settings")
    }
}
