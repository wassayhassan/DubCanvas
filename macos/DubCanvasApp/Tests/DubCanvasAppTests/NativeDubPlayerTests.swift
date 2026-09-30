import AVKit
import XCTest
@testable import DubCanvasApp

final class NativeDubPlayerTests: XCTestCase {
    @MainActor private func flushMainQueue() async {
        await withCheckedContinuation { continuation in
            DispatchQueue.main.async { continuation.resume() }
        }
    }

    @MainActor func testAttachmentWaitsUntilRepresentableUpdateHasReturned() async {
        let view = AVPlayerView()
        let player = AVPlayer()
        let coordinator = NativeDubPlayer.Coordinator()
        coordinator.attach(player, to: view)
        XCTAssertNil(view.player)
        await flushMainQueue()
        XCTAssertTrue(view.player === player)
        coordinator.detach()
        XCTAssertTrue(view.player === player)
        await flushMainQueue()
        XCTAssertNil(view.player)
    }

    @MainActor func testRapidPlayerChangesAttachOnlyLatestRequest() async {
        let view = AVPlayerView()
        let first = AVPlayer(), latest = AVPlayer()
        let coordinator = NativeDubPlayer.Coordinator()
        coordinator.attach(first, to: view)
        coordinator.attach(latest, to: view)
        coordinator.attach(latest, to: view)
        XCTAssertNil(view.player)
        await flushMainQueue()
        XCTAssertTrue(view.player === latest)
        coordinator.detach()
        await flushMainQueue()
    }

    @MainActor func testRemovedViewNeverReceivesPendingPlayer() async {
        let view = AVPlayerView()
        let coordinator = NativeDubPlayer.Coordinator()
        coordinator.attach(AVPlayer(), to: view)
        coordinator.detach()
        await flushMainQueue()
        XCTAssertNil(view.player)
    }
}
