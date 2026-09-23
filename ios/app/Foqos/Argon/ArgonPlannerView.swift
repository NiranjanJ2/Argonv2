import SwiftUI

/// The afternoon planning sheet, as four questions rather than one form.
///
/// Argon used to assume anything past its due date was still outstanding and
/// had no way to learn otherwise, so finished work sat on the board for days
/// and got asked about every evening. This is where it asks instead.
///
/// Four steps rather than one screen because they are four different questions
/// and one of them — long-term work — loses every time it shares space with
/// something on fire. Asked first, before the deadlines are even visible, it
/// gets a fair hearing.
///
/// Ported from v1. The answers a late item can take depend on whose it is:
/// see `ArgonPlannerItem.answers` for why Classroom work cannot "do today".
struct ArgonPlannerView: View {
  let store: ArgonStore
  let payload: ArgonPlannerPayload
  @Environment(\.dismiss) private var dismiss

  @State private var step = 0
  @State private var longTermPicked: Set<String> = []
  @State private var answers: [String: ArgonOverdueAnswer] = [:]
  @State private var accepted: Set<String> = []
  @State private var extras: [String] = []
  @State private var newTitle = ""
  @State private var startAt = Date()
  @State private var isSaving = false

  init(store: ArgonStore, payload: ArgonPlannerPayload) {
    self.store = store
    self.payload = payload
    // Lang lines arrive pre-ticked (the teacher said so); Chem never does.
    _accepted = State(initialValue: Set(payload.suggestions.filter(\.isDefault).map(\.id)))
    let hhmm = ArgonRoutine.minutes(payload.startAt ?? payload.defaultStart) ?? 18 * 60
    _startAt = State(initialValue: Calendar.current.date(
      bySettingHour: hhmm / 60, minute: hhmm % 60, second: 0, of: Date()) ?? Date())
  }

  private let steps = ["Long-term", "Past due", "Anything else", "Your day"]

  var body: some View {
    VStack(spacing: 0) {
      header

      Group {
        switch step {
        case 0: longTermStep
        case 1: overdueStep
        case 2: extrasStep
        default: dayStep
        }
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity)

      controls
    }
    .argonAmbience()
    .tint(Argon.accent)
    .preferredColorScheme(.dark)
    .interactiveDismissDisabled(isSaving)
  }

  // MARK: - Chrome

