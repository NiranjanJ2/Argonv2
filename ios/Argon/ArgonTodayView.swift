import SwiftUI

/// What he opens the app to see: what's due, what he's on, and whether any of
/// it can be trusted right now.
struct ArgonTodayView: View {
  let store: ArgonStore
  @State private var newTask = ""
  @State private var addingTask = false

  var body: some View {
    NavigationStack {
      List {
        if let failure = store.failure {
          ArgonFailureRow(text: failure) { store.dismissFailure() }
        }

        headline

        Section {
          ForEach(store.state.sortedTasks) { task in
            ArgonTaskRow(task: task, store: store)
          }
          if store.state.sortedTasks.isEmpty {
            Text(store.connection.isLive ? "Nothing open." : "Nothing cached.")
              .foregroundStyle(.secondary)
          }
          addRow
        } header: {
          HStack {
            Text("Open")
            Spacer()
            if store.state.overdueCount > 0 {
              Text("\(store.state.overdueCount) overdue")
                .foregroundStyle(.red)
            }
          }
        }

        if !store.state.facts.isEmpty {
          Section("What Argon knows") {
            ForEach(store.state.facts, id: \.self) { fact in
              Text(fact).font(.callout).foregroundStyle(.secondary)
            }
          }
        }
      }
      .navigationTitle("Today")
      .toolbar { ArgonConnectionBadge(store: store) }
      .refreshable { await store.refresh() }
      .animation(.default, value: store.state.tasks)
    }
  }

  @ViewBuilder private var headline: some View {
    Section {
      if let started = store.state.started {
        Label {
          VStack(alignment: .leading, spacing: 2) {
            Text("Working on \(started.title)").fontWeight(.medium)
            if let since = started.startedAt.flatMap(ArgonDate.parse) {
              Text("since \(since.formatted(date: .omitted, time: .shortened))")
                .font(.caption).foregroundStyle(.secondary)
            }
          }
        } icon: {
          Image(systemName: "play.circle.fill").foregroundStyle(.green)
        }
      }

      if let schedule = store.state.school.schedule {
        Label(schedule, systemImage: "building.columns")
          .font(.callout).foregroundStyle(.secondary)
      }
      if let period = store.state.school.period {
        Label("In \(period)", systemImage: "clock")
          .font(.callout).foregroundStyle(.secondary)
      }
      if !store.state.ticking {
        Label("Argon isn't watching right now", systemImage: "moon.zzz")
          .font(.callout).foregroundStyle(.secondary)
      }
    }
  }

  @ViewBuilder private var addRow: some View {
    if addingTask {
      HStack {
        TextField("New task", text: $newTask)
          .onSubmit { commit() }
          .submitLabel(.done)
        Button("Add") { commit() }
          .disabled(newTask.trimmingCharacters(in: .whitespaces).isEmpty)
      }
    } else {
      Button {
        addingTask = true
      } label: {
        Label("Add a task", systemImage: "plus.circle")
      }
      .foregroundStyle(.secondary)
    }
  }

  private func commit() {
    let title = newTask
    newTask = ""
    addingTask = false
    Task { await store.add(title: title) }
  }
}

struct ArgonTaskRow: View {
  let task: ArgonTask
  let store: ArgonStore

  var body: some View {
    HStack(spacing: 12) {
      Button {
        Task { await store.complete(task) }
      } label: {
        Image(systemName: task.done ? "checkmark.circle.fill" : "circle")
          .font(.title3)
          .foregroundStyle(task.done ? .green : .secondary)
      }
      .buttonStyle(.plain)
      .disabled(task.isLocal)

      VStack(alignment: .leading, spacing: 2) {
        Text(task.title)
          .strikethrough(task.done)
          .foregroundStyle(task.isLocal ? .secondary : .primary)
        HStack(spacing: 6) {
          if let subject = task.subject, !subject.isEmpty {
            Text(subject)
          }
          if task.isLocal {
            Text("saving…")
          }
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
      }

      Spacer()

      if task.isStarted {
        Image(systemName: "play.fill").font(.caption).foregroundStyle(.green)
      }
      let label = task.dueLabel()
      if !label.isEmpty {
        Text(label)
          .font(.caption)
          .foregroundStyle(label == "overdue" ? .red : .secondary)
      }
    }
    .swipeActions(edge: .leading, allowsFullSwipe: true) {
      if !task.isStarted && !task.isLocal {
        Button { Task { await store.start(task) } } label: {
          Label("Start", systemImage: "play.fill")
        }.tint(.green)
      }
    }
  }
}

/// Says where the numbers came from. An app that silently shows old data is
/// worse than one that admits it.
struct ArgonConnectionBadge: ToolbarContent {
  let store: ArgonStore

  var body: some ToolbarContent {
    ToolbarItem(placement: .topBarTrailing) {
      HStack(spacing: 6) {
        if store.pendingCount > 0 {
          Image(systemName: "arrow.up.circle")
          Text("\(store.pendingCount)")
        }
        if store.isLoading {
          ProgressView().controlSize(.mini)
        } else {
          switch store.connection {
          case .never:
            Image(systemName: "wifi.slash").foregroundStyle(.secondary)
          case .live:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green.opacity(0.7))
          case .stale(let at, _):
            Text(at.argonAgo).foregroundStyle(.orange)
          }
        }
      }
      .font(.caption)
    }
  }
}

struct ArgonFailureRow: View {
  let text: String
  let dismiss: () -> Void

  var body: some View {
    Section {
      HStack(alignment: .top, spacing: 10) {
        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        Text(text).font(.footnote)
        Spacer()
        Button("OK", action: dismiss).font(.footnote)
      }
    }
  }
}
