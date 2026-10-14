import SwiftUI
import AppKit

struct ManagedSetupView: View {
    @ObservedObject private var setup = ManagedSetup.shared
    @State private var quality = ProcessInfo.processInfo.physicalMemory >= 44_000_000_000
    @State private var token = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Set up DubCanvas").font(.largeTitle.bold())
            Text("The app downloads its tools and models automatically. No Terminal commands or model paths are needed.")
            Toggle("Install Chinese → English Quality Pipeline", isOn: $quality).disabled(setup.busy)
            if quality {
                Text("Experimental · Requires 48 GB memory and a large model download. Model performance on your Mac still needs a first-video test.").font(.caption)
                Link("Accept speaker model access conditions", destination: URL(string: "https://huggingface.co/pyannote/speaker-diarization-community-1")!)
                Link("Create a Hugging Face read token", destination: URL(string: "https://huggingface.co/settings/tokens")!)
                SecureField("Hugging Face token", text: $token).disabled(setup.busy)
                Text("Your token is used for model downloads and saved in macOS Keychain. Cloud dubbing and voice cloning stay off.").font(.caption)
            }
            if setup.busy { ProgressView().controlSize(.small) }
            Text(setup.status).font(.callout).textSelection(.enabled)
            if let error = setup.error { Text(error).foregroundStyle(.red).textSelection(.enabled) }
            HStack {
                Button(setup.error == nil ? "Download and Set Up" : "Retry Setup") {
                    if !token.isEmpty {
                        do { try KeychainStore.set(token, for: "huggingface-token") }
                        catch { setup.error = error.localizedDescription; return }
                    }
                    setup.start(quality: quality, token: token.isEmpty ? (KeychainStore.string(for: "huggingface-token") ?? "") : token)
                }.buttonStyle(.borderedProminent).disabled(setup.busy)
                if let url = setup.logURL { Button("Open Setup Log") { NSWorkspace.shared.open(url) } }
            }
            DisclosureGroup("Setup details") {
                ScrollView { Text(setup.log).font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(height: 160)
            }
        }.padding(28).frame(maxWidth: 760)
    }
}

struct SetupGate<Content: View>: View {
    @ObservedObject private var setup = ManagedSetup.shared
    @ViewBuilder var content: () -> Content
    var body: some View {
        if setup.needsSetup { ManagedSetupView() }
        else { content().disabled(setup.busy) }
    }
}
