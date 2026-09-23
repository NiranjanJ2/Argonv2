import SwiftUI

/// What he opens the app to see: what's due, what he's on, and whether any of
/// it can be trusted right now.
struct ArgonTodayView: View {
  let store: ArgonStore
  @State private var newTask = ""
  @State private var adding = false
  @State private var showOverdue = false
  @State private var showFuture = false
  @FocusState private var addFocused: Bool

  var body: some View {
    // A List, not a ScrollView of cards: `.swipeActions` only exists on List
    // rows. Inside the old ScrollView the Start swipe compiled and did nothing,
    // so the phone had no way to start a task at all — every start in the
    // transcript came from the Mac widget.
    List {
      Group {
        header
        if let failure = store.failure {
          ArgonFailureCard(text: failure) { store.dismissFailure() }
        }
        // The brief first. It is the one thing on this screen written for him
        // today, and it sat under the board and the late pile.
        briefCard
        statusCard
      }
      .argonCardRow()

      boardSection
      overdueSection
      futureSection
    }
    .listStyle(.plain)
    .scrollContentBackground(.hidden)
    .scrollIndicators(.hidden)
    .environment(\.defaultMinListRowHeight, 0)
    // Clears the floating tab bar.
    .contentMargins(.bottom, 96, for: .scrollContent)
    .refreshable { await store.refresh() }
    .argonAmbience(ticking: store.state.ticking)
    .animation(.spring(duration: 0.35), value: store.state.tasks)
  }

  // MARK: header

  private var header: some View {
    VStack(alignment: .leading, spacing: 6) {
      HStack(alignment: .firstTextBaseline) {
        Text("Today")
          .font(Argon.screenTitle)
          .foregroundStyle(Argon.Tone.primary)
          .background(alignment: .leading) {
            // The screen has a source of light, off behind the title.
            ArgonBloom(size: 300, opacity: store.state.ticking ? 0.5 : 0.18)
              .offset(x: -60, y: -30)
          }
        Spacer()
        ArgonStatusDot(store: store)
      }
      Text(subtitle)
        .font(Argon.detail)
        .foregroundStyle(Argon.Tone.secondary)
    }
    .padding(.top, 10)
  }

  private var subtitle: String {
    if let period = store.state.school.period { return "In \(period)" }
    if let schedule = store.state.school.schedule { return schedule }
    return Date().formatted(date: .complete, time: .omitted)
  }

  // MARK: cards

  @ViewBuilder private var statusCard: some View {
    if let started = store.state.started {
      ArgonGlass(tint: Argon.running) {
        HStack(spacing: 14) {
          ArgonPulse()
          VStack(alignment: .leading, spacing: 3) {
            Text(started.title)
              .font(Argon.cardTitle)
              .foregroundStyle(Argon.Tone.primary)
              // Unbounded, a real Codecademy title took five lines at default
              // and eight at the largest text size, pushing the board off the
              // fold. Every other row caps at two; so does this.
              .lineLimit(2)
            if let since = started.startedAt.flatMap(ArgonDate.parse) {
              Text("working since \(since.formatted(date: .omitted, time: .shortened))")
                .font(Argon.detail)
                .foregroundStyle(Argon.Tone.secondary)
            }
            if store.state.lock?.isLive == true {
              Label("Apps blocked", systemImage: "shield.lefthalf.filled")
                .font(Argon.label)
                .foregroundStyle(Argon.accentSoft)
            }
          }
          Spacer(minLength: 8)
          // Stop and Done are different claims — "not working on this now" and
          // "this is finished" — and only he can make either. With Done alone
          // the only way out of a started task was to declare it complete.
          VStack(spacing: 10) {
            Button("Done") { Task { await store.complete(started) } }
              .font(Argon.heading)
              .foregroundStyle(Argon.running)
            Button("Stop") { Task { await store.stop(started) } }
              .font(Argon.detail)
              .foregroundStyle(Argon.Tone.secondary)
          }
          .buttonStyle(.plain)
        }
      }
    } else if !store.state.ticking {
      ArgonGlass(tint: Argon.accentDeep) {
        HStack(spacing: 14) {
          Image(systemName: "moon.stars.fill")
            .font(.title2)
            .foregroundStyle(Argon.accentSoft)
          VStack(alignment: .leading, spacing: 2) {
            Text("Off duty").font(Argon.heading).foregroundStyle(Argon.Tone.primary)
            Text("Argon watches 4pm to midnight on school nights")
              .font(Argon.detail).foregroundStyle(Argon.Tone.secondary)
          }
        }
      }
    }
  }

