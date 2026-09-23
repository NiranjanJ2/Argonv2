import XCTest
@testable import ArgonSync

/// A stubbed transport, so the whole client/store/outbox path runs for real
/// without a server. Each test scripts the responses it wants.
final class StubProtocol: URLProtocol {
  nonisolated(unsafe) static var handler: ((URLRequest) -> (Int, Data))?
  nonisolated(unsafe) static var seen: [String] = []

  override class func canInit(with request: URLRequest) -> Bool { true }
  override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

  override func startLoading() {
    Self.seen.append("\(request.httpMethod ?? "?") \(request.url?.path ?? "")")
    let (status, body) = Self.handler?(request) ?? (200, Data("{}".utf8))
    let response = HTTPURLResponse(url: request.url!, statusCode: status,
                                   httpVersion: nil, headerFields: nil)!
    client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
    client?.urlProtocol(self, didLoad: body)
    client?.urlProtocolDidFinishLoading(self)
  }

  override func stopLoading() {}

  static func reset() { handler = nil; seen = [] }

  static var session: URLSession {
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [StubProtocol.self]
    return URLSession(configuration: config)
  }
}

private func readAll(_ stream: InputStream) -> Data {
  stream.open()
  defer { stream.close() }
  var data = Data()
  var buffer = [UInt8](repeating: 0, count: 1024)
  while stream.hasBytesAvailable {
    let n = stream.read(&buffer, maxLength: buffer.count)
    if n <= 0 { break }
    data.append(buffer, count: n)
  }
  return data
}

private func tempName(_ label: String) -> String {
  "argon-test-\(label)-\(UUID().uuidString).json"
}

private let stateJSON = """
{"now":"2026-09-14T18:00:00-07:00","school":{"schedule":"Regular","period":null},
 "ticking":true,"unread":0,"facts":[],
 "budget":{"spent":0.29,"cap":5.0,"cached_fraction":0.6},
 "tasks":[{"id":"t1","title":"AP Chem pset","due":"2026-09-01","done":false},
          {"id":"t2","title":"Read Ch 3","due":"2099-09-20","done":false}]}
"""

// MARK: - the outbox

