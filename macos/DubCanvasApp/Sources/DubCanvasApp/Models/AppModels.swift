import SwiftUI

enum SidebarDestination: Hashable {
    case newProject
    case overview
    case media
    case subtitles
    case dubs
    case dub(String)
    case review(String)
    case projectSettings
    case newDub
    case newSubtitles
    case projects
    case processing
    case characters
    case activity
    case settings

    var storageKey: String {
        switch self {
        case .dub(let id): "dub:\(id)"
        case .review(let id): "review:\(id)"
        case .overview: "overview"
        case .media: "media"
        case .subtitles: "subtitles"
        case .characters: "characters"
        case .dubs: "dubs"
        case .newDub: "newDub"
        case .newSubtitles: "newSubtitles"
        case .activity: "activity"
        case .settings: "settings"
        case .projectSettings: "projectSettings"
        case .processing: "processing"
        case .projects: "projects"
        case .newProject: "newProject"
        }
    }

    init?(storageKey: String) {
        if storageKey.hasPrefix("dub:") { self = .dub(String(storageKey.dropFirst(4))); return }
        if storageKey.hasPrefix("review:") { self = .review(String(storageKey.dropFirst(7))); return }
        switch storageKey {
        case "overview": self = .overview
        case "media": self = .media
        case "subtitles": self = .subtitles
        case "characters": self = .characters
        case "dubs": self = .dubs
        case "newDub": self = .newDub
        case "newSubtitles": self = .newSubtitles
        case "activity": self = .activity
        case "settings": self = .settings
        case "projectSettings": self = .projectSettings
        default: return nil
        }
    }

    var title: String {
        switch self {
        case .newProject: "New Project"
        case .overview: "Overview"
        case .media: "Source & Analysis"
        case .subtitles: "Subtitles"
        case .dubs: "Dubs"
        case .dub: "Dub Details"
        case .review: "Review"
        case .projectSettings: "Project Settings"
        case .newDub: "New Dub"
        case .newSubtitles: "Generate Subtitles"
        case .projects: "Projects"
        case .processing: "Processing"
        case .characters: "Speakers"
        case .activity: "Activity"
        case .settings: "Settings"
        }
    }

    var symbol: String {
        switch self {
        case .newProject: "folder.badge.plus"
        case .overview: "square.grid.2x2"
        case .media: "film"
        case .subtitles: "captions.bubble"
        case .dubs: "waveform"
        case .dub: "waveform.circle"
        case .review: "text.magnifyingglass"
        case .projectSettings: "slider.horizontal.3"
        case .newDub: "waveform.badge.plus"
        case .newSubtitles: "text.badge.plus"
        case .projects: "square.stack.3d.up"
        case .processing: "waveform.path"
        case .characters: "person.2"
        case .activity: "list.bullet.rectangle"
        case .settings: "gearshape"
        }
    }
}

enum OutputMode: String, CaseIterable, Identifiable {
    case dub
    case subtitles

    var id: String { rawValue }
    var title: String {
        switch self {
        case .dub: "Dub + subtitles"
        case .subtitles: "Subtitles only"
        }
    }
}

enum ASRProvider: String, CaseIterable, Identifiable {
    case auto
    case mlxWhisper = "mlx_whisper"
    case fasterWhisper = "faster_whisper"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .auto: "Automatic"
        case .mlxWhisper: "MLX Whisper"
        case .fasterWhisper: "Faster-Whisper"
        }
    }
}

enum TranslationProvider: String, CaseIterable, Identifiable {
    case auto
    case llm
    case ollama
    case whisper

    var id: String { rawValue }
    var title: String {
        switch self {
        case .auto: "Automatic"
        case .llm: "Local MLX LLM"
        case .ollama: "Ollama"
        case .whisper: "Whisper direct"
        }
    }
}

enum VoiceProvider: String, CaseIterable, Identifiable {
    case auto
    case chatterbox
    case kokoro
    case elevenlabs
    case macos
    case piper

