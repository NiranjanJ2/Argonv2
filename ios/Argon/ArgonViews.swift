import SwiftUI

/// The Argon half of the app: a dashboard and a conversation.
///
/// Foqos keeps its own screens; these are added alongside. Nothing here reaches
/// the network directly — every view reads `ArgonStore` and calls it back, so
/// there is one refresh path and the screens cannot disagree.

struct ArgonRootView: View {
  @State private var store: ArgonStore
  @State private var tab = Tab.today

  enum Tab { case today, chat }

  init(store: ArgonStore) {
    _store = State(initialValue: store)
  }

  var body: some View {
    TabView(selection: $tab) {
      ArgonDashboardView(store: store)
        .tabItem { Label("Today", systemImage: "list.bullet") }
        .tag(Tab.today)

      ArgonChatView(store: store)
        .tabItem { Label("Argon", systemImage: "bubble.left.and.bubble.right") }
        .badge(store.state.unread)
        .tag(Tab.chat)
    }
    .task { await store.refresh() }
    // Refresh when he brings the app forward. Not a timer: iOS suspends those
    // in the background, so a timer-driven refresh is a refresh that only
    // happens while he is already looking at it.
    .onChange(of: tab) { _, new in
      Task {
        await store.refresh()
        if new == .chat { await store.markRead() }
      }
    }
  }
}

struct ArgonDashboardView: View {
  let store: ArgonStore

  var body: some View {
    NavigationStack {
      List {
        if let error = store.lastError {
          Section { Label(error, systemImage: "exclamationmark.triangle").font(.footnote) }
        }

        Section {
          if let started = store.state.started {
            Label("Working on \(started.title)", systemImage: "play.circle.fill")
              .foregroundStyle(.green)
          }
          if let schedule = store.state.school.schedule {
            Text(schedule).font(.footnote).foregroundStyle(.secondary)
          }
          if let period = store.state.school.period {
            Text("In \(period)").font(.footnote).foregroundStyle(.secondary)
          }
          if !store.state.ticking {
            Text("Argon isn't watching right now")
              .font(.footnote).foregroundStyle(.secondary)
          }
        }

        Section("Open") {
          if store.state.sortedTasks.isEmpty {
            Text("Nothing open").foregroundStyle(.secondary)
          }
          ForEach(store.state.sortedTasks) { task in
            ArgonTaskRow(task: task, store: store)
          }
        }

        if let budget = store.state.budget, budget.cap > 0 {
          Section {
            LabeledContent("Spend this month",
                           value: String(format: "$%.2f of $%.0f", budget.spent, budget.cap))
              .font(.footnote)
              .foregroundStyle(budget.isNearCap ? .orange : .secondary)
          } footer: {
            if budget.isNearCap {
              Text("Argon stops when it reaches the cap.")
            }
          }
        }
      }
      .navigationTitle("Today")
      .refreshable { await store.refresh() }
    }
  }
}

struct ArgonTaskRow: View {
  let task: ArgonTask
  let store: ArgonStore

  var body: some View {
    HStack(spacing: 10) {
      Button {
        Task { await store.complete(task) }
      } label: {
        Image(systemName: task.done ? "checkmark.circle.fill" : "circle")
          .foregroundStyle(task.done ? .green : .secondary)
      }
      .buttonStyle(.plain)

      VStack(alignment: .leading, spacing: 2) {
        Text(task.title).strikethrough(task.done)
        if let subject = task.subject, !subject.isEmpty {
          Text(subject).font(.caption2).foregroundStyle(.secondary)
        }
      }

      Spacer()

      let label = task.dueLabel()
      if !label.isEmpty {
        Text(label)
          .font(.caption2)
          .foregroundStyle(label == "overdue" ? .red : .secondary)
      }
      if task.isStarted {
        Image(systemName: "play.fill").font(.caption2).foregroundStyle(.green)
      }
    }
    .swipeActions(edge: .leading) {
      if !task.isStarted {
        Button("Start") { Task { await store.start(task) } }.tint(.green)
      }
    }
  }
}

struct ArgonChatView: View {
  let store: ArgonStore
  @State private var draft = ""
  @FocusState private var focused: Bool

  var body: some View {
    NavigationStack {
      VStack(spacing: 0) {
        ScrollViewReader { proxy in
          ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
              ForEach(store.messages) { message in
                ArgonBubble(message: message).id(message.seq)
              }
            }
            .padding()
          }
          .onChange(of: store.messages.count) { _, _ in
            withAnimation { proxy.scrollTo(store.messages.last?.seq, anchor: .bottom) }
          }
        }

        HStack(spacing: 8) {
          TextField("Message", text: $draft, axis: .vertical)
            .textFieldStyle(.roundedBorder)
            .lineLimit(1...5)
            .focused($focused)
          Button {
            let text = draft
            draft = ""
            Task { await store.send(text) }
          } label: {
            Image(systemName: "arrow.up.circle.fill").font(.title2)
          }
          .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding()
      }
      .navigationTitle("Argon")
      .navigationBarTitleDisplayMode(.inline)
      .refreshable { await store.refresh() }
    }
  }
}

struct ArgonBubble: View {
  let message: ArgonMessage

  var body: some View {
    HStack {
      if !message.isFromArgon { Spacer(minLength: 40) }
      Text(attributed)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(message.isFromArgon ? Color.secondary.opacity(0.15) : Color.accentColor)
        .foregroundStyle(message.isFromArgon ? Color.primary : Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 16))
      if message.isFromArgon { Spacer(minLength: 40) }
    }
  }

  /// Argon writes markdown. Falling back to the raw string keeps a malformed
  /// message readable instead of blank.
  private var attributed: AttributedString {
    (try? AttributedString(markdown: message.text,
                           options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
      ?? AttributedString(message.text)
  }
}
