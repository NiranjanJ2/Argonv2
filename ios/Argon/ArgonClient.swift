import Foundation

/// Every request to the server goes through here.
///
/// Replaces `ArgonBridge`, which grew a method per endpoint and its own
/// reconciliation timer. There is no timer in this file: `Timer.scheduledTimer`
/// is suspended the moment iOS backgrounds the app, so anything scheduled that
/// way only ran while the app was on screen. Whatever must happen with the app
/// closed belongs in `ArgonRoutineActivity`, which the system runs for us.
actor ArgonClient {
  struct Failure: LocalizedError {
    let status: Int
    let detail: String
    var errorDescription: String? {
      status == 401 ? "Server rejected the token." : "Server error \(status): \(detail)"
    }
  }

  private let session: URLSession
  private var base: URL
  private var token: String

  init(base: URL, token: String, session: URLSession = .shared) {
    self.base = base
    self.token = token
    self.session = session
  }

  func configure(base: URL, token: String) {
    self.base = base
    self.token = token
  }

  private func request(_ path: String, method: String = "GET",
                       body: [String: Any]? = nil) async throws -> Data {
    var req = URLRequest(url: base.appendingPathComponent(path))
    req.httpMethod = method
    req.timeoutInterval = 20
    req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    if let body {
      req.setValue("application/json", forHTTPHeaderField: "Content-Type")
      req.httpBody = try JSONSerialization.data(withJSONObject: body)
    }
    let (data, response) = try await session.data(for: req)
    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
    guard (200..<300).contains(status) else {
      throw Failure(status: status,
                    detail: String(data: data, encoding: .utf8)?.prefix(200).description ?? "")
    }
    return data
  }

  private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
    try JSONDecoder().decode(type, from: data)
  }

  // MARK: reads

  func state() async throws -> ArgonState {
    try decode(ArgonState.self, from: await request("v2/state"))
  }

  /// Pass the highest `seq` already held to fetch only what is new. Sequence
  /// numbers rather than timestamps, so the phone and server need not agree on
  /// a clock.
  func messages(since: Int? = nil) async throws -> ArgonMessagesResponse {
    let path = since.map { "v2/messages?since=\($0)" } ?? "v2/messages"
    return try decode(ArgonMessagesResponse.self, from: await request(path))
  }

  // MARK: writes

  func say(_ text: String) async throws -> ArgonSayResponse {
    try decode(ArgonSayResponse.self,
               from: await request("v2/say", method: "POST",
                                   body: ["text": text, "source": "ios"]))
  }

  func start(_ taskID: String) async throws {
    _ = try await request("v1/tasks/\(taskID)", method: "PATCH", body: ["started": true])
  }

  func complete(_ taskID: String) async throws {
    _ = try await request("v1/tasks/\(taskID)", method: "PATCH", body: ["done": true])
  }

  func addTask(title: String, due: String? = nil) async throws {
    var body: [String: Any] = ["title": title]
    if let due { body["due"] = due }
    _ = try await request("v1/tasks", method: "POST", body: body)
  }

  func markRead() async throws {
    _ = try await request("v1/ios/read", method: "POST", body: [:])
  }

  func register(deviceToken: String) async throws {
    _ = try await request("v1/ios/register", method: "POST", body: ["token": deviceToken])
  }

  /// What the phone knows about itself. Reported as an observation, never as a
  /// command — the server records it and the agent decides what it means.
  func report(_ facts: [String: Any]) async throws {
    _ = try await request("v1/ios/state", method: "POST", body: facts)
  }
}
