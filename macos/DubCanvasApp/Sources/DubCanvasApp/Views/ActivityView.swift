import SwiftUI

struct ActivityView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        DiagnosticLogView().navigationTitle("Activity & Logs")
    }
}