final class OutboxTests: XCTestCase {
  func testWriteIDIsSentAsIdempotencyKey() async throws {
    var received: String?
    StubProtocol.handler = { request in
      received = request.value(forHTTPHeaderField: "Idempotency-Key")
      return (201, Data(#"{"task":{}}"#.utf8))
    }
    let client = ArgonClient(base: URL(string: "http://stub")!, token: "t",
                             session: StubProtocol.session)
    let write = PendingWrite(.add(title: "Only once", due: nil))
    try await client.apply(write)
    XCTAssertEqual(received, write.id.uuidString)
  }

  func testWritesSurviveTheAppDying() async {
    let name = tempName("survive")
    let first = ArgonOutbox(filename: name)
    _ = await first.enqueue(.complete(taskID: "t1"))
    _ = await first.enqueue(.say(text: "hello"))

    // A fresh instance is what a relaunch looks like.
    let reborn = ArgonOutbox(filename: name)
    let pending = await reborn.pending
    XCTAssertEqual(pending.count, 2, "queued writes must outlive the process")
    XCTAssertEqual(pending.first?.kind, .complete(taskID: "t1"), "order preserved")
    await reborn.clear()
  }

  func testExhaustionIsReportedOnTheTenthAttempt() async {
    let outbox = ArgonOutbox(filename: tempName("exhaust"))
    let write = await outbox.enqueue(.start(taskID: "t1"))
    var spentAfter = 0
    for n in 1...10 {
      if await outbox.recordAttempt(write.id) { spentAfter = n; break }
    }
    // The caller holds a pre-increment snapshot, so asking the write itself
    // gave up one attempt late. recordAttempt reports the post-increment truth.
    XCTAssertEqual(spentAfter, 10, "must be spent on the tenth, not the eleventh")
    await outbox.clear()
  }
}

// MARK: - URL construction

final class URLTests: XCTestCase {
  /// The bug this exists for: `appendingPathComponent` percent-encodes `?`,
  /// so `v2/messages?since=7` was requested as `v2/messages%3Fsince=7` and the
  /// server answered 404 — freezing the board on the disk cache forever.
  func testQueryIsAQueryAndNotPartOfThePath() async {
    let client = ArgonClient(base: URL(string: "http://host")!, token: "t")
    let plain = await client.url(path: "v2/messages")
    let paged = await client.url(path: "v2/messages", query: ["since": "7"])

    XCTAssertEqual(plain.absoluteString, "http://host/v2/messages")
    XCTAssertEqual(paged.absoluteString, "http://host/v2/messages?since=7")
    XCTAssertFalse(paged.absoluteString.contains("%3F"), "the ? must not be encoded")
    XCTAssertEqual(paged.path, "/v2/messages", "query must not leak into the path")
    XCTAssertEqual(paged.query, "since=7")
  }

  func testIncrementalReadActuallyHitsTheRightURL() async throws {
    StubProtocol.reset()
    StubProtocol.handler = { _ in (200, Data(#"{"messages":[],"unread":0}"#.utf8)) }
    let client = ArgonClient(base: URL(string: "http://host")!, token: "t",
                             session: StubProtocol.session)
    _ = try await client.messages(since: 42)
    XCTAssertEqual(StubProtocol.seen, ["GET /v2/messages"],
                   "a 404 here is what froze every refresh after the first message")
  }
}

// MARK: - error classification

final class FailureTests: XCTestCase {
  func testOnlyWorthRetryingWhenRetryingCouldHelp() {
    XCTAssertTrue(ArgonClient.Failure.offline("down").isTransient)
    XCTAssertTrue(ArgonClient.Failure.timedOut.isTransient)
    XCTAssertTrue(ArgonClient.Failure.server(status: 503, detail: "").isTransient)
    XCTAssertFalse(ArgonClient.Failure.unauthorised.isTransient,
                   "a bad token will still be bad next time")
    XCTAssertFalse(ArgonClient.Failure.rejected(status: 404, detail: "").isTransient)
  }
}

// MARK: - push registration receipt

final class RegistrationReceiptTests: XCTestCase {
  func testMissingOrDifferentServerReceiptRequiresRegistration() {
    XCTAssertTrue(ArgonRegistrationReceipt.needsUpload(
      storedReceipt: nil, serverIdentity: "server-b"))
    XCTAssertTrue(ArgonRegistrationReceipt.needsUpload(
      storedReceipt: "server-a", serverIdentity: "server-b"))
  }

  func testMatchingServerReceiptSkipsRedundantUpload() {
    XCTAssertFalse(ArgonRegistrationReceipt.needsUpload(
      storedReceipt: "server-a", serverIdentity: "server-a"))
  }
}

// MARK: - the store

@MainActor
final class StoreTests: XCTestCase {
  private func makeStore(label: String) -> ArgonStore {
    let client = ArgonClient(base: URL(string: "http://stub")!, token: "t",
                             session: StubProtocol.session)
    return ArgonStore(client: client,
                      outbox: ArgonOutbox(filename: tempName("\(label)-out")),
                      cache: ArgonCache(filename: tempName("\(label)-cache")))
  }

  override func setUp() { StubProtocol.reset() }

  /// Asserts the screen changes *before* the round trip. The previous version
  /// of this test passed with the entire optimistic-write feature deleted.
  func testCompletingATaskShowsImmediately() async {
    StubProtocol.handler = { request in
      request.url!.path.contains("state") ? (200, Data(stateJSON.utf8))
                                          : (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let store = makeStore(label: "optimistic")
    await store.refresh()
    XCTAssertEqual(store.state.sortedTasks.count, 2)

    // Hang every write, so nothing can have reached the server yet.
    let gate = DispatchSemaphore(value: 0)
    StubProtocol.handler = { request in
      if request.httpMethod != "GET" { gate.wait() }
      return request.url!.path.contains("state") ? (200, Data(stateJSON.utf8))
                                                 : (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let target = store.state.sortedTasks[0]
    let work = Task { await store.complete(target) }
    // Give the optimistic mutation a moment; the network is still blocked.
    try? await Task.sleep(nanoseconds: 120_000_000)
    XCTAssertTrue(store.state.tasks.first { $0.id == target.id }!.done,
                  "the tap must change the screen, not the round trip")
    gate.signal()
    await work.value
  }

  func testOverlappingFlushesDoNotSendTheSameWriteTwice() async {
    StubProtocol.handler = { request in
      request.url!.path.contains("state") ? (200, Data(stateJSON.utf8))
                                          : (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let store = makeStore(label: "doubleflush")
    await store.refresh()
    StubProtocol.seen = []

    // Foregrounding, a silent push and the background task can all land at once.
    await store.add(title: "Buy milk")
    async let a: Void = store.refresh()
    async let b: Void = store.refresh()
    async let c: Void = store.flush()
    _ = await (a, b, c)

    let posts = StubProtocol.seen.filter { $0 == "POST /v1/tasks" }
    XCTAssertEqual(posts.count, 1, "a queued write must be applied exactly once")
  }

  func testMovingATaskSendsTheNewDateAndShowsItAtOnce() async {
    StubProtocol.handler = { request in
      request.url!.path.contains("state") ? (200, Data(stateJSON.utf8))
                                          : (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let store = makeStore(label: "move")
    await store.refresh()
    let task = store.state.tasks.first { $0.id == "t2" }!

    var body: [String: Any] = [:]
    StubProtocol.handler = { request in
      guard request.httpMethod == "PATCH" else { return (503, Data()) }
      // URLProtocol sees the body as a stream, not httpBody.
      let data = request.httpBodyStream.map(readAll) ?? Data()
      body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
      return (200, Data(#"{"task":{}}"#.utf8))
    }
    // The read-back after the write fails, so the screen shows the optimistic
    // change — which is the point: the swipe moves the row, not the round trip.
    await store.move(task, to: "2099-09-21")
    XCTAssertEqual(body["due"] as? String, "2099-09-21")
    XCTAssertEqual(store.state.tasks.first { $0.id == "t2" }?.due, "2099-09-21")
  }

  func testRepeatedTextRetiresOneEchoPerArrival() async {
    StubProtocol.handler = { request in
      if request.url!.path.contains("state") { return (200, Data(stateJSON.utf8)) }
      if request.httpMethod == "POST" { return (200, Data(#"{"reply":"","spoke":true}"#.utf8)) }
      return (200, Data(#"{"messages":[{"seq":1,"role":"user","text":"ok","at":null}],"unread":0}"#.utf8))
    }
    let store = makeStore(label: "echo")
    store.stageForTest(pendingTexts: ["ok", "ok"])
    await store.refresh()
    XCTAssertEqual(store.messages.filter { $0.text == "ok" }.count, 2,
                   "one server copy retires one echo, not both")
  }

  func testAPermanentRejectionIsSurfacedNotSwallowed() async {
    StubProtocol.handler = { request in
      if request.httpMethod == "PATCH" { return (404, Data(#"{"error":"gone"}"#.utf8)) }
      return request.url!.path.contains("state") ? (200, Data(stateJSON.utf8))
                                                 : (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let store = makeStore(label: "reject")
    await store.refresh()
    await store.complete(store.state.sortedTasks[0])

    XCTAssertEqual(store.pendingCount, 0, "a 404 must not be retried forever")
    XCTAssertNotNil(store.failure, "v1 discarded this; he must be told")
    XCTAssertTrue(store.failure!.contains("completing a task"), store.failure ?? "")
  }

  func testATransientFailureKeepsTheWriteQueued() async {
    StubProtocol.handler = { request in
      if request.httpMethod != "GET" { return (503, Data("busy".utf8)) }
      return request.url!.path.contains("state") ? (200, Data(stateJSON.utf8))
                                                 : (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let store = makeStore(label: "transient")
    await store.refresh()
    await store.complete(store.state.sortedTasks[0])

    XCTAssertEqual(store.pendingCount, 1, "a 503 keeps the write for later")
    XCTAssertNil(store.failure, "a retryable blip is not worth alarming him")
  }

  func testOfflineStillShowsTheLastKnownBoard() async {
    let cacheName = tempName("offline-cache")
    StubProtocol.handler = { request in
      request.url!.path.contains("state") ? (200, Data(stateJSON.utf8))
                                          : (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let client = ArgonClient(base: URL(string: "http://stub")!, token: "t",
                             session: StubProtocol.session)
    let warm = ArgonStore(client: client,
                          outbox: ArgonOutbox(filename: tempName("offline-out")),
                          cache: ArgonCache(filename: cacheName))
    await warm.refresh()
    XCTAssertEqual(warm.state.sortedTasks.count, 2)

    // A new launch with the server unreachable must not show an empty board:
    // "nothing due" is the worst lie this app can tell.
    StubProtocol.handler = { _ in (500, Data("down".utf8)) }
    let cold = ArgonStore(client: client,
                          outbox: ArgonOutbox(filename: tempName("offline-out2")),
                          cache: ArgonCache(filename: cacheName))
    XCTAssertEqual(cold.state.sortedTasks.count, 2, "cache drawn before any request")
    if case .stale = cold.connection {} else { XCTFail("must admit the data is cached") }

    await cold.refresh()
    XCTAssertEqual(cold.state.sortedTasks.count, 2, "a failed refresh keeps the cache")
    if case .live = cold.connection { XCTFail("must not claim to be live") }
  }

  func testPendingMessageSortsLastThenIsReplaced() async {
    StubProtocol.handler = { request in
      if request.url!.path.contains("state") { return (200, Data(stateJSON.utf8)) }
      return (200, Data(#"{"messages":[{"seq":7,"role":"assistant","text":"older","at":null}],"unread":0}"#.utf8))
    }
    let store = makeStore(label: "pending")
    await store.refresh()
    XCTAssertEqual(store.messages.map(\.text), ["older"])

    // Typed but not yet acknowledged: newest, despite having no sequence.
    StubProtocol.handler = { request in
      if request.url!.path.contains("state") { return (200, Data(stateJSON.utf8)) }
      if request.httpMethod == "POST" { return (200, Data(#"{"reply":"","spoke":true}"#.utf8)) }
      return (200, Data(#"{"messages":[{"seq":8,"role":"user","text":"hi there","at":null}],"unread":0}"#.utf8))
    }
    await store.send("hi there")
    XCTAssertEqual(store.messages.map(\.text), ["older", "hi there"], "order kept")
    XCTAssertEqual(store.messages.filter(\.pending).count, 0,
                   "the local echo is retired once the real row arrives")
  }

  func testOverdueSortsFirst() async {
    StubProtocol.handler = { request in
      request.url!.path.contains("state") ? (200, Data(stateJSON.utf8))
                                          : (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let store = makeStore(label: "sort")
    await store.refresh()
    XCTAssertEqual(store.state.sortedTasks.first?.id, "t1", "overdue before upcoming")
    XCTAssertEqual(store.state.overdueCount, 1)
  }

  func testMalformedServerPayloadDegradesInsteadOfBlanking() async {
    // A field the server has not shipped yet must not fail the whole decode.
    StubProtocol.handler = { request in
      request.url!.path.contains("state")
        ? (200, Data(#"{"tasks":[{"id":"t9","title":"Only an id and a title"}]}"#.utf8))
        : (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let store = makeStore(label: "partial")
    await store.refresh()
    XCTAssertEqual(store.state.sortedTasks.count, 1)
    XCTAssertEqual(store.state.sortedTasks[0].priority, "normal", "defaults fill in")
    XCTAssertFalse(store.state.ticking)
  }
}

// MARK: - the afternoon planner

private let plannerJSON = """
{"needed":true,"today_key":"2026-09-14","default_start":"18:00","warning_minutes":30,
 "overdue":[{"id":"mine","title":"SAT reading","subject":"","due":"2026-09-10",
             "days_overdue":4,"classroom":false},
            {"id":"cw","title":"HW 3","subject":"Math","due":"2026-09-12",
             "days_overdue":2,"classroom":true},
            {"id":"cw2","title":"Worksheet","subject":"Japanese","due":"2026-09-11",
             "classroom":true}],
 "today":[],
 "long_term":[{"id":"essay","title":"Essay draft","due":"2026-09-18","classroom":true}],
 "suggestions":[{"kind":"chem","title":"AP Chem homework","subject":"AP Chemistry",
                 "prompt":"Did AP Chem assign homework today?","default":true},
                {"kind":"lang","title":"Read p. 4-7","subject":"AP English Lang",
                 "default":true}]}
"""

@MainActor
final class PlannerTests: XCTestCase {
  override func setUp() { StubProtocol.reset() }

  private func payload() -> ArgonPlannerPayload {
    try! JSONDecoder().decode(ArgonPlannerPayload.self, from: Data(plannerJSON.utf8))
  }

  func testChemIsNeverPreTickedWhateverTheServerSays() {
    let chem = payload().suggestions.first { $0.kind == "chem" }!
    XCTAssertFalse(chem.isDefault, "on invents work; off would be the server's claim")
    XCTAssertTrue(payload().suggestions.first { $0.kind == "lang" }!.isDefault)
  }

  func testClassroomWorkCannotBeMovedAndOwnWorkCannotBeIgnored() {
    let items = Dictionary(uniqueKeysWithValues: payload().overdue.map { ($0.id, $0) })
    XCTAssertEqual(items["cw"]!.answers, [.stillToDo, .done, .notDoing])
    XCTAssertEqual(items["mine"]!.answers, [.doToday, .done, .skip])
  }

  func testEveryLateItemNeedsAnAnswer() {
    let p = payload()
    XCTAssertFalse(p.allAnswered(["mine": .done, "cw": .done]))
    XCTAssertTrue(p.allAnswered(["mine": .skip, "cw": .done, "cw2": .notDoing]))
  }

  func testTapsBecomeTheServersVocabulary() {
    let p = payload()
    let plan = ArgonPlanSubmission.from(
      payload: p, longTermPicked: ["essay"],
      answers: ["mine": .doToday, "cw": .stillToDo, "cw2": .notDoing],
      accepted: ["chem:AP Chem homework", "lang:Read p. 4-7"],
      extras: ["  Email counsellor ", " "], startAt: "19:30")
    XCTAssertEqual(plan.today, ["mine"])
    XCTAssertEqual(plan.ignore, ["cw2"])
    // Classroom "still to do" joins focus: its date is the teacher's.
    XCTAssertEqual(plan.focus, ["essay", "cw"])
    XCTAssertTrue(plan.chem)
    XCTAssertEqual(plan.add.map(\.title), ["Read p. 4-7", "Email counsellor"])
    XCTAssertEqual(plan.body["start_at"] as? String, "19:30")
    XCTAssertTrue(ArgonPlanSubmission().body["start_at"] is NSNull,
                  "an unset start is sent as null, so the default comes back")
  }

  func testRoutineDecodesAndKnowsItsTimes() throws {
    let state = try JSONDecoder().decode(ArgonState.self, from: Data("""
    {"tasks":[{"id":"essay","title":"Essay","due":"2099-01-01"},
              {"id":"late","title":"Late","due":"2000-01-01"}],
     "planner":{"due":true,"focus":["essay","late"]},
     "routine":{"start_at":"00:10","chosen":true,"window_minutes":90,
                "warning_minutes":30,"school_nights":[6,0,1,2,3]}}
    """.utf8))
    XCTAssertEqual(state.planner?.due, true)
    let routine = try XCTUnwrap(state.routine)
    XCTAssertEqual(routine.startMinutes, 10)
    XCTAssertEqual(routine.endMinutes, 100)
    XCTAssertEqual(routine.warning?.minutes, 23 * 60 + 40)
    XCTAssertEqual(routine.warning?.dayBefore, true)
    XCTAssertNil(ArgonRoutine.minutes("25:00"))
    // Picks are tonight's, whatever their dates say — and not also late.
    XCTAssertEqual(Set(state.tonight.map(\.id)), ["essay", "late"])
    XCTAssertTrue(state.overdue.isEmpty && state.future.isEmpty)
  }

  func testSubmittingPostsOnceAndDoesNotReopen() async {
    let suite = "argon-test-planner-\(UUID().uuidString)"
    let defaults = UserDefaults(suiteName: suite)!
    defer { defaults.removePersistentDomain(forName: suite) }
    let due = #"{"tasks":[{"id":"cw","title":"HW 3","due":"2000-01-01"}],"planner":{"due":true}}"#
    var posts: [[String: Any]] = []
    var keys: [String] = []
    StubProtocol.handler = { request in
      let path = request.url!.path
      if path.hasSuffix("v1/planner") && request.httpMethod == "POST" {
        let data = request.httpBodyStream.map(readAll) ?? Data()
        posts.append((try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:])
        keys.append(request.value(forHTTPHeaderField: "Idempotency-Key") ?? "")
        return (200, Data("{}".utf8))
      }
      if path.hasSuffix("v1/planner") { return (200, Data(plannerJSON.utf8)) }
      if path.contains("state") { return (200, Data(due.utf8)) }
      return (200, Data(#"{"messages":[],"unread":0}"#.utf8))
    }
    let client = ArgonClient(base: URL(string: "http://stub")!, token: "t",
                             session: StubProtocol.session)
    let store = ArgonStore(client: client,
                           outbox: ArgonOutbox(filename: tempName("plan-out")),
                           cache: ArgonCache(filename: tempName("plan-cache")),
                           defaults: defaults)
    await store.refresh()
    let sheet = await store.plannerIfDue()
    XCTAssertNotNil(sheet)

    await store.submitPlan(ArgonPlanSubmission(done: ["cw"], startAt: nil))
    XCTAssertEqual(posts.count, 1)
    XCTAssertEqual(posts.first?["done"] as? [String], ["cw"])
    XCTAssertFalse(keys.first?.isEmpty ?? true, "keyed, so a retry cannot add twice")
    // The stub still says due — as a server would with the plan stuck in the
    // outbox. The phone's own record keeps the sheet shut.
    await store.refresh()
    let again = await store.plannerIfDue()
    XCTAssertNil(again, "a submitted sheet must not reopen the same day")
  }
}
