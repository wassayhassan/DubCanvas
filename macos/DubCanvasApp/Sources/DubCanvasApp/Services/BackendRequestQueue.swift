import Foundation

final class BackendRequestQueue {
    private let writer = DispatchQueue(label: "DubCanvas.backend.requests")
    private let lock = NSLock()
    private var pending: [String: DispatchWorkItem] = [:]

    func enqueue(id: String, timeout: TimeInterval, write: @escaping () throws -> Void,
                 failure: @escaping (String) -> Void) throws {
        let deadline = DispatchWorkItem { [weak self] in
            guard self?.complete(id: id) == true else { return }
            failure("The processing service did not respond in time. Reconnect and retry; saved project work is preserved.")
        }
        lock.lock()
        guard pending[id] == nil else {
            lock.unlock()
            throw UpdateFailure("This request is already pending. Wait for it to finish.")
        }
        pending[id] = deadline
        lock.unlock()
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: deadline)
        writer.async { [weak self] in
            guard let self, self.contains(id: id) else { return }
            do { try write() }
            catch {
                if self.complete(id: id) { failure(error.localizedDescription) }
            }
        }
    }

    private func contains(id: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return pending[id] != nil
    }

    @discardableResult func complete(id: String) -> Bool {
        lock.lock()
        let item = pending.removeValue(forKey: id)
        lock.unlock()
        item?.cancel()
        return item != nil
    }

    func cancelAll() {
        lock.lock()
        let items = Array(pending.values)
        pending.removeAll()
        lock.unlock()
        for item in items { item.cancel() }
    }
}
