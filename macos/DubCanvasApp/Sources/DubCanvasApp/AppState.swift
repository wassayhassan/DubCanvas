import AppKit
import Foundation
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class AppState: ObservableObject {
    private final class WeakState { weak var value: AppState?; init(_ value: AppState) { self.value = value } }
    private static var instances: [WeakState] = []
    static var hasRunningJobs: Bool {
        instances.contains { $0.value.map { $0.activeJobID != nil || $0.startPending || $0.jobStartPending } ?? false }
    }
    static func shutdownAllForUpdate() { for instance in instances { instance.value?.backend.stop() } }
    @Published var selection: SidebarDestination? = .projects

    @Published var source = ""
    @Published var projectName = ""
    @Published var sourceInspection: SourceInspection?
    @Published var sourceValidationIssue: String?
    @Published var sourceValidationPending = false
    private enum PendingSourceAction { case firstDub, projectOnly }
    private var pendingSourceAction: PendingSourceAction?
    private var inspectionRequests: [String: String] = [:]
    @Published var dubName = ""
    @Published var versionVoiceOverrides: [String: [String: String]] = [:]
    @Published var targetLanguage = "en"
    @Published var outputFolder = "~/Movies/DubCanvas"
    @Published var seriesID = ""
    @Published var outputMode: OutputMode = .dub

    @Published var asrProvider: ASRProvider = .auto
    @Published var mlxWhisperModel = "mlx-community/whisper-large-v3-mlx"
    @Published var fasterWhisperModel = "large-v3"
    @Published var fasterWhisperDevice = "auto"
    @Published var fasterWhisperComputeType = "auto"

    @Published var translationProvider: TranslationProvider = .llm
    @Published var llmModel = "mlx-community/Qwen3-8B-4bit"
    @Published var reviewModel = "mlx-community/Qwen3-8B-4bit"
    @Published var ollamaURL = "http://127.0.0.1:11434"
    @Published var ollamaModel = "qwen3:4b"

    @Published var voiceProvider: VoiceProvider = .auto
    @Published var fallbackVoice = ""
    @Published var ttsRate = 210
    @Published var chatterboxReferenceAudio = ""
    @Published var autoSourceVoices = true
    @Published var chatterboxExpressiveness = 0.5
    @Published var chatterboxDevice = "auto"
    @Published var chatterboxTurbo = true
    @Published var kokoroVoice = "auto"
    @Published var piperModel = ""
    @Published var piperSpeaker = -1
    @Published var elevenLabsVoiceID = "JBFqnCBsd6RMkjVDRZzb"
    @Published var elevenLabsAPIKey = ""
    @Published var credentialStatus = ""

    @Published var detectCharacters = true
    @Published var resumeCachedWork = true
    @Published var reviewBeforeDub = true
    @Published var speakerBackend = "auto"
    @Published var maxSpeakers = 12
    @Published var speakerThreshold = 0.0
    @Published var seriesContext = ""
    @Published var backgroundVolume = 1.0
    @Published var dubVolume = 1.15
    @Published var backgroundDucking = false

    @Published var backendState: BackendConnectionState = .connecting
    @Published var backendDiagnostics = ""
    @Published var activity: [ActivityEntry] = [] {
        didSet {
            if let entry = activity.last, entry.jobID.isEmpty, entry.id != oldValue.last?.id {
                diagnosticStore.append(entry.formatted + "\n")
            }
        }
    }
    let diagnosticStore = DiagnosticStore()
    var diagnosticReport: String {
        let paths = [currentProject?.artifacts["diagnostic_log"]].compactMap { $0 } +
            (currentProject?.dubs.compactMap { $0.artifacts["diagnostic_log"] } ?? [])
        return redactDiagnostic(diagnosticStore.fullReport(extraPaths: paths) +
            "\n\n=== UPDATE DIAGNOSTICS ===\n" + AppUpdater.shared.diagnostics.fullReport(extraPaths: []))
    }
    func exportDiagnostics() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.plainText]
        panel.nameFieldStringValue = "DubCanvas-diagnostics.log"
        if panel.runModal() == .OK, let url = panel.url {
            do { try diagnosticReport.write(to: url, atomically: true, encoding: .utf8) }
            catch { activity.append(ActivityEntry(kind: .error, message: "Could not save diagnostics: \(error.localizedDescription)")) }
        }
    }
    private func redactDiagnostic(_ text: String) -> String {
        var clean = text.replacingOccurrences(of: "\u{001B}\\[[0-?]*[ -/]*[@-~]", with: "", options: .regularExpression)
        if !elevenLabsAPIKey.isEmpty { clean = clean.replacingOccurrences(of: elevenLabsAPIKey, with: "<redacted>") }
        clean = clean.replacingOccurrences(of: "(?i)([?&](?:token|key|api_key|auth|signature|sig)=)[^&\\s]+", with: "$1<redacted>", options: .regularExpression)
        return clean.replacingOccurrences(of: "(?i)(Bearer\\s+)[A-Za-z0-9._-]+", with: "$1<redacted>", options: .regularExpression)
    }
    @Published var activityExpanded = false
    @Published var statusText = "Connecting to backend…"
    @Published var progressFraction: Double?
    @Published var currentStage = "preparing"
    @Published var stageDetail = ""
    @Published var downloadDetail = ""
    @Published var jobIssue = ""
    @Published var jobIssueDetail = ""
    @Published var startPending = false
    @Published var jobStartPending = false
    @Published var activeJobID: String?
    private var pendingQuickStart = false
    private var pendingQuickProjectID: String?
    private var openResultForJobID: String?
    private var openSubtitlesOnFinish = false
    private var subtitlesJobFinished = false
    private var awaitingDubJob = false
    private var finishedBeforeStartResponse: Set<String> = []
    private var analyzingCurrentJob = false
    private var pendingReviewJobID: String?
    @Published var systemCheckItems: [SystemCheckItem] = []
    @Published var showingSystemCheck = false
    @Published var settingsShowProviders = false
    @Published var availableProviders: [String: [String: Bool]] = [:]

    @Published var projects: [ProjectSummary] = []
    @Published var selectedProjectID: String?
    @Published var showingDeleteConfirmation = false
    @Published var deletingProjectID: String?
    @Published var lastDeletedProjectID: String?
    @Published var projectDeletionError: String?

    var currentProject: ProjectSummary? {
        projects.first { $0.id == selectedProjectID }
    }

    @Published var characterMaps: [CharacterMapSummary] = []
    @Published var selectedCharacterMapPath: String?
    @Published var characters: [CharacterItem] = []
    @Published var selectedCharacterID: String?
    @Published var characterDraft = CharacterDraft()
    @Published var installedVoices: [String] = []
    @Published var characterSaveMessage = ""

    private let backend = BackendProcess()

    init() {
        Self.instances.removeAll { $0.value == nil }
        Self.instances.append(WeakState(self))
        loadPreferences()
        elevenLabsAPIKey = KeychainStore.string(for: "elevenlabs-api-key") ?? ""
        connectBackend()
    }

    deinit {
        backend.stop()
    }

    var canStartJob: Bool {
        guard case .ready = backendState else { return false }
        return currentProject != nil && activeJobID == nil && !jobStartPending
    }

    var settingsSnapshot: SettingsSnapshot {
        SettingsSnapshot(
            outputFolder: outputFolder,
            seriesID: seriesID,
            outputMode: outputMode,
            asrProvider: asrProvider,
            mlxWhisperModel: mlxWhisperModel,
            fasterWhisperModel: fasterWhisperModel,
            fasterWhisperDevice: fasterWhisperDevice,
            fasterWhisperComputeType: fasterWhisperComputeType,
            translationProvider: translationProvider,
            llmModel: llmModel,
            reviewModel: reviewModel,
            ollamaURL: ollamaURL,
            ollamaModel: ollamaModel,
            voiceProvider: voiceProvider,
            fallbackVoice: fallbackVoice,
            ttsRate: ttsRate,
            chatterboxReferenceAudio: chatterboxReferenceAudio,
            autoSourceVoices: autoSourceVoices,
            chatterboxExpressiveness: chatterboxExpressiveness,
            chatterboxDevice: chatterboxDevice,
            chatterboxTurbo: chatterboxTurbo,
            kokoroVoice: kokoroVoice,
            piperModel: piperModel,
            piperSpeaker: piperSpeaker,
            elevenLabsVoiceID: elevenLabsVoiceID,
            detectCharacters: detectCharacters,
            resumeCachedWork: resumeCachedWork,
            reviewBeforeDub: reviewBeforeDub,
            speakerBackend: speakerBackend,
            maxSpeakers: maxSpeakers,
            speakerThreshold: speakerThreshold,
            seriesContext: seriesContext,
            backgroundVolume: backgroundVolume,
            dubVolume: dubVolume,
            backgroundDucking: backgroundDucking
        )
    }

    func savePreferences() {
        AppPreferences(
            outputFolder: outputFolder,
            seriesID: seriesID,
            outputMode: outputMode.rawValue,
            asrProvider: asrProvider.rawValue,
            mlxWhisperModel: mlxWhisperModel,
            fasterWhisperModel: fasterWhisperModel,
            fasterWhisperDevice: fasterWhisperDevice,
            fasterWhisperComputeType: fasterWhisperComputeType,
            translationProvider: translationProvider.rawValue,
            llmModel: llmModel,
            reviewModel: reviewModel,
            ollamaURL: ollamaURL,
            ollamaModel: ollamaModel,
            voiceProvider: voiceProvider.rawValue,
            fallbackVoice: fallbackVoice,
            ttsRate: ttsRate,
            chatterboxReferenceAudio: chatterboxReferenceAudio,
            autoSourceVoices: autoSourceVoices,
            chatterboxExpressiveness: chatterboxExpressiveness,
            chatterboxDevice: chatterboxDevice,
            chatterboxTurbo: chatterboxTurbo,
            kokoroVoice: kokoroVoice,
            piperModel: piperModel,
            piperSpeaker: piperSpeaker,
            elevenLabsVoiceID: elevenLabsVoiceID,
            detectCharacters: detectCharacters,
            resumeCachedWork: resumeCachedWork,
            reviewBeforeDub: reviewBeforeDub,
            speakerBackend: speakerBackend,
            maxSpeakers: maxSpeakers,
            speakerThreshold: speakerThreshold,
            seriesContext: seriesContext,
            backgroundVolume: backgroundVolume,
            dubVolume: dubVolume,
            backgroundDucking: backgroundDucking
        ).save()
    }

    func saveSecrets() {
        do {
            let trimmed = elevenLabsAPIKey.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty {
                try KeychainStore.delete("elevenlabs-api-key")
                credentialStatus = "API key removed from Keychain."
            } else {
                try KeychainStore.set(trimmed, for: "elevenlabs-api-key")
                credentialStatus = "API key saved securely in Keychain."
            }
        } catch {
            credentialStatus = error.localizedDescription
        }
    }

    private func loadPreferences() {
        let preferences = AppPreferences.load()

        outputFolder = preferences.outputFolder
        seriesID = preferences.seriesID
        outputMode = OutputMode(rawValue: preferences.outputMode) ?? .dub
        asrProvider = ASRProvider(rawValue: preferences.asrProvider) ?? .auto
        mlxWhisperModel = preferences.mlxWhisperModel ?? mlxWhisperModel
        fasterWhisperModel = preferences.fasterWhisperModel
        fasterWhisperDevice = preferences.fasterWhisperDevice
        fasterWhisperComputeType = preferences.fasterWhisperComputeType
        translationProvider = TranslationProvider(rawValue: preferences.translationProvider) ?? .llm
        llmModel = preferences.llmModel ?? llmModel
        reviewModel = preferences.reviewModel ?? reviewModel
        ollamaURL = preferences.ollamaURL
        ollamaModel = preferences.ollamaModel
        voiceProvider = VoiceProvider(rawValue: preferences.voiceProvider) ?? .auto
        fallbackVoice = preferences.fallbackVoice
        ttsRate = preferences.ttsRate
        chatterboxReferenceAudio = preferences.chatterboxReferenceAudio
        autoSourceVoices = preferences.autoSourceVoices ?? true
        chatterboxExpressiveness = preferences.chatterboxExpressiveness
        chatterboxDevice = preferences.chatterboxDevice
        chatterboxTurbo = preferences.chatterboxTurbo
        kokoroVoice = preferences.kokoroVoice
        piperModel = preferences.piperModel
        piperSpeaker = preferences.piperSpeaker
        elevenLabsVoiceID = preferences.elevenLabsVoiceID
        detectCharacters = preferences.detectCharacters
        resumeCachedWork = preferences.resumeCachedWork
        reviewBeforeDub = preferences.reviewBeforeDub ?? true
        speakerBackend = preferences.speakerBackend
        maxSpeakers = preferences.maxSpeakers
        speakerThreshold = preferences.speakerThreshold
        let oldExample = "Chinese xianxia/xuanhuan cultivation animation. Keep names, sects, realms, system terms, and cultivation terminology consistent."
        seriesContext = preferences.seriesContext == oldExample ? "" : preferences.seriesContext
        backgroundVolume = preferences.backgroundVolume
        dubVolume = preferences.dubVolume
        backgroundDucking = preferences.backgroundDucking
    }

    func connectBackend() {
        backendState = .connecting
        statusText = "Connecting to backend…"

        do {
            try backend.start(
                onMessage: { [weak self] payload in
                    Task { @MainActor in
                        self?.handleBackendMessage(payload)
                    }
                },
                onDiagnostic: { [weak self] text in
                    Task { @MainActor in
                        guard let self else { return }
                        let clean = self.redactDiagnostic(text)
                        self.backendDiagnostics += clean
                        if self.backendDiagnostics.count > 200_000 { self.backendDiagnostics = String(self.backendDiagnostics.suffix(200_000)) }
                        self.diagnosticStore.append("\(Date().ISO8601Format()) [backend output] \(clean)")
                    }
                },
                onTermination: { [weak self] code in
                    Task { @MainActor in
                        guard let self else { return }
                        if self.activeJobID != nil {
                            self.activity.append(ActivityEntry(kind: .error, message: "Backend exited with code \(code)."))
                        }
                        self.activeJobID = nil
                        self.jobStartPending = false
                        self.awaitingDubJob = false
                        self.finishedBeforeStartResponse.removeAll()
                        if self.startPending {
                            self.failQuickStart("The processing service stopped while preparing your dub. Reconnect and try again.")
                        } else if self.statusText != "Completed" {
                            self.jobIssue = "The processing service stopped. Reconnect and try again."
                        }
                        self.backendState = .disconnected
                        self.statusText = "Backend offline"
                    }
                }
            )

            _ = try backend.send(method: "hello", id: "hello")
            _ = try backend.send(method: "capabilities", id: "capabilities")
        } catch {
            backendState = .failed(error.localizedDescription)
            statusText = error.localizedDescription
            activity.append(ActivityEntry(kind: .error, message: error.localizedDescription))
        }
    }

    func runSystemCheck() {
        do {
            _ = try backend.send(method: "system_check", id: "system-check")
        } catch {
            systemCheckItems = [SystemCheckItem(name: "Backend", ok: false, detail: error.localizedDescription)]
            showingSystemCheck = true
        }
    }

    var canQuickStart: Bool {
        if case .ready = backendState {
            return activeJobID == nil && !startPending && !jobStartPending
        }
        return false
    }

    func projectNameProblem(excluding projectID: String? = nil) -> String? {
        let name = projectName.trimmingCharacters(in: .whitespacesAndNewlines)
        if name.isEmpty { return "Enter a project name." }
        let normalized = name.split(whereSeparator: \.isWhitespace).joined(separator: " ").lowercased()
        if projects.contains(where: {
            $0.id != projectID &&
            $0.name.split(whereSeparator: \.isWhitespace).joined(separator: " ").lowercased() == normalized
        }) {
            return "A project with this name already exists. Choose a different name."
        }
        return nil
    }

    var suggestedProjectName: String? {
        let base = projectName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !base.isEmpty, projectNameProblem() != nil else { return nil }
        let taken = Set(projects.map { $0.name.split(whereSeparator: \.isWhitespace).joined(separator: " ").lowercased() })
        var number = 2
        while taken.contains("\(base) \(number)".lowercased()) { number += 1 }
        return "\(base) \(number)"
    }

    func resetSourceInspection() {
        sourceInspection = nil
        sourceValidationIssue = nil
        sourceValidationPending = false
        pendingSourceAction = nil
    }

    func inspectSource() {
        let input = source.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty else { return }
        if sourceValidationPending && inspectionRequests.values.contains(input) { return }
        sourceValidationPending = true
        sourceValidationIssue = nil
        do {
            let id = "inspect-source-\(UUID().uuidString)"
            inspectionRequests[id] = input
            _ = try backend.send(method: "inspect_source", params: ["source": input], id: id)
        } catch {
            sourceValidationPending = false
            sourceValidationIssue = error.localizedDescription
        }
    }

    private func sourceIsInspected(for action: PendingSourceAction) -> Bool {
        if let inspection = sourceInspection,
           inspection.source == source.trimmingCharacters(in: .whitespacesAndNewlines) {
            if inspection.kind != "file" { return true }
            let path = (inspection.source as NSString).expandingTildeInPath
            if let attributes = try? FileManager.default.attributesOfItem(atPath: path),
               let size = attributes[.size] as? NSNumber,
               let modified = attributes[.modificationDate] as? Date,
               size.int64Value == inspection.sizeBytes,
               let checkedAt = inspection.modifiedAt,
               abs(modified.timeIntervalSince1970 - checkedAt) < 0.001 { return true }
            sourceInspection = nil
        }
        pendingSourceAction = action
        inspectSource()
        return false
    }

    var quickStartProblem: String? {
        if let issue = projectNameProblem() { return issue }
        let input = source.trimmingCharacters(in: .whitespacesAndNewlines)
        if input.isEmpty { return "Choose a video file or paste a video page link." }
        if let url = URL(string: input), ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
            if url.host?.contains("googlevideo.com") == true || url.path.contains("/videoplayback") {
                return "This temporary playback link expires. Paste the video's normal page link instead."
            }
        } else {
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: (input as NSString).expandingTildeInPath, isDirectory: &isDirectory),
                  !isDirectory.boolValue else {
                return "The video file could not be found. Choose it again or paste a video page link."
            }
        }
        if voiceProvider == .elevenlabs && elevenLabsAPIKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "The selected ElevenLabs voice needs an API key. Add one in Settings before dubbing."
        }
        if voiceProvider == .piper && piperModel.isEmpty {
            return "The selected Piper voice needs a model file. Choose one in Advanced settings."
        }
        if reviewBeforeDub && translationProvider == .whisper {
            return "Automatic subtitle correction needs a local translation model. Change the translation provider in Advanced settings."
        }
        if targetLanguage != "en" {
            if ![VoiceProvider.auto, .chatterbox, .elevenlabs].contains(voiceProvider) {
                return "Choose Automatic, Chatterbox, or ElevenLabs voices for this language in Settings."
            }
            if translationProvider == .whisper {
                return "Choose a local translation model in Advanced settings for this language."
            }
        }
        if outputFolder.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "Choose an output folder in Settings." }
        return nil
    }

    func quickStart() {
        guard canQuickStart else { return }
        if let problem = quickStartProblem {
            jobIssue = problem
            selection = .newProject
            return
        }
        guard sourceIsInspected(for: .firstDub) else { return }
        jobIssue = ""
        jobIssueDetail = ""
        startPending = true
        pendingQuickStart = true
        selection = .newProject
        statusText = "Checking your setup…"
        do {
            _ = try backend.send(method: "system_check", id: "quick-system-check-\(UUID().uuidString)")
        } catch {
            failQuickStart(error.localizedDescription)
        }
    }

    private func failQuickStart(_ message: String) {
        pendingQuickStart = false
        pendingQuickProjectID = nil
        startPending = false
        jobIssue = message
        jobIssueDetail = message
        statusText = "Could not start"
        selection = .newProject
    }

    func startJob(analysis: Bool) {
        guard canStartJob, let project = currentProject else { return }

        jobStartPending = true
        awaitingDubJob = !analysis && outputMode == .dub
        openResultForJobID = nil
        savePreferences()
        let threshold: Any = speakerThreshold == 0 ? NSNull() : speakerThreshold

        let params: [String: Any] = [
            "source": project.source,
            "verify_setup": true,
            "output_dir": project.outputDir,
            "series_id": project.seriesID,
            "mode": outputMode.rawValue,
            "target_language": targetLanguage,
            "source_language": "auto",
            "dub_name": dubName,
            "asr": [
                "provider": asrProvider.rawValue,
                "mlx_model": mlxWhisperModel,
                "model": fasterWhisperModel,
                "device": fasterWhisperDevice,
                "compute_type": fasterWhisperComputeType,
            ],
            "translation": [
                "provider": translationProvider.rawValue,
                "llm_model": llmModel,
                "ollama_url": ollamaURL,
                "model": ollamaModel,
            ],
            "tts": [
                "provider": voiceProvider.rawValue,
                "fallback_voice": fallbackVoice,
                "rate": ttsRate,
                "chatterbox_reference_audio": chatterboxReferenceAudio,
                "auto_voice_references": autoSourceVoices,
                "chatterbox_expressiveness": chatterboxExpressiveness,
                "chatterbox_device": chatterboxDevice,
                "chatterbox_turbo": chatterboxTurbo,
                "prefer_american_accent": true,
                "kokoro_voice": kokoroVoice,
                "piper_model": piperModel,
                "piper_speaker": piperSpeaker,
                "api_key": elevenLabsAPIKey,
                "voice_id": elevenLabsVoiceID,
            ],
            "speaker_analysis": [
                "enabled": detectCharacters,
                "backend": speakerBackend,
                "max_speakers": maxSpeakers,
                "threshold": threshold,
            ],
            "audio": [
                "background_volume": backgroundVolume,
                "dub_volume": dubVolume,
                "ducking": backgroundDucking,
            ],
            "context": seriesContext,
            "resume": resumeCachedWork,
            "review_before_dub": reviewBeforeDub,
            "review_model": reviewModel,
            "voice_overrides": versionVoiceOverrides,
        ]

        do {
            statusText = analysis ? "Starting character analysis…" : "Starting dub…"
            progressFraction = nil
            currentStage = "preparing"
            stageDetail = "Preparing video"
            downloadDetail = ""
            jobIssue = ""
            if analysis { activityExpanded = true }
            let id = analysis ? "analyze-\(UUID().uuidString)" : "run-\(UUID().uuidString)"
            _ = try backend.send(
                method: analysis ? "analyze_characters" : "run_job",
                params: params,
                id: id
            )
            versionVoiceOverrides = [:]
            if !analysis { selection = .overview }
            openSubtitlesOnFinish = !analysis && outputMode == .subtitles
            subtitlesJobFinished = false
        } catch {
            jobStartPending = false
            awaitingDubJob = false
            activity.append(ActivityEntry(kind: .error, message: error.localizedDescription))
            statusText = "Could not start"
            jobIssue = error.localizedDescription
            if !analysis { selection = .overview }
        }
    }

    func cancelActiveJob() {
        guard let activeJobID else { return }
        do {
            _ = try backend.send(
                method: "pause_job",
                params: ["job_id": activeJobID],
                id: "cancel-\(UUID().uuidString)"
            )
            statusText = "Pausing…"
        } catch {
            activity.append(ActivityEntry(kind: .error, message: error.localizedDescription))
        }
    }

    func resumeDub(_ dub: DubSummary) {
        guard activeJobID == nil, !jobStartPending, let project = currentProject else { return }
        jobStartPending = true
        awaitingDubJob = true
        openResultForJobID = nil
        do {
            statusText = "Resuming \(dub.title)…"
            progressFraction = nil
            jobIssue = ""
            _ = try backend.send(method: "resume_dub", params: [
                "output_dir": project.outputDir,
                "project_id": project.id,
                "dub_id": dub.id,
                "elevenlabs_api_key": elevenLabsAPIKey,
            ], id: "resume-\(UUID().uuidString)")
            selection = .overview
        } catch {
            jobStartPending = false
            awaitingDubJob = false
            statusText = error.localizedDescription
            jobIssue = error.localizedDescription
            selection = .overview
        }
    }

    func approveReview(_ dub: DubSummary, revisions: [String: String]) {
        guard let project = currentProject, activeJobID == nil else { return }
        do {
            _ = try backend.send(method: "approve_review", params: [
                "output_dir": project.outputDir,
                "project_id": project.id,
                "dub_id": dub.id,
                "revisions": revisions,
            ], id: "approve-review-\(dub.id)-\(UUID().uuidString)")
            statusText = "Saving subtitle review…"
        } catch { statusText = error.localizedDescription }
    }

    func refreshProjects() {
        do {
            _ = try backend.send(
                method: "list_projects",
                params: ["output_dir": outputFolder],
                id: "projects-\(UUID().uuidString)"
            )
        } catch {
            activity.append(ActivityEntry(kind: .error, message: error.localizedDescription))
        }
    }

    func createProject() {
        if let issue = projectNameProblem() {
            jobIssue = issue
            return
        }
        guard !source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            statusText = "Choose a source video first."
            return
        }
        guard sourceIsInspected(for: .projectOnly) else { return }
        do {
            _ = try backend.send(method: "create_project", params: [
                "source": source.trimmingCharacters(in: .whitespacesAndNewlines),
                "output_dir": outputFolder,
                "name": projectName,
                "series_id": seriesID,
                "source_title": sourceInspection?.title ?? "",
            ], id: "create-project-\(UUID().uuidString)")
        } catch {
            statusText = error.localizedDescription
        }
    }

    func saveProjectSettings() {
        guard let project = currentProject else { return }
        if let issue = projectNameProblem(excluding: project.id) {
            jobIssue = issue
            return
        }
        jobIssue = ""
        do {
            _ = try backend.send(method: "update_project", params: [
                "project_id": project.id, "output_dir": project.outputDir,
                "name": projectName, "series_id": seriesID,
            ], id: "update-project-\(UUID().uuidString)")
        } catch { statusText = error.localizedDescription }
    }

    func openProject(_ project: ProjectSummary) {
        let keepDestination = selectedProjectID == project.id ? selection : nil
        if characterMaps.first(where: { $0.path == selectedCharacterMapPath })?.sourceKey != project.id {
            versionVoiceOverrides = [:]
            selectedCharacterMapPath = nil
            characters = []
            jobIssue = ""
        }
        selectedProjectID = project.id
        source = project.source
        outputFolder = project.outputDir
        seriesID = project.seriesID
        projectName = project.name
        selection = keepDestination ?? .overview
        refreshCharacterMaps()
    }

    func newProject() {
        selectedProjectID = nil
        source = ""
        projectName = ""
        dubName = ""
        versionVoiceOverrides = [:]
        seriesID = ""
        seriesContext = ""
        jobIssue = ""
        resetSourceInspection()
        outputMode = .dub
        selection = .newProject
    }

    func selectDub(_ dub: DubSummary) {
        targetLanguage = dub.language
        selection = .dub(dub.id)
    }

    func regenerate(_ dub: DubSummary) {
        targetLanguage = dub.language
        dubName = dub.name + " (new version)"
        let config = dub.config
        versionVoiceOverrides = config["voice_overrides"] as? [String: [String: String]] ?? [:]
        translationProvider = TranslationProvider(rawValue: config["translation"] as? String ?? "") ?? translationProvider
        voiceProvider = VoiceProvider(rawValue: config["tts_engine"] as? String ?? "") ?? voiceProvider
        asrProvider = ASRProvider(rawValue: config["asr_provider"] as? String ?? "") ?? asrProvider
        fasterWhisperModel = config["faster_whisper_model"] as? String ?? fasterWhisperModel
        mlxWhisperModel = config["mlx_whisper_model"] as? String ?? mlxWhisperModel
        llmModel = config["llm_model"] as? String ?? llmModel
        reviewModel = config["review_model"] as? String ?? reviewModel
        ollamaModel = config["ollama_model"] as? String ?? ollamaModel
        ollamaURL = config["ollama_url"] as? String ?? ollamaURL
        fallbackVoice = config["voice"] as? String ?? fallbackVoice
        chatterboxReferenceAudio = config["chatterbox_reference_audio"] as? String ?? chatterboxReferenceAudio
        autoSourceVoices = config["auto_voice_references"] as? Bool ?? autoSourceVoices
        chatterboxExpressiveness = (config["chatterbox_expressiveness"] as? NSNumber)?.doubleValue ?? chatterboxExpressiveness
        chatterboxTurbo = config["chatterbox_turbo"] as? Bool ?? chatterboxTurbo
        kokoroVoice = config["kokoro_voice"] as? String ?? kokoroVoice
        piperModel = config["piper_model"] as? String ?? piperModel
        elevenLabsVoiceID = config["elevenlabs_voice_id"] as? String ?? elevenLabsVoiceID
        detectCharacters = config["multi_character"] as? Bool ?? detectCharacters
        speakerBackend = config["speaker_backend"] as? String ?? speakerBackend
        backgroundVolume = (config["background_volume"] as? NSNumber)?.doubleValue ?? backgroundVolume
        dubVolume = (config["dub_volume"] as? NSNumber)?.doubleValue ?? dubVolume
        backgroundDucking = config["ducking"] as? Bool ?? backgroundDucking
        reviewBeforeDub = config["review_before_dub"] as? Bool ?? true
        selection = .newDub
    }

    func deleteDub(_ dub: DubSummary) {
        guard let project = currentProject else { return }
        do {
            _ = try backend.send(method: "delete_dub", params: [
                "output_dir": project.outputDir, "project_id": project.id, "dub_id": dub.id,
            ], id: "delete-dub-\(UUID().uuidString)")
        } catch {
            statusText = error.localizedDescription
        }
    }

    func deleteProject(_ project: ProjectSummary) {
        guard deletingProjectID == nil else { return }
        deletingProjectID = project.id
        projectDeletionError = nil
        do {
            _ = try backend.send(method: "delete_project", params: [
                "output_dir": project.outputDir, "project_id": project.id,
            ], id: "delete-project-\(UUID().uuidString)")
        } catch {
            deletingProjectID = nil
            projectDeletionError = error.localizedDescription
        }
    }

    func useProject(_ project: ProjectSummary) {
        openProject(project)
        selection = .newDub
    }

    func openProjectOutput(_ project: ProjectSummary) {
        let preferred = project.artifacts["dubbed_video"]
            ?? project.artifacts["english_srt"]
            ?? project.artifacts.values.first
        guard let preferred, !preferred.isEmpty else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: preferred)])
    }

    func refreshCharacterMaps() {
        do {
            _ = try backend.send(
                method: "list_character_maps",
                params: ["output_dir": outputFolder],
                id: "character-maps-\(UUID().uuidString)"
            )
        } catch {
            activity.append(ActivityEntry(kind: .error, message: error.localizedDescription))
        }
    }

    func loadCharacterMap(_ path: String) {
        guard !path.isEmpty else { return }
        selectedCharacterMapPath = path
        selectedCharacterID = nil
        characters = []
        characterDraft = CharacterDraft()
        do {
            _ = try backend.send(
                method: "get_characters",
                params: ["path": path],
                id: "characters-\(UUID().uuidString)"
            )
        } catch {
            activity.append(ActivityEntry(kind: .error, message: error.localizedDescription))
        }
    }

    func selectCharacter(_ id: String?) {
        selectedCharacterID = id
        guard let id, let character = characters.first(where: { $0.id == id }) else {
            characterDraft = CharacterDraft()
            return
        }
        characterDraft = CharacterDraft(character: character)
        characterSaveMessage = ""
    }

    func saveSelectedCharacter() {
        guard let path = selectedCharacterMapPath,
              let characterID = selectedCharacterID else { return }
        do {
            characterSaveMessage = "Saving…"
            _ = try backend.send(
                method: "update_character",
                params: [
                    "path": path,
                    "character_id": characterID,
                    "updates": characterDraft.updates,
                ],
                id: "save-character-\(UUID().uuidString)"
            )
        } catch {
            characterSaveMessage = error.localizedDescription
        }
    }

    func previewSelectedVoice() {
        guard selectedCharacterID != nil else { return }
        let text = characterDraft.displayName.isEmpty
            ? "This is the selected character speaking in English."
            : "This is \(characterDraft.displayName) speaking in English."

        let inheritedProvider = characterDraft.ttsProvider == "inherit"
            ? voiceProvider.rawValue
            : characterDraft.ttsProvider
        let reference = !characterDraft.referenceAudio.isEmpty
            ? characterDraft.referenceAudio
            : (characterDraft.autoReferenceEnabled && autoSourceVoices
               ? characterDraft.suggestedReferenceAudio : "")
        let selectedKokoro = characterDraft.kokoroVoice.isEmpty
            ? kokoroVoice
            : characterDraft.kokoroVoice

        do {
            _ = try backend.send(
                method: "preview_voice",
                params: [
                    "provider": inheritedProvider,
                    "character_id": selectedCharacterID ?? "",
                    "voice": characterDraft.macosVoice,
                    "text": text,
                    "rate": characterDraft.ttsRate,
                    "reference_audio": reference,
                    "expressiveness": characterDraft.expressiveness,
                    "device": chatterboxDevice,
                    "turbo": chatterboxTurbo,
                    "prefer_american_accent": true,
                    "kokoro_voice": selectedKokoro,
                    "voice_class": characterDraft.voiceClass,
                    "age_group": characterDraft.ageGroup,
                    "piper_model": piperModel,
                    "piper_speaker": piperSpeaker,
                    "api_key": elevenLabsAPIKey,
                    "voice_id": characterDraft.elevenLabsVoiceID.isEmpty ? elevenLabsVoiceID : characterDraft.elevenLabsVoiceID,
                ],
                id: "preview-\(UUID().uuidString)"
            )
        } catch {
            activity.append(ActivityEntry(kind: .error, message: error.localizedDescription))
        }
    }

    func chooseSourceFile() {
        let panel = NSOpenPanel()
        panel.title = "Choose Source Video"
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.movie, .video]
        if panel.runModal() == .OK, let url = panel.url {
            source = url.path
        }
    }

    func chooseOutputFolder() {
        let panel = NSOpenPanel()
        panel.title = "Choose Output Folder"
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url {
            outputFolder = url.path
            savePreferences()
        }
    }

    func choosePiperModel() {
        let panel = NSOpenPanel()
        panel.title = "Choose Piper Voice Model"
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        if let onnx = UTType(filenameExtension: "onnx") {
            panel.allowedContentTypes = [onnx]
        }
        if panel.runModal() == .OK, let url = panel.url {
            piperModel = url.path
            savePreferences()
        }
    }

    func chooseChatterboxReference() {
        let panel = NSOpenPanel()
        panel.title = "Choose Voice Reference Clip"
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.audio]
        if panel.runModal() == .OK, let url = panel.url {
            chatterboxReferenceAudio = url.path
            savePreferences()
        }
    }

    func chooseCharacterReference() {
        let panel = NSOpenPanel()
        panel.title = "Choose Character Voice Reference Clip"
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.audio]
        if panel.runModal() == .OK, let url = panel.url {
            characterDraft.referenceAudio = url.path
        }
    }

    private func handleBackendMessage(_ payload: [String: Any]) {
        let type = payload["type"] as? String ?? ""

        if type == "response" {
            handleResponse(payload)
        } else if type == "event" {
            handleEvent(payload)
        }
    }

    private func handleResponse(_ payload: [String: Any]) {
        let id = payload["id"] as? String ?? ""
        let ok = payload["ok"] as? Bool ?? false

        if !ok {
            let error = payload["error"] as? [String: Any]
            let message = error?["message"] as? String ?? "Backend request failed."
            if id.hasPrefix("inspect-source-") {
                let requested = inspectionRequests.removeValue(forKey: id)
                if requested == source.trimmingCharacters(in: .whitespacesAndNewlines) {
                    sourceValidationPending = false
                    sourceValidationIssue = message
                    pendingSourceAction = nil
                }
                return
            }
            activity.append(ActivityEntry(kind: .error, message: message))
            statusText = message
            if id.hasPrefix("delete-project-") {
                deletingProjectID = nil
                projectDeletionError = message
            }
            if id.hasPrefix("quick-") || (pendingQuickStart && id.hasPrefix("create-project-")) {
                failQuickStart(message)
            } else if id.hasPrefix("create-project-") || id.hasPrefix("update-project-") {
                jobIssue = message
            } else if id.hasPrefix("projects-") && pendingQuickStart {
                failQuickStart("Could not load the new project: \(message)")
            } else if id.hasPrefix("run-") || id.hasPrefix("resume-") || id.hasPrefix("analyze-") {
                jobStartPending = false
                awaitingDubJob = false
                jobIssue = message
                jobIssueDetail = message
                if !id.hasPrefix("analyze-") { selection = .overview }
            }
            if id.hasPrefix("save-character-") {
                characterSaveMessage = message
            }
            return
        }

        let resultAny = payload["result"]
        let result = resultAny as? [String: Any] ?? [:]

        switch id {
        case let id where id.hasPrefix("inspect-source-"):
            let requested = inspectionRequests.removeValue(forKey: id)
            guard requested == source.trimmingCharacters(in: .whitespacesAndNewlines),
                  let inspection = SourceInspection(dictionary: result) else { return }
            sourceValidationPending = false
            sourceValidationIssue = nil
            sourceInspection = inspection
            let action = pendingSourceAction
            pendingSourceAction = nil
            if action == .firstDub { quickStart() }
            if action == .projectOnly { createProject() }

        case "hello":
            let version = result["version"] as? String ?? "ready"
            backendState = .ready(version: version)
            statusText = "Ready"
            _ = try? backend.send(method: "list_voices", id: "voices")
            refreshProjects()
            refreshCharacterMaps()

        case "voices":
            installedVoices = resultAny as? [String] ?? []

        case "capabilities":
            let providers = result["providers"] as? [String: Any] ?? [:]
            availableProviders = providers.compactMapValues { $0 as? [String: Bool] }

        case "system-check":
            let checks = result["checks"] as? [[String: Any]] ?? []
            systemCheckItems = checks.map {
                SystemCheckItem(
                    name: $0["name"] as? String ?? "Unknown",
                    ok: $0["ok"] as? Bool ?? false,
                    detail: $0["detail"] as? String ?? "",
                    optional: $0["optional"] as? Bool ?? false
                )
            }
            showingSystemCheck = true

        case let id where id.hasPrefix("quick-system-check-"):
            let checks = result["checks"] as? [[String: Any]] ?? []
            let required = ["ffmpeg", "ffprobe", "demucs"] + (source.hasPrefix("http") ? ["yt-dlp"] : [])
            var missing = required.filter { name in
                !checks.contains { ($0["name"] as? String) == name && ($0["ok"] as? Bool) == true }
            }
            let capabilities = result["capabilities"] as? [String: Any] ?? [:]
            let providers = capabilities["providers"] as? [String: Any] ?? [:]
            let speech = providers["asr"] as? [String: Bool] ?? [:]
            let translations = providers["translation"] as? [String: Bool] ?? [:]
            let voices = providers["tts"] as? [String: Bool] ?? [:]
            if (asrProvider == .auto && !(speech["mlx_whisper"] == true || speech["faster_whisper"] == true)) ||
                (asrProvider == .mlxWhisper && speech["mlx_whisper"] != true) ||
                (asrProvider == .fasterWhisper && speech["faster_whisper"] != true) { missing.append("speech recognition") }
            if (translationProvider == .llm && translations["mlx_llm"] != true) ||
                (translationProvider == .ollama && translations["ollama"] != true) ||
                (translationProvider == .auto && !(translations["mlx_llm"] == true || translations["ollama"] == true)) {
                missing.append("translation model runtime")
            }
            if targetLanguage == "en" && voiceProvider != .auto && voiceProvider != .elevenlabs && voices[voiceProvider.rawValue] != true {
                missing.append("selected voice engine")
            }
            if targetLanguage != "en" && voiceProvider == .chatterbox && voices["chatterbox_multilingual"] != true {
                missing.append("Chatterbox Multilingual")
            }
            if targetLanguage != "en" && voiceProvider == .auto && voices["chatterbox_multilingual"] != true && elevenLabsAPIKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                missing.append("Chatterbox Multilingual or ElevenLabs API key")
            }
            if !missing.isEmpty {
                failQuickStart("Setup needed: \(missing.joined(separator: ", ")). Open System Check for details, then retry.")
            } else {
                do {
                    statusText = "Creating project…"
                    _ = try backend.send(method: "create_project", params: [
                        "source": source.trimmingCharacters(in: .whitespacesAndNewlines),
                        "output_dir": outputFolder,
                        "name": projectName,
                        "series_id": seriesID,
                        "source_title": sourceInspection?.title ?? "",
                    ], id: "create-project-quick-\(UUID().uuidString)")
                } catch { failQuickStart(error.localizedDescription) }
            }

        default:
            if id.hasPrefix("run-") || id.hasPrefix("analyze-") || id.hasPrefix("resume-") {
                if let jobID = result["job_id"] as? String {
                    let finishedEarly = finishedBeforeStartResponse.remove(jobID) != nil
                    let isDub = awaitingDubJob
                    jobStartPending = false
                    awaitingDubJob = false
                    if isDub { openResultForJobID = jobID }
                    if !finishedEarly {
                        activeJobID = jobID
                        statusText = "Queued"
                    }
                    activity.append(ActivityEntry(kind: .info, message: "Started \(jobID)."))
                    refreshProjects()
                } else {
                    jobStartPending = false
                    awaitingDubJob = false
                    jobIssue = "The processing service did not return a job ID. Reconnect and try again."
                    statusText = "Could not start"
                    selection = .overview
                }
            } else if id.hasPrefix("projects-") {
                let rows = resultAny as? [[String: Any]] ?? []
                projects = rows.compactMap(ProjectSummary.init(dictionary:))
                if let pendingQuickProjectID,
                   projects.contains(where: { $0.id == pendingQuickProjectID }) {
                    self.pendingQuickProjectID = nil
                    pendingQuickStart = false
                    startPending = false
                    outputMode = .dub
                    startJob(analysis: false)
                    selection = .overview
                }
                if let openResultForJobID,
                   let project = projects.first(where: { $0.dubs.contains(where: { $0.jobID == openResultForJobID && $0.status != "running" }) }),
                   let dub = project.dubs.first(where: { $0.jobID == openResultForJobID }) {
                    self.openResultForJobID = nil
                    if selection == .overview && selectedProjectID == project.id {
                        selectedProjectID = project.id
                        selection = .dub(dub.id)
                    }
                }
                if openSubtitlesOnFinish && subtitlesJobFinished {
                    openSubtitlesOnFinish = false
                    subtitlesJobFinished = false
                    if selection == .overview { selection = .subtitles }
                }
                if let pendingReviewJobID {
                    if let waiting = currentProject?.dubs.first(where: { $0.jobID == pendingReviewJobID && $0.status == "paused" && $0.error == "Subtitle review required before voice generation" }) {
                        selection = .dub(waiting.id)
                        statusText = "Review subtitles before dubbing"
                    }
                    self.pendingReviewJobID = nil
                }
                if let selectedProjectID, !projects.contains(where: { $0.id == selectedProjectID }) {
                    self.selectedProjectID = nil
                    selection = .projects
                }
            } else if id.hasPrefix("approve-review-") {
                let suffix = id.dropFirst("approve-review-".count)
                if let dub = currentProject?.dubs.first(where: { suffix.hasPrefix($0.id + "-") }) {
                    resumeDub(dub)
                }
            } else if id.hasPrefix("create-project-") {
                jobIssue = ""
                selectedProjectID = result["project_id"] as? String
                if id.hasPrefix("create-project-quick-") {
                    pendingQuickProjectID = selectedProjectID
                    if selectedProjectID == nil {
                        failQuickStart("The processing service did not return a project ID. Reconnect and try again.")
                        return
                    }
                    statusText = "Preparing dub…"
                    selection = .overview
                } else {
                    selection = .overview
                }
                refreshProjects()
            } else if id.hasPrefix("update-project-") {
                jobIssue = ""
                statusText = "Project saved"
                refreshProjects()
            } else if id.hasPrefix("delete-dub-") {
                selection = .dubs
                refreshProjects()
            } else if id.hasPrefix("delete-project-") {
                if let deletedID = result["project_id"] as? String {
                    if selectedProjectID == deletedID {
                        selectedProjectID = nil
                        selection = .projects
                    }
                    lastDeletedProjectID = deletedID
                    projects.removeAll { $0.id == deletedID }
                    refreshProjects()
                }
                deletingProjectID = nil
            } else if id.hasPrefix("character-maps-") {
                let rows = resultAny as? [[String: Any]] ?? []
                characterMaps = rows.compactMap(CharacterMapSummary.init(dictionary:))
                let scoped = characterMaps.filter { $0.sourceKey == selectedProjectID }
                if let selected = selectedCharacterMapPath,
                   scoped.contains(where: { $0.path == selected }) {
                    loadCharacterMap(selected)
                } else if let first = scoped.first {
                    loadCharacterMap(first.path)
                } else {
                    selectedCharacterMapPath = nil
                    characters = []
                    selectedCharacterID = nil
                }
            } else if id.hasPrefix("characters-") {
                let rows = result["characters"] as? [[String: Any]] ?? []
                characters = rows.compactMap(CharacterItem.init(dictionary:))
                    .sorted { $0.speakingShare > $1.speakingShare }
                if let first = characters.first {
                    selectCharacter(first.id)
                } else {
                    selectCharacter(nil)
                }
            } else if id.hasPrefix("save-character-") {
                if let saved = CharacterItem(dictionary: result),
                   let index = characters.firstIndex(where: { $0.id == saved.id }) {
                    characters[index] = saved
                    selectCharacter(saved.id)
                }
                characterSaveMessage = "Saved"
                activity.append(ActivityEntry(kind: .info, message: "Saved character voice override."))
                refreshProjects()
            }
        }
    }

    private func handleEvent(_ payload: [String: Any]) {
        let event = payload["event"] as? String ?? ""
        let data = payload["data"] as? [String: Any] ?? [:]
        let jobID = payload["job_id"] as? String ?? ""
        let logStage = data["stage"] as? String ?? currentStage
        if let path = data["log_path"] as? String { diagnosticStore.jobPaths[jobID] = path }
        func appendLog(_ kind: ActivityEntry.Kind, _ message: String, debug: Bool = false) {
            let entry = ActivityEntry(kind: kind, message: redactDiagnostic(message), stage: logStage, jobID: jobID, isDebug: debug)
            activity.append(entry)
            diagnosticStore.append(entry.formatted + "\n")
            if activity.count > 5000 { activity.removeFirst(activity.count - 5000) }
        }

        switch event {
        case "job_started":
            appendLog(.info, "Job started")
            analyzingCurrentJob = data["kind"] as? String == "analyze"
            if let jobID = payload["job_id"] as? String, jobStartPending {
                activeJobID = jobID
                if awaitingDubJob { openResultForJobID = jobID }
            }

        case "stage":
            let title = data["title"] as? String ?? "Working"
            let stage = data["stage"] as? String ?? "preparing"
            if stage != "preparing" || currentStage == "preparing" {
                currentStage = stage
                statusText = stage.replacingOccurrences(of: "_", with: " ").capitalized
                progressFraction = data["fraction"] as? Double
                if !title.lowercased().hasPrefix("warning") { stageDetail = title }
            }

        case "progress":
            statusText = "Downloading video"
            progressFraction = data["fraction"] as? Double
            currentStage = data["stage"] as? String ?? "preparing"
            stageDetail = data["title"] as? String ?? "Downloading video"
            downloadDetail = [data["total"] as? String, data["speed"] as? String, data["eta"] as? String].compactMap { $0 }.joined(separator: " · ")

        case "log":
            if let message = data["message"] as? String, !message.isEmpty {
                let details = data["details"] as? [String: Any] ?? [:]
                let detailText = (try? JSONSerialization.data(withJSONObject: details, options: [.prettyPrinted, .sortedKeys]))
                    .flatMap { String(data: $0, encoding: .utf8) } ?? ""
                appendLog(data["level"] as? String == "warning" ? .warning : .info,
                          message + (details.isEmpty ? "" : "\n" + detailText), debug: data["level"] as? String == "debug")
            }

        case "warning":
            appendLog(.warning, data["message"] as? String ?? "Warning")

        case "artifact":
            let kind = data["kind"] as? String ?? "output"
            let path = data["path"] as? String ?? ""
            if kind == "diagnostic_log" { diagnosticStore.jobPaths[jobID] = path }
            appendLog(.artifact, "\(kind): \(path)")
            refreshProjects()

        case "error":
            let raw = data["message"] as? String ?? "Backend error"
            jobIssueDetail = raw
            jobIssue = raw.contains("403") ? "The video site refused the download. Try the normal video page link, or choose a local file." : raw.contains("ffprobe") || raw.contains("ffmpeg") ? "Video tools are missing. Open System Check for setup details." : raw
            appendLog(.error, raw)
            if let traceback = data["traceback"] as? String { appendLog(.info, traceback, debug: true) }
            statusText = "Failed"

        case "finished":
            let status = data["status"] as? String ?? "completed"
            appendLog(.info, "Job \(status)")
            if jobStartPending, let jobID = payload["job_id"] as? String {
                finishedBeforeStartResponse.insert(jobID)
            }
            if status != "completed" { openSubtitlesOnFinish = false }
            subtitlesJobFinished = openSubtitlesOnFinish && status == "completed"
            pendingReviewJobID = status == "paused" ? payload["job_id"] as? String : nil
            let showVoices = analyzingCurrentJob && status == "completed"
            analyzingCurrentJob = false
            activeJobID = nil
            progressFraction = status == "completed" ? 1 : nil
            statusText = status == "completed" ? "Completed" : status.capitalized
            refreshProjects()
            refreshCharacterMaps()
            if showVoices {
                selection = .overview
                statusText = "Source analysis ready"
            }

        default:
            break
        }
    }
}
