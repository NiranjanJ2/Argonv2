import SwiftUI

/// An Argon message rendered as the system prompt promises: headings, lists,
/// checkboxes, and rows of buttons. The parsing is `ArgonMarkdown`; this is
/// only the drawing.
///
/// Font and colour are left to the caller, so it inherits the bubble's
/// `Argon.body` and `Tone.primary` like the plain `Text` it replaced.
struct ArgonRichText: View {
  let text: String
  /// `ArgonMessage.id`. Checkbox ticks are stored against it, so it must be
  /// the server's id, stable across polls and relaunches.
  let messageID: String
  /// The board, to decide which buttons can act. A button whose task is
  /// missing, ambiguous, or already in the state it asks for renders
  /// disabled rather than doing nothing when tapped.
  var tasks: [ArgonTask] = []
  /// Nil in previews and anywhere nothing can act, which disables every button.
  var onAction: ((ArgonAction) -> Void)? = nil

  // Mirrors UserDefaults so a tap redraws at once; the defaults are the truth
  // across relaunches.
  @State private var ticks: [String: Bool] = [:]

  var body: some View {
    let blocks = ArgonMarkdown.blocks(text, messageID: messageID)
    VStack(alignment: .leading, spacing: 7) {
      // By position: identical lines are legal and must not collide.
      ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
        switch block {
        case .heading(let t):
          Text(ArgonMarkdown.inline(t))
            .font(Argon.heading)
            .padding(.top, 2)

        case .paragraph(let t):
          Text(ArgonMarkdown.inline(t))

        case .bullet(let t):
          row(marker: Text("•"), text: t)

        case .numbered(let i, let t):
          row(marker: Text("\(i).").monospacedDigit(), text: t)

        case .checkbox(let id, let t, let initial):
          checkbox(id: id, text: t, initial: initial)

        case .actions(let row):
          buttons(row)

        case .divider:
          Rectangle().fill(Argon.hairline).frame(height: 1).padding(.vertical, 3)
        }
      }
    }
  }

  private func row(marker: Text, text: String) -> some View {
    HStack(alignment: .firstTextBaseline, spacing: 8) {
      marker.fontWeight(.semibold).foregroundStyle(Argon.accentSoft)
      Text(ArgonMarkdown.inline(text))
      Spacer(minLength: 0)
    }
  }

  private func buttons(_ row: [ArgonAction]) -> some View {
    // Wraps rather than truncating: three long labels overflow a phone-width
    // bubble, and a clipped "Start Chem la…" hides which task it starts.
    ViewThatFits(in: .horizontal) {
      HStack(spacing: 8) { buttonRow(row) }
      VStack(alignment: .leading, spacing: 8) { buttonRow(row) }
    }
    .padding(.top, 2)
  }

  @ViewBuilder
  private func buttonRow(_ row: [ArgonAction]) -> some View {
    ForEach(Array(row.enumerated()), id: \.offset) { _, action in
      let live = onAction != nil && action.target(in: tasks) != nil
      Button { onAction?(action) } label: {
        Text(action.label)
          .font(Argon.detail.weight(.semibold))
          .foregroundStyle(live ? Argon.accent : Argon.Tone.faint)
          .padding(.horizontal, 12).padding(.vertical, 6)
          .background(Argon.Ink.raised,
                      in: RoundedRectangle(cornerRadius: 8, style: .continuous))
          .fixedSize()
      }
      .buttonStyle(.plain)
      .disabled(!live)
      .accessibilityHint(live ? "" : "Nothing to act on")
    }
  }

  private func checkbox(id: String, text: String, initial: Bool) -> some View {
    let on = ticks[id] ?? ArgonCheckboxState.checked(id) ?? initial
    return Button {
      ticks[id] = !on
      ArgonCheckboxState.set(id, !on)
    } label: {
      HStack(alignment: .firstTextBaseline, spacing: 8) {
        Image(systemName: on ? "checkmark.circle.fill" : "circle")
          .foregroundStyle(on ? Argon.accent : Argon.Tone.secondary)
        Text(ArgonMarkdown.inline(text))
          .strikethrough(on, color: Argon.Tone.faint)
          .foregroundStyle(on ? Argon.Tone.faint : Argon.Tone.primary)
        Spacer(minLength: 0)
      }
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .accessibilityAddTraits(on ? .isSelected : [])
  }
}
