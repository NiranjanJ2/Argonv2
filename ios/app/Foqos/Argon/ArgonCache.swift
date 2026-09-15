import Foundation

/// The last state the server gave us, on disk.
///
/// Opening the app offline used to show an empty screen, which is
/// indistinguishable from "you have nothing to do" — the worst possible lie for
/// an app whose job is telling him what is due. Now the last known board is
/// drawn immediately with an honest note about its age, and the refresh
/// replaces it when it lands.
struct ArgonCache {
  private let file: URL

  init(filename: String = "argon-state.json") {
    let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                       in: .userDomainMask)[0]
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    self.file = dir.appendingPathComponent(filename)
  }

  struct Snapshot: Codable {
    let state: Data       // encoded ArgonState JSON, stored raw
    let messages: Data
    let at: Date
  }

  func save(stateJSON: Data, messagesJSON: Data) {
    let snapshot = Snapshot(state: stateJSON, messages: messagesJSON, at: Date())
    guard let data = try? JSONEncoder().encode(snapshot) else { return }
    try? data.write(to: file, options: .atomic)
  }

  func load() -> (state: ArgonState, messages: [ArgonMessage], at: Date)? {
    guard let data = try? Data(contentsOf: file),
          let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data),
          let state = try? JSONDecoder().decode(ArgonState.self, from: snapshot.state)
    else { return nil }
    let messages = (try? JSONDecoder().decode([ArgonMessage].self, from: snapshot.messages)) ?? []
    return (state, messages, snapshot.at)
  }

  func clear() {
    try? FileManager.default.removeItem(at: file)
  }
}

extension Date {
  /// "just now", "3m ago", "2h ago" — for saying how stale something is.
  var argonAgo: String {
    let seconds = Int(Date().timeIntervalSince(self))
    if seconds < 45 { return "just now" }
    if seconds < 3600 { return "\(seconds / 60)m ago" }
    if seconds < 86_400 { return "\(seconds / 3600)h ago" }
    return "\(seconds / 86_400)d ago"
  }
}
