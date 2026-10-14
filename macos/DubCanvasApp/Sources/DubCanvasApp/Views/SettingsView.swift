import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var state: AppState
    @State private var selectedTab = "general"

    private let kokoroVoices = [
        "auto", "af_heart", "af_bella", "af_sarah", "af_sky",
        "am_adam", "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_lewis",
    ]

    var body: some View {
        TabView(selection: $selectedTab) {
            general
                .tabItem { Label("General", systemImage: "slider.horizontal.3") }
                .tag("general")

            providers
                .tabItem { Label("Providers", systemImage: "cpu") }
                .tag("providers")

            QualitySettingsView()
                .tabItem { Label("Quality Pipeline", systemImage: "waveform.badge.magnifyingglass") }
                .tag("quality")

            backend
                .tabItem { Label("Backend", systemImage: "server.rack") }
                .tag("backend")
        }
        .padding(16)
        .onChange(of: state.settingsSnapshot) { _, _ in
            state.savePreferences()
        }
        .onDisappear {
            state.savePreferences()
        }
        .onAppear {
            if state.targetLanguage != "en" || state.settingsShowProviders { selectedTab = "providers" }
            state.settingsShowProviders = false
        }
    }

    private var general: some View {
        Form {
            Section("Project Defaults") {
                LabeledContent("Output folder") {
                    HStack {
                        TextField("Output folder", text: $state.outputFolder)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 300)
                        Button("Choose…") {
                            state.chooseOutputFolder()
                        }
                    }
                }

                LabeledContent("Series ID") {
                    TextField("Optional default series ID", text: $state.seriesID)
                        .textFieldStyle(.roundedBorder)
                        .frame(minWidth: 300)
                }

                Picker("Output", selection: $state.outputMode) {
                    ForEach(OutputMode.allCases) { item in
                        Text(item.title).tag(item)
                    }
                }

                Toggle("Resume cached work", isOn: $state.resumeCachedWork)
                Toggle("Detect separate speakers", isOn: $state.detectCharacters)
            }

            Section("Audio Defaults") {
                LabeledContent("Music / SFX") {
                    HStack {
                        Slider(value: $state.backgroundVolume, in: 0.2...2.0, step: 0.05)
                            .frame(width: 220)
                        Text(state.backgroundVolume.formatted(.number.precision(.fractionLength(2))))
                            .monospacedDigit()
                            .frame(width: 40)
                    }
                }

                LabeledContent("Dub voice") {
                    HStack {
                        Slider(value: $state.dubVolume, in: 0.2...2.0, step: 0.05)
                            .frame(width: 220)
                        Text(state.dubVolume.formatted(.number.precision(.fractionLength(2))))
                            .monospacedDigit()
                            .frame(width: 40)
                    }
                }

                Toggle("Duck background under dialogue", isOn: $state.backgroundDucking)
            }
        }
        .formStyle(.grouped)
    }

    private var providers: some View {
        Form {
            Section("Speech Recognition") {
                Picker("ASR provider", selection: $state.asrProvider) {
                    ForEach(ASRProvider.allCases) { provider in
                        Text(provider.title).tag(provider)
                    }
                }

                if state.asrProvider == .fasterWhisper {
                    LabeledContent("Model") {
                        TextField("large-v3", text: $state.fasterWhisperModel)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 220)
                    }
                    Picker("Device", selection: $state.fasterWhisperDevice) {
                        Text("Automatic").tag("auto")
                        Text("CPU").tag("cpu")
                        Text("CUDA").tag("cuda")
                    }
                    LabeledContent("Compute type") {
                        TextField("auto", text: $state.fasterWhisperComputeType)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 160)
                    }
                }
                if state.asrProvider != .fasterWhisper {
                    Picker("MLX Whisper model", selection: $state.mlxWhisperModel) {
                        Text("Large v3 Turbo · faster").tag("mlx-community/whisper-large-v3-turbo")
                        Text("Large v3 · stronger").tag("mlx-community/whisper-large-v3-mlx")
                    }
                    TextField("MLX Whisper model ID", text: $state.mlxWhisperModel)
                        .textFieldStyle(.roundedBorder)
                }
            }

            Section("Translation") {
                Picker("Translation provider", selection: $state.translationProvider) {
                    ForEach(TranslationProvider.allCases) { provider in
                        Text(provider.title).tag(provider)
                    }
                }

                if state.translationProvider == .ollama {
                    LabeledContent("Ollama URL") {
                        TextField("http://127.0.0.1:11434", text: $state.ollamaURL)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 280)
                    }
                    LabeledContent("Model") {
                        TextField("qwen3:4b", text: $state.ollamaModel)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 220)
                    }
                }
                if state.translationProvider == .llm || state.translationProvider == .auto {
                    Picker("Local translation model", selection: $state.llmModel) {
                        Text("Qwen3 4B · faster").tag("mlx-community/Qwen3-4B-Instruct-2507-4bit")
                        Text("Qwen3 8B · stronger").tag("mlx-community/Qwen3-8B-4bit")
                        Text("Qwen3.5 9B · experimental").tag("mlx-community/Qwen3.5-9B-MLX-4bit")
                        Text("Qwen3 14B · high memory").tag("mlx-community/Qwen3-14B-4bit")
                    }
                    TextField("MLX model ID", text: $state.llmModel)
                        .textFieldStyle(.roundedBorder)
                }
                Toggle("Automatically check and correct subtitles", isOn: $state.reviewBeforeDub)
                if state.reviewBeforeDub {
                    Picker("Review model", selection: $state.reviewModel) {
                        Text("Qwen3 8B").tag("mlx-community/Qwen3-8B-4bit")
                        Text("Qwen3.5 9B · experimental").tag("mlx-community/Qwen3.5-9B-MLX-4bit")
                        Text("Qwen3 14B · high memory").tag("mlx-community/Qwen3-14B-4bit")
                    }
                    TextField("Review model ID", text: $state.reviewModel)
                        .textFieldStyle(.roundedBorder)
                }
            }

            Section("Dub Voice") {
                Picker("Voice engine", selection: $state.voiceProvider) {
                    ForEach(VoiceProvider.allCases) { provider in
                        Text(provider.title).tag(provider)
                    }
                }

                if state.voiceProvider == .auto {
                    Text("Automatic prefers Chatterbox for local voice cloning. Other dub languages use Chatterbox Multilingual when installed, or ElevenLabs with an API key.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                LabeledContent("Speaking rate") {
                    Stepper(value: $state.ttsRate, in: 80...450, step: 5) {
                        Text("\(state.ttsRate)")
                            .monospacedDigit()
                    }
                }

                if state.voiceProvider == .chatterbox || state.voiceProvider == .auto {
                    Group {
                        LabeledContent("Chatterbox device") {
                            Picker("Device", selection: $state.chatterboxDevice) {
                                Text("Automatic").tag("auto")
                                Text("Apple GPU · MPS").tag("mps")
                                Text("NVIDIA · CUDA").tag("cuda")
                                Text("CPU").tag("cpu")
                            }
                            .labelsHidden()
                            .frame(width: 180)
                        }

                        Toggle("Use Chatterbox Turbo", isOn: $state.chatterboxTurbo)
                        Text("English dubs using a non-English voice reference use Chatterbox Standard to reduce accent transfer, even when Turbo is selected. This can take longer.")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        LabeledContent("Expressiveness") {
                            HStack {
                                Slider(value: $state.chatterboxExpressiveness, in: 0...1.5, step: 0.05)
                                    .frame(width: 220)
                                Text(state.chatterboxExpressiveness.formatted(.number.precision(.fractionLength(2))))
                                    .monospacedDigit()
                                    .frame(width: 40)
                            }
                        }

                        LabeledContent("Default reference") {
                            HStack {
                                TextField("Optional reference audio", text: $state.chatterboxReferenceAudio)
                                    .textFieldStyle(.roundedBorder)
                                    .frame(minWidth: 280)
                                Button("Choose…") {
                                    state.chooseChatterboxReference()
                                }
                                if !state.chatterboxReferenceAudio.isEmpty {
                                    Button("Clear") {
                                        state.chatterboxReferenceAudio = ""
                                    }
                                }
                            }
                        }

                        Text("A default reference overrides automatic character clips. Clear it to use individually selected source voices when that option is enabled in New Dub.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if state.voiceProvider == .kokoro || state.voiceProvider == .auto {
                    LabeledContent("Kokoro voice") {
                        Picker("Kokoro voice", selection: $state.kokoroVoice) {
                            ForEach(kokoroVoices, id: \.self) { voice in
                                Text(voice == "auto" ? "Automatic by character" : voice)
                                    .tag(voice)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 220)
                    }
                }

                if state.voiceProvider == .macos || state.voiceProvider == .auto {
                    LabeledContent("macOS fallback") {
                        Picker("Fallback voice", selection: $state.fallbackVoice) {
                            Text("System default").tag("")
                            ForEach(state.installedVoices, id: \.self) { voice in
                                Text(voice).tag(voice)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 230)
                    }
                }

                if state.voiceProvider == .piper {
                    LabeledContent("Piper model") {
                        HStack {
                            TextField("Path to .onnx voice", text: $state.piperModel)
                                .textFieldStyle(.roundedBorder)
                                .frame(minWidth: 280)
                            Button("Choose…") {
                                state.choosePiperModel()
                            }
                        }
                    }
                    LabeledContent("Speaker ID") {
                        Stepper(value: $state.piperSpeaker, in: -1...64) {
                            Text(state.piperSpeaker < 0 ? "Default" : "\(state.piperSpeaker)")
                                .monospacedDigit()
                        }
                    }
                }

                if state.voiceProvider == .elevenlabs {
                    LabeledContent("API key") {
                        SecureField("ElevenLabs API key", text: $state.elevenLabsAPIKey)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 300)
                    }

                    LabeledContent("Voice ID") {
                        TextField("Voice ID", text: $state.elevenLabsVoiceID)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 300)
                    }

                    HStack {
                        Text("The API key is stored in macOS Keychain, not project files.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Spacer()
                        Button("Save API Key") {
                            state.saveSecrets()
                        }
                    }

                    if !state.credentialStatus.isEmpty {
                        Text(state.credentialStatus)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if state.voiceProvider == .chatterbox || state.voiceProvider == .kokoro || state.voiceProvider == .auto {
                    Text("Premium local engines are optional Python packages. Run macos/install_voice_engines.sh once, then rebuild the app.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .formStyle(.grouped)
    }

    private var backend: some View {
        Form {
            Section("Backend") {
                LabeledContent("Status") {
                    HStack(spacing: 7) {
                        Circle()
                            .fill(state.backendState.color)
                            .frame(width: 8, height: 8)
                        Text(state.backendState.label)
                    }
                }

                Button("Run System Check") {
                    state.runSystemCheck()
                }

                Text("System Check reports whether Chatterbox, Kokoro, and other providers are installed.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Diagnostics") {
                Text(state.backendDiagnostics.isEmpty ? "No backend diagnostics." : state.backendDiagnostics)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .lineLimit(12)

                if !state.backendDiagnostics.isEmpty {
                    Button("Clear Diagnostics") {
                        state.backendDiagnostics = ""
                    }
                }
            }
        }
        .formStyle(.grouped)
    }
}
