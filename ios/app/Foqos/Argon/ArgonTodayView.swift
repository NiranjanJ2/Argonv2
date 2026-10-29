import SwiftUI
import SwiftData

/// What he opens the app to see: what's due, what he's on, and whether any of
/// it can be trusted right now.
///
/// A native inset-grouped list. `.swipeActions` only exists on List rows — the
/// ScrollView of cards this replaced had a Start swipe that compiled and did
/// nothing — and grouping does the job the glass cards were straining at.
struct ArgonTodayView: View {
  let store: ArgonStore
  var adjustPlan: () -> Void = {}
  @Environment(\.modelContext) private var context
  @State private var newTask = ""
  @State private var adding = false
  @State private var showOverdue = false
  @State private var showFuture = false
  @State private var replanning = false
  @State private var extending = false
  @State private var adjustmentResult: String?
  @AppStorage("argon.rest.until") private var restTimestamp = 0.0
  @FocusState private var addFocused: Bool

  var body: some View {
    List {
      header.draftLabelRow(top: 6)

      if let failure = store.failure {
        // Red as a tint over red: the box carries the wash, the text the colour.
        ArgonFailureRow(text: failure) { store.dismissFailure() }
          .padding(.vertical, 10)
          .draftRow(.single, stroke: Argon.overdue.opacity(0.45),
                    fill: Argon.overdue.opacity(0.10))
      }
      briefRows
      runningRows
      nextStepRows
      boardRows
      overdueRows
      futureRows
    }
    .listStyle(.plain)
    .scrollContentBackground(.hidden)
    .environment(\.defaultMinListRowHeight, 0)
    // Clears the floating tab bar.
    .contentMargins(.bottom, 96, for: .scrollContent)
    .refreshable { await store.refresh() }
    .argonAmbience()
    .animation(.snappy(duration: 0.25), value: store.state.tasks)
  }

  // MARK: header

  private var header: some View {
    VStack(alignment: .leading, spacing: 4) {
      HStack(alignment: .firstTextBaseline) {
        Text("Today").font(Argon.screenTitle).foregroundStyle(Argon.Tone.primary)
        Spacer()
        ArgonStatusDot(store: store)
      }
      Text(subtitle).font(Argon.detail).foregroundStyle(Argon.Tone.secondary)
    }
  }

  /// The day and — outside 4 PM to midnight on school nights — that Argon is
  /// not watching. That used to be a whole card with a moon on it.
  private var subtitle: String {
    let day = Date().formatted(.dateTime.weekday(.wide).month(.wide).day())
    // A release he asked for is the first thing he'd want confirmed.
    if let until = store.state.release?.untilDate, until > Date() {
      return "\(day). Unblocked until \(until.formatted(date: .omitted, time: .shortened))."
    }
    if let period = store.state.school.period { return "\(day), in \(period)" }
    return store.state.ticking ? day : "\(day). Argon is off until 4 PM."
  }

  // MARK: sections — a label row, then rows that together draw one box

