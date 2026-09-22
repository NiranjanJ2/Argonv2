import Foundation

/// Every request to the server goes through here.
///
/// Replaces `ArgonBridge`, which had a method per endpoint, a 20-second polling
/// timer, and four writes of the shape `_ = try? await perform(request)` whose
/// result was discarded.
///
/// There is no timer in this file. `Timer.scheduledTimer` is suspended the
/// moment iOS backgrounds the app, so a timer-driven refresh only runs while he
/// is already looking at the screen — which is the one time he doesn't need it.
/// Refresh is driven by the app coming forward, a silent push, or a background
/// task. Anything that must happen with the app closed lives in
/// `ArgonRoutineActivity`, which the system runs for us.
actor ArgonClient {
  /// Transient failures are worth retrying; permanent ones never will be, and
  /// retrying them forever is how a queue silently stops making progress.
  enum Failure: LocalizedError, Equatable {
    case offline(String)
    case timedOut
    case unauthorised
    case rejected(status: Int, detail: String)   // 4xx — asking again won't help
    case server(status: Int, detail: String)     // 5xx — probably will

    var isTransient: Bool {
      switch self {
      case .offline, .timedOut, .server: return true
      case .unauthorised, .rejected: return false
      }
    }

    var errorDescription: String? {
      switch self {
      case .offline: return "Can't reach Argon."
      case .timedOut: return "Argon didn't answer in time."
      case .unauthorised: return "Server rejected the token — check Settings."
      case .rejected(let status, let detail): return "Argon refused that (\(status)). \(detail)"
      case .server(let status, _): return "Argon had an error (\(status))."
      }
    }
  }

  private let session: URLSession
  private var base: URL
  private var token: String

  init(base: URL, token: String, session: URLSession? = nil) {
    self.base = base
    self.token = token
    if let session {
      self.session = session
    } else {
      let config = URLSessionConfiguration.default
      config.timeoutIntervalForRequest = 20
      config.waitsForConnectivity = false   // fail fast; the outbox will retry
      self.session = URLSession(configuration: config)
    }
  }

  func configure(base: URL, token: String) {
    self.base = base
    self.token = token
  }

  var endpoint: URL { base }

  /// Build the URL for a path plus optional query.
  ///
  /// Not `appendingPathComponent`: it percent-encodes `?` into `%3F`, so
  /// `v2/messages?since=7` was requested as `v2/messages%3Fsince=7` and the
  /// server answered 404. Every refresh after the first message failed, and
  /// because the read is inside a concurrent `try await`, the whole state
  /// update was skipped — the board simply froze on the disk cache.
  func url(path: String, query: [String: String] = [:]) -> URL {
    var components = URLComponents(url: base.appendingPathComponent(path),
                                   resolvingAgainstBaseURL: false)
    if !query.isEmpty {
      components?.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
    }
    return components?.url ?? base.appendingPathComponent(path)
  }

  private func request(_ path: String, method: String = "GET",
                       query: [String: String] = [:],
                       body: [String: Any]? = nil,
                       timeout: TimeInterval = 20) async throws -> Data {
    var req = URLRequest(url: url(path: path, query: query))
    req.httpMethod = method
    req.timeoutInterval = timeout
    req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    if let body {
      req.setValue("application/json", forHTTPHeaderField: "Content-Type")
      req.httpBody = try JSONSerialization.data(withJSONObject: body)
    }

    let data: Data, response: URLResponse
    do {
      (data, response) = try await session.data(for: req)
    } catch let error as URLError {
      throw error.code == .timedOut ? Failure.timedOut
                                    : Failure.offline(error.localizedDescription)
    }

    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
    let detail = String(data: data, encoding: .utf8)?.prefix(200).description ?? ""
    switch status {
    case 200..<300: return data
    case 401, 403: throw Failure.unauthorised
    case 400..<500: throw Failure.rejected(status: status, detail: detail)
    default: throw Failure.server(status: status, detail: detail)
    }
  }

  private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
    try JSONDecoder().decode(type, from: data)
  }

  // MARK: reads — raw data is returned too, so the cache stores exactly what arrived

  func state() async throws -> (ArgonState, Data) {
    let data = try await request("v2/state")
    return (try decode(ArgonState.self, from: data), data)
  }

  /// Pass the highest `seq` already held to fetch only what is new. Sequence
  /// numbers rather than timestamps, so the phone and server never need to
  /// agree about a clock.
  func messages(since: Int? = nil) async throws -> ArgonMessagesResponse {
    let query = since.map { ["since": String($0)] } ?? [:]
    return try decode(ArgonMessagesResponse.self,
                      from: await request("v2/messages", query: query))
  }

  // MARK: writes — one per PendingWrite.Kind, all returning or throwing

  func apply(_ write: PendingWrite) async throws {
    switch write.kind {
    case .start(let id):
      _ = try await request("v1/tasks/\(id)", method: "PATCH", body: ["started": true])
    case .stop(let id):
      _ = try await request("v1/tasks/\(id)", method: "PATCH", body: ["started": false])
    case .complete(let id):
      _ = try await request("v1/tasks/\(id)", method: "PATCH", body: ["done": true])
    case .add(let title, let due):
      var body: [String: Any] = ["title": title]
      if let due { body["due"] = due }
      _ = try await request("v1/tasks", method: "POST", body: body)
    case .say(let text):
      // The server accepts and answers on a worker, so this is a fast append
      // rather than the whole turn. It used to wait up to 120s with the bubble
      // stuck on "sending…", which is what made the chat feel like a poll.
      _ = try await request("v2/say", method: "POST",
                            body: ["text": text, "source": "ios"], timeout: 20)
    case .markRead:
      _ = try await request("v1/ios/read", method: "POST", body: [:])
    }
  }

  /// Diagnostics. Fire-and-forget from the caller's point of view, but it
  /// throws so ArgonLog can keep the batch when it does not land.
  func log(_ entries: [[String: Any]]) async throws {
    _ = try await request("v2/log", method: "POST", body: ["entries": entries])
  }

  /// He has read the brief. Explicit, not inferred from a fetch: the app
  /// refreshes on every wake, and treating that as "seen" dismissed briefs he
  /// never looked at.
  func ackBrief() async throws {
    _ = try await request("v2/brief/ack", method: "POST", body: [:])
  }

  /// What the phone actually did with the lock.
  ///
  /// Sent even — especially — on failure: a failure the server never hears
  /// about is indistinguishable from a phone that is switched off, which is
  /// how Argon ends up believing it has locked a device that is wide open.
  func reportLock(version: Int, shielded: Bool, error: String?) async throws {
    var body: [String: Any] = ["version": version, "shielded": shielded,
                               "applied_at": ISO8601DateFormatter().string(from: Date())]
    if let error { body["error"] = error }
    _ = try await request("v1/ios/state", method: "POST", body: body)
  }

  func register(deviceToken: String) async throws {
    _ = try await request("v1/ios/register", method: "POST", body: ["token": deviceToken])
  }

  /// What the phone knows about itself. Reported as an observation, never a
  /// command — the server records it and the agent decides what it means.
  func report(_ facts: [String: Any]) async throws {
    _ = try await request("v1/ios/state", method: "POST", body: facts)
  }
}
