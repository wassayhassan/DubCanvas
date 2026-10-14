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
        let initial = ManagedSetup.fingerprint(at: root)
        XCTAssertNotNil(initial)
        try Data("torch<2.7\n".utf8).write(to: root.appendingPathComponent("constraints.txt"))
        XCTAssertNotEqual(initial, ManagedSetup.fingerprint(at: root))
    }
}
