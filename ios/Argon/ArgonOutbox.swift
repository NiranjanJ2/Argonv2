import Foundation

/// Writes that must survive the app dying.
///
/// v1 had four calls shaped `_ = try? await perform(request)` — the result
/// discarded. Tap Done, the server 500s, the row stays ticked on screen and the
/// task is still open tomorrow. There was no way for him to find out.
///
/// Here every write is enqueued, persisted to disk, and retried until the
/// server accepts it or rejects it permanently. A 4xx is the server saying "no,
/// and asking again won't help", so it is dropped and surfaced. Anything else —
/// offline, timeout, 5xx — stays queued.
struct PendingWrite: Codable, Identifiable, Equatable {
  enum Kind: Codable, Equatable {
    case start(taskID: String)
    case complete(taskID: String)
    case add(title: String, due: String?)
    case say(text: String)
    case markRead
  }

  let id: UUID
  let kind: Kind
  let created: Date
  var attempts: Int

  init(_ kind: Kind) {
    self.id = UUID()
    self.kind = kind
    self.created = Date()
    self.attempts = 0
  }

  /// Give up and tell him. Ten attempts with backoff spans a long offline
  /// stretch; past that the write is stale enough to be wrong to apply.
  var isExhausted: Bool { attempts >= 10 }

  var describedForHim: String {
    switch kind {
    case .start(let id): return "starting a task (\(id.prefix(6)))"
    case .complete(let id): return "completing a task (\(id.prefix(6)))"
    case .add(let title, _): return "adding “\(title)”"
    case .say(let text): return "sending “\(text.prefix(30))”"
    case .markRead: return "marking messages read"
    }
  }
}

/// Durable FIFO queue, persisted as JSON next to the app's data.
actor ArgonOutbox {
  private var queue: [PendingWrite] = []
  private let file: URL

  init(filename: String = "argon-outbox.json") {
    let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                       in: .userDomainMask)[0]
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    self.file = dir.appendingPathComponent(filename)
    // Read on the way in rather than calling an isolated method from init,
    // which is an error under the Swift 6 language mode.
    self.queue = Self.read(from: file)
  }

  var pending: [PendingWrite] { queue }
  var count: Int { queue.count }

  func enqueue(_ kind: PendingWrite.Kind) -> PendingWrite {
    let write = PendingWrite(kind)
    queue.append(write)
    save()
    return write
  }

  func remove(_ id: UUID) {
    queue.removeAll { $0.id == id }
    save()
  }

  func recordAttempt(_ id: UUID) {
    guard let i = queue.firstIndex(where: { $0.id == id }) else { return }
    queue[i].attempts += 1
    save()
  }

  func clear() {
    queue.removeAll()
    save()
  }

  private static func read(from file: URL) -> [PendingWrite] {
    guard let data = try? Data(contentsOf: file),
          let decoded = try? JSONDecoder().decode([PendingWrite].self, from: data)
    else { return [] }
    return decoded
  }

  private func save() {
    guard let data = try? JSONEncoder().encode(queue) else { return }
    try? data.write(to: file, options: .atomic)
  }
}