  /// The ruler, then the step's name. The steps are a real sequence, so they
  /// are drawn as one: stations on a single construction line.
  private var header: some View {
    VStack(alignment: .leading, spacing: 14) {
      PlannerRuler(count: steps.count, at: step)
        .padding(.top, 22)
      Text(steps[min(step, steps.count - 1)])
        .font(Argon.screenTitle)
        .foregroundStyle(Argon.Tone.primary)
        .contentTransition(.opacity)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(.horizontal, Argon.margin)
  }

  private var controls: some View {
    HStack(spacing: 12) {
      if step > 0 {
        Button("Back") { withAnimation { step -= 1 } }
          .font(Argon.body)
          .foregroundStyle(Argon.Tone.secondary)
          .buttonStyle(.plain)
          .frame(minWidth: 64, minHeight: 48)
      }
      Button(step == steps.count - 1 ? (isSaving ? "Saving…" : "Start the day") : "Next") {
        if step == steps.count - 1 { save() } else { withAnimation { step += 1 } }
      }
      .buttonStyle(ArgonButtonStyle())
      .disabled(isSaving || !canAdvance)
      .opacity(isSaving || !canAdvance ? 0.4 : 1)
    }
    .padding(.horizontal, Argon.margin - 6)
    .padding(.vertical, 8)
    .overlay(alignment: .top) { ArgonDivider() }
  }

  /// The only gate: every late item gets an answer. "Later" is an answer; not
  /// looking is not.
  private var canAdvance: Bool {
    step != 1 || payload.allAnswered(answers)
  }

  // MARK: - Steps

  private var longTermStep: some View {
    stepBody(
      question: "What long-term work do you want to touch today?",
      note: "Nothing forces these, so they lose to whatever is due. Pick them first — they go on tonight's list and keep their real dates."
    ) {
      if payload.longTerm.isEmpty {
        emptyLine("No long-term work on the board.")
      } else {
        box {
          ForEach(Array(payload.longTerm.enumerated()), id: \.element.id) { i, item in
            if i > 0 { ArgonDivider() }
            choice(item.title, caption: item.subject,
                   due: item.due.map(ArgonDate.short),
                   on: longTermPicked.contains(item.id)) {
              toggle(item.id, in: &longTermPicked)
            }
          }
        }
      }
    }
  }

  private var overdueStep: some View {
    stepBody(
      question: "Which of these still actually exist?",
      note: "Argon has no way to know what you already finished. Done and Not doing take Classroom work off for good; its dates are the teacher's, so it can't be moved."
    ) {
      if payload.overdue.isEmpty {
        emptyLine("Nothing is past due.")
      } else {
        box {
          choice("All of it is done", caption: "Marks every item below done",
                 on: allDone) {
            let on = !allDone
            for item in payload.overdue { answers[item.id] = on ? .done : nil }
          }
        }

        box {
          ForEach(Array(payload.overdue.enumerated()), id: \.element.id) { i, item in
            if i > 0 { ArgonDivider() }
            VStack(alignment: .leading, spacing: 10) {
              HStack(alignment: .firstTextBaseline, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                  Text(item.title).font(Argon.body).foregroundStyle(Argon.Tone.primary)
                    .fixedSize(horizontal: false, vertical: true)
                  if !item.subject.isEmpty {
                    Text(item.subject).font(Argon.label).foregroundStyle(Argon.Tone.faint)
                  }
                }
                Spacer(minLength: 8)
                if let late = item.staleness {
                  // Red as a tint over red: the lateness, and nothing else.
                  ArgonPill(text: late, colour: Argon.overdue, tinted: true)
                }
              }
              AnswerSegments(options: item.answers, chosen: answers[item.id]) { answer in
                answers[item.id] = answers[item.id] == answer ? nil : answer
              }
            }
            .padding(.horizontal, 14).padding(.vertical, 14)
          }
        }
      }
    }
  }

  private var extrasStep: some View {
    stepBody(
      question: "Anything else on today?",
      note: "AP Chem is here because nothing else can see it — it is never assumed either way."
    ) {
      box {
        ForEach(payload.suggestions) { suggestion in
          choice(suggestion.kind == "chem"
                   ? (suggestion.prompt ?? suggestion.title) : suggestion.title,
                 caption: suggestion.kind == "chem"
                   ? "Adds “\(suggestion.title)” for tonight"
                   : (suggestion.prompt ?? suggestion.subject ?? ""),
                 on: accepted.contains(suggestion.id)) {
            toggle(suggestion.id, in: &accepted)
          }
          ArgonDivider()
        }
        ForEach(extras, id: \.self) { title in
          HStack(spacing: 12) {
            DraftCheck(on: true)
            Text(title).font(Argon.body).foregroundStyle(Argon.Tone.primary)
            Spacer(minLength: 0)
          }
          .padding(.horizontal, 14).padding(.vertical, 12)
          ArgonDivider()
        }
        addRow
      }
    }
  }

  private var addRow: some View {
    let empty = newTitle.trimmingCharacters(in: .whitespaces).isEmpty
    return HStack(spacing: 12) {
      Image(systemName: "plus")
        .font(.footnote.weight(.semibold))
        .foregroundStyle(Argon.accent)
        .frame(width: 18)
      TextField("", text: $newTitle,
                prompt: Text("Something else").foregroundStyle(Argon.Tone.faint))
        .font(Argon.body).foregroundStyle(Argon.Tone.primary)
        .submitLabel(.done)
        .onSubmit(addTyped)
      if !empty {
        Button("Add", action: addTyped)
          .font(Argon.detail.weight(.medium))
          .foregroundStyle(Argon.accent)
          .buttonStyle(.plain)
      }
    }
    .padding(.horizontal, 14).padding(.vertical, 12)
  }

