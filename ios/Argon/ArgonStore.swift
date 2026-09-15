import Foundation
import Observation

/// The app's view of the server, and the only place it is held.
///
/// One store, one refresh path. The old app kept chat, tasks and status in
/// three objects refreshed on three schedules, which is how the UI could show
/// a task as open on one screen and done on another.
@Observable
@MainActor
final class ArgonStore {
  private(set) var state: ArgonState = .empty
  private(set) var messages: [ArgonMessage] = []
  private(set) var lastError: String?
  private(set) var isLoading = false
  private(set) var lastRefresh: Date?

  private let client: ArgonClient
  private var highestSeq: Int? { messages.map(\.seq).max() }

  init(client: ArgonClient) {
    self.client = client
  }

  var isReachable: Bool { lastError == nil && lastRefresh != nil }

  func refresh() async {
    isLoading = true
    defer { isLoading = false }
    do {
      async let newState = client.state()
      async let newMessages = client.messages(since: highestSeq)
      let (s, m) = try await (newState, newMessages)
      state = s
      merge(m.messages)
      lastError = nil
      lastRefresh = Date()
    } catch {
      lastError = error.localizedDescription
    }
  }

  /// Append only what is genuinely new. `since` should make this a no-op, but
  /// a retried request or a resumed app can repeat a page.
  private func merge(_ incoming: [ArgonMessage]) {
    guard !incoming.isEmpty else { return }
    let known = Set(messages.map(\.seq))
    messages.append(contentsOf: incoming.filter { !known.contains($0.seq) })
    messages.sort { $0.seq < $1.seq }
  }

  func send(_ text: String) async {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return }
    // Show it immediately; the server assigns the real sequence on refresh.
    let pending = ArgonMessage.local(seq: (highestSeq ?? 0) + 1, role: "user", text: trimmed)
    messages.append(pending)
    do {
      _ = try await client.say(trimmed)
      await refresh()
    } catch {
      lastError = error.localizedDescription
    }
  }

  func start(_ task: ArgonTask) async { await mutate { try await self.client.start(task.id) } }
  func complete(_ task: ArgonTask) async { await mutate { try await self.client.complete(task.id) } }
  func add(title: String, due: String? = nil) async {
    await mutate { try await self.client.addTask(title: title, due: due) }
  }

  func markRead() async {
    try? await client.markRead()
    await refresh()
  }

  private func mutate(_ work: @escaping () async throws -> Void) async {
    do {
      try await work()
      await refresh()
    } catch {
      lastError = error.localizedDescription
    }
  }
}

extension ArgonMessage {
  /// A locally created message, before the server has numbered it.
  static func local(seq: Int, role: String, text: String) -> ArgonMessage {
    let json = """
    {"seq": \(seq), "role": "\(role)", "text": \(Self.quote(text)), "at": null}
    """
    return try! JSONDecoder().decode(ArgonMessage.self, from: Data(json.utf8))
  }

  private static func quote(_ s: String) -> String {
    String(data: try! JSONSerialization.data(withJSONObject: [s]), encoding: .utf8)!
      .dropFirst().dropLast().description
  }
}
