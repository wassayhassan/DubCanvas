import AVKit
import SwiftUI

// AVKit changes enabled controls and traverses the window's key-view loop when
// attaching a player. Doing that inside a SwiftUI representable update can
// re-enter AttributeGraph and freeze the main thread on affected macOS builds.
struct NativeDubPlayer: NSViewRepresentable {
    let player: AVPlayer

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.controlsStyle = .floating
        context.coordinator.attach(player, to: view)
        return view
    }

    func updateNSView(_ view: AVPlayerView, context: Context) {
        context.coordinator.attach(player, to: view)
    }

    static func dismantleNSView(_ view: AVPlayerView, coordinator: Coordinator) {
        coordinator.detach()
    }

    @MainActor
    final class Coordinator {
        private weak var view: AVPlayerView?
        private var requestedPlayer: AVPlayer?
        private var generation = 0
        private var scheduled = false

        func attach(_ player: AVPlayer, to view: AVPlayerView) {
            if self.view === view, requestedPlayer === player,
               scheduled || view.player === player { return }
            self.view = view
            requestedPlayer = player
            generation += 1
            let expected = generation
            scheduled = true
            // A separate main-queue turn keeps AVKit's focus notifications out
            // of makeNSView/updateNSView and the current SwiftUI transaction.
            DispatchQueue.main.async { [weak self] in
                guard let self, self.generation == expected else { return }
                self.scheduled = false
                guard let view = self.view, let player = self.requestedPlayer,
                      view.player !== player else { return }
                view.player = player
            }
        }

        func detach() {
            generation += 1
            scheduled = false
            requestedPlayer = nil
            let oldView = view
            let oldPlayer = oldView?.player
            view = nil
            DispatchQueue.main.async { [weak oldView] in
                guard let oldView, oldView.player === oldPlayer else { return }
                oldPlayer?.pause()
                oldView.player = nil
            }
        }
    }
}