  private var dayStep: some View {
    stepBody(
      question: "Here's your day. When do you want to start?",
      note: "You'll get a notification \(payload.warningMinutes) minutes before, and on a school night your phone locks down at that time. Not choosing means \(payload.defaultStart)."
    ) {
      caption("Tonight", count: plannedTitles.count)
      if plannedTitles.isEmpty {
        emptyLine("Nothing planned — a clear evening.")
      } else {
        box {
          ForEach(Array(plannedTitles.enumerated()), id: \.offset) { i, title in
            if i > 0 { ArgonDivider() }
            HStack(alignment: .firstTextBaseline, spacing: 12) {
              Text("\(i + 1)")
                .font(Argon.mono).foregroundStyle(Argon.Tone.faint)
                .frame(width: 18, alignment: .leading)
              Text(title).font(Argon.body).foregroundStyle(Argon.Tone.primary)
              Spacer(minLength: 0)
            }
            .padding(.horizontal, 14).padding(.vertical, 12)
          }
        }
      }

      caption("Start", count: nil).padding(.top, 6)
      box(stroke: Argon.accent.opacity(0.55)) {
        HStack {
          Text("Start at").font(Argon.body).foregroundStyle(Argon.Tone.primary)
          Spacer()
          DatePicker("Start at", selection: $startAt, displayedComponents: .hourAndMinute)
            .labelsHidden()
            .datePickerStyle(.compact)
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
      }
    }
  }

  // MARK: - Pieces

  private func stepBody<Content: View>(
    question: String, note: String, @ViewBuilder content: () -> Content
  ) -> some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 8) {
        VStack(alignment: .leading, spacing: 6) {
          Text(question)
            .font(Argon.heading)
            .foregroundStyle(Argon.Tone.primary)
            .fixedSize(horizontal: false, vertical: true)
          Text(note)
            .font(Argon.label)
            .foregroundStyle(Argon.Tone.faint)
            .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, Argon.overshoot)
        .padding(.bottom, 10)

        content()
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      // The boxes' vertical lines sit at `margin`; their overshoot lives in
      // the gap, as on every other screen.
      .padding(.horizontal, Argon.margin - Argon.overshoot)
      .padding(.top, 12)
      .padding(.bottom, 24)
    }
    .scrollIndicators(.hidden)
  }

  /// A draft box of rows. The rows draw their own rules between them.
  private func box<C: View>(stroke: Color = Argon.line,
                            @ViewBuilder _ content: () -> C) -> some View {
    ArgonGlass(padding: 0, stroke: stroke) {
      VStack(alignment: .leading, spacing: 0) { content() }
    }
  }

  /// A row that is a yes or no: the square checkbox, the words, maybe a date.
  private func choice(_ title: String, caption: String, due: String? = nil,
                      on: Bool, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      HStack(alignment: .firstTextBaseline, spacing: 12) {
        DraftCheck(on: on)
          .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 3 }
        VStack(alignment: .leading, spacing: 2) {
          Text(title).font(Argon.body).foregroundStyle(Argon.Tone.primary)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
          if !caption.isEmpty {
            Text(caption).font(Argon.label).foregroundStyle(Argon.Tone.faint)
              .multilineTextAlignment(.leading)
          }
        }
        Spacer(minLength: 8)
        if let due { ArgonPill(text: due, colour: Argon.Tone.faint) }
      }
      .padding(.horizontal, 14)
      .padding(.vertical, 12)
      .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
      .background(on ? Argon.accent.opacity(0.06) : .clear)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .accessibilityAddTraits(on ? .isSelected : [])
  }

  private func caption(_ title: String, count: Int?) -> some View {
    DraftSectionLabel(title: title, count: count).padding(.horizontal, Argon.overshoot)
  }

  private func emptyLine(_ text: String) -> some View {
    Text(text).font(Argon.body).foregroundStyle(Argon.Tone.faint)
      .padding(.horizontal, Argon.overshoot)
  }

  private var allDone: Bool {
    !payload.overdue.isEmpty && payload.overdue.allSatisfy { answers[$0.id] == .done }
  }

  /// What tonight looks like given the answers so far.
  private var plannedTitles: [String] {
    let stays: Set<ArgonOverdueAnswer> = [.stillToDo, .doToday]
    return payload.today.map(\.title)
      + payload.longTerm.filter { longTermPicked.contains($0.id) }.map(\.title)
      + payload.overdue.filter { answers[$0.id].map(stays.contains) ?? false }.map(\.title)
      + payload.suggestions.filter { accepted.contains($0.id) }.map(\.title)
      + extras
  }

  // MARK: - State

  private func toggle(_ id: String, in set: inout Set<String>) {
    if set.contains(id) { set.remove(id) } else { set.insert(id) }
  }

  private func addTyped() {
    let title = newTitle.trimmingCharacters(in: .whitespaces)
    guard !title.isEmpty else { return }
    extras.append(title)
    newTitle = ""
  }

  private func save() {
    isSaving = true
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "HH:mm"
    let plan = ArgonPlanSubmission.from(
      payload: payload, longTermPicked: longTermPicked, answers: answers,
      accepted: accepted, extras: extras, startAt: formatter.string(from: startAt))
    Task {
      // Durable: the outbox holds it through a dead network, and the store
      // records the submission locally so the sheet does not reopen meanwhile.
      await store.submitPlan(plan)
      isSaving = false
      dismiss()
    }
  }
}

