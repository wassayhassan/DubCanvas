import XCTest
@testable import DubCanvasApp

final class VoiceDefaultsTests: XCTestCase {
    func testNewPreferencesUseKokoroWithoutCloning() {
        let defaults = AppPreferences()
        XCTAssertEqual(defaults.voiceProvider, VoiceProvider.kokoro.rawValue)
        XCTAssertEqual(defaults.autoSourceVoices, false)
    }
    func testMigrationChangesOldAutomaticDefaultsAndPreservesExplicitCloning() {
        var old = AppPreferences()
        old.voiceDefaultsRevision = nil
        old.voiceProvider = VoiceProvider.auto.rawValue
        old.autoSourceVoices = true
        let migrated = old.migratingVoiceDefaults()
        XCTAssertEqual(migrated.voiceProvider, VoiceProvider.kokoro.rawValue)
        XCTAssertEqual(migrated.autoSourceVoices, false)
        old.voiceProvider = VoiceProvider.chatterbox.rawValue
        let explicit = old.migratingVoiceDefaults()
        XCTAssertEqual(explicit.voiceProvider, VoiceProvider.chatterbox.rawValue)
        XCTAssertEqual(explicit.autoSourceVoices, true)
    }
}
