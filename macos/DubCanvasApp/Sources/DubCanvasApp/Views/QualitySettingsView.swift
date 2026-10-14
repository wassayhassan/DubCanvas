import SwiftUI

struct QualitySettingsView: View {
    @EnvironmentObject private var state: AppState

    private func string(_ key: String, default value: String = "") -> Binding<String> {
        Binding(get: { state.parsedQualitySettings?[key] as? String ?? value },
                set: { update(key, $0) })
    }
    private func flag(_ key: String, default value: Bool = false) -> Binding<Bool> {
        Binding(get: { state.parsedQualitySettings?[key] as? Bool ?? value },
                set: { update(key, $0) })
    }
    private func update(_ key: String, _ value: Any) {
        var settings = state.parsedQualitySettings ?? [:]
        settings[key] = value
        if let data = try? JSONSerialization.data(withJSONObject: settings, options: [.prettyPrinted, .sortedKeys]),
           let text = String(data: data, encoding: .utf8) { state.qualitySettingsJSON = text }
    }

    var body: some View {
        Form {
            Section("Processing Profile") {
                Picker("Pipeline", selection: $state.pipelineProfile) {
                    Text("Existing providers").tag("legacy")
                    Text("Chinese → English · Quality v1 (experimental)").tag("quality_v1")
                }
                Text("Quality v1 requires isolated Apple Silicon workers and audited model assets. Existing projects and casts keep their settings. It has not been benchmarked on your Mac.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Setup Verification") {
                Button("Check Quality Setup") { state.checkQualitySetup() }
                if !state.qualitySetupReport.isEmpty {
                    Text(state.qualitySetupReport).font(.caption).textSelection(.enabled)
                }
            }
            Section("Selected Quality Models") {
                model("Chinese transcription", "asr", "FireRedASR2-AED · MLX")
                model("Independent transcription", "verifier", "Qwen3-ASR 1.7B BF16 · MLX")
                model("Alignment", "aligner", "Qwen3 ForcedAligner 0.6B BF16 · MLX")
                model("Translation", "translator", "Hy-MT2 30B-A3B 8-bit · audited MLX adapter")
                model("Meaning review", "reviewer", "Qwen3.8 27B 8-bit · MLX-VLM")
                model("Preset speech", "tts", "Qwen3-TTS 1.7B CustomVoice BF16 · MLX")
                model("Speaker tracks", "diarizer", "pyannote Community-1 · CPU")
                Text("Translation and review run sequentially. Worker memory ceiling: 36 GB; system target: 42 GB. Context budget: 4,096 tokens. Actual Metal footprint and performance require Mac testing.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Worker Environments") {
                TextField("Audio Python path", text: string("mlx_python"))
                TextField("Translation Python path", text: string("translation_python"))
                TextField("Review Python path", text: string("review_python"))
                TextField("BandIt Python path", text: string("separator_python"))
                TextField("Diarization / Kokoro Python path", text: string("torch_python"))
            }
            Section("Audited Separation and Translation Assets") {
                TextField("MSST checkout", text: string("msst_path"))
                TextField("BandIt v2 config", text: string("bandit_config"))
                TextField("Config SHA-256", text: string("bandit_config_sha256"))
                TextField("DnR-v3 checkpoint", text: string("bandit_checkpoint"))
                TextField("Checkpoint SHA-256", text: string("bandit_sha256"))
                TextField("Local Hy model directory", text: string("translation_model_path"))
                TextField("Audited hy_v3.py SHA-256", text: string("translation_adapter_sha256"))
                TextField("Series glossary JSON (optional)", text: string("series_glossary_path"))
                TextField("Episode evidence ledger JSON (optional)", text: string("episode_context_path"))
            }
            Section("Speech and Timing") {
                Picker("Quality voice engine", selection: string("voice_engine", default: "qwen")) {
                    Text("Qwen expressive presets").tag("qwen")
                    Text("Kokoro American presets").tag("kokoro")
                    Text("Eleven v4 · cloud").tag("elevenlabs")
                    Text("IndexTTS2.5 · optional reference clone").tag("index_clone")
                }
                Picker("Default Qwen speaker", selection: string("qwen_speaker", default: "Aiden")) {
                    ForEach(["Aiden", "Ryan", "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ono_Anna", "Sohee"], id: \.self) { Text($0).tag($0) }
                }
                Toggle("Use Kokoro for identified female American roles", isOn: flag("american_female_fallback", default: true))
                Toggle("Allow exceptional timing rescue up to 1.5×", isOn: flag("allow_rescue_speed"))
                Text("Normal stretch ≤1.15×; shouts/whispers ≤1.10×. No fit saves subtitles and voices, then blocks rendering. Set each character's voice in Speakers.")
                    .font(.caption).foregroundStyle(.secondary)
                Toggle("I permit selected cloud services to receive dialogue text", isOn: flag("allow_cloud"))
                Picker("Meaning review", selection: string("review_provider", default: "local")) {
                    Text("Local Qwen3.8").tag("local")
                    Text("Qwen3.8-Max-0902 · cloud").tag("cloud")
                }
                Text("Cloud: Eleven v4 lists $0.08/1,000 characters; Qwen review lists $2/million input + $6/million output tokens. Billing and retention depend on provider terms. Configure Eleven credentials in Providers; Qwen uses DUBCANVAS_QWEN_API_KEY in the backend environment.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            DisclosureGroup("Optional reference cloning") {
                Toggle("I authorize use of the selected reference voice", isOn: flag("reference_consent"))
                TextField("IndexTTS Python path", text: string("clone_python"))
                TextField("IndexTTS2.5 model directory", text: string("clone_model_dir"))
                TextField("IndexTTS2.5 config", text: string("clone_config"))
            }
            DisclosureGroup("Advanced quality configuration") {
                TextEditor(text: $state.qualitySettingsJSON).font(.system(.caption, design: .monospaced)).frame(minHeight: 220)
                if state.parsedQualitySettings == nil { Text("Enter a valid JSON object before starting.").foregroundStyle(.red) }
            }
        }
        .formStyle(.grouped)
    }

    private func model(_ title: String, _ stage: String, _ fallback: String) -> some View {
        let models = state.parsedQualitySettings?["models"] as? [String: [String: Any]]
        return LabeledContent(title, value: models?[stage]?["repo"] as? String ?? fallback)
    }
}