// MARK: - drawing

/// Progress as a drafting rule: one line with a tick at each station. Done
/// stretches of the line and their ticks are blue; the current station stands
/// taller than the rest. The line overshoots the end ticks like every other
/// construction line in the app.
private struct PlannerRuler: View {
  let count: Int
  let at: Int

  var body: some View {
    GeometryReader { g in
      let o = Argon.overshoot, w = g.size.width, y: CGFloat = 8
      let gap = (w - 2 * o) / CGFloat(max(count - 1, 1))
      let x = { (i: Int) in o + CGFloat(i) * gap }
      ZStack(alignment: .topLeading) {
        Path { p in
          p.move(to: CGPoint(x: 0, y: y)); p.addLine(to: CGPoint(x: w, y: y))
          for i in (at + 1)..<max(count, at + 1) {
            p.move(to: CGPoint(x: x(i), y: y - 4)); p.addLine(to: CGPoint(x: x(i), y: y + 4))
          }
        }
        .stroke(Argon.lineStrong, lineWidth: 1)

        Path { p in
          p.move(to: CGPoint(x: 0, y: y)); p.addLine(to: CGPoint(x: x(at), y: y))
          for i in 0..<at {
            p.move(to: CGPoint(x: x(i), y: y - 4)); p.addLine(to: CGPoint(x: x(i), y: y + 4))
          }
          p.move(to: CGPoint(x: x(at), y: y - 8)); p.addLine(to: CGPoint(x: x(at), y: y + 8))
        }
        .stroke(Argon.accent, lineWidth: 1)

        ForEach(0..<count, id: \.self) { i in
          Text("\(i + 1)")
            .font(Argon.label.monospacedDigit())
            .foregroundStyle(i == at ? Argon.accent : Argon.Tone.faint)
            .fixedSize()
            .position(x: x(i), y: y + 20)
        }
      }
    }
    .frame(height: 36)
    .animation(.snappy(duration: 0.25), value: at)
    .accessibilityElement()
    .accessibilityLabel("Step \(at + 1) of \(count)")
  }
}

/// The three answers a late item can take, as one outlined bar split in
/// three. The chosen third is outlined and washed in blue.
private struct AnswerSegments: View {
  let options: [ArgonOverdueAnswer]
  let chosen: ArgonOverdueAnswer?
  let pick: (ArgonOverdueAnswer) -> Void

  var body: some View {
    HStack(spacing: 0) {
      ForEach(Array(options.enumerated()), id: \.element) { i, answer in
        let on = chosen == answer
        if i > 0 { Rectangle().fill(Argon.line).frame(width: 1) }
        Button { pick(answer) } label: {
          Text(answer.label)
            .font(Argon.detail.weight(on ? .medium : .regular))
            .foregroundStyle(on ? Argon.accent : Argon.Tone.secondary)
            .lineLimit(1).minimumScaleFactor(0.8)
            .frame(maxWidth: .infinity, minHeight: 40)
            .background(on ? Argon.accent.opacity(0.14) : .clear)
            .overlay(Rectangle().strokeBorder(on ? Argon.accent.opacity(0.7) : .clear,
                                              lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
      }
    }
    .fixedSize(horizontal: false, vertical: true)
    .overlay(Rectangle().strokeBorder(Argon.line, lineWidth: 1))
  }
}