  private var boardSection: some View {
    Section {
      // The started task is the hero card above; repeating it here cost
      // most of a screenful.
      ForEach(store.state.tonight.filter { !$0.isStarted }) { task in
        ArgonTaskRow(task: task, store: store).argonTaskRow()
      }
      if store.state.tonight.isEmpty {
        Text(store.connection.isLive ? "Nothing due tonight." : "Nothing cached.")
          .font(Argon.body).foregroundStyle(Argon.Tone.faint)
          .padding(.vertical, 6)
          .argonTaskRow()
      }
      addRow.argonTaskRow()
    } header: {
      sectionHeader("Due", count: store.state.tonight.count, colour: Argon.accent)
    }
  }

  private var addRow: some View {
    Group {
      if adding {
        HStack(spacing: 12) {
          Image(systemName: "plus.circle.fill")
            .font(.title3).foregroundStyle(Argon.accent)
            .argonGlow(strength: 0.6)
          TextField("", text: $newTask, prompt:
                      Text("New task for tonight").foregroundStyle(Argon.Tone.faint))
            .font(Argon.body).foregroundStyle(Argon.Tone.primary)
            .focused($addFocused).submitLabel(.done).onSubmit(commit)
          Button("Add", action: commit)
            .font(Argon.heading).foregroundStyle(Argon.accent)
            .buttonStyle(.plain)
            .disabled(newTask.trimmingCharacters(in: .whitespaces).isEmpty)
        }
      } else {
        Button {
          adding = true
          addFocused = true
        } label: {
          HStack(spacing: 12) {
            Image(systemName: "plus.circle.fill").font(.title3)
            Text("Add a task").font(Argon.body)
            Spacer()
          }
          .foregroundStyle(Argon.accent.opacity(0.85))
          .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
      }
    }
    .padding(.vertical, 6)
  }

  /// Late work, behind a count. Twenty-two red pills on the main board made
  /// every row look equally urgent, which is the same as none of them being.
  @ViewBuilder private var overdueSection: some View {
    if !store.state.overdue.isEmpty {
      Section {
        if showOverdue {
          ForEach(store.state.overdue) { task in
            ArgonTaskRow(task: task, store: store, showDue: true).argonTaskRow()
          }
        }
      } header: {
        foldHeader("Late", count: store.state.overdue.count, colour: Argon.overdue,
                   open: $showOverdue)
      }
    }
  }

  /// Work that is real but not yet his problem, folded away with the count in
  /// the header, so nothing is hidden and next week is not fifty rows down.
  @ViewBuilder private var futureSection: some View {
    let later = store.state.future
    if !later.isEmpty {
      Section {
        if showFuture {
          ForEach(later) { task in
            ArgonTaskRow(task: task, store: store, showDue: true).argonTaskRow()
          }
        }
      } header: {
        foldHeader("Future work", count: later.count, colour: Argon.accent,
                   open: $showFuture)
      }
    }
  }

  private func sectionHeader(_ title: String, count: Int, colour: Color) -> some View {
    HStack {
      Text(title).font(Argon.cardTitle).foregroundStyle(Argon.Tone.primary)
      Spacer()
      if count > 0 { ArgonPill(text: "\(count)", colour: colour) }
    }
    .textCase(nil)
    .padding(.top, 8)
  }

  private func foldHeader(_ title: String, count: Int, colour: Color,
                          open: Binding<Bool>) -> some View {
    Button {
      withAnimation(.snappy(duration: 0.22)) { open.wrappedValue.toggle() }
    } label: {
      HStack(spacing: 10) {
        Text(title).font(Argon.cardTitle).foregroundStyle(Argon.Tone.primary)
        ArgonPill(text: "\(count)", colour: colour)
        Spacer()
        Image(systemName: open.wrappedValue ? "chevron.up" : "chevron.down")
          .font(.footnote).foregroundStyle(Argon.Tone.faint)
      }
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .textCase(nil)
    .padding(.top, 8)
    .accessibilityLabel("\(title), \(count) items")
  }

  /// The afternoon brief, at the top, until he dismisses it.
  ///
  /// The push notification is how he hears about it; this is what he opens the
  /// app to read. Delivered into the chat thread alone it was one more line to
  /// scroll past, which is not what a briefing is. Dismissal is explicit and
  /// remembered server-side, so it does not come back on the next refresh or
  /// on another device.
  @ViewBuilder private var briefCard: some View {
    if let brief = store.state.brief, !brief.acked,
       !brief.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
      ArgonGlass(tint: Argon.accent) {
        VStack(alignment: .leading, spacing: 12) {
          HStack(spacing: 8) {
            Image(systemName: "sun.horizon.fill")
              .font(.footnote).foregroundStyle(Argon.accentSoft)
            Text("THIS AFTERNOON")
              .font(Argon.label.weight(.semibold)).tracking(1.2)
              .foregroundStyle(Argon.accentSoft)
            Spacer()
            if let at = ArgonDate.parse(brief.at) {
              Text(at.formatted(date: .omitted, time: .shortened))
                .font(Argon.label).foregroundStyle(Argon.Tone.faint)
            }
          }
          Text(brief.text)
            .font(Argon.body).foregroundStyle(Argon.Tone.primary)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
          Button("Got it") { Task { await store.ackBrief() } }
            .font(Argon.body.weight(.semibold))
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background { Capsule().fill(LinearGradient(
              colors: [Argon.accent, Argon.accentDeep],
              startPoint: .topLeading, endPoint: .bottomTrailing)) }
            .buttonStyle(.plain)
        }
      }
      .argonGlow(Argon.accent, strength: 0.6)
    }
  }

  private func commit() {
    let title = newTask
    newTask = ""
    adding = false
    // Typed under "Due", so due tonight. Undated, it filed itself under the
    // folded Future card and looked like it had not been added.
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
    HStack(spacing: 14) {
      Button { Task { await store.complete(task) } } label: {
        Image(systemName: task.done ? "checkmark.circle.fill" : "circle")
          .font(.title2)
          .foregroundStyle(task.done ? Argon.running : Argon.Tone.faint)
          // A 24pt circle is too small to hit reliably; v1 padded it to 44.
          .frame(width: 44, height: 44)
          .contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .disabled(task.isLocal)
      .accessibilityLabel("Complete \(task.title)")

      VStack(alignment: .leading, spacing: 3) {
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

      if task.isStarted {
        Image(systemName: "play.fill").font(.caption).foregroundStyle(Argon.running)
      }
      let label = showDue ? (task.due.map(ArgonDate.short) ?? "") : task.dueLabel()
      if !label.isEmpty {
        ArgonPill(text: label,
                  colour: showDue ? Argon.Tone.faint
                        : label == "overdue" ? Argon.overdue
                        : label == "today" ? Argon.accentSoft : Argon.Tone.faint)
      }
    }
    .padding(.vertical, 4)
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
        }.tint(Argon.running)
      }
      if movable {
        Button { Task { await store.move(task, to: ArgonDate.tomorrow()) } } label: {
          Label("Tomorrow", systemImage: "moon.zzz.fill")
        }.tint(Argon.accentDeep)
      }
    }
    .swipeActions(edge: .leading, allowsFullSwipe: true) {
      if movable {
        Button { Task { await store.move(task, to: ArgonDate.tomorrow()) } } label: {
          Label("Tomorrow", systemImage: "moon.zzz.fill")
        }.tint(Argon.accentDeep)
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

private extension View {
  /// A glass card sitting in the list as itself: no row chrome around it.
  func argonCardRow() -> some View {
    listRowBackground(Color.clear)
      .listRowSeparator(.hidden)
      .listRowInsets(EdgeInsets(top: 8, leading: 18, bottom: 8, trailing: 18))
  }

  /// One task on a faint pane, hairline between rows.
  func argonTaskRow() -> some View {
    listRowBackground(Color.white.opacity(0.05))
      .listRowSeparatorTint(Argon.hairline)
      .listRowInsets(EdgeInsets(top: 4, leading: 12, bottom: 4, trailing: 18))
  }
}

/// Says where the numbers came from. An app that silently shows old data is
/// worse than one that admits it.
struct ArgonStatusDot: View {
  let store: ArgonStore

  var body: some View {
    HStack(spacing: 8) {
      if store.pendingCount > 0 {
        ArgonPill(text: "\(store.pendingCount) queued")
      }
      if store.isLoading {
        ProgressView().controlSize(.small).tint(Argon.accentSoft)
      } else {
        switch store.connection {
        case .never:
          ArgonPill(text: "offline", colour: Argon.Tone.faint)
        case .live:
          HStack(spacing: 6) {
            Circle().fill(Argon.running).frame(width: 7, height: 7)
            Text("live").font(Argon.label).foregroundStyle(Argon.Tone.secondary)
          }
        case .stale(let at, _):
          ArgonPill(text: at.argonAgo, colour: Argon.Tone.faint)
        }
      }
    }
  }
}

struct ArgonFailureCard: View {
  let text: String
  let dismiss: () -> Void

  var body: some View {
    ArgonGlass(tint: Argon.overdue, padding: 16) {
      HStack(alignment: .top, spacing: 12) {
        Image(systemName: "exclamationmark.triangle.fill")
          .font(.body).foregroundStyle(Argon.overdue)
        Text(text).font(Argon.detail).foregroundStyle(Argon.Tone.primary)
        Spacer(minLength: 4)
        Button("Dismiss", action: dismiss)
          .font(Argon.label).foregroundStyle(Argon.Tone.secondary)
          .buttonStyle(.plain)
      }
    }
  }
}
