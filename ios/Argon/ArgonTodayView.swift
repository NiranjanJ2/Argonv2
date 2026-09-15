import SwiftUI

/// What he opens the app to see: what's due, what he's on, and whether any of
/// it can be trusted right now.
///
/// A ScrollView of glass rather than a List: `List` insists on its own
/// background and separators, and fighting it to sit over the ambient field
/// costs more than laying the cards out directly.
struct ArgonTodayView: View {
  let store: ArgonStore
  @State private var newTask = ""
  @State private var adding = false
  @FocusState private var addFocused: Bool

  var body: some View {
    ZStack {
      ArgonAmbience.now(ticking: store.state.ticking)

      ScrollView {
        VStack(spacing: 14) {
          header
          if let failure = store.failure { ArgonFailureCard(text: failure) { store.dismissFailure() } }
          if let started = store.state.started { runningCard(started) }
          boardCard
          if !store.state.facts.isEmpty { factsCard }
          budgetCard
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 32)
      }
      .scrollIndicators(.hidden)
      .refreshable { await store.refresh() }
    }
    .toolbarBackground(.hidden, for: .navigationBar)
    .animation(.spring(duration: 0.35), value: store.state.tasks)
  }

  // MARK: pieces

  private var header: some View {
    HStack(alignment: .firstTextBaseline) {
      VStack(alignment: .leading, spacing: 2) {
        Text("Today").font(Argon.title).foregroundStyle(Argon.Text.primary)
        Text(subtitle).font(Argon.label).foregroundStyle(Argon.Text.faint)
      }
      Spacer()
      ArgonStatusDot(store: store)
    }
    .padding(.top, 8)
  }

  private var subtitle: String {
    if let period = store.state.school.period { return "In \(period)" }
    return store.state.school.schedule ?? Date().formatted(date: .complete, time: .omitted)
  }

  private func runningCard(_ task: ArgonTask) -> some View {
    ArgonGlass(tint: Argon.running) {
      HStack(spacing: 12) {
        ArgonPulse()
        VStack(alignment: .leading, spacing: 3) {
          Text(task.title).font(Argon.heading).foregroundStyle(Argon.Text.primary)
          if let since = task.startedAt.flatMap(ArgonDate.parse) {
            Text("since \(since.formatted(date: .omitted, time: .shortened))")
              .font(Argon.label).foregroundStyle(Argon.Text.faint)
          }
        }
        Spacer()
        Button { Task { await store.complete(task) } } label: {
          Text("Done").font(Argon.label).foregroundStyle(Argon.running)
        }
        .buttonStyle(.plain)
      }
    }
  }

