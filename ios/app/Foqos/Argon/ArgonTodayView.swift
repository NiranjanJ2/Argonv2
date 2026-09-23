import SwiftUI

/// What he opens the app to see: what's due, what he's on, and whether any of
/// it can be trusted right now.
///
/// A native inset-grouped list. `.swipeActions` only exists on List rows — the
/// ScrollView of cards this replaced had a Start swipe that compiled and did
/// nothing — and grouping does the job the glass cards were straining at.
struct ArgonTodayView: View {
  let store: ArgonStore
  @State private var newTask = ""
  @State private var adding = false
  @State private var showOverdue = false
  @State private var showFuture = false
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
    if let period = store.state.school.period { return "\(day), in \(period)" }
    return store.state.ticking ? day : "\(day). Argon is off until 4 PM."
  }

  // MARK: sections — a label row, then rows that together draw one box

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
      HStack(spacing: 12) {
        ArgonPulse()
        VStack(alignment: .leading, spacing: 2) {
          Text(started.title)
            .font(Argon.body.weight(.medium))
            .foregroundStyle(Argon.Tone.primary)
            // Unbounded, a real Codecademy title took five lines. Rows cap at two.
            .lineLimit(2)
          Text(runningDetail(started))
            .font(Argon.label).foregroundStyle(Argon.Tone.secondary)
        }
        Spacer(minLength: 8)
        // Stop and Done are different claims — "not on this now" and "this is
        // finished" — and only he can make either.
        Button("Stop") { Task { await store.stop(started) } }
          .foregroundStyle(Argon.Tone.secondary)
        Button("Done") { Task { await store.complete(started) } }
          .fontWeight(.semibold)
          .foregroundStyle(Argon.accent)
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
    return store.state.lock?.isLive == true ? "\(since), apps blocked" : since
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
    // v1: tap the row to start it. Starting also raises the shield, and Stop
    // on the card above takes it down again.
    .onTapGesture {
      guard !task.isStarted, !task.isLocal else { return }
      Task { await store.start(task) }
    }
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
        Button("Start", systemImage: "play.fill") { Task { await store.start(task) } }
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
