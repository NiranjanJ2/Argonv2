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

  private func request(_ path: String, method: String = "GET",
                       body: [String: Any]? = nil,
                       timeout: TimeInterval = 20) async throws -> Data {
    var req = URLRequest(url: base.appendingPathComponent(path))
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
    let path = since.map { "v2/messages?since=\($0)" } ?? "v2/messages"
    return try decode(ArgonMessagesResponse.self, from: await request(path))
  }

  // MARK: writes — one per PendingWrite.Kind, all returning or throwing

  func apply(_ write: PendingWrite) async throws {
    switch write.kind {
    case .start(let id):
      _ = try await request("v1/tasks/\(id)", method: "PATCH", body: ["started": true])
    case .complete(let id):
      _ = try await request("v1/tasks/\(id)", method: "PATCH", body: ["done": true])
    case .add(let title, let due):
      var body: [String: Any] = ["title": title]
      if let due { body["due"] = due }
      _ = try await request("v1/tasks", method: "POST", body: body)
    case .say(let text):
      // A turn can take a while — the model may run tools.
      _ = try await request("v2/say", method: "POST",
                            body: ["text": text, "source": "ios"], timeout: 120)
    case .markRead:
      _ = try await request("v1/ios/read", method: "POST", body: [:])
    }
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
