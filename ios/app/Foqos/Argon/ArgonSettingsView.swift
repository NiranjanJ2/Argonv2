import SwiftUI

/// Where the server lives, and whether anything is stuck.
///
/// v1 had no way to see that a write had failed or that the token was wrong;
/// the app just quietly did nothing. Everything that can go wrong between the
/// phone and the server is visible here.
///
/// Built from ArgonGlass rather than a `Form`. A Form paints its own grouped
/// background and its own row insets, so on the ambience it read as a stock
/// iOS settings screen dropped into a different app — the one screen that
/// ignored the design system.
struct ArgonSettingsView: View {
  let store: ArgonStore
  @AppStorage("argon.base") private var base = "http://192.168.68.72:3997"
  @AppStorage("argon.token") private var token = ArgonBridge.defaultToken
  @State private var checking = false
  @State private var result: String?

  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(spacing: 16) {
          server
          status
          actions
        }
        .padding(.horizontal, 16)
        .padding(.top, 4)
      }
      // The floating tab bar sits over the last card otherwise. A trailing
      // spacer does not help: when the content already fits there is nothing
      // to scroll, so the card just stays underneath the bar.
      .contentMargins(.bottom, 96, for: .scrollContent)
      .scrollContentBackground(.hidden)
      .argonAmbience(ticking: store.state.ticking)
      .navigationTitle("Settings")
      .navigationBarTitleDisplayMode(.large)
      .toolbarBackground(.hidden, for: .navigationBar)
    }
    .tint(Argon.accent)
  }

  // MARK: - cards

  private var server: some View {
    ArgonGlass {
      VStack(alignment: .leading, spacing: 14) {
        heading("Server")
        field("Address") {
          TextField("http://host:port", text: $base)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .keyboardType(.URL)
        }
        field("API token") { SecureField("required", text: $token) }

        Button {
          Task { await check() }
        } label: {
          HStack(spacing: 8) {
            if checking {
              ProgressView().controlSize(.small).tint(.white)
            } else {
              Image(systemName: "antenna.radiowaves.left.and.right")
            }
            Text(checking ? "Checking…" : "Test connection")
          }
          .font(Argon.body.weight(.semibold))
          .foregroundStyle(.white)
          .frame(maxWidth: .infinity, minHeight: 44)
          .background {
            Capsule().fill(LinearGradient(
              colors: [Argon.accent, Argon.accentDeep],
              startPoint: .topLeading, endPoint: .bottomTrailing))
          }
        }
        .buttonStyle(.plain)
        .disabled(checking)

        if let result {
          Label(result, systemImage: result.hasPrefix("OK")
                ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
            .font(Argon.detail)
            .foregroundStyle(result.hasPrefix("OK") ? Argon.running : Argon.overdue)
            .fixedSize(horizontal: false, vertical: true)
        }
      }
    }
  }

  private var status: some View {
    ArgonGlass {
      VStack(alignment: .leading, spacing: 12) {
        heading("Status")

        row("Connection") {
          switch store.connection {
          case .never:
            Text("never reached").foregroundStyle(Argon.Tone.faint)
          case .live(let at):
            Label("live, \(at.argonAgo)", systemImage: "circle.fill")
              .font(Argon.detail)
              .foregroundStyle(Argon.running)
          case .stale(let at, let why):
            VStack(alignment: .trailing, spacing: 2) {
              Text("cached \(at.argonAgo)").foregroundStyle(Argon.overdue)
              // Only when it adds something. This rendered "cached 13h ago"
              // with the word "cached" underneath it.
              if why != "cached" {
                Text(why).font(Argon.label)
                  .foregroundStyle(Argon.Tone.faint)
                  .multilineTextAlignment(.trailing)
              }
            }
          }
        }

        ArgonDivider()
        row("Queued writes") {
          Text("\(store.pendingCount)")
            .foregroundStyle(store.pendingCount > 0 ? Argon.overdue : Argon.Tone.secondary)
        }

        if let budget = store.state.budget, budget.cap > 0 {
          ArgonDivider()
          row("Spend this month") {
            Text(String(format: "$%.2f / $%.0f", budget.spent, budget.cap))
              .foregroundStyle(budget.isNearCap ? Argon.overdue : Argon.Tone.secondary)
          }
          ArgonDivider()
          row("Prompt cache") {
            Text(String(format: "%.0f%%", budget.cachedFraction * 100))
              .foregroundStyle(Argon.Tone.secondary)
          }
        }
      }
    }
  }

  private var actions: some View {
    ArgonGlass {
      VStack(alignment: .leading, spacing: 12) {
        secondary("Retry queued writes", icon: "arrow.clockwise") {
          Task { await store.flush() }
        }
        .disabled(store.pendingCount == 0)
        .opacity(store.pendingCount == 0 ? 0.4 : 1)

        ArgonDivider()
        secondary("Refresh now", icon: "arrow.down.circle") {
          Task { await store.refresh() }
        }

        Text("Writes are kept on the phone until the server accepts them, "
             + "so a tap is never lost to a dropped connection.")
          .font(Argon.label)
          .foregroundStyle(Argon.Tone.faint)
          .fixedSize(horizontal: false, vertical: true)
          .padding(.top, 2)
      }
    }
  }

  // MARK: - parts

  private func heading(_ text: String) -> some View {
    Text(text.uppercased())
      .font(Argon.label.weight(.semibold))
      .tracking(1.2)
      .foregroundStyle(Argon.accentSoft)
  }

  private func field<C: View>(_ label: String,
                              @ViewBuilder content: () -> C) -> some View {
    VStack(alignment: .leading, spacing: 5) {
      Text(label).font(Argon.label).foregroundStyle(Argon.Tone.faint)
      content()
        .font(Argon.body)
        .foregroundStyle(Argon.Tone.primary)
        .padding(.horizontal, 12)
        .frame(minHeight: 44)
        .background {
          RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(Color.black.opacity(0.22))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
              .strokeBorder(Argon.hairline, lineWidth: 1))
        }
    }
  }

  private func row<C: View>(_ label: String,
                            @ViewBuilder value: () -> C) -> some View {
    HStack(alignment: .firstTextBaseline) {
      Text(label).font(Argon.body).foregroundStyle(Argon.Tone.primary)
      Spacer(minLength: 12)
      value().font(Argon.detail)
    }
    .frame(minHeight: 28)
  }

  private func secondary(_ title: String, icon: String,
                         action: @escaping () -> Void) -> some View {
    Button(action: action) {
      HStack(spacing: 10) {
        Image(systemName: icon).foregroundStyle(Argon.accentSoft)
        Text(title).font(Argon.body).foregroundStyle(Argon.Tone.primary)
        Spacer()
      }
      .frame(minHeight: 44)          // the whole row is the target, not the text
      .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
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
