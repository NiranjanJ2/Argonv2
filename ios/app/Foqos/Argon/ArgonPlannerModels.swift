import Foundation

/// The afternoon planning sheet: what `GET /v1/planner` sends, and what the
/// sheet's answers turn into. Pure Foundation, so the mapping from taps to a
/// submission is tested on the Mac rather than discovered on the phone.
///
/// Ported from v1's `ArgonPlannerModels`. The one real change is the answers a
/// late item can get: v1 had "still to do / done / skip" for everything, and
/// "still to do" moved the date to today. v2's Classroom sync owns Classroom
/// dates and puts them back within minutes, so Classroom work answers "still
/// to do" by joining tonight's focus instead, and gains "not doing" (the
/// ignored disposition, which the sync honours). Only his own tasks can move.

/// One thing the sheet can ask about.
struct ArgonPlannerItem: Codable, Identifiable, Equatable {
  let id: String
  let title: String
  let subject: String
  let due: String?
  let daysOverdue: Int?
  /// Classroom work: its date is the teacher's, and it can be ignored.
  let classroom: Bool

  enum CodingKeys: String, CodingKey {
    case id, title, subject, due, classroom
    case daysOverdue = "days_overdue"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    id = (try? c.decode(String.self, forKey: .id)) ?? UUID().uuidString
    title = (try? c.decode(String.self, forKey: .title)) ?? "(untitled)"
    subject = (try? c.decode(String.self, forKey: .subject)) ?? ""
    due = try? c.decode(String.self, forKey: .due)
    daysOverdue = try? c.decode(Int.self, forKey: .daysOverdue)
    classroom = (try? c.decode(Bool.self, forKey: .classroom)) ?? false
  }

  var staleness: String? {
    guard let daysOverdue, daysOverdue > 0 else { return nil }
    return daysOverdue == 1 ? "1 day late" : "\(daysOverdue) days late"
  }

  /// The answers this item can take, in the order they are shown.
  var answers: [ArgonOverdueAnswer] {
    classroom ? [.stillToDo, .done, .notDoing] : [.doToday, .done, .skip]
  }
}

/// What he said about one late item.
enum ArgonOverdueAnswer: String, Codable, Hashable {
  /// Classroom work he still owes: on tonight's list, date untouched.
  case stillToDo
  /// Finished. Classroom work gets the done disposition.
  case done
  /// Classroom work he is not doing. Stays gone across syncs.
  case notDoing
  /// His own task, due date moved to today.
  case doToday
  /// His own task, left as it is.
  case skip

  var label: String {
    switch self {
    case .stillToDo: return "Still to do"
    case .done: return "Done"
    case .notDoing: return "Not doing"
    case .doToday: return "Do today"
    case .skip: return "Later"
    }
  }
}

/// Something no source can see, that only he can confirm.
struct ArgonPlannerSuggestion: Codable, Identifiable, Equatable {
  let kind: String
  let title: String
  let subject: String?
  let prompt: String?
  let estimateMin: Int?
  /// Never true for Chem. Ticking it by default invents work; omitting it
  /// asserts a free night. Argon knows neither.
  let isDefault: Bool

  var id: String { "\(kind):\(title)" }

  enum CodingKeys: String, CodingKey {
    case kind, title, subject, prompt
    case estimateMin = "estimate_min"
    case isDefault = "default"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    kind = (try? c.decode(String.self, forKey: .kind)) ?? "other"
    title = (try? c.decode(String.self, forKey: .title)) ?? ""
    subject = try? c.decode(String.self, forKey: .subject)
    prompt = try? c.decode(String.self, forKey: .prompt)
    estimateMin = try? c.decode(Int.self, forKey: .estimateMin)
    // Chem is forced off here too, whatever the server says. It is the one
    // default whose failure mode — homework invented or a free night asserted
    // — he would not notice until it had already cost him an evening.
    let sent = (try? c.decode(Bool.self, forKey: .isDefault)) ?? false
    isDefault = kind == "chem" ? false : sent
  }
}

struct ArgonPlannerPayload: Codable, Equatable, Identifiable {
  let needed: Bool
  let lastPlanned: String?
  let todayKey: String
  let overdue: [ArgonPlannerItem]
  let today: [ArgonPlannerItem]
  let longTerm: [ArgonPlannerItem]
  let suggestions: [ArgonPlannerSuggestion]
  let startAt: String?
  let defaultStart: String
  let warningMinutes: Int

