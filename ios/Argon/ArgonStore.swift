import Foundation
import Observation

/// The app's whole view of the server, and the only place it is held.
///
/// v1 kept chat, tasks, status and the planner in four objects on four refresh
/// schedules, which is how a task could read open on one screen and done on
/// another. One store, one refresh path, one truth.
///
/// Three rules make the network feel like it isn't there:
///
/// **Cache first.** The last known board is on disk and is drawn before any
/// request is made, labelled with its age. An empty screen and "you have
/// nothing due" used to be indistinguishable, which is the worst lie this app
/// can tell.
///
/// **Writes are optimistic and durable.** A tap changes the screen immediately
/// and enqueues a `PendingWrite` that survives the app being killed. The server
/// is still the truth: when the write lands, a refresh overwrites the guess.
///
/// **Failures are visible.** A write the server permanently refuses is rolled
/// back and surfaced. v1 discarded four write results entirely.
@Observable
@MainActor
final class ArgonStore {
  private(set) var state: ArgonState = .empty
  private(set) var messages: [ArgonMessage] = []
  private(set) var pendingCount = 0
  private(set) var isLoading = false
  private(set) var lastRefresh: Date?
  private(set) var failure: String?

  /// Honest about where the numbers came from.
  enum Connection: Equatable {
    case never
    case live(at: Date)
    case stale(at: Date, why: String)

    var isLive: Bool { if case .live = self { return true }; return false }
  }

  private(set) var connection: Connection = .never

  private let client: ArgonClient
  private let outbox: ArgonOutbox
  private let cache: ArgonCache
  private var highestSeq: Int? { messages.filter { !$0.pending }.map(\.seq).max() }

  init(client: ArgonClient, outbox: ArgonOutbox = ArgonOutbox(), cache: ArgonCache = ArgonCache()) {
    self.client = client
    self.outbox = outbox
    self.cache = cache
    if let snapshot = cache.load() {
      state = snapshot.state
      messages = snapshot.messages
      connection = .stale(at: snapshot.at, why: "cached")
    }
  }

  // MARK: refresh

  /// Flush anything queued first, then read. Reading before flushing would show
  /// him a board that contradicts the tap he just made.
  func refresh() async {
    isLoading = true
    defer { isLoading = false }

    await flush()

    do {
      async let stateCall = client.state()
      async let messageCall = client.messages(since: highestSeq)
      let ((newState, stateJSON), newMessages) = try await (stateCall, messageCall)

      state = newState
      merge(newMessages.messages)
      lastRefresh = Date()
      connection = .live(at: Date())
      failure = nil
      persist(stateJSON: stateJSON)
    } catch {
      let text = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
      connection = .stale(at: lastRefresh ?? Date(), why: text)
      // A read failing is not worth a banner when there is cached data to show;
      // the connection badge already says so.
      if lastRefresh == nil { failure = text }
    }
    pendingCount = await outbox.count
  }

  private func merge(_ incoming: [ArgonMessage]) {
    guard !incoming.isEmpty else { return }
    // Retire the local echo once the server's own copy of it arrives.
    let arrived = Set(incoming.map(\.text))
    messages.removeAll { $0.pending && arrived.contains($0.text) }

    let known = Set(messages.filter { !$0.pending }.map(\.seq))
    let settled = (messages.filter { !$0.pending }
                   + incoming.filter { !known.contains($0.seq) })
      .sorted { $0.seq < $1.seq }
    // Pending messages are the newest thing he did, so they belong at the end
    // regardless of having no sequence number yet.
    messages = settled + messages.filter { $0.pending }
  }

  private func persist(stateJSON: Data) {
    let messagesJSON = (try? JSONEncoder().encode(messages.filter { !$0.pending })) ?? Data("[]".utf8)
    cache.save(stateJSON: stateJSON, messagesJSON: messagesJSON)
  }

  // MARK: writes

  func start(_ task: ArgonTask) async {
    apply(.start(taskID: task.id))
    await enqueue(.start(taskID: task.id))
  }

  func complete(_ task: ArgonTask) async {
    apply(.complete(taskID: task.id))
    await enqueue(.complete(taskID: task.id))
  }

  func add(title: String, due: String? = nil) async {
    let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return }
    state.tasks.append(.local(title: trimmed, due: due))
    await enqueue(.add(title: trimmed, due: due))
  }

  func send(_ text: String) async {
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return }
    messages.append(.local(role: "user", text: trimmed))
    await enqueue(.say(text: trimmed))
  }

  func markRead() async {
    guard state.unread > 0 else { return }
    state.unread = 0
    await enqueue(.markRead)
  }

  /// Change local state the way the server will, so the screen responds to the
  /// tap rather than to the round trip.
  private func apply(_ kind: PendingWrite.Kind) {
    switch kind {
    case .start(let id):
      guard let i = state.tasks.firstIndex(where: { $0.id == id }) else { return }
      state.tasks[i].startedAt = ISO8601DateFormatter().string(from: Date())
    case .complete(let id):
      guard let i = state.tasks.firstIndex(where: { $0.id == id }) else { return }
      state.tasks[i].done = true
    case .add, .say, .markRead:
      break   // handled by the caller, which has the richer value
    }
  }

  private func enqueue(_ kind: PendingWrite.Kind) async {
    _ = await outbox.enqueue(kind)
    pendingCount = await outbox.count
    await flush()
    await refreshAfterWrite()
  }

  /// A read after a successful write, so the server's version replaces the
  /// optimistic guess — including the real id of a locally created task.
  private func refreshAfterWrite() async {
    guard await outbox.count == 0 else { return }
    do {
      let (newState, stateJSON) = try await client.state()
      let newMessages = try await client.messages(since: highestSeq)
      state = newState
      merge(newMessages.messages)
      connection = .live(at: Date())
      lastRefresh = Date()
      persist(stateJSON: stateJSON)
    } catch {
      // Queue is empty, so the write landed; only the read-back failed. The
      // optimistic state is still correct enough to show.
    }
  }

  // MARK: the outbox

  /// Drain the queue. Stops at the first transient failure so writes stay in
  /// order — completing a task the server has not been told about yet would be
  /// rejected for the wrong reason.
  func flush() async {
    for write in await outbox.pending {
      do {
        try await client.apply(write)
        await outbox.remove(write.id)
        failure = nil
      } catch let error as ArgonClient.Failure where error.isTransient {
        await outbox.recordAttempt(write.id)
        if write.isExhausted {
          await outbox.remove(write.id)
          failure = "Gave up \(write.describedForHim). \(error.errorDescription ?? "")"
        }
        break   // preserve order; try again on the next refresh
      } catch {
        // Permanent: the server will never accept this. Drop it and say so,
        // rather than leaving the screen showing something that did not happen.
        await outbox.remove(write.id)
        let detail = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        failure = "Couldn't finish \(write.describedForHim). \(detail)"
      }
    }
    pendingCount = await outbox.count
  }

  func dismissFailure() { failure = nil }
}
