import SwiftUI

struct ArgonChatView: View {
  let store: ArgonStore
  @State private var draft = ""
  @FocusState private var focused: Bool

  var body: some View {
    VStack(spacing: 0) {
      ScrollViewReader { proxy in
        ScrollView {
          LazyVStack(alignment: .leading, spacing: 12) {
            if store.messages.isEmpty { empty }
            ForEach(store.messages) { ArgonBubble(message: $0).id($0.id) }
          }
          .padding(.horizontal, 18).padding(.vertical, 14)
        }
        .scrollIndicators(.hidden)
        .onChange(of: store.messages.count) { _, _ in scroll(proxy) }
        .onAppear { scroll(proxy, animated: false) }
      }

      if let failure = store.failure {
        HStack(spacing: 10) {
          Image(systemName: "exclamationmark.triangle.fill").font(.footnote)
          Text(failure).font(Argon.label)
          Spacer()
          Button("Dismiss") { store.dismissFailure() }
            .font(Argon.label).buttonStyle(.plain)
        }
        .foregroundStyle(Argon.overdue)
        .padding(.horizontal, 18).padding(.vertical, 10)
        .background(.regularMaterial)
      }

      composer
    }
    .refreshable { await store.refresh() }
    .argonAmbience(ticking: store.state.ticking)
    .task { await store.markRead() }
  }

  private var empty: some View {
    VStack(spacing: 8) {
      Image(systemName: "bubble.left.and.bubble.right.fill")
        .font(.largeTitle).foregroundStyle(Argon.accentSoft)
        .background(ArgonBloom(size: 200))
        .argonGlow(strength: 0.8)
      Text(store.connection.isLive ? "Nothing yet." : "No cached conversation.")
        .font(Argon.body).foregroundStyle(Argon.Tone.secondary)
      Text("Ask him what's due.").font(Argon.detail).foregroundStyle(Argon.Tone.faint)
    }
    .frame(maxWidth: .infinity).padding(.top, 70)
  }

  private var composer: some View {
    HStack(spacing: 12) {
      TextField("", text: $draft, prompt:
                  Text("Message Argon").foregroundStyle(Argon.Tone.faint), axis: .vertical)
        .font(Argon.body)
        .foregroundStyle(Argon.Tone.primary)
        .lineLimit(1...5)
        .focused($focused)
        .padding(.horizontal, 16).padding(.vertical, 11)
        .background {
          Capsule().fill(.regularMaterial)
            .overlay(Capsule().strokeBorder(Argon.hairline, lineWidth: 1))
        }

      Button(action: send) {
        Image(systemName: "arrow.up")
          .font(.body.weight(.bold))
          .foregroundStyle(canSend ? Color.white : Argon.Tone.faint)
          .frame(width: 42, height: 42)
          .argonGlow(strength: canSend ? 0.9 : 0)
          .background {
            Circle().fill(canSend
              ? AnyShapeStyle(LinearGradient(colors: [Argon.accent, Argon.accentDeep],
                                             startPoint: .top, endPoint: .bottom))
              : AnyShapeStyle(Color.white.opacity(0.08)))
          }
      }
      .buttonStyle(.plain)
      .disabled(!canSend)
      .animation(.easeOut(duration: 0.15), value: canSend)
    }
    .padding(.horizontal, 18).padding(.vertical, 12)
    .background(.regularMaterial)
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
      if !message.isFromArgon { Spacer(minLength: 44) }
      VStack(alignment: message.isFromArgon ? .leading : .trailing, spacing: 4) {
        Text(attributed)
          .font(Argon.body)
          .textSelection(.enabled)
          .foregroundStyle(message.isFromArgon ? Argon.Tone.primary : Color.white)
          .padding(.horizontal, 15).padding(.vertical, 11)
          .background {
            if message.isFromArgon {
              RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(.regularMaterial)
                .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous)
                  .fill(Argon.accent.opacity(0.10)))
                .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous)
                  .strokeBorder(Argon.hairline, lineWidth: 1))
            } else {
              RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(LinearGradient(colors: [Argon.accent, Argon.accentDeep],
                                     startPoint: .topLeading, endPoint: .bottomTrailing))
                .opacity(message.pending ? 0.5 : 1)
            }
          }
        if message.pending {
          Text("sending…").font(Argon.label).foregroundStyle(Argon.Tone.faint)
        }
      }
      if message.isFromArgon { Spacer(minLength: 44) }
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
