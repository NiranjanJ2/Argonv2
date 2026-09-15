import Foundation

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

  /// `overdue`, `today`, or a short date. Empty when there is no due date.
  func dueLabel(today: String = ArgonDate.today()) -> String {
    guard let due, due.count >= 10 else { return "" }
    let day = String(due.prefix(10))
    if day < today { return "overdue" }
    if day == today { return "today" }
    return String(day.dropFirst(5))
  }

  var isOverdue: Bool { dueLabel() == "overdue" }
}

struct ArgonMessage: Codable, Identifiable, Equatable {
  let seq: Int
  let role: String
  let text: String
  let at: String?

  var id: Int { seq }
  var isFromArgon: Bool { role == "assistant" }
  var sentDate: Date? { at.flatMap(ArgonDate.parse) }
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
struct ArgonState: Codable, Equatable {
  var now: String?
  var school: ArgonSchool
  var ticking: Bool
  var tasks: [ArgonTask]
  var facts: [String]
  var unread: Int
  var budget: ArgonBudget?

  enum CodingKeys: String, CodingKey {
    case now, school, ticking, tasks, facts, unread, budget
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
  var overdueCount: Int { tasks.filter { !$0.done && $0.isOverdue }.count }
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
    let f = DateFormatter()
    f.calendar = Calendar(identifier: .gregorian)
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = TimeZone(identifier: "America/Los_Angeles")
    f.dateFormat = "yyyy-MM-dd"
    return f.string(from: Date())
  }
}