  @ViewBuilder private var nextStepRows: some View {
    if restTimestamp > Date().timeIntervalSince1970 {
      let until = Date(timeIntervalSince1970: restTimestamp)
      label("Taking a break")
      HStack {
        Text(store.pendingCount > 0
             ? "Pause waiting to sync."
             : "Argon is paused until \(until.formatted(date: .omitted, time: .shortened)).")
          .foregroundStyle(Argon.Tone.primary)
        Spacer()
        Button("Resume") { resume() }.foregroundStyle(Argon.accent)
      }
      .font(Argon.detail).buttonStyle(.plain)
      .padding(.vertical, 12).draftRow(.single, stroke: Argon.lineStrong)
    } else if store.state.started == nil, let task = nextTask {
      label("Next")
      VStack(alignment: .leading, spacing: 12) {
        Text(task.title).font(Argon.body.weight(.medium))
          .foregroundStyle(Argon.Tone.primary)
        if let start = plannedStart {
          Text("Planned for \(start)")
            .font(Argon.label).foregroundStyle(Argon.Tone.secondary)
        }
        HStack(spacing: 10) {
          Button { Task { await store.start(task) } } label: {
            Label("Start 20 min", systemImage: "play.fill")
              .frame(maxWidth: .infinity, minHeight: 44)
          }
          .foregroundStyle(Argon.Ink.base)
          .background(Argon.Tone.primary)
          adjustmentMenu
        }
        .font(Argon.detail.weight(.medium)).buttonStyle(.plain)
        if replanning {
          Text("Argon is adjusting tonight…")
            .font(Argon.label).foregroundStyle(Argon.Tone.secondary)
        } else if let adjustmentResult {
          Text(adjustmentResult)
            .font(Argon.detail).foregroundStyle(Argon.Tone.secondary)
            .fixedSize(horizontal: false, vertical: true)
        } else if store.pendingCount > 0 {
          Text("Changes waiting for connection")
            .font(Argon.label).foregroundStyle(Argon.Tone.secondary)
        }
      }
      .padding(.vertical, 12)
      .draftRow(.single, stroke: Argon.lineStrong)
    } else if store.state.started == nil {
      HStack {
        Button("Make a plan", action: adjustPlan).foregroundStyle(Argon.accent)
        Spacer()
        adjustmentMenu
      }
      .font(Argon.body).buttonStyle(.plain)
      .padding(.vertical, 12).draftRow(.single, stroke: Argon.lineStrong)
    }
  }

  private var nextTask: ArgonTask? {
    let available = store.state.tonight.filter { !$0.isLocal && !$0.isStarted }
    for id in store.state.planner?.focus ?? [] {
      if let task = available.first(where: { $0.id == id }) { return task }
    }
    return available.first
  }

  private var adjustmentMenu: some View {
    Menu {
      Button("Low energy") {
        replan("I'm low on energy. Adjust tonight's focus and start time to a manageable plan, using my tasks and calendar. Don't ask me to fill in a form.")
      }
      Button("Nap for an hour") { nap() }
      Button("Not tonight") { restTonight() }
      Button("Full plan", action: adjustPlan)
    } label: {
      Text("Adjust").frame(minWidth: 82, minHeight: 44)
        .overlay(Rectangle().strokeBorder(Argon.lineStrong, lineWidth: 1))
    }
    .foregroundStyle(Argon.Tone.secondary)
    .disabled(replanning)
  }

  private func restTonight() {
    guard let until = Calendar.current.nextDate(after: Date(), matching: DateComponents(hour: 7),
                                                 matchingPolicy: .nextTime) else { return }
    ArgonLockReconciler.restLocally(until: until, context: context)
    restTimestamp = until.timeIntervalSince1970
    Task { await store.rest(until: until) }
  }

  private func nap() {
    let seconds = ceil((Date().timeIntervalSince1970 + 3600) / 60) * 60
    let until = Date(timeIntervalSince1970: seconds)
    ArgonLockReconciler.restLocally(until: until, context: context)
    restTimestamp = seconds
    Task { await store.rest(until: until, planAfter: true) }
  }

  private func resume() {
    let until = Date(timeIntervalSince1970: restTimestamp)
    ArgonOverride.clear()
    restTimestamp = 0
    Task { await store.resume(until: until) }
  }

  private var plannedStart: String? {
    guard let routine = store.state.routine, routine.chosen,
          let minutes = routine.startMinutes,
          let date = Calendar.current.date(bySettingHour: minutes / 60,
                                           minute: minutes % 60, second: 0, of: Date())
    else { return nil }
    return date.formatted(date: .omitted, time: .shortened)
  }

