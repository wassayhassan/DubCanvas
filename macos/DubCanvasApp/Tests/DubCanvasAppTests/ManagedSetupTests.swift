import XCTest
@testable import DubCanvasApp

final class ManagedSetupTests: XCTestCase {
    func testRuntimeFingerprintChangesWithRequirementsAndConstraints() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        XCTAssertNil(ManagedSetup.fingerprint(at: root))
        try Data("numpy==1.26.4\n".utf8).write(to: root.appendingPathComponent("requirements.txt"))
        try Data("torch<2.6\n".utf8).write(to: root.appendingPathComponent("constraints.txt"))
        try Data("-r requirements.txt\nkokoro==0.9.4\n".utf8).write(to: root.appendingPathComponent("requirements-managed-core.txt"))
        let initial = ManagedSetup.fingerprint(at: root)
        XCTAssertNotNil(initial)
        try Data("torch<2.7\n".utf8).write(to: root.appendingPathComponent("constraints.txt"))
        XCTAssertNotEqual(initial, ManagedSetup.fingerprint(at: root))
    }
    func testUnbundledToolsPreserveExistingVoiceConfiguration() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let original = ["PATH": "/custom/bin", "PHONEMIZER_ESPEAK_LIBRARY": "/custom/espeak.dylib"]
        XCTAssertEqual(BackendProcess.bundledToolsEnvironment(resources: root, environment: original), original)
        let tools = root.appendingPathComponent("tools")
        try FileManager.default.createDirectory(at: tools.appendingPathComponent("lib"), withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        // Partial tools never replace a working phonemizer configuration.
        let partial = BackendProcess.bundledToolsEnvironment(resources: root, environment: original)
        XCTAssertEqual(partial["PHONEMIZER_ESPEAK_LIBRARY"], original["PHONEMIZER_ESPEAK_LIBRARY"])
        try Data().write(to: tools.appendingPathComponent("lib/libespeak-ng.1.dylib"))
        try FileManager.default.createDirectory(at: tools.appendingPathComponent("share/espeak-ng-data"), withIntermediateDirectories: true)
        let bundled = BackendProcess.bundledToolsEnvironment(resources: root, environment: original)
        XCTAssertEqual(bundled["PHONEMIZER_ESPEAK_LIBRARY"], tools.appendingPathComponent("lib/libespeak-ng.1.dylib").path)
    }

}
