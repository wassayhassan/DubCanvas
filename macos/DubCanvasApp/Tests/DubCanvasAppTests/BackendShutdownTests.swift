import Foundation
import XCTest
@testable import DubCanvasApp

final class BackendShutdownTests: XCTestCase {
    func testBlockedWriterDoesNotDelayStopOrTermination() throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sleep")
        process.arguments = ["30"]
        try process.run()
        let terminated = expectation(description: "Backend terminates despite blocked writer")
        process.terminationHandler = { _ in terminated.fulfill() }
        let writeLock = NSLock()
        writeLock.lock()
        defer {
            writeLock.unlock()
            if process.isRunning { process.terminate() }
        }
        let started = Date()
        BackendProcess.requestShutdown(process: process, input: nil, writeLock: writeLock)
        XCTAssertLessThan(Date().timeIntervalSince(started), 0.25)
        wait(for: [terminated], timeout: 5)
        XCTAssertFalse(process.isRunning)
    }
}
