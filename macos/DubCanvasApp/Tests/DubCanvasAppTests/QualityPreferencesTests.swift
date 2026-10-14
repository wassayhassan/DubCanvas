import XCTest
@testable import DubCanvasApp

final class QualityPreferencesTests: XCTestCase {
    func testMissingQualityFieldsPreserveLegacyPreferences() throws {
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(AppPreferences())) as? [String: Any])
        object.removeValue(forKey: "pipelineProfile")
        object.removeValue(forKey: "qualitySettingsJSON")
        let loaded = try JSONDecoder().decode(AppPreferences.self, from: JSONSerialization.data(withJSONObject: object))
        XCTAssertNil(loaded.pipelineProfile)
        XCTAssertEqual(loaded.voiceProvider, "kokoro")
    }

    func testQualitySettingsRoundTripWithoutChangingVoiceDefaults() throws {
        var preferences = AppPreferences()
        preferences.pipelineProfile = "quality_v1"
        preferences.qualitySettingsJSON = "{\"allow_cloud\":false}"
        let loaded = try JSONDecoder().decode(AppPreferences.self, from: JSONEncoder().encode(preferences))
        XCTAssertEqual(loaded.pipelineProfile, "quality_v1")
        XCTAssertEqual(loaded.qualitySettingsJSON, preferences.qualitySettingsJSON)
        XCTAssertEqual(loaded.autoSourceVoices, false)
    }
}
