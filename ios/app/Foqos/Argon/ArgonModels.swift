import Foundation

/// Receipt proving which server accepted the current APNs token.
///
/// A boolean survives server resets and address changes, so the phone can say
/// "already synced" while the active server has no device. The receipt is tied
/// to the destination without storing the API token itself.
enum ArgonRegistrationReceipt {
  static func needsUpload(storedReceipt: String?, serverIdentity: String) -> Bool {
    storedReceipt != serverIdentity
  }

  static func serverIdentity(base: String, token: String) -> String {
    // Stable FNV-1a is sufficient here: this is an invalidation fingerprint,
    // not authentication. The actual token remains only in its existing store.
    var hash: UInt64 = 14_695_981_039_346_656_037
    for byte in Data("\(base)\u{0}\(token)".utf8) {
      hash ^= UInt64(byte)
      hash &*= 1_099_511_628_211
    }
    return String(hash, radix: 16)
  }
}

/// Everything the server sends, decoded once.
///
/// Every field is optional-tolerant on purpose. The two halves of this system
/// have drifted before — the app called `/v1/messages` and `/v1/ios/read` for
/// months before either route existed — so a field the server has not shipped
/// yet must degrade to a default rather than failing the whole decode and
/// blanking the screen.

struct ArgonTask: Codable, Identifiable, Equatable, Hashable {
  let id: String
  var title: String
  var done: Bool
  var priority: String
  var source: String
  var subject: String?
  var due: String?
  var startedAt: String?

  enum CodingKeys: String, CodingKey {
    case id, title, done, priority, source, subject, due
    case startedAt = "started_at"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    id = try c.decode(String.self, forKey: .id)
    title = try c.decodeIfPresent(String.self, forKey: .title) ?? "(untitled)"
    done = try c.decodeIfPresent(Bool.self, forKey: .done) ?? false
    priority = try c.decodeIfPresent(String.self, forKey: .priority) ?? "normal"
    source = try c.decodeIfPresent(String.self, forKey: .source) ?? "argon"
    subject = try c.decodeIfPresent(String.self, forKey: .subject)
    due = try c.decodeIfPresent(String.self, forKey: .due)
    startedAt = try c.decodeIfPresent(String.self, forKey: .startedAt)
  }

  var isStarted: Bool { startedAt != nil }

  /// A task created on the phone, before the server has given it a real id.
  /// Carries a `local:` prefix so reconciliation can tell it apart from a row
  /// the server actually knows about.
  static func local(title: String, due: String?) -> ArgonTask {
    var task = try! JSONDecoder().decode(ArgonTask.self, from: Data("""
    {"id": "local:\(UUID().uuidString)", "title": "", "source": "app"}
    """.utf8))
    task.title = title
    task.due = due
    return task
  }

  var isLocal: Bool { id.hasPrefix("local:") }

  /// `overdue`, `today`, or a short date. Empty when there is no due date.
  func dueLabel(today: String = ArgonDate.today()) -> String {
    guard let due, due.count >= 10 else { return "" }
    let day = String(due.prefix(10))
    if day < today { return "overdue" }
    if day == today { return "today" }
    if day == ArgonDate.tomorrow(after: today) { return "tomorrow" }
    return ArgonDate.short(day)
  }

  /// Tonight's work: late, due today, or due tomorrow.
  ///
  /// Tomorrow counts because he does homework the evening before it is
  /// collected — filing "due tomorrow" with next week's reading hid the thing
  /// he was about to sit down to.
  var isTonight: Bool {
    ["overdue", "today", "tomorrow"].contains(dueLabel())
  }

  var isOverdue: Bool { dueLabel() == "overdue" }
}

struct ArgonMessage: Codable, Identifiable, Equatable {
  let seq: Int
  let role: String
  let text: String
  let at: String?

  /// Typed on the phone, not yet acknowledged by the server. Never decoded —
  /// the server has no opinion about it — and never written to the cache.
  var pending: Bool = false

  enum CodingKeys: String, CodingKey { case seq, role, text, at }

  /// Unique even before the server assigns a sequence: two pending messages
  /// both at seq 0 would collide as identifiers and SwiftUI would drop one.
  var id: String { pending ? "pending:\(localID)" : "seq:\(seq)" }

  private var localID: String { "\(at ?? "")\(text.prefix(40))" }

  var isFromArgon: Bool { role == "assistant" }
  var sentDate: Date? { at.flatMap(ArgonDate.parse) }

