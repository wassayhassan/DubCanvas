import Foundation
import XCTest
@testable import DubCanvasApp

final class BackendRequestQueueTests: XCTestCase {
    func testBlockedWriteDoesNotBlockCallerAndTimesOutIndependently() throws {
        let queue = BackendRequestQueue()
        let gate = DispatchSemaphore(value: 0)
        defer { gate.signal() }
        let failed = expectation(description: "request deadline")
        let start = Date()
        try queue.enqueue(id: "test", timeout: 0.05, write: { gate.wait() }, failure: { _ in failed.fulfill() })
        XCTAssertLessThan(Date().timeIntervalSince(start), 0.25)
        wait(for: [failed], timeout: 3)
        XCTAssertFalse(queue.complete(id: "test"))
    }

    func testCompletedRequestCancelsDeadline() throws {
        let queue = BackendRequestQueue()
        let failed = expectation(description: "must not fail")
        failed.isInverted = true
        try queue.enqueue(id: "done", timeout: 0.05, write: {}, failure: { _ in failed.fulfill() })
        XCTAssertTrue(queue.complete(id: "done"))
        wait(for: [failed], timeout: 0.15)
    }
}
