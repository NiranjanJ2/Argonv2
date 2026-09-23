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
    NavigationStack {
      VStack(spacing: 0) {
        progress
        ArgonDivider()

        Group {
          switch step {
          case 0: longTermStep
          case 1: overdueStep
          case 2: extrasStep
          default: dayStep
          }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)

        ArgonDivider()
        controls
      }
      .background(ArgonPalette.canvas.ignoresSafeArea())
      .navigationTitle(steps[min(step, steps.count - 1)])
      .navigationBarTitleDisplayMode(.inline)
    }
    .preferredColorScheme(.dark)
    .interactiveDismissDisabled(isSaving)
  }

  // MARK: - Chrome

  private var progress: some View {
    HStack(spacing: 6) {
      ForEach(0..<steps.count, id: \.self) { index in
        Capsule()
          .fill(index <= step ? Argon.accent : ArgonPalette.mutedInk.opacity(0.25))
          .frame(height: 3)
      }
    }
    .padding(.horizontal, 16)
    .padding(.vertical, 10)
  }

  private var controls: some View {
    HStack {
      if step > 0 {
        Button("Back") { withAnimation { step -= 1 } }
          .buttonStyle(ArgonSecondaryButtonStyle())
          .frame(maxWidth: 110)
      }
      Spacer()
      Button(step == steps.count - 1 ? (isSaving ? "Saving…" : "Start the day") : "Next") {
        if step == steps.count - 1 { save() } else { withAnimation { step += 1 } }
      }
      .buttonStyle(ArgonPrimaryButtonStyle())
      .disabled(isSaving || !canAdvance)
    }
    .padding(16)
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
        ForEach(payload.longTerm) { item in
          ArgonChoiceButton(
            title: item.title,
            caption: [item.subject, item.due.map(ArgonDate.short) ?? ""]
              .filter { !$0.isEmpty }.joined(separator: " · "),
            isSelected: longTermPicked.contains(item.id)
          ) { toggle(item.id, in: &longTermPicked) }
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
        ArgonChoiceButton(
          title: "All of it is done",
          caption: "Marks every item below done",
          isSelected: allDone
        ) {
          let on = !allDone
          for item in payload.overdue { answers[item.id] = on ? .done : nil }
        }

        ForEach(payload.overdue) { item in
          VStack(alignment: .leading, spacing: 6) {
            Text(item.title).font(.subheadline).foregroundStyle(ArgonPalette.ink)
            let caption = [item.subject, item.staleness ?? ""].filter { !$0.isEmpty }
            if !caption.isEmpty {
              Text(caption.joined(separator: " · "))
                .font(.caption2).foregroundStyle(ArgonPalette.warning)
            }
            HStack(spacing: 8) {
              ForEach(item.answers, id: \.self) { answer in
                answerButton(answer, for: item.id)
              }
            }
          }
          .padding(.vertical, 4)
        }
      }
    }
  }

  private func answerButton(_ answer: ArgonOverdueAnswer, for id: String) -> some View {
    let chosen = answers[id] == answer
    return Button {
      answers[id] = chosen ? nil : answer
    } label: {
      Text(answer.label)
        .font(.system(size: 13, weight: .medium))
        .foregroundStyle(chosen ? ArgonPalette.ink : ArgonPalette.mutedInk)
        .frame(maxWidth: .infinity)
        .frame(height: 44)
        .argonSelectable(chosen, cornerRadius: 10)
        .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
  }

  private var extrasStep: some View {
    stepBody(
      question: "Anything else on today?",
      note: "AP Chem is here because nothing else can see it — it is never assumed either way."
    ) {
      ForEach(payload.suggestions) { suggestion in
        ArgonChoiceButton(
          title: suggestion.kind == "chem"
            ? (suggestion.prompt ?? suggestion.title) : suggestion.title,
          caption: suggestion.kind == "chem"
            ? "Adds “\(suggestion.title)” for tonight"
            : (suggestion.prompt ?? suggestion.subject ?? ""),
          isSelected: accepted.contains(suggestion.id)
        ) { toggle(suggestion.id, in: &accepted) }
      }
      ForEach(extras, id: \.self) { title in
        Label(title, systemImage: "checkmark.circle.fill")
          .font(.subheadline)
          .foregroundStyle(Argon.running)
      }
      HStack {
        TextField("Something else", text: $newTitle)
          .submitLabel(.done)
          .onSubmit(addTyped)
        Button(action: addTyped) { Image(systemName: "plus.circle.fill") }
          .disabled(newTitle.trimmingCharacters(in: .whitespaces).isEmpty)
      }
      .padding(.top, 4)
    }
  }

  private var dayStep: some View {
    stepBody(
      question: "Here's your day. When do you want to start?",
      note: "You'll get a notification \(payload.warningMinutes) minutes before, and on a school night your phone locks down at that time. Not choosing means \(payload.defaultStart)."
    ) {
      ForEach(plannedTitles, id: \.self) { title in
        Label(title, systemImage: "circle")
          .font(.subheadline)
          .foregroundStyle(ArgonPalette.ink)
      }
      if plannedTitles.isEmpty {
        emptyLine("Nothing planned — a clear evening.")
      }

      ArgonDivider().padding(.vertical, 6)

      DatePicker("Start at", selection: $startAt, displayedComponents: .hourAndMinute)
        .datePickerStyle(.compact)
    }
  }

  // MARK: - Pieces

  private func stepBody<Content: View>(
    question: String, note: String, @ViewBuilder content: () -> Content
  ) -> some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 14) {
        Text(question)
          .font(.title3.weight(.semibold))
          .foregroundStyle(ArgonPalette.ink)
          .fixedSize(horizontal: false, vertical: true)
        Text(note)
          .font(.caption)
          .foregroundStyle(ArgonPalette.mutedInk)
          .fixedSize(horizontal: false, vertical: true)
        VStack(alignment: .leading, spacing: 10) { content() }
          .padding(.top, 4)
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(20)
    }
  }

  private func emptyLine(_ text: String) -> some View {
    Text(text).font(.subheadline).foregroundStyle(ArgonPalette.mutedInk)
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