  /// A message shown before the server has numbered it.
  static func local(role: String, text: String) -> ArgonMessage {
    let json = """
    {"seq": 0, "role": "\(role)", "text": \(quote(text)),
     "at": "\(ISO8601DateFormatter().string(from: Date()))"}
    """
    var message = try! JSONDecoder().decode(ArgonMessage.self, from: Data(json.utf8))
    message.pending = true
    return message
  }

  private static func quote(_ s: String) -> String {
    String(data: try! JSONSerialization.data(withJSONObject: [s]), encoding: .utf8)!
      .dropFirst().dropLast().description
  }
}

struct ArgonSchool: Codable, Equatable {
  var schedule: String?
  var period: String?
}

struct ArgonBudget: Codable, Equatable {
  var spent: Double
  var cap: Double
  var cachedFraction: Double

  enum CodingKeys: String, CodingKey {
    case spent, cap
    case cachedFraction = "cached_fraction"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    spent = try c.decodeIfPresent(Double.self, forKey: .spent) ?? 0
    cap = try c.decodeIfPresent(Double.self, forKey: .cap) ?? 0
    cachedFraction = try c.decodeIfPresent(Double.self, forKey: .cachedFraction) ?? 0
  }

  var isNearCap: Bool { cap > 0 && spent / cap > 0.85 }
}

/// `GET /v2/state` — everything the app needs in one call.
/// A lock the server wants applied, as published state rather than a command.
///
/// `secondsLeft` is computed server-side, so a phone that wakes after the lock
/// lapsed releases instead of holding the shield up on a stale boolean.
struct ArgonLock: Codable, Equatable {
  var from: String
  var until: String
  var reason: String
  var active: Bool
  var startsInSeconds: Int
  var secondsLeft: Int
  /// Bumped whenever the desired lock changes. The phone reports back the
  /// version it applied, which is how the server tells "done" from "never
  /// heard" — v1's whole protocol, and the thing v2 had no answer for.
  var version: Int

  enum CodingKeys: String, CodingKey {
    case from, until, reason, active, version
    case startsInSeconds = "starts_in_seconds"
    case secondsLeft = "seconds_left"
  }

  /// Blocking right now.
  var isLive: Bool { active && secondsLeft > 0 }
  /// Booked, but not yet. The phone arms the window so iOS opens it on time
  /// rather than waiting for a push that may never arrive.
  var isScheduled: Bool { !active && secondsLeft > 0 }

  var startsAt: Date? { ArgonDate.parse(from) }
  var endsAt: Date? { ArgonDate.parse(until) }
}

/// The afternoon brief, as something he opens the app to read.
///
/// The channel message is the notification; this is the briefing. v1 queued it
/// in a mailbox the app collected and acknowledged, and dropping that is why it
/// stopped feeling like a brief — it became one more line to scroll past in the
/// chat thread.
struct ArgonBrief: Codable, Equatable {
  var text: String
  var at: String
  var acked: Bool
}

/// Whether the afternoon sheet has anything to ask today, and what he picked.
///
/// The cheap half of the planner, sent on every state read. The sheet itself
/// needs a Classroom read, so it is fetched only when `due` says so.
struct ArgonPlannerStatus: Codable, Equatable {
  var due: Bool
  var opensAfter: String
  var plannedFor: String?
  /// Task ids he chose for tonight without moving their dates — Classroom
  /// owns those, and a moved date is put back by the next sync.
  var focus: [String]

  enum CodingKeys: String, CodingKey {
    case due, focus
    case opensAfter = "opens_after"
    case plannedFor = "planned_for"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    due = try c.decodeIfPresent(Bool.self, forKey: .due) ?? false
    opensAfter = try c.decodeIfPresent(String.self, forKey: .opensAfter) ?? "15:36"
    plannedFor = try c.decodeIfPresent(String.self, forKey: .plannedFor)
    focus = try c.decodeIfPresent([String].self, forKey: .focus) ?? []
  }
}

/// Tonight's shape, as the server describes it.
///
/// The server does not decide *when*. It reports the time he chose in the
/// afternoon sheet — or the default he gets by not filling it in — and the
/// phone arms a `DeviceActivitySchedule` from it (`ArgonRoutineScheduler`),
/// so the block fires with the app closed and the server down.
/// The server's release: every block off until `until`. Versioned so the
/// phone applies each one once, and so a release it already applied is not
/// re-applied after he has chosen to block again.
struct ArgonRelease: Codable, Equatable {
  var until: String
  var version: Int

