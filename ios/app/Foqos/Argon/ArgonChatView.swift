import SwiftUI

struct ArgonChatView: View {
  let store: ArgonStore
  @State private var draft = ""
  @FocusState private var focused: Bool

  var body: some View {
    ZStack {
      ArgonAmbience.now(ticking: store.state.ticking)

      VStack(spacing: 0) {
        ScrollViewReader { proxy in
          ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
              if store.messages.isEmpty { empty }
              ForEach(store.messages) { ArgonBubble(message: $0).id($0.id) }
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
          }
          .scrollIndicators(.hidden)
          .onChange(of: store.messages.count) { _, _ in scroll(proxy) }
          .onAppear { scroll(proxy, animated: false) }
        }

        if let failure = store.failure {
          HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").font(.caption2)
            Text(failure).font(Argon.label)
            Spacer()
            Button("Dismiss") { store.dismissFailure() }.font(Argon.label)
          }
          .foregroundStyle(Argon.overdue)
          .padding(.horizontal, 16).padding(.vertical, 8)
          .background(.ultraThinMaterial)
        }

        composer
      }
    }
    .refreshable { await store.refresh() }
    .task { await store.markRead() }
  }

  private var empty: some View {
    VStack(spacing: 6) {
      Text(store.connection.isLive ? "Nothing yet." : "No cached conversation.")
        .font(Argon.body).foregroundStyle(Argon.Tone.secondary)
      Text("Ask him what's due.").font(Argon.label).foregroundStyle(Argon.Tone.faint)
    }
    .frame(maxWidth: .infinity).padding(.top, 60)
  }

  private var composer: some View {
    HStack(spacing: 10) {
      TextField("", text: $draft, prompt:
                  Text("Message Argon").foregroundStyle(Argon.Tone.faint), axis: .vertical)
        .font(Argon.body)
        .foregroundStyle(Argon.Tone.primary)
        .lineLimit(1...5)
        .focused($focused)
        .padding(.horizontal, 14).padding(.vertical, 9)
        .background {
          Capsule().fill(.ultraThinMaterial)
            .overlay(Capsule().strokeBorder(Argon.hairline, lineWidth: 1))
        }

      Button(action: send) {
        Image(systemName: "arrow.up")
          .font(.system(size: 15, weight: .semibold))
          .foregroundStyle(canSend ? Argon.Ink.void : Argon.Tone.faint)
          .frame(width: 34, height: 34)
          .background(Circle().fill(canSend ? Argon.accent : Color.white.opacity(0.07)))
      }
      .buttonStyle(.plain)
      .disabled(!canSend)
      .animation(.easeOut(duration: 0.15), value: canSend)
    }
    .padding(.horizontal, 16).padding(.vertical, 10)
    .background(.ultraThinMaterial)
  }

  private var canSend: Bool { !draft.trimmingCharacters(in: .whitespaces).isEmpty }

  private func send() {
    let text = draft
    draft = ""
    Task { await store.send(text) }
  }

  private func scroll(_ proxy: ScrollViewProxy, animated: Bool = true) {
    guard let last = store.messages.last?.id else { return }
    if animated { withAnimation { proxy.scrollTo(last, anchor: .bottom) } }
    else { proxy.scrollTo(last, anchor: .bottom) }
  }
}

struct ArgonBubble: View {
  let message: ArgonMessage

  var body: some View {
    HStack {
      if !message.isFromArgon { Spacer(minLength: 48) }
      VStack(alignment: message.isFromArgon ? .leading : .trailing, spacing: 3) {
        Text(attributed)
          .font(Argon.body)
          .textSelection(.enabled)
          .foregroundStyle(message.isFromArgon ? Argon.Tone.primary : Argon.Ink.void)
          .padding(.horizontal, 13).padding(.vertical, 9)
          .background {
            if message.isFromArgon {
              RoundedRectangle(cornerRadius: 17, style: .continuous)
                .fill(.ultraThinMaterial)
                .overlay(RoundedRectangle(cornerRadius: 17, style: .continuous)
                  .strokeBorder(Argon.hairline, lineWidth: 1))
            } else {
              RoundedRectangle(cornerRadius: 17, style: .continuous)
                .fill(Argon.accent.opacity(message.pending ? 0.45 : 0.92))
            }
          }
        if message.pending {
          Text("sending…").font(Argon.label).foregroundStyle(Argon.Tone.faint)
        }
      }
      if message.isFromArgon { Spacer(minLength: 48) }
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