  private func replan(_ message: String) {
    guard !replanning else { return }
    replanning = true
    adjustmentResult = nil
    let previous = store.messages.filter { $0.isFromArgon }.map(\.seq).max() ?? 0
    Task {
      await store.send(message)
      await store.refresh()
      adjustmentResult = store.messages.last(where: { $0.isFromArgon && $0.seq > previous })?.text
        ?? (store.pendingCount > 0 ? "Waiting for connection." : "Argon hasn't replied yet.")
      replanning = false
    }
  }

  @ViewBuilder private var briefRows: some View {
    if let brief = store.state.brief, !brief.acked,
       !brief.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
      label(ArgonDate.parse(brief.at)
        .map { "Brief at \($0.formatted(date: .omitted, time: .shortened))" } ?? "Brief")
      VStack(alignment: .leading, spacing: 12) {
        Text(brief.text)
          .font(Argon.body).foregroundStyle(Argon.Tone.primary)
          .fixedSize(horizontal: false, vertical: true)
          .textSelection(.enabled)
        Button("Dismiss") { Task { await store.ackBrief() } }
          .font(Argon.detail.weight(.medium))
          .foregroundStyle(Argon.accent)
          .buttonStyle(.plain)
      }
      .padding(.vertical, 12)
      .draftRow(.single, stroke: Argon.lineStrong)
    }
  }

  @ViewBuilder private var runningRows: some View {
    if let started = store.state.started {
      label("Working on")
      VStack(alignment: .leading, spacing: 12) {
        HStack(alignment: .top, spacing: 12) {
          ArgonPulse().padding(.top, 6)
          VStack(alignment: .leading, spacing: 2) {
          Text(started.title)
            .font(Argon.body.weight(.medium))
            .foregroundStyle(Argon.Tone.primary)
            .lineLimit(2)
          Text(runningDetail(started))
            .font(Argon.label).foregroundStyle(Argon.Tone.secondary)
          }
        }
        HStack(spacing: 18) {
          Button(extending ? "Extending…" : "20 more min") {
            extending = true
            Task { await store.extend(started); extending = false }
          }.disabled(extending).foregroundStyle(Argon.accent)
          Button("Stop") { Task { await store.stop(started) } }
            .foregroundStyle(Argon.Tone.secondary)
          Button("Done") { Task { await store.complete(started) } }
            .fontWeight(.semibold).foregroundStyle(Argon.accent)
        }
        .frame(minHeight: 44)
      }
      .font(Argon.detail)
      .buttonStyle(.plain)
      .padding(.vertical, 12)
      .draftRow(.single, stroke: Argon.accent.opacity(0.55), fill: Argon.accent.opacity(0.06))
    }
  }

  private func runningDetail(_ task: ArgonTask) -> String {
    let since = task.startedAt.flatMap(ArgonDate.parse)
      .map { "Since \($0.formatted(date: .omitted, time: .shortened))" } ?? "Running"
    if let lock = store.state.lock, lock.isLive, let end = lock.endsAt {
      return "\(since), apps blocked until \(end.formatted(date: .omitted, time: .shortened))"
    }
    return since
  }

  @ViewBuilder private var boardRows: some View {
    // The running task is shown above; repeating it here cost a row.
    let tasks = store.state.tonight.filter { !$0.isStarted }
    let count = tasks.count + (tasks.isEmpty ? 2 : 1)   // + empty line, + add row
    label("Due", count: store.state.tonight.count)
    ForEach(Array(tasks.enumerated()), id: \.element.id) { i, task in
      ArgonTaskRow(task: task, store: store).draftRow(.of(i, in: count))
    }
    if tasks.isEmpty {
      Text(store.connection.isLive ? "Nothing due tonight" : "Nothing cached")
        .font(Argon.body).foregroundStyle(Argon.Tone.faint)
        .padding(.vertical, 12)
        .draftRow(.of(0, in: count))
    }
    addRow.draftRow(.of(count - 1, in: count))
  }

  private var addRow: some View {
    HStack(spacing: 12) {
      Image(systemName: "plus")
        .font(.footnote.weight(.semibold))
        .foregroundStyle(Argon.accent)
        .frame(width: 18)
      if adding {
        TextField("", text: $newTask, prompt:
                    Text("New task for tonight").foregroundStyle(Argon.Tone.faint))
          .font(Argon.body).foregroundStyle(Argon.Tone.primary)
          .focused($addFocused).submitLabel(.done).onSubmit(commit)
      } else {
        Text("Add a task").font(Argon.body).foregroundStyle(Argon.accent)
        Spacer()
      }
    }
    .padding(.vertical, 12)
    .contentShape(Rectangle())
    .onTapGesture {
      guard !adding else { return }
      adding = true
      addFocused = true
    }
  }

  /// Late work, folded behind a count. Every row red on the main board made
  /// every row look equally urgent, which is the same as none of them being.
  @ViewBuilder private var overdueRows: some View {
    let late = store.state.overdue
    if !late.isEmpty {
      fold("Late", count: late.count, tint: Argon.overdue, open: $showOverdue)
      if showOverdue {
        ForEach(Array(late.enumerated()), id: \.element.id) { i, task in
          ArgonTaskRow(task: task, store: store, showDue: true)
            .draftRow(.of(i, in: late.count))
        }
      }
    }
  }

  /// Work that is real but not yet tonight's, folded with the count shown.
  @ViewBuilder private var futureRows: some View {
    let later = store.state.future
    if !later.isEmpty {
      fold("Later", count: later.count, tint: nil, open: $showFuture)
      if showFuture {
        ForEach(Array(later.enumerated()), id: \.element.id) { i, task in
          ArgonTaskRow(task: task, store: store, showDue: true)
            .draftRow(.of(i, in: later.count))
        }
      }
    }
  }

  private func label(_ title: String, count: Int? = nil) -> some View {
    HStack(spacing: 8) {
      Text(title).foregroundStyle(Argon.Tone.secondary)
      if let count, count > 0 { Text("\(count)").foregroundStyle(Argon.Tone.faint) }
    }
    .font(Argon.caption)
    .draftLabelRow()
  }

  private func fold(_ title: String, count: Int, tint: Color?,
                    open: Binding<Bool>) -> some View {
    Button {
      withAnimation(.snappy(duration: 0.22)) { open.wrappedValue.toggle() }
    } label: {
      HStack(spacing: 8) {
        Text(title).foregroundStyle(Argon.Tone.secondary)
        if let tint {
          ArgonPill(text: "\(count)", colour: tint, tinted: true)
        } else {
          Text("\(count)").foregroundStyle(Argon.Tone.faint)
        }
        Spacer()
        Image(systemName: "chevron.right")
          .font(.caption2.weight(.semibold))
          .foregroundStyle(Argon.Tone.faint)
          .rotationEffect(.degrees(open.wrappedValue ? 90 : 0))
      }
      .font(Argon.caption)
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .accessibilityLabel("\(title), \(count) items")
    .draftLabelRow()
  }

  private func commit() {
    let title = newTask
    newTask = ""
    adding = false
    // Typed under "Due", so due tonight. Undated, it filed itself under the
    // folded Later section and looked like it had not been added.
    Task { await store.add(title: title, due: ArgonDate.today()) }
  }
}

