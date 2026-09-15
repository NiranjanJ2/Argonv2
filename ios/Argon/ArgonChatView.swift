import SwiftUI

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
              if store.messages.isEmpty {
                Text(store.connection.isLive
                     ? "Nothing yet. Ask him something."
                     : "No cached conversation.")
                  .font(.footnote).foregroundStyle(.secondary)
                  .frame(maxWidth: .infinity).padding(.top, 40)
              }
              ForEach(store.messages) { message in
                ArgonBubble(message: message).id(message.id)
              }
            }
            .padding()
          }
          .onChange(of: store.messages.count) { _, _ in scroll(proxy) }
          .onAppear { scroll(proxy, animated: false) }
        }

        if let failure = store.failure {
          HStack {
            Text(failure).font(.caption).foregroundStyle(.orange)
            Spacer()
            Button("OK") { store.dismissFailure() }.font(.caption)
          }
          .padding(.horizontal).padding(.vertical, 6)
          .background(.orange.opacity(0.1))
        }

        HStack(spacing: 8) {
          TextField("Message", text: $draft, axis: .vertical)
            .textFieldStyle(.roundedBorder)
            .lineLimit(1...5)
            .focused($focused)
            .onSubmit(send)
          Button(action: send) {
            Image(systemName: "arrow.up.circle.fill").font(.title2)
          }
          .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding()
      }
      .navigationTitle("Argon")
      .navigationBarTitleDisplayMode(.inline)
      .toolbar { ArgonConnectionBadge(store: store) }
      .refreshable { await store.refresh() }
      .task { await store.markRead() }
    }
  }

  private func send() {
    let text = draft
    draft = ""
    Task { await store.send(text) }
  }

  private func scroll(_ proxy: ScrollViewProxy, animated: Bool = true) {
    guard let last = store.messages.last?.id else { return }
    if animated {
      withAnimation { proxy.scrollTo(last, anchor: .bottom) }
    } else {
      proxy.scrollTo(last, anchor: .bottom)
    }
  }
}

struct ArgonBubble: View {
  let message: ArgonMessage

  var body: some View {
    HStack {
      if !message.isFromArgon { Spacer(minLength: 40) }
      VStack(alignment: message.isFromArgon ? .leading : .trailing, spacing: 2) {
        Text(attributed)
          .textSelection(.enabled)
          .padding(.horizontal, 12).padding(.vertical, 8)
          .background(message.isFromArgon ? AnyShapeStyle(.quaternary)
                                          : AnyShapeStyle(Color.accentColor))
          .foregroundStyle(message.isFromArgon ? Color.primary : Color.white)
          .clipShape(RoundedRectangle(cornerRadius: 16))
          .opacity(message.pending ? 0.55 : 1)
        if message.pending {
          Text("sending…").font(.caption2).foregroundStyle(.secondary)
        }
      }
      if message.isFromArgon { Spacer(minLength: 40) }
    }
  }

  /// Argon writes markdown. Falling back to the raw string keeps a malformed
  /// message readable instead of blank.
  private var attributed: AttributedString {
    (try? AttributedString(
      markdown: message.text,
      options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
      ?? AttributedString(message.text)
  }
}
