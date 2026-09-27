import SwiftUI

struct NewDubView: View {
    @EnvironmentObject private var state: AppState
    @State private var advancedExpanded = false
    @State private var pipelineExpanded = false
    @State private var voiceChoicesExpanded = false

    private var configurationProblem: String? {
        if state.outputMode == .dub && state.targetLanguage != "en" {
            if state.translationProvider == .whisper {
                return "Whisper direct translation only supports English. Choose a local LLM or Ollama."
            }
            if ![VoiceProvider.auto, .chatterbox, .elevenlabs].contains(state.voiceProvider) {
                return "This language needs Chatterbox Multilingual or ElevenLabs voices."
            }
            let multilingual = state.availableProviders["tts"]?["chatterbox_multilingual"] == true
            let hasKey = !state.elevenLabsAPIKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            if state.voiceProvider == .chatterbox && !multilingual {
                return "Install Chatterbox Multilingual or choose ElevenLabs for this language."
            }
            if state.voiceProvider == .auto && !multilingual && !hasKey {
                return "Install Chatterbox Multilingual or add an ElevenLabs API key in Settings."
            }
            if state.voiceProvider == .elevenlabs && !hasKey {
                return "Add an ElevenLabs API key in Settings before starting."
            }
        }
        if state.outputMode == .dub && state.reviewBeforeDub && state.translationProvider == .whisper {
            return "Automatic subtitle review needs a local translation model."
        }
        return nil
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                header
                identitySection
                if state.outputMode == .dub { automaticSetup }
                DisclosureGroup("Advanced processing settings", isExpanded: $pipelineExpanded) {
                    pipelineSection
                }
                if state.outputMode == .dub {
                    DisclosureGroup("Speaker voices for this version", isExpanded: $voiceChoicesExpanded) {
                        voiceChoices
                    }
                }
                if let configurationProblem {
                    Label(configurationProblem, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }
                actions
            }
            .frame(maxWidth: 860, alignment: .leading)
            .padding(28)
            .frame(maxWidth: .infinity, alignment: .topLeading)
        }
        .navigationTitle("New Dub")
        .onAppear { state.refreshCharacterMaps() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(state.outputMode == .subtitles ? "Generate Subtitles" : "Create Dub Version")
                .font(.largeTitle.bold())
            Text("\(state.currentProject?.displayName ?? "Open a project") · \(state.source)")
                .font(.title3)
                .foregroundStyle(.secondary)
            if let project = state.currentProject {
                Label("Reuses this project's source video, transcript, speaker identities and source subtitle timing. Translation, voices and output belong to the new version.", systemImage: "arrow.triangle.2.circlepath")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                if project.status == "running" {
                    Label("Source analysis is running. The new version can use it after it finishes.", systemImage: "hourglass")
                        .font(.caption)
                }
            }
        }
    }

    private var identitySection: some View {
        GroupBox("This Version") {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 12) {
                GridRow {
                    Text("Project")
                        .foregroundStyle(.secondary)
                    Text(state.currentProject?.displayName ?? "No project selected")
                }
                GridRow {
                    Text("Language")
                        .foregroundStyle(.secondary)
                    Picker("Language", selection: $state.targetLanguage) {
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
                    }.labelsHidden()
                }
                if state.outputMode == .dub {
                    GridRow {
                        Text("Dub name").foregroundStyle(.secondary)
                        TextField("Optional, e.g. English — Chatterbox", text: $state.dubName)
                    }
                }
            }
            .padding(.top, 4)
        }
    }

    private var automaticSetup: some View {
        GroupBox("Automatic setup") {
            VStack(alignment: .leading, spacing: 10) {
                Label("Detect speech and reuse this project's transcript, speakers and source subtitles when available.", systemImage: "text.bubble")
                Label("Translate and review subtitles automatically; choose voices for the target language.", systemImage: "waveform")
                Label("Preserve background audio and keep generated lines aligned to source timing.", systemImage: "film.stack")
                Text("This version gets its own translation, voice choices and output. Model and voice overrides are below.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 4)
        }
    }

    private var pipelineSection: some View {
        GroupBox("Pipeline") {
            VStack(spacing: 0) {
                settingRow("Speech recognition") {
                    Picker("Speech recognition", selection: $state.asrProvider) {
                        ForEach(ASRProvider.allCases) { item in
                            Text(item.title).tag(item)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 240)
                }

                if state.asrProvider != .fasterWhisper {
                    settingRow("Whisper model") {
                        Picker("Whisper model", selection: $state.mlxWhisperModel) {
                            Text("Large v3 Turbo · faster").tag("mlx-community/whisper-large-v3-turbo")
                            Text("Large v3 · stronger").tag("mlx-community/whisper-large-v3-mlx")
                        }
                        .labelsHidden()
                        .frame(width: 240)
                    }
                }

                Divider()

                settingRow("Translation") {
                    Picker("Translation", selection: $state.translationProvider) {
                        ForEach(TranslationProvider.allCases) { item in
                            Text(item.title).tag(item)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 240)
                }

                if state.translationProvider == .llm || state.translationProvider == .auto {
                    settingRow("Translation model") {
                        Picker("Translation model", selection: $state.llmModel) {
                            Text("Qwen3 4B · faster").tag("mlx-community/Qwen3-4B-Instruct-2507-4bit")
                            Text("Qwen3 8B · stronger").tag("mlx-community/Qwen3-8B-4bit")
                            Text("Qwen3.5 9B · experimental").tag("mlx-community/Qwen3.5-9B-MLX-4bit")
                            Text("Qwen3 14B · high memory").tag("mlx-community/Qwen3-14B-4bit")
                        }
                        .labelsHidden()
                        .frame(width: 240)
                    }
                }

                Divider()

                settingRow("Voices") {
                    Picker("Voices", selection: $state.voiceProvider) {
                        ForEach(VoiceProvider.allCases) { item in
                            Text(item.title).tag(item)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 240)
                }

                Divider()

                settingRow("Characters") {
                    Toggle("Detect separate speakers", isOn: $state.detectCharacters)
                }

                if state.outputMode == .dub {
                    Divider()
                    settingRow("Subtitle review") {
                        Toggle("Automatically check and correct subtitles", isOn: $state.reviewBeforeDub)
                    }
                    if state.reviewBeforeDub {
                        settingRow("Review model") {
                            Picker("Review model", selection: $state.reviewModel) {
                                Text("Qwen3 8B").tag("mlx-community/Qwen3-8B-4bit")
                                Text("Qwen3.5 9B · experimental").tag("mlx-community/Qwen3.5-9B-MLX-4bit")
                                Text("Qwen3 14B · high memory").tag("mlx-community/Qwen3-14B-4bit")
                            }
                            .labelsHidden()
                            .frame(width: 240)
                        }
                        Text("After subtitles are saved, the local model corrects clear errors automatically and continues dubbing. Uncertain cues stay visible in the report. This adds processing time and downloads models on first use.")
                            .font(.caption).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if state.translationProvider == .whisper {
                            Text("Choose Local LLM or Ollama translation for line-aligned review.")
                                .font(.caption).foregroundStyle(.orange)
                        }
                    }
                }

                if state.detectCharacters && state.outputMode == .dub &&
                    (state.voiceProvider == .chatterbox || state.voiceProvider == .auto) {
                    settingRow("Source voices") {
                        Toggle("Choose character voice clips automatically", isOn: $state.autoSourceVoices)
                    }
                    Text("Character voice clips are chosen automatically during dubbing. You can inspect or change them later. A character without a usable clip gets a fallback voice.")
                        .font(.caption).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                if state.targetLanguage != "en" {
                    Text("Automatic voices use Chatterbox Multilingual when installed, with ElevenLabs as an alternative. Local LLM or Ollama handles translation.")
                        .font(.caption).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 8)
                }

                Divider()

                DisclosureGroup("Advanced", isExpanded: $advancedExpanded) {
                    advancedSettings
                        .padding(.top, 10)
                }
                .padding(.vertical, 11)
            }
            .padding(.horizontal, 2)
        }
    }

    private var advancedSettings: some View {
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 12) {
            GridRow {
                Text("Speaker backend")
                    .foregroundStyle(.secondary)
                Picker("Speaker backend", selection: $state.speakerBackend) {
                    Text("Auto").tag("auto")
                    Text("ECAPA").tag("ecapa")
                    Text("Acoustic").tag("acoustic")
                }
                .labelsHidden()
                .frame(width: 180)
            }

            GridRow {
                Text("Maximum speakers")
                    .foregroundStyle(.secondary)
                Stepper(value: $state.maxSpeakers, in: 2...30) {
                    Text("\(state.maxSpeakers)")
                        .monospacedDigit()
                }
            }

            GridRow {
                Text("Speaker threshold")
                    .foregroundStyle(.secondary)
                HStack {
                    TextField("0", value: $state.speakerThreshold, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 90)
                    Text("0 = automatic")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            GridRow {
                Text("Music / SFX")
                    .foregroundStyle(.secondary)
                HStack {
                    Slider(value: $state.backgroundVolume, in: 0.2...2.0, step: 0.05)
                    Text(state.backgroundVolume.formatted(.number.precision(.fractionLength(2))))
                        .monospacedDigit()
                        .frame(width: 42)
                }
            }

            GridRow {
                Text("Dub voice")
                    .foregroundStyle(.secondary)
                HStack {
                    Slider(value: $state.dubVolume, in: 0.2...2.0, step: 0.05)
                    Text(state.dubVolume.formatted(.number.precision(.fractionLength(2))))
                        .monospacedDigit()
                        .frame(width: 42)
                }
            }

            GridRow {
                Text("")
                Toggle("Duck background under dialogue", isOn: $state.backgroundDucking)
            }

            GridRow {
                Text("Series context")
                    .foregroundStyle(.secondary)
                TextEditor(text: $state.seriesContext)
                    .font(.body)
                    .frame(minHeight: 70, maxHeight: 100)
                    .overlay {
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(.separator, lineWidth: 1)
                    }
            }
        }
    }

    private var actions: some View {
        HStack {
            Spacer()

            if pipelineExpanded {
                Button("Analyze Characters Only") {
                    state.startJob(analysis: true)
                }
                .disabled(!state.canStartJob)
            }

            Button(state.outputMode == .subtitles ? "Generate Subtitles" : "Create Dub") {
                state.startJob(analysis: false)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(!state.canStartJob || configurationProblem != nil)
        }
    }

    private var voiceChoices: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Optional overrides for this dub only. Speaker identities stay shared; completed versions keep their own voice choices.")
                .font(.caption).foregroundStyle(.secondary)
            if state.characters.isEmpty {
                Text("Speakers will be detected automatically. You can inspect project speakers after analysis.")
                    .foregroundStyle(.secondary)
            }
            ForEach(state.characters) { speaker in
                GroupBox(speaker.displayName) {
                    VStack(alignment: .leading, spacing: 8) {
                        Picker("Voice engine", selection: voiceChoice(speaker.id, key: "tts_provider", defaultValue: "inherit")) {
                            Text("Automatic / project speaker voice").tag("inherit")
                            Text("Chatterbox").tag("chatterbox")
                            if state.targetLanguage == "en" {
                                Text("Kokoro").tag("kokoro")
                                Text("macOS Voice").tag("macos")
                            }
                            Text("ElevenLabs").tag("elevenlabs")
                        }
                        let provider = state.versionVoiceOverrides[speaker.id]?["tts_provider"] ?? "inherit"
                        if provider == "kokoro" {
                            TextField("Kokoro preset (e.g. am_adam)", text: voiceChoice(speaker.id, key: "kokoro_voice", defaultValue: speaker.kokoroVoice))
                        } else if provider == "macos" {
                            TextField("macOS voice name", text: voiceChoice(speaker.id, key: "macos_voice", defaultValue: speaker.macosVoice))
                        } else if provider == "chatterbox" {
                            TextField("Reference audio path (optional)", text: voiceChoice(speaker.id, key: "reference_audio", defaultValue: ""))
                        } else if provider == "elevenlabs" {
                            TextField("ElevenLabs voice ID (optional)", text: voiceChoice(speaker.id, key: "elevenlabs_voice_id", defaultValue: ""))
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }.padding(.top, 8)
    }

    private func voiceChoice(_ speakerID: String, key: String, defaultValue: String) -> Binding<String> {
        Binding(
            get: { state.versionVoiceOverrides[speakerID]?[key] ?? defaultValue },
            set: { value in
                var choices = state.versionVoiceOverrides[speakerID] ?? [:]
                choices[key] = value
                state.versionVoiceOverrides[speakerID] = choices
            }
        )
    }

    private func settingRow<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack {
            Text(title)
                .foregroundStyle(.secondary)
            Spacer()
            content()
        }
        .padding(.vertical, 11)
    }
}