    var id: String { rawValue }
    var title: String {
        switch self {
        case .auto: "Automatic · Best Local"
        case .chatterbox: "Chatterbox Voice Clone"
        case .kokoro: "Kokoro · Fast Local"
        case .elevenlabs: "ElevenLabs"
        case .macos: "macOS Voice"
        case .piper: "Piper"
        }
    }
}

enum BackendConnectionState: Equatable {
    case disconnected
    case connecting
    case ready(version: String)
    case failed(String)

    var label: String {
        switch self {
        case .disconnected: "Backend Offline"
        case .connecting: "Connecting…"
        case .ready(let version): "Backend \(version)"
        case .failed: "Backend Error"
        }
    }

    var color: Color {
        switch self {
        case .ready: .green
        case .connecting: .orange
        case .disconnected, .failed: .red
        }
    }
}

struct ActivityEntry: Identifiable {
    enum Kind {
        case info
        case warning
        case error
        case artifact
    }

    let id = UUID()
    let date = Date()
    let kind: Kind
    let message: String

    var symbol: String {
        switch kind {
        case .info: "circle.fill"
        case .warning: "exclamationmark.triangle.fill"
        case .error: "xmark.octagon.fill"
        case .artifact: "doc.fill"
        }
    }

    var tint: Color {
        switch kind {
        case .info: .secondary
        case .warning: .orange
        case .error: .red
        case .artifact: .blue
        }
    }
}

struct SystemCheckItem: Identifiable {
    let id = UUID()
    let name: String
    let ok: Bool
    let detail: String
    var optional = false
}

struct SourceInspection {
    let source: String
    let kind: String
    let title: String
    let duration: Double
    let sizeBytes: Int64?
    let thumbnailURL: String?

    init?(dictionary: [String: Any]) {
        guard let source = dictionary["source"] as? String else { return nil }
        self.source = source
        kind = dictionary["kind"] as? String ?? "file"
        title = dictionary["title"] as? String ?? "Video"
        duration = (dictionary["duration"] as? NSNumber)?.doubleValue ?? 0
        sizeBytes = (dictionary["size_bytes"] as? NSNumber)?.int64Value
        thumbnailURL = dictionary["thumbnail_url"] as? String
    }
}


struct ProjectSummary: Identifiable {
    let id: String
    let source: String
    let sourceTitle: String
    let name: String
    let seriesID: String
    let outputDir: String
    let status: String
    let stage: String
    let stageTitle: String
    let progress: Double?
    let updatedAt: String
    let startedAt: String
    let lastRunMode: String
    let artifacts: [String: String]
    let subtitles: [SubtitleSummary]
    let dubs: [DubSummary]
    let warningCount: Int
    let lastError: String?
    let sourceLanguage: String?
    let analysisRevision: Int

    init?(dictionary: [String: Any]) {
        guard let projectID = dictionary["project_id"] as? String, !projectID.isEmpty else { return nil }
        id = projectID
        source = dictionary["source"] as? String ?? ""
        let inspectedTitle = dictionary["source_title"] as? String ?? ""
        sourceTitle = !inspectedTitle.isEmpty ? inspectedTitle :
            (source.hasPrefix("http") ? (URL(string: source)?.host ?? "Video link") : URL(fileURLWithPath: source).lastPathComponent)
        name = dictionary["name"] as? String ?? ""
        seriesID = dictionary["series_id"] as? String ?? ""
        outputDir = dictionary["output_dir"] as? String ?? ""
        status = dictionary["status"] as? String ?? "unknown"
        stage = dictionary["stage"] as? String ?? ""
        stageTitle = dictionary["stage_title"] as? String ?? ""
        if let number = dictionary["progress"] as? NSNumber {
            progress = number.doubleValue
        } else {
            progress = nil
        }
        updatedAt = dictionary["updated_at"] as? String ?? ""
        startedAt = dictionary["started_at"] as? String ?? ""
        lastRunMode = dictionary["last_run_mode"] as? String ?? ""
        artifacts = dictionary["artifacts"] as? [String: String] ?? [:]
        let subtitleRows = dictionary["subtitles"] as? [String: [String: Any]] ?? [:]
        subtitles = subtitleRows.map { SubtitleSummary(id: $0.key, dictionary: $0.value) }
            .sorted { $0.createdAt > $1.createdAt }
        dubs = (dictionary["dubs"] as? [[String: Any]] ?? []).compactMap(DubSummary.init(dictionary:))
            .sorted { $0.createdAt > $1.createdAt }
        warningCount = (dictionary["warning_count"] as? NSNumber)?.intValue ?? 0
        lastError = dictionary["last_error"] as? String
        sourceLanguage = dictionary["source_language"] as? String
        analysisRevision = (dictionary["analysis_revision"] as? NSNumber)?.intValue ?? 0
    }

