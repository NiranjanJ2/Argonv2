import SwiftUI

/// Where the server lives, and whether anything is stuck.
///
/// v1 had no way to see that a write had failed or that the token was wrong;
/// the app just quietly did nothing. Everything that can go wrong between the
/// phone and the server is visible here.
///
/// The same drawing sheet as Today: named sections, each one draft box.
struct ArgonSettingsView: View {
  let store: ArgonStore
  @AppStorage("argon.base") private var base = ArgonBridge.publicURL
  @AppStorage("argon.token") private var token = ArgonBridge.defaultToken
  @State private var checking = false
  @State private var result: String?

  var body: some View {
    List {
      Text("Settings").font(Argon.screenTitle).foregroundStyle(Argon.Tone.primary)
        .draftLabelRow(top: 6)

      label("Server")
      field("Address") {
        TextField("http://host:port", text: $base)
          .textInputAutocapitalization(.never)
          .autocorrectionDisabled()
          .keyboardType(.URL)
      }
      .draftRow(.first)
      field("Token") { SecureField("required", text: $token) }
        .draftRow(.middle)
      Button {
        Task { await check() }
      } label: {
        HStack {
          Text(checking ? "Checking…" : "Test connection").foregroundStyle(Argon.accent)
          Spacer()
          if checking { ProgressView().controlSize(.small) }
        }
        .padding(.vertical, 12)
        .contentShape(Rectangle())
      }
      .buttonStyle(.plain)
      .disabled(checking)
      .draftRow(result == nil ? .last : .middle)
      if let result {
        let ok = result.hasPrefix("OK")
        Text(result)
          .font(Argon.detail)
          .foregroundStyle(ok ? Argon.accent : Argon.overdue)
          .padding(.vertical, 12)
          .draftRow(.last, fill: ok ? .clear : Argon.overdue.opacity(0.10))
      }

      label("Status")
      value("Connection") { connection }.draftRow(.first)
      value("Queued writes") {
        Text("\(store.pendingCount)")
          .foregroundStyle(store.pendingCount > 0 ? Argon.overdue : Argon.Tone.secondary)
      }
      .draftRow(store.state.budget.map { $0.cap > 0 } == true ? .middle : .last)
      if let budget = store.state.budget, budget.cap > 0 {
        value("Spend this month") {
          Text(String(format: "$%.2f of $%.0f", budget.spent, budget.cap))
            .foregroundStyle(budget.isNearCap ? Argon.overdue : Argon.Tone.secondary)
        }
        .draftRow(.middle)
        value("Prompt cache") {
          Text(String(format: "%.0f%%", budget.cachedFraction * 100))
            .foregroundStyle(Argon.Tone.secondary)
        }
        .draftRow(.last)
      }

      label("Sync")
      action("Retry queued writes") { Task { await store.flush() } }
        .disabled(store.pendingCount == 0)
        .opacity(store.pendingCount == 0 ? 0.45 : 1)
        .draftRow(.first)
      action("Refresh now") { Task { await store.refresh() } }
        .draftRow(.last)
      Text("Writes are kept on the phone until the server accepts them, "
           + "so a tap is never lost to a dropped connection.")
        .font(Argon.label).foregroundStyle(Argon.Tone.faint)
        .draftLabelRow(top: 4)
    }
    .listStyle(.plain)
    .scrollContentBackground(.hidden)
    .environment(\.defaultMinListRowHeight, 0)
    .contentMargins(.bottom, 96, for: .scrollContent)
    .argonAmbience()
    .tint(Argon.accent)
  }

  private func label(_ title: String) -> some View {
    Text(title).font(Argon.caption).foregroundStyle(Argon.Tone.secondary)
      .draftLabelRow()
  }

  private func field<C: View>(_ name: String, @ViewBuilder content: () -> C) -> some View {
    HStack {
      Text(name).foregroundStyle(Argon.Tone.secondary)
      Spacer(minLength: 16)
      content().multilineTextAlignment(.trailing).foregroundStyle(Argon.Tone.primary)
    }
    .font(Argon.body)
    .padding(.vertical, 12)
  }

  private func value<C: View>(_ name: String, @ViewBuilder content: () -> C) -> some View {
    HStack(alignment: .firstTextBaseline) {
      Text(name).foregroundStyle(Argon.Tone.primary)
      Spacer(minLength: 16)
      content()
    }
    .font(Argon.body)
    .padding(.vertical, 12)
  }

  private func action(_ title: String, run: @escaping () -> Void) -> some View {
    Button(action: run) {
      HStack { Text(title).foregroundStyle(Argon.accent); Spacer() }
        .font(Argon.body)
        .padding(.vertical, 12)
        .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
  }

  @ViewBuilder private var connection: some View {
    switch store.connection {
    case .never:
      Text("never reached").foregroundStyle(Argon.Tone.faint)
    case .live(let at):
      Text("live, \(at.argonAgo)").foregroundStyle(Argon.Tone.secondary)
    case .stale(let at, let why):
      VStack(alignment: .trailing, spacing: 2) {
        Text("cached \(at.argonAgo)").foregroundStyle(Argon.overdue)
        // Only when it adds something: "cached 13h ago" over "cached".
        if why != "cached" {
          Text(why).font(Argon.label).foregroundStyle(Argon.Tone.faint)
            .multilineTextAlignment(.trailing)
        }
      }
    }
  }

  private func check() async {
    checking = true
    defer { checking = false }
    guard let url = URL(string: base) else { result = "That isn't a URL."; return }
    await ArgonAppDelegate.shared.reconfigure()
    await store.refresh()
    result = store.connection.isLive
      ? "OK — reached \(url.host() ?? base)"
      : (store.failure ?? "Couldn't reach it.")
  }
}
