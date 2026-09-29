import CryptoKit
import Foundation
import XCTest
@testable import DubCanvasApp

final class AppUpdaterTests: XCTestCase {
    private let revision = String(repeating: "a", count: 40)
    func testManifestRejectsInvalidFields() throws {
        let valid = AppUpdateManifest(schema: 1, revision: revision, sourceTimestamp: 123, version: "4.0", sha256: String(repeating: "b", count: 64))
        XCTAssertNoThrow(try valid.validate())
        XCTAssertEqual(valid.downloadURL.host, "github.com")
        XCTAssertThrowsError(try AppUpdateManifest(schema: 1, revision: "../other", sourceTimestamp: 123, version: "4.0", sha256: valid.sha256).validate())
        XCTAssertThrowsError(try AppUpdateManifest(schema: 1, revision: revision, sourceTimestamp: 123, version: "4.0", sha256: "invalid").validate())
    }
    func testBadChecksumLeavesCurrentAppUntouched() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let current = root.appendingPathComponent("current.app")
        try Data("original app".utf8).write(to: current)
        let archive = root.appendingPathComponent("update.zip")
        try Data("corrupt download".utf8).write(to: archive)
        let manifest = AppUpdateManifest(schema: 1, revision: revision, sourceTimestamp: 123, version: "4.0", sha256: String(repeating: "0", count: 64))
        XCTAssertThrowsError(try AppUpdater.prepare(archive: archive, work: root, staged: root.appendingPathComponent("staged.app"), current: current, manifest: manifest, logURL: root.appendingPathComponent("update.log")))
        XCTAssertEqual(try Data(contentsOf: current), Data("original app".utf8))
    }
    func testVerifiedUpdateCopiesRuntimeWithoutReplacingCurrentApp() throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? fm.removeItem(at: root) }
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        let source = root.appendingPathComponent("source/DubCanvas.app")
        let current = root.appendingPathComponent("current.app")
        for app in [source, current] {
            let backend = app.appendingPathComponent("Contents/Resources/backend")
            try fm.createDirectory(at: backend.appendingPathComponent("dubcanvas"), withIntermediateDirectories: true)
            try Data("print('backend smoke test passed')".utf8).write(to: backend.appendingPathComponent("dubcanvas/__main__.py"))
            // Exercise the updater commands without installing into Xcode's Python.
            try fm.createDirectory(at: backend.appendingPathComponent("pip"), withIntermediateDirectories: true)
            try Data("".utf8).write(to: backend.appendingPathComponent("pip/__init__.py"))
            try Data("print('test dependency check passed')".utf8).write(to: backend.appendingPathComponent("pip/__main__.py"))
            for name in ["requirements.txt", "requirements-cross-platform.txt", "requirements-premium-voices.txt", "constraints.txt"] {
                try Data("# unchanged\n".utf8).write(to: backend.appendingPathComponent(name))
            }
        }
        let bin = current.appendingPathComponent("Contents/Resources/backend/.venv/bin")
        // Older installed bundles did not contain constraints.txt.
        try fm.removeItem(at: current.appendingPathComponent("Contents/Resources/backend/constraints.txt"))
        try fm.createDirectory(at: bin, withIntermediateDirectories: true)
        try fm.createSymbolicLink(at: bin.appendingPathComponent("python"), withDestinationURL: URL(fileURLWithPath: "/usr/bin/python3"))
        try Data("keep environment".utf8).write(to: bin.appendingPathComponent("marker"))
        let executable = source.appendingPathComponent("Contents/MacOS")
        try fm.createDirectory(at: executable, withIntermediateDirectories: true)
        try fm.copyItem(at: URL(fileURLWithPath: "/bin/echo"), to: executable.appendingPathComponent("DubCanvas"))
        let info: [String: Any] = ["CFBundleIdentifier": "com.animedubber.app", "CFBundleExecutable": "DubCanvas", "CFBundlePackageType": "APPL", "DubCanvasSourceRevision": revision]
        try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0).write(to: source.appendingPathComponent("Contents/Info.plist"))
        try Data("#!/bin/zsh\nexit 0\n".utf8).write(to: source.appendingPathComponent("Contents/Resources/install_update.sh"))
        let log = root.appendingPathComponent("update.log")
        fm.createFile(atPath: log.path, contents: nil)
        try AppUpdater.run("/usr/bin/codesign", ["--force", "--deep", "--sign", "-", source.path], logURL: log)
        let archive = root.appendingPathComponent("update.zip")
        try AppUpdater.run("/usr/bin/ditto", ["-c", "-k", "--keepParent", source.path, archive.path], logURL: log)
        let hash = SHA256.hash(data: try Data(contentsOf: archive)).map { String(format: "%02x", $0) }.joined()
        let manifest = AppUpdateManifest(schema: 1, revision: revision, sourceTimestamp: 123, version: "4.0", sha256: hash)
        let staged = root.appendingPathComponent("staged.app")
        let helper = try AppUpdater.prepare(archive: archive, work: root, staged: staged, current: current, manifest: manifest, logURL: log)
        XCTAssertTrue(fm.fileExists(atPath: helper.path))
        XCTAssertEqual(try Data(contentsOf: staged.appendingPathComponent("Contents/Resources/backend/.venv/bin/marker")), Data("keep environment".utf8))
        XCTAssertTrue(fm.fileExists(atPath: bin.appendingPathComponent("marker").path))
        XCTAssertFalse(fm.fileExists(atPath: current.appendingPathComponent("Contents/MacOS/DubCanvas").path))
    }
}