    var displayName: String {
        if !name.isEmpty { return name }
        if !seriesID.isEmpty { return seriesID }
        if source.hasPrefix("http") { return id }
        let url = URL(fileURLWithPath: source)
        return url.lastPathComponent.isEmpty ? id : url.lastPathComponent
    }

    var statusLabel: String {
        status.replacingOccurrences(of: "_", with: " ").capitalized
    }
}

struct SubtitleSummary: Identifiable {
    let id: String
    let language: String
    let createdAt: String
    let artifacts: [String: String]

    init(id: String, dictionary: [String: Any]) {
        self.id = id
        language = dictionary["language"] as? String ?? id
        createdAt = dictionary["created_at"] as? String ?? ""
        artifacts = dictionary["artifacts"] as? [String: String] ?? [:]
    }
}

struct DubSummary: Identifiable {
    let id: String
    let jobID: String
    let name: String
    let language: String
    let sourceLanguage: String
    let status: String
    let stage: String
    let createdAt: String
    let updatedAt: String
    let config: [String: Any]
    let artifacts: [String: String]
    let warnings: [String]
    let error: String?
    let sync: String
    let duration: Double?
    let analysisRevision: Int

    init?(dictionary: [String: Any]) {
        guard let id = dictionary["id"] as? String else { return nil }
        self.id = id
        jobID = dictionary["job_id"] as? String ?? ""
        name = dictionary["name"] as? String ?? id
        language = dictionary["language"] as? String ?? "en"
        sourceLanguage = dictionary["source_language"] as? String ?? "zh"
        status = dictionary["status"] as? String ?? "unknown"
        stage = dictionary["stage"] as? String ?? ""
        createdAt = dictionary["created_at"] as? String ?? ""
        updatedAt = dictionary["updated_at"] as? String ?? ""
        config = dictionary["config"] as? [String: Any] ?? [:]
        artifacts = dictionary["artifacts"] as? [String: String] ?? [:]
        warnings = dictionary["warnings"] as? [String] ?? []
        error = dictionary["error"] as? String
        sync = dictionary["sync"] as? String ?? "Timing metadata unavailable"
        duration = (dictionary["duration"] as? NSNumber)?.doubleValue
        analysisRevision = (dictionary["analysis_revision"] as? NSNumber)?.intValue ?? 0
    }

    var title: String { name.isEmpty ? "\(language.uppercased()) dub" : name }
}

struct CharacterMapSummary: Identifiable, Hashable {
    let path: String
    let sourceKey: String
    let seriesID: String
    let speakerBackend: String
    let characterCount: Int
    let modifiedAt: Double

    var id: String { path }

    init?(dictionary: [String: Any]) {
        guard let path = dictionary["path"] as? String, !path.isEmpty else { return nil }
        self.path = path
        sourceKey = dictionary["source_key"] as? String ?? ""
        seriesID = dictionary["series_id"] as? String ?? ""
        speakerBackend = dictionary["speaker_backend"] as? String ?? ""
        characterCount = (dictionary["character_count"] as? NSNumber)?.intValue ?? 0
        modifiedAt = (dictionary["modified_at"] as? NSNumber)?.doubleValue ?? 0
    }

    var displayName: String {
        seriesID.isEmpty ? sourceKey : seriesID
    }
}

