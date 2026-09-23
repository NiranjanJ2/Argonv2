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
            ForEach(store.messages) { message in
              ArgonBubble(message: message, tasks: store.state.tasks) { action in
                Task { await store.perform(action) }
              }
              .id(message.id)
            }
            if store.awaitingReply { ArgonTyping().id("typing") }
          }
          .padding(.horizontal, 18).padding(.vertical, 14)
        }
        .scrollIndicators(.hidden)
        // Drag the transcript to put the keyboard away. The composer is pinned
        // to the keyboard and the field is multiline, so Return inserts a
        // newline rather than dismissing — without this there was no way out
        // of the keyboard at all except switching tabs.
        .scrollDismissesKeyboard(.interactively)
        // Without this the topmost bubble rides up under the status bar.
        .safeAreaPadding(.top, 8)
        .onChange(of: store.messages.count) { _, _ in scroll(proxy) }
        .onChange(of: store.awaitingReply) { _, _ in scroll(proxy) }
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
    // An `argon:` link inside a sentence is prose, not a button row, but it is
    // still a link: without this, iOS tries to open the scheme and nothing
    // happens. Same path as the buttons; every other scheme opens as usual.
    .environment(\.openURL, OpenURLAction { url in
      guard url.scheme == "argon" else { return .systemAction }
      if let action = ArgonAction(label: "", url: url.absoluteString) {
        Task { await store.perform(action) }
      }
      return .handled
    })
    .argonAmbience(ticking: store.state.ticking)
    .task { await store.markRead() }
    .toolbar {
      // Dragging is discoverable only if you already know it. This is the
      // affordance people actually look for.
      ToolbarItemGroup(placement: .keyboard) {
        Spacer()
        Button {
          focused = false
        } label: {
          Label("Done", systemImage: "keyboard.chevron.compact.down")
        }
        .font(Argon.body.weight(.semibold))
        .tint(Argon.accent)
      }
    }
  }

  private var empty: some View {
    VStack(spacing: 8) {
      Image(systemName: "bubble.left.and.bubble.right.fill")
        .font(.largeTitle).foregroundStyle(Argon.accentSoft)
        .background(ArgonBloom(size: 200))
        .argonGlow(strength: 0.8)
      Text(store.connection.isLive ? "Nothing yet." : "No cached conversation.")
        .font(Argon.body).foregroundStyle(Argon.Tone.secondary)
      Text("Ask what's due tonight.").font(Argon.detail).foregroundStyle(Argon.Tone.faint)
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
          // Was Tone.faint on 8% white — about 2.2:1, under the 3:1 minimum
          // for a non-text element.
          .foregroundStyle(canSend ? Color.white : Argon.Tone.secondary)
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
      .accessibilityLabel("Send message")
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
    guard let last = store.awaitingReply ? "typing" : store.messages.last?.id else { return }
    if animated { withAnimation { proxy.scrollTo(last, anchor: .bottom) } }
    else { proxy.scrollTo(last, anchor: .bottom) }
  }
}

/// Argon is working on an answer. v1 had this; v2 went straight from
/// "sending…" to nothing for up to a minute.
struct ArgonTyping: View {
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
    TimelineView(.animation(paused: reduceMotion)) { context in
      let t = context.date.timeIntervalSinceReferenceDate * 4
      HStack(spacing: 5) {
        ForEach(0..<3, id: \.self) { i in
          Circle()
            .fill(Argon.Tone.secondary)
            .frame(width: 7, height: 7)
            .opacity(reduceMotion ? 0.7 : 0.35 + 0.65 * max(0, sin(t - Double(i) * 0.9)))
        }
      }
    }
    .padding(.horizontal, 16).padding(.vertical, 14)
    .background(RoundedRectangle(cornerRadius: 20, style: .continuous)
      .fill(.ultraThinMaterial))
    .frame(maxWidth: .infinity, alignment: .leading)
    .accessibilityLabel("Argon is replying")
  }
}

struct ArgonBubble: View {
  let message: ArgonMessage
  /// The board, so Argon's buttons know whether they can act.
  var tasks: [ArgonTask] = []
  var onAction: ((ArgonAction) -> Void)? = nil

  var body: some View {
    HStack {
      if !message.isFromArgon { Spacer(minLength: 44) }
      VStack(alignment: message.isFromArgon ? .leading : .trailing, spacing: 4) {
        content
          .font(Argon.body)
          .textSelection(.enabled)
          .foregroundStyle(message.isFromArgon ? Argon.Tone.primary : Color.white)
          .padding(.horizontal, 15).padding(.vertical, 11)
          .background {
            if message.isFromArgon {
              // 10% accent over regularMaterial reads as plain grey on a dark
              // ground — the material's own opacity swallows it. A thinner
              // material and a real gradient let the blue actually arrive,
              // while staying clearly lighter than his own solid-blue bubble.
              RoundedRectangle(cornerRadius: 20, style: .continuous)
                .fill(.ultraThinMaterial)
                .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous)
                  .fill(LinearGradient(
                    colors: [Argon.accent.opacity(0.26), Argon.accentDeep.opacity(0.20)],
                    startPoint: .topLeading, endPoint: .bottomTrailing)))
                .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous)
                  .strokeBorder(Argon.hairlineBright, lineWidth: 1))
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

  /// Only Argon writes markdown. What he typed is shown as typed: an
  /// asterisk or a `- [ ]` in his own message is not a request for styling.
  @ViewBuilder private var content: some View {
    if message.isFromArgon {
      ArgonRichText(text: message.text, messageID: message.id,
                    tasks: tasks, onAction: onAction)
    } else {
      Text(verbatim: message.text)
    }
  }
}