  var untilDate: Date? { ArgonDate.parse(until) }
}

struct ArgonRoutine: Codable, Equatable {
  /// "HH:MM". His answer if he gave one, otherwise `defaultStart`.
  var startAt: String
  /// Did he actually fill in the sheet today?
  var chosen: Bool
  var defaultStart: String
  var plannedToday: Bool
  /// Weekday numbers, Python-style: Monday 0 … Sunday 6. Sun–Thu by default.
  var schoolNights: [Int]
  var windowMinutes: Int
  var warningMinutes: Int

  enum CodingKeys: String, CodingKey {
    case startAt = "start_at"
    case chosen
    case defaultStart = "default_start"
    case plannedToday = "planned_today"
    case schoolNights = "school_nights"
    case windowMinutes = "window_minutes"
    case warningMinutes = "warning_minutes"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    defaultStart = try c.decodeIfPresent(String.self, forKey: .defaultStart) ?? "20:00"
    startAt = try c.decodeIfPresent(String.self, forKey: .startAt) ?? defaultStart
    chosen = try c.decodeIfPresent(Bool.self, forKey: .chosen) ?? false
    plannedToday = try c.decodeIfPresent(Bool.self, forKey: .plannedToday) ?? false
    schoolNights = try c.decodeIfPresent([Int].self, forKey: .schoolNights) ?? [6, 0, 1, 2, 3]
    windowMinutes = try c.decodeIfPresent(Int.self, forKey: .windowMinutes) ?? 90
    warningMinutes = try c.decodeIfPresent(Int.self, forKey: .warningMinutes) ?? 30
  }

  /// Minutes after midnight, or nil when the server sent something that is
  /// not HH:MM — arming a block at a guessed hour is worse than not arming.
  static func minutes(_ hhmm: String) -> Int? {
    let parts = hhmm.split(separator: ":")
    guard parts.count == 2, let hour = Int(parts[0]), let minute = Int(parts[1]),
          (0..<24).contains(hour), (0..<60).contains(minute)
    else { return nil }
    return hour * 60 + minute
  }

  var startMinutes: Int? { Self.minutes(startAt) }

  /// The block's end, wrapped past midnight.
  var endMinutes: Int? { startMinutes.map { ($0 + windowMinutes) % (24 * 60) } }

  /// When the heads-up lands, and whether that is the evening before (a
  /// start at 00:10 warns at 23:40 the previous day).
  var warning: (minutes: Int, dayBefore: Bool)? {
    guard let start = startMinutes else { return nil }
    let at = start - warningMinutes
    return at >= 0 ? (at, false) : (at + 24 * 60, true)
  }

  /// What was last armed, so an unchanged routine is a no-op. Re-arming is
  /// not free: `startMonitoring` tears the activity down and rebuilds it.
  func fingerprint(profile: String) -> String {
    "\(profile)|\(startAt)|\(windowMinutes)|\(warningMinutes)|\(schoolNights)"
  }
}

struct ArgonState: Codable, Equatable {
  var now: String?
  var school: ArgonSchool
  var ticking: Bool
  var tasks: [ArgonTask]
  var facts: [String]
  var unread: Int
  var budget: ArgonBudget?
  var lock: ArgonLock?
  var brief: ArgonBrief?
  var planner: ArgonPlannerStatus?
  var routine: ArgonRoutine?
  /// "Let me out" from the server: drop every block until `until`.
  var release: ArgonRelease?

  enum CodingKeys: String, CodingKey {
    case now, school, ticking, tasks, facts, unread, budget, lock, brief, planner, routine
    case release
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    now = try c.decodeIfPresent(String.self, forKey: .now)
    school = try c.decodeIfPresent(ArgonSchool.self, forKey: .school) ?? ArgonSchool()
    ticking = try c.decodeIfPresent(Bool.self, forKey: .ticking) ?? false
    tasks = try c.decodeIfPresent([ArgonTask].self, forKey: .tasks) ?? []
    facts = try c.decodeIfPresent([String].self, forKey: .facts) ?? []
    unread = try c.decodeIfPresent(Int.self, forKey: .unread) ?? 0
    budget = try c.decodeIfPresent(ArgonBudget.self, forKey: .budget)
    lock = try c.decodeIfPresent(ArgonLock.self, forKey: .lock)
    brief = try c.decodeIfPresent(ArgonBrief.self, forKey: .brief)
    // try? rather than decodeIfPresent: a malformed planner block is a missing
    // sheet, not a blank board.
    planner = try? c.decodeIfPresent(ArgonPlannerStatus.self, forKey: .planner)
    routine = try? c.decodeIfPresent(ArgonRoutine.self, forKey: .routine)
    release = try? c.decodeIfPresent(ArgonRelease.self, forKey: .release)
  }