struct CharacterItem: Identifiable, Hashable {
    let id: String
    var displayName: String
    var role: String
    var voiceClass: String
    var ageGroup: String
    let lineCount: Int
    let speakingShare: Double
    var ttsProvider: String
    var macosVoice: String
    var kokoroVoice: String
    var referenceAudio: String
    let suggestedReferenceAudio: String
    let referenceTiming: [[Double]]
    var autoReferenceEnabled: Bool
    var expressiveness: Double
    var elevenLabsVoiceID: String
    var ttsRate: Int
    var pitchSemitones: Double
    var voiceGain: Double
    var notes: String
    var manual: Bool

    init?(dictionary: [String: Any]) {
        guard let id = dictionary["id"] as? String, !id.isEmpty else { return nil }
        self.id = id
        displayName = dictionary["display_name"] as? String ?? id
        role = dictionary["role"] as? String ?? "minor"
        voiceClass = dictionary["voice_class"] as? String ?? "neutral"
        ageGroup = dictionary["age_group"] as? String ?? "adult"
        lineCount = (dictionary["line_count"] as? NSNumber)?.intValue ?? 0
        speakingShare = (dictionary["speaking_share"] as? NSNumber)?.doubleValue ?? 0
        ttsProvider = dictionary["tts_provider"] as? String ?? "inherit"
        macosVoice = dictionary["macos_voice"] as? String ?? ""
        kokoroVoice = dictionary["kokoro_voice"] as? String ?? "auto"
        referenceAudio = dictionary["reference_audio"] as? String ?? ""
        suggestedReferenceAudio = dictionary["suggested_reference_audio"] as? String ?? ""
        referenceTiming = dictionary["reference_timing"] as? [[Double]] ?? []
        autoReferenceEnabled = dictionary["auto_reference_enabled"] as? Bool ?? true
        expressiveness = (dictionary["expressiveness"] as? NSNumber)?.doubleValue ?? 0.5
        elevenLabsVoiceID = dictionary["elevenlabs_voice_id"] as? String ?? ""
        ttsRate = (dictionary["tts_rate"] as? NSNumber)?.intValue ?? 205
        pitchSemitones = (dictionary["pitch_semitones"] as? NSNumber)?.doubleValue ?? 0
        voiceGain = (dictionary["voice_gain"] as? NSNumber)?.doubleValue ?? 1
        notes = dictionary["notes"] as? String ?? ""
        manual = dictionary["manual"] as? Bool ?? false
    }
}

struct CharacterDraft: Equatable {
    var displayName = ""
    var role = "minor"
    var voiceClass = "neutral"
    var ageGroup = "adult"
    var ttsProvider = "inherit"
    var macosVoice = ""
    var kokoroVoice = "auto"
    var referenceAudio = ""
    var suggestedReferenceAudio = ""
    var autoReferenceEnabled = true
    var expressiveness = 0.5
    var elevenLabsVoiceID = ""
    var ttsRate = 205
    var pitchSemitones = 0.0
    var voiceGain = 1.0
    var notes = ""

    init() {}

    init(character: CharacterItem) {
        displayName = character.displayName
        role = character.role
        voiceClass = character.voiceClass
        ageGroup = character.ageGroup
        ttsProvider = character.ttsProvider
        macosVoice = character.macosVoice
        kokoroVoice = character.kokoroVoice
        referenceAudio = character.referenceAudio
        suggestedReferenceAudio = character.suggestedReferenceAudio
        autoReferenceEnabled = character.autoReferenceEnabled
        expressiveness = character.expressiveness
        elevenLabsVoiceID = character.elevenLabsVoiceID
        ttsRate = character.ttsRate
        pitchSemitones = character.pitchSemitones
        voiceGain = character.voiceGain
        notes = character.notes
    }

    var updates: [String: Any] {
        [
            "display_name": displayName,
            "role": role,
            "voice_class": voiceClass,
            "age_group": ageGroup,
            "tts_provider": ttsProvider,
            "macos_voice": macosVoice,
            "kokoro_voice": kokoroVoice,
            "reference_audio": referenceAudio,
            "auto_reference_enabled": autoReferenceEnabled,
            "expressiveness": expressiveness,
            "elevenlabs_voice_id": elevenLabsVoiceID,
            "tts_rate": ttsRate,
            "pitch_semitones": pitchSemitones,
            "voice_gain": voiceGain,
            "notes": notes,
        ]
    }
}