  private var boardCard: some View {
    ArgonGlass(padding: 0) {
      VStack(spacing: 0) {
        HStack {
          Text("Open").font(Argon.heading).foregroundStyle(Argon.Text.primary)
          Spacer()
          if store.state.overdueCount > 0 {
            ArgonPill(text: "\(store.state.overdueCount) overdue", colour: Argon.overdue)
          }
        }
        .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 10)

        if store.state.sortedTasks.isEmpty {
          Text(store.connection.isLive ? "Nothing open." : "Nothing cached.")
            .font(Argon.body).foregroundStyle(Argon.Text.faint)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16).padding(.bottom, 14)
        }

        ForEach(store.state.sortedTasks) { task in
          ArgonDivider().padding(.leading, 16)
          ArgonTaskRow(task: task, store: store)
        }

        ArgonDivider().padding(.leading, 16)
        addRow
      }
    }
  }

  private var addRow: some View {
    Group {
      if adding {
        HStack(spacing: 10) {
          Image(systemName: "plus").font(.caption).foregroundStyle(Argon.accent)
          TextField("", text: $newTask, prompt:
                      Text("New task").foregroundStyle(Argon.Text.faint))
            .font(Argon.body).foregroundStyle(Argon.Text.primary)
            .focused($addFocused).submitLabel(.done).onSubmit(commit)
          Button("Add", action: commit)
            .font(Argon.label).foregroundStyle(Argon.accent)
            .disabled(newTask.trimmingCharacters(in: .whitespaces).isEmpty)
        }
      } else {
        Button {
          adding = true
          addFocused = true
        } label: {
          HStack(spacing: 10) {
            Image(systemName: "plus").font(.caption)
            Text("Add a task").font(Argon.body)
            Spacer()
          }
          .foregroundStyle(Argon.Text.faint)
        }
        .buttonStyle(.plain)
      }
    }
    .padding(.horizontal, 16).padding(.vertical, 13)
  }

  private var factsCard: some View {
    ArgonGlass {
      VStack(alignment: .leading, spacing: 8) {
        Text("What Argon knows").font(Argon.heading).foregroundStyle(Argon.Text.primary)
        ForEach(store.state.facts, id: \.self) { fact in
          HStack(alignment: .top, spacing: 8) {
            Circle().fill(Argon.accentDim).frame(width: 4, height: 4).padding(.top, 7)
            Text(fact).font(Argon.body).foregroundStyle(Argon.Text.secondary)
          }
        }
      }
    }
  }

  @ViewBuilder private var budgetCard: some View {
    if let budget = store.state.budget, budget.cap > 0 {
      ArgonGlass {
        VStack(alignment: .leading, spacing: 8) {
          HStack {
            Text("This month").font(Argon.label).foregroundStyle(Argon.Text.faint)
            Spacer()
            Text(String(format: "$%.2f / $%.0f", budget.spent, budget.cap))
              .font(Argon.mono)
              .foregroundStyle(budget.isNearCap ? Argon.overdue : Argon.Text.secondary)
          }
          GeometryReader { geo in
            ZStack(alignment: .leading) {
              Capsule().fill(Color.white.opacity(0.07))
              Capsule()
                .fill(budget.isNearCap ? Argon.overdue : Argon.accent)
                .frame(width: geo.size.width * min(budget.spent / budget.cap, 1))
            }
          }
          .frame(height: 4)
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

  var body: some View {
    HStack(spacing: 12) {
      Button { Task { await store.complete(task) } } label: {
        Image(systemName: task.done ? "checkmark.circle.fill" : "circle")
          .font(.system(size: 19))
          .foregroundStyle(task.done ? Argon.running : Argon.Text.faint)
      }
      .buttonStyle(.plain)
      .disabled(task.isLocal)

      VStack(alignment: .leading, spacing: 2) {
        Text(task.title)
          .font(Argon.body)
          .foregroundStyle(task.isLocal ? Argon.Text.faint : Argon.Text.primary)
          .strikethrough(task.done, color: Argon.Text.faint)
        if let subject = task.subject, !subject.isEmpty {
          Text(subject).font(Argon.label).foregroundStyle(Argon.Text.faint)
        } else if task.isLocal {
          Text("saving…").font(Argon.label).foregroundStyle(Argon.Text.faint)
        }
      }

      Spacer(minLength: 8)

      if task.isStarted {
        Image(systemName: "play.fill").font(.system(size: 9)).foregroundStyle(Argon.running)
      }
      let label = task.dueLabel()
      if !label.isEmpty {
        ArgonPill(text: label,
                  colour: label == "overdue" ? Argon.overdue
                        : label == "today" ? Argon.accent : Argon.Text.faint)
      }
    }
    .padding(.horizontal, 16).padding(.vertical, 11)
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
    HStack(spacing: 7) {
      if store.pendingCount > 0 {
        ArgonPill(text: "\(store.pendingCount) queued", colour: Argon.accent)
      }
      if store.isLoading {
        ProgressView().controlSize(.mini).tint(Argon.Text.faint)
      } else {
        switch store.connection {
        case .never:
          ArgonPill(text: "offline", colour: Argon.Text.faint)
        case .live:
          HStack(spacing: 5) {
            Circle().fill(Argon.running).frame(width: 6, height: 6)
            Text("live").font(Argon.label).foregroundStyle(Argon.Text.faint)
          }
        case .stale(let at, _):
          ArgonPill(text: at.argonAgo, colour: Argon.overdue)
        }
      }
    }
  }
}

struct ArgonFailureCard: View {
  let text: String
  let dismiss: () -> Void

  var body: some View {
    ArgonGlass(tint: Argon.overdue) {
      HStack(alignment: .top, spacing: 10) {
        Image(systemName: "exclamationmark.triangle.fill")
          .font(.caption).foregroundStyle(Argon.overdue)
        Text(text).font(Argon.label).foregroundStyle(Argon.Text.secondary)
        Spacer()
        Button("Dismiss", action: dismiss)
          .font(Argon.label).foregroundStyle(Argon.Text.faint)
      }
    }
  }
}
