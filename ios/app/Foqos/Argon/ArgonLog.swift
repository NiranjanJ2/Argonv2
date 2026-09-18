import Foundation

/// What the app did while nobody was watching.
///
/// The phone is the half of this system with no console. When twenty checkmarks
/// were answered 200 by the server and then quietly reverted, the only evidence
/// anywhere was the server's own access log — nothing recorded what the app
/// believed, what it queued, or what it retried. Reconstructing it took reading
/// journald by hand.
///
/// Deliberately small and dumb: a bounded ring on disk, flushed opportunistically
/// with whatever else is already talking to the server. It is a diagnostic, so
/// it must never be the reason a write is delayed or a screen is slow, and it
/// must never grow without bound on a phone that has been offline for a week.
actor ArgonLog {
  static let shared = ArgonLog()

  /// Enough to cover a session's worth of taps, small enough that the whole
  /// buffer fits in one flush and one file write.
  private static let limit = 300

  private var pending: [Entry] = []
  private let file: URL = {
    let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                       in: .userDomainMask)[0]
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return dir.appendingPathComponent("argon-log.json")
  }()

  struct Entry: Codable, Sendable {
    let at: String
    let area: String
    let text: String
  }

  private init() {
    if let data = try? Data(contentsOf: file),
       let saved = try? JSONDecoder().decode([Entry].self, from: data) {
      pending = saved
    }
  }

  /// Record one line. Never throws, never blocks the caller on disk.
  static func note(_ area: String, _ text: String) {
    Task { await shared.append(area: area, text: text) }
  }

  private func append(area: String, text: String) {
    pending.append(Entry(at: ISO8601DateFormatter().string(from: Date()),
                         area: area, text: text))
    // Drop the oldest rather than the newest: the lines near a failure are the
    // ones worth having, and they are always the recent ones.
    if pending.count > Self.limit { pending.removeFirst(pending.count - Self.limit) }
    persist()
  }

  private func persist() {
    guard let data = try? JSONEncoder().encode(pending) else { return }
    try? data.write(to: file, options: .atomic)
  }

  /// Hand everything to the server, and keep it if that fails.
  ///
  /// Cleared only on success, so a flush that dies mid-flight loses nothing —
  /// the same contract as the write outbox, for the same reason.
  func flush(using client: ArgonClient) async {
    guard !pending.isEmpty else { return }
    let batch = pending
    do {
      try await client.log(batch.map { ["at": $0.at, "area": $0.area, "text": $0.text] })
      pending.removeAll()
      persist()
    } catch {
      // Keep them. A diagnostic that discards itself when the network is down
      // is missing exactly the window worth diagnosing.
    }
  }
}
