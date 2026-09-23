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
        .background(Argon.Ink.slate)
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
    .argonAmbience()
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
      Text(store.connection.isLive ? "No messages yet" : "No cached conversation")
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
        .padding(.horizontal, 12).padding(.vertical, 10)
        .overlay(DraftFrame(overshoot: 6).stroke(Argon.lineStrong, lineWidth: 1))

      Button(action: send) {
        // A square key, like the checkbox: blue outline when there is
        // something to send, a faint one when there is not.
        Image(systemName: "arrow.up")
          .font(.body.weight(.semibold))
          .foregroundStyle(canSend ? Argon.accent : Argon.Tone.faint)
          .frame(width: 40, height: 40)
          .background(canSend ? Argon.accent.opacity(0.12) : .clear)
          .overlay(Rectangle().strokeBorder(canSend ? Argon.accent.opacity(0.6)
                                                    : Argon.line, lineWidth: 1))
      }
      .buttonStyle(.plain)
      .disabled(!canSend)
      .accessibilityLabel("Send message")
      .animation(.easeOut(duration: 0.15), value: canSend)
    }
    .padding(.horizontal, 22).padding(.vertical, 12)
    .background(Argon.Ink.base)
    .overlay(alignment: .top) { ArgonDivider() }
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
    .padding(.leading, 14).padding(.vertical, 6)
    .overlay(alignment: .leading) { ArgonRule() }
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
          .foregroundStyle(Argon.Tone.primary)
          .modifier(BubbleFrame(fromArgon: message.isFromArgon))
          .opacity(message.pending ? 0.6 : 1)
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

/// Argon speaks on a rule; he speaks in a box.
///
/// Argon's text sits against a single vertical construction line that runs a
/// little past the paragraph at both ends. His own messages are outlined in
/// blue with the corners overshooting. No filled bubbles: which voice is which
/// is carried by the linework alone.
private struct BubbleFrame: ViewModifier {
  let fromArgon: Bool

  func body(content: Content) -> some View {
    if fromArgon {
      content
        .padding(.leading, 14).padding(.vertical, 4)
        .overlay(alignment: .leading) { ArgonRule() }
    } else {
      content
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(Argon.accent.opacity(0.08))
        .overlay(DraftFrame(overshoot: 6).stroke(Argon.accent.opacity(0.6), lineWidth: 1))
        .padding(6)
    }
  }
}

/// The vertical line Argon's words sit against, overshooting both ends.
struct ArgonRule: View {
  var body: some View {
    Rectangle().fill(Argon.lineStrong).frame(width: 1)
      .padding(.vertical, -Argon.overshoot / 2)
  }
}