// MARK: - rows

struct ArgonTaskRow: View {
  let task: ArgonTask
  let store: ArgonStore
  /// Inside the Late and Future sections the date is what varies.
  var showDue = false

  /// Only his own tasks can be moved. The Classroom sync owns an assignment's
  /// date and would put it back within minutes, which reads as the swipe
  /// failing.
  private var movable: Bool { !task.isLocal && task.source != "classroom" }

  var body: some View {
    HStack(spacing: 12) {
      Button { Task { await store.complete(task) } } label: {
        DraftCheck(on: task.done)
          // The square is 18pt; the hit area is v1's 44.
          .contentShape(Rectangle().inset(by: -13))
      }
      .buttonStyle(.plain)
      .disabled(task.isLocal)
      .accessibilityLabel("Complete \(task.title)")

      VStack(alignment: .leading, spacing: 2) {
        Text(task.title)
          .font(Argon.body)
          .foregroundStyle(task.isLocal ? Argon.Tone.faint : Argon.Tone.primary)
          .strikethrough(task.done, color: Argon.Tone.faint)
          .lineLimit(2)
          .fixedSize(horizontal: false, vertical: true)
        if let subject = task.subject, !subject.isEmpty {
          Text(subject).font(Argon.label).foregroundStyle(Argon.Tone.faint)
            .lineLimit(1).truncationMode(.tail)
        } else if task.isLocal {
          Text("saving…").font(Argon.label).foregroundStyle(Argon.Tone.faint)
        }
      }

      Spacer(minLength: 8)

      if task.isStarted { ArgonPulse() }
      let label = showDue ? (task.due.map(ArgonDate.short) ?? "") : task.dueLabel()
      if !label.isEmpty {
        ArgonPill(text: label,
                  colour: showDue ? Argon.Tone.faint
                        : label == "overdue" ? Argon.overdue
                        : label == "today" ? Argon.accent : Argon.Tone.faint,
                  tinted: !showDue && (label == "overdue" || label == "today"))
      }
    }
    .padding(.vertical, 12)
    .contentShape(Rectangle())
    // His muscle memory from v1: swipe left completes, swipe right defers.
    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
      if !task.isLocal {
        Button { Task { await store.complete(task) } } label: {
          Label("Done", systemImage: "checkmark.circle.fill")
        }.tint(Argon.accent)
      }
      if movable {
        Button { Task { await store.move(task, to: ArgonDate.tomorrow()) } } label: {
          Label("Tomorrow", systemImage: "moon.zzz.fill")
        }.tint(Argon.Tone.faint)
      }
    }
    .swipeActions(edge: .leading, allowsFullSwipe: true) {
      if movable {
        Button { Task { await store.move(task, to: ArgonDate.tomorrow()) } } label: {
          Label("Tomorrow", systemImage: "moon.zzz.fill")
        }.tint(Argon.Tone.faint)
      }
    }
    .contextMenu {
      if !task.isStarted && !task.isLocal {
        Button("Start 20 min", systemImage: "play.fill") { Task { await store.start(task) } }
      }
      if movable {
        Button("Due tomorrow", systemImage: "calendar.badge.clock") {
          Task { await store.move(task, to: ArgonDate.tomorrow()) }
        }
      }
      if !task.isLocal {
        Button("Complete", systemImage: "checkmark.circle") {
          Task { await store.complete(task) }
        }
      }
    }
  }
}