  enum CodingKeys: String, CodingKey {
    case needed, overdue, today, suggestions
    case lastPlanned = "last_planned"
    case todayKey = "today_key"
    case longTerm = "long_term"
    case startAt = "start_at"
    case defaultStart = "default_start"
    case warningMinutes = "warning_minutes"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    needed = (try? c.decode(Bool.self, forKey: .needed)) ?? false
    lastPlanned = try? c.decode(String.self, forKey: .lastPlanned)
    todayKey = (try? c.decode(String.self, forKey: .todayKey)) ?? ArgonDate.today()
    overdue = (try? c.decode([ArgonPlannerItem].self, forKey: .overdue)) ?? []
    today = (try? c.decode([ArgonPlannerItem].self, forKey: .today)) ?? []
    longTerm = (try? c.decode([ArgonPlannerItem].self, forKey: .longTerm)) ?? []
    suggestions = (try? c.decode([ArgonPlannerSuggestion].self, forKey: .suggestions)) ?? []
    startAt = try? c.decode(String.self, forKey: .startAt)
    defaultStart = (try? c.decode(String.self, forKey: .defaultStart)) ?? "18:00"
    warningMinutes = (try? c.decode(Int.self, forKey: .warningMinutes)) ?? 30
  }

  var id: String { todayKey }

  /// Nothing to decide, nothing shown: a sheet with no late work, no
  /// long-term work and no invisible-class prompts would be a dialog standing
  /// between him and his own task list.
  var hasAnythingToDecide: Bool {
    !overdue.isEmpty || !suggestions.isEmpty || !longTerm.isEmpty
  }

  /// Every late item has an answer. The one gate in the sheet.
  func allAnswered(_ answers: [String: ArgonOverdueAnswer]) -> Bool {
    overdue.allSatisfy { answers[$0.id] != nil }
  }
}

/// What the sheet sends, as one durable outbox write.
struct ArgonPlanSubmission: Codable, Equatable {
  struct Addition: Codable, Equatable {
    var title: String
    var subject: String?
  }

  var done: [String] = []
  var ignore: [String] = []
  var today: [String] = []
  var focus: [String] = []
  var add: [Addition] = []
  var chem = false
  /// "HH:MM", or nil to fall back to the default start.
  var startAt: String?

  /// Turn the sheet's taps into the server's vocabulary.
  static func from(
    payload: ArgonPlannerPayload,
    longTermPicked: Set<String>,
    answers: [String: ArgonOverdueAnswer],
    accepted: Set<String>,
    extras: [String],
    startAt: String?
  ) -> ArgonPlanSubmission {
    var plan = ArgonPlanSubmission(startAt: startAt)
    plan.focus = payload.longTerm.map(\.id).filter(longTermPicked.contains)
    for item in payload.overdue {
      switch answers[item.id] {
      case .done: plan.done.append(item.id)
      case .notDoing where item.classroom: plan.ignore.append(item.id)
      case .stillToDo where item.classroom: plan.focus.append(item.id)
      case .doToday where !item.classroom: plan.today.append(item.id)
      default: break
      }
    }
    for suggestion in payload.suggestions where accepted.contains(suggestion.id) {
      if suggestion.kind == "chem" {
        plan.chem = true
      } else {
        plan.add.append(Addition(title: suggestion.title, subject: suggestion.subject))
      }
    }
    plan.add += extras
      .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
      .filter { !$0.isEmpty }
      .map { Addition(title: $0, subject: nil) }
    return plan
  }

  /// The POST body. `start_at` is always present, as null when unset:
  /// clearing the time has to reset tonight's block, not leave it where it was.
  var body: [String: Any] {
    [
      "done": done, "ignore": ignore, "today": today, "focus": focus,
      "add": add.map { a -> [String: Any] in
        var row: [String: Any] = ["title": a.title]
        if let subject = a.subject { row["subject"] = subject }
        return row
      },
      "chem": chem,
      "start_at": startAt.map { $0 as Any } ?? NSNull(),
    ]
  }
}