  static let empty = try! JSONDecoder().decode(ArgonState.self,
                                               from: Data("{}".utf8))

  /// Overdue first, then today, then the rest — the order he reads them in.
  var sortedTasks: [ArgonTask] {
    let today = ArgonDate.today()
    return tasks.filter { !$0.done }.sorted { a, b in
      func rank(_ t: ArgonTask) -> Int {
        let label = t.dueLabel(today: today)
        return label == "overdue" ? 0 : label == "today" ? 1 : t.due == nil ? 3 : 2
      }
      if rank(a) != rank(b) { return rank(a) < rank(b) }
      return (a.due ?? "9999") < (b.due ?? "9999")
    }
  }

  var started: ArgonTask? { tasks.first { $0.isStarted && !$0.done } }
  var overdueCount: Int { overdue.count }

  /// Work that is still live: due today, or ahead, or undated.
  ///
  /// Split from the overdue pile because they answer different questions.
  /// With twenty-two items three weeks late, sorting everything by date put
  /// August at the top and tomorrow fifty rows down — the board was truthful
  /// and useless.
  var upcoming: [ArgonTask] { sortedTasks.filter { !$0.isOverdue } }

  /// Late work, most recent first: the newest miss is the one he might still
  /// rescue, and the August ones are archaeology.
  var overdue: [ArgonTask] {
    sortedTasks.filter { $0.isOverdue && !isFocus($0) }
      .sorted { ($0.due ?? "") > ($1.due ?? "") }
  }

  /// What he can still act on today, and what is real but not yet his problem.
  ///
  /// Tonight also holds what he picked in the afternoon sheet. Those keep
  /// their real dates — a Classroom date moved to today is put back by the
  /// next sync — so without this a pick would stay filed under next week or
  /// the late pile, and the sheet's answer would vanish from the screen.
  var tonight: [ArgonTask] {
    sortedTasks.filter { ($0.isTonight && !$0.isOverdue) || isFocus($0) }
  }
  var future: [ArgonTask] { upcoming.filter { !$0.isTonight && !isFocus($0) } }

  private func isFocus(_ task: ArgonTask) -> Bool {
    planner?.focus.contains(task.id) ?? false
  }
}

struct ArgonMessagesResponse: Codable {
  let messages: [ArgonMessage]
  let unread: Int
}

struct ArgonSayResponse: Codable {
  let reply: String
  let spoke: Bool
  let error: String?
}

/// Date handling in one place. The server always sends Pacific ISO8601.
enum ArgonDate {
  private static let iso: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
  }()

  private static let isoPlain: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f
  }()

  static func parse(_ raw: String) -> Date? {
    iso.date(from: raw) ?? isoPlain.date(from: raw)
  }

  static func today() -> String {
    dayString(Date())
  }

  /// The day after *today*, as a yyyy-MM-dd string.
  ///
  /// Via the calendar rather than by adding 86,400 seconds: a DST boundary
  /// makes one day 23 hours long, and "tomorrow" is a calendar question.
  static func tomorrow(after today: String = ArgonDate.today()) -> String {
    let f = formatter()
    guard let date = f.date(from: today),
          let next = Calendar(identifier: .gregorian).date(byAdding: .day,
                                                           value: 1, to: date)
    else { return today }
    return f.string(from: next)
  }

  /// "Fri Sep 18" from "2026-09-18". v1 showed the weekday; the bare "09-18"
  /// this replaced made him convert a date to a day in his head.
  static func short(_ day: String) -> String {
    guard let date = formatter().date(from: String(day.prefix(10))) else { return day }
    let f = formatter()
    f.dateFormat = "EEE MMM d"
    return f.string(from: date)
  }

  private static func dayString(_ date: Date) -> String {
    formatter().string(from: date)
  }

  private static func formatter() -> DateFormatter {
    let f = DateFormatter()
    f.calendar = Calendar(identifier: .gregorian)
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = TimeZone(identifier: "America/Los_Angeles")
    f.dateFormat = "yyyy-MM-dd"
    return f
  }
}