/// Says where the numbers came from. An app that silently shows old data is
/// worse than one that admits it.
struct ArgonStatusDot: View {
  let store: ArgonStore

  var body: some View {
    HStack(spacing: 6) {
      if store.pendingCount > 0 {
        Text("\(store.pendingCount) queued")
      }
      if store.isLoading {
        ProgressView().controlSize(.mini)
      } else {
        switch store.connection {
        case .never: Text("offline")
        case .live: SwiftUI.EmptyView()   // live is the normal case; say nothing
        case .stale(let at, _): Text("updated \(at.argonAgo)")
        }
      }
    }
    .font(Argon.label)
    .foregroundStyle(Argon.Tone.faint)
  }
}

/// Something failed and he should know. A row, not a red card.
struct ArgonFailureRow: View {
  let text: String
  let dismiss: () -> Void

  var body: some View {
    HStack(alignment: .firstTextBaseline, spacing: 10) {
      Image(systemName: "exclamationmark.circle").foregroundStyle(Argon.overdue)
      Text(text).foregroundStyle(Argon.Tone.primary)
      Spacer(minLength: 4)
      Button("Dismiss", action: dismiss)
        .foregroundStyle(Argon.accent).buttonStyle(.plain)
    }
    .font(Argon.detail)
    .padding(.vertical, 4)
  }
}
