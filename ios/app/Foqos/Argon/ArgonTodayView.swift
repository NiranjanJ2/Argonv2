import SwiftUI

/// What he opens the app to see: what's due, what he's on, and whether any of
/// it can be trusted right now.
struct ArgonTodayView: View {
  let store: ArgonStore
  @State private var newTask = ""
  @State private var adding = false
  @State private var showOverdue = false
  @FocusState private var addFocused: Bool

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 16) {
        header
        if let failure = store.failure {
          ArgonFailureCard(text: failure) { store.dismissFailure() }
        }
        statusCard
        boardCard
        overdueCard
        if !store.state.facts.isEmpty { factsCard }
        budgetCard
        Color.clear.frame(height: 24)
      }
      .padding(.horizontal, 18)
    }
    .scrollIndicators(.hidden)
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
            if let since = started.startedAt.flatMap(ArgonDate.parse) {
              Text("working since \(since.formatted(date: .omitted, time: .shortened))")
                .font(Argon.detail)
                .foregroundStyle(Argon.Tone.secondary)
            }
          }
          Spacer(minLength: 8)
          Button("Done") { Task { await store.complete(started) } }
            .font(Argon.heading)
            .foregroundStyle(Argon.running)
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

  private var boardCard: some View {
    ArgonGlass(padding: 0) {
      VStack(spacing: 0) {
        HStack {
          Text("Due").font(Argon.cardTitle).foregroundStyle(Argon.Tone.primary)
          Spacer()
          if !store.state.upcoming.isEmpty {
            ArgonPill(text: "\(store.state.upcoming.count)")
          }
        }
        .padding(.horizontal, 18).padding(.top, 18).padding(.bottom, 12)

        if store.state.upcoming.isEmpty {
          Text(store.connection.isLive ? "Nothing due." : "Nothing cached.")
            .font(Argon.body).foregroundStyle(Argon.Tone.faint)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 18).padding(.bottom, 18)
        }

        ForEach(store.state.upcoming) { task in
          ArgonDivider().padding(.leading, 18)
          ArgonTaskRow(task: task, store: store)
        }

        ArgonDivider().padding(.leading, 18)
        addRow
      }
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
                      Text("New task").foregroundStyle(Argon.Tone.faint))
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
        }
        .buttonStyle(.plain)
      }
    }
    .padding(.horizontal, 18).padding(.vertical, 15)
  }

  /// Late work, behind a count. Twenty-two red pills on the main board made
  /// every row look equally urgent, which is the same as none of them being.
  @ViewBuilder private var overdueCard: some View {
    if !store.state.overdue.isEmpty {
      ArgonGlass(tint: Argon.overdue, padding: 0) {
        VStack(spacing: 0) {
          Button {
            withAnimation(.spring(duration: 0.3)) { showOverdue.toggle() }
          } label: {
            HStack {
              Text("Late").font(Argon.cardTitle).foregroundStyle(Argon.Tone.primary)
              ArgonPill(text: "\(store.state.overdue.count)", colour: Argon.overdue)
              Spacer()
              Image(systemName: showOverdue ? "chevron.up" : "chevron.down")
                .font(.footnote).foregroundStyle(Argon.Tone.faint)
            }
            .padding(.horizontal, 18).padding(.vertical, 18)
            .contentShape(Rectangle())
          }
          .buttonStyle(.plain)

          if showOverdue {
            ForEach(store.state.overdue) { task in
              ArgonDivider().padding(.leading, 18)
              ArgonTaskRow(task: task, store: store, showDue: true)
            }
          }
        }
      }
    }
  }

  private var factsCard: some View {
    ArgonGlass(tint: Argon.accentDeep) {
      VStack(alignment: .leading, spacing: 10) {
        Text("What Argon knows")
          .font(Argon.heading).foregroundStyle(Argon.Tone.primary)
        ForEach(store.state.facts, id: \.self) { fact in
          HStack(alignment: .top, spacing: 10) {
            Circle().fill(Argon.accent).frame(width: 5, height: 5).padding(.top, 8)
            Text(fact).font(Argon.body).foregroundStyle(Argon.Tone.secondary)
          }
        }
      }
    }
  }

  @ViewBuilder private var budgetCard: some View {
    if let budget = store.state.budget, budget.cap > 0 {
      ArgonGlass(padding: 16) {
        VStack(alignment: .leading, spacing: 10) {
          HStack {
            Text("This month").font(Argon.label).foregroundStyle(Argon.Tone.secondary)
            Spacer()
            Text(String(format: "$%.2f of $%.0f", budget.spent, budget.cap))
              .font(Argon.mono)
              .foregroundStyle(budget.isNearCap ? Argon.overdue : Argon.Tone.primary)
          }
          GeometryReader { geo in
            ZStack(alignment: .leading) {
              Capsule().fill(Color.white.opacity(0.10))
              Capsule()
                .fill(LinearGradient(
                  colors: budget.isNearCap ? [Argon.overdue, Argon.overdue]
                                           : [Argon.accentDeep, Argon.accent],
                  startPoint: .leading, endPoint: .trailing))
                .frame(width: max(6, geo.size.width * min(budget.spent / budget.cap, 1)))
            }
          }
          .frame(height: 6)
          if budget.isNearCap {
            Text("Argon goes quiet at the cap.")
              .font(Argon.label).foregroundStyle(Argon.overdue)
          }
        }
      }
    }
  }

  private func commit() {
    let title = newTask
    newTask = ""
    adding = false
    Task { await store.add(title: title) }
  }
}

// MARK: - rows

struct ArgonTaskRow: View {
  let task: ArgonTask
  let store: ArgonStore
  /// Inside the Late card every row is late, so the date is what varies.
  var showDue = false

  var body: some View {
    HStack(spacing: 14) {
      Button { Task { await store.complete(task) } } label: {
        Image(systemName: task.done ? "checkmark.circle.fill" : "circle")
          .font(.title2)
          .foregroundStyle(task.done ? Argon.running : Argon.Tone.faint)
      }
      .buttonStyle(.plain)
      .disabled(task.isLocal)

      VStack(alignment: .leading, spacing: 3) {
        Text(task.title)
          .font(Argon.body)
          .foregroundStyle(task.isLocal ? Argon.Tone.faint : Argon.Tone.primary)
          .strikethrough(task.done, color: Argon.Tone.faint)
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
      let label = showDue ? (task.due.map { String($0.prefix(10).dropFirst(5)) } ?? "")
                          : task.dueLabel()
      if !label.isEmpty {
        ArgonPill(text: label,
                  colour: showDue ? Argon.Tone.faint
                        : label == "overdue" ? Argon.overdue
                        : label == "today" ? Argon.accentSoft : Argon.Tone.faint)
      }
    }
    .padding(.horizontal, 18).padding(.vertical, 14)
    .contentShape(Rectangle())
    .swipeActions(edge: .leading, allowsFullSwipe: true) {
      if !task.isStarted && !task.isLocal {
        Button { Task { await store.start(task) } } label: {
          Label("Start", systemImage: "play.fill")
        }.tint(Argon.running)
      }
    }
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
