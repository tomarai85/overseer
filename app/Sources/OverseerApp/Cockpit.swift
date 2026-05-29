import AppKit
import SwiftUI

// MARK: - Models

struct Lead: Identifiable, Decodable, Hashable {
    let session_id: String
    let name: String
    let state: String
    let age: String
    var id: String { session_id }
}

struct ChatMsg: Identifiable, Decodable, Hashable {
    let role: String
    let text: String
    let ts: Double
    var id: String { "\(ts)-\(role)-\(text.hashValue)" }
}

struct Worker: Decodable, Hashable {
    let name: String
    let state: String
    let hint: String?
}

struct Supervise: Decodable {
    let children: [Worker]
    let summary: String
}

enum Selection: Hashable { case parent, worker(String) }

func glyph(_ state: String) -> String {
    switch state {
    case "needs-human": return "⚠"
    case "crashed":     return "✗"
    case "blocked":     return "◐"
    case "waiting":     return "⏸"
    case "working":     return "▶"
    case "done":        return "✓"
    default:            return "•"
    }
}

func stateColor(_ state: String) -> Color {
    switch state {
    case "needs-human": return .orange
    case "crashed":     return .red
    case "blocked":     return .yellow
    case "waiting":     return .gray
    case "working":     return .green
    default:            return .secondary
    }
}

// MARK: - Service (calls the proven Python core via bin/overseer)

@MainActor
final class Cockpit: ObservableObject {
    @Published var leads: [Lead] = []
    @Published var workersState: [Worker] = []
    @Published var awaySummary: String = ""
    @Published var messages: [ChatMsg] = []
    @Published var selection: Selection = .parent
    @Published var input: String = ""
    @Published var sending = false
    @Published var workerScreen = ""
    @Published var notify = true

    private var prevState: [String: String] = [:]
    private var lastNotified: [String: Double] = [:]
    private var polling = false   // single-flight: a slow poll must not overlap the next

    nonisolated static let bin = NSHomeDirectory() + "/overseer/bin/overseer"

    /// Fire a native macOS notification via osascript — works for a non-bundled
    /// binary (UNUserNotificationCenter needs a bundle id; osascript doesn't).
    nonisolated static func postNotification(_ title: String, _ body: String) {
        func esc(_ s: String) -> String {
            s.replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "\"", with: "\\\"")
                .replacingOccurrences(of: "\n", with: " ")
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", "display notification \"\(esc(body))\" with title \"\(esc(title))\""]
        try? p.run()
    }

    /// Alert only on ENTRY into needs-you/crashed, debounced 120s per session,
    /// so a session sitting in needs-you doesn't nag and a busy fleet isn't spam.
    func detectAndNotify(_ fresh: [Lead]) {
        let alert: Set<String> = ["needs-human", "crashed"]
        let now = Date().timeIntervalSince1970
        if notify {
            for l in fresh where alert.contains(l.state) && prevState[l.session_id] != l.state {
                if now - (lastNotified[l.session_id] ?? 0) > 120 {
                    let what = l.state == "crashed" ? "crashed" : "needs you"
                    Self.postNotification("Overseer", "\(l.name) — \(what)")
                    lastNotified[l.session_id] = now
                }
            }
        }
        prevState = Dictionary(fresh.map { ($0.session_id, $0.state) }, uniquingKeysWith: { a, _ in a })
    }

    nonisolated static func run(_ args: [String]) -> Data {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: bin)
        p.arguments = args
        let out = Pipe(); p.standardOutput = out; p.standardError = Pipe()
        do { try p.run() } catch { return Data() }
        let data = out.fileHandleForReading.readDataToEndOfFile()  // drain before wait (no pipe-buffer deadlock)
        p.waitUntilExit()
        return data
    }

    func refresh() {
        guard !polling else { return }   // skip if a poll is still in flight (no overlap/race)
        polling = true
        Task.detached {
            let leadsData = Self.run(["leads"])
            let supData = Self.run(["supervise"])
            let leads = (try? JSONDecoder().decode([Lead].self, from: leadsData)) ?? []
            let sup = try? JSONDecoder().decode(Supervise.self, from: supData)
            await MainActor.run {
                self.detectAndNotify(leads)
                self.detectWorkerAlerts(sup?.children ?? [])
                self.leads = leads
                self.workersState = sup?.children ?? []
                self.awaySummary = sup?.summary ?? ""
                self.polling = false
            }
        }
    }

    /// Alert on an owned child entering stuck/crashed/needs-human (debounced).
    func detectWorkerAlerts(_ workers: [Worker]) {
        guard notify else { return }
        let alert: Set<String> = ["stuck", "crashed", "needs-human"]
        let now = Date().timeIntervalSince1970
        for w in workers where alert.contains(w.state) {
            let key = "worker:" + w.name
            if prevState[key] != w.state, now - (lastNotified[key] ?? 0) > 120 {
                Self.postNotification("Overseer — worker", "\(w.name) — \(w.state)")
                lastNotified[key] = now
            }
            prevState[key] = w.state
        }
        for w in workers where !alert.contains(w.state) { prevState["worker:" + w.name] = w.state }
    }

    func loadHistory() {
        Task.detached {
            let data = Self.run(["parent-history"])
            let msgs = (try? JSONDecoder().decode([ChatMsg].self, from: data)) ?? []
            await MainActor.run { self.messages = msgs }
        }
    }

    func refreshWorker(_ name: String) {
        Task.detached {
            let data = Self.run(["read", name, "44"])
            let screen = String(data: data, encoding: .utf8) ?? ""
            await MainActor.run { self.workerScreen = screen }
        }
    }

    func send() {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        input = ""
        messages.append(ChatMsg(role: "user", text: text, ts: Date().timeIntervalSince1970))
        sending = true
        Task.detached {
            let data = Self.run(["parent-send", text])
            let reply: String = {
                guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                    return "(no response)"
                }
                if let r = obj["response"] as? String, !r.isEmpty { return r }
                if let e = obj["error"] as? String { return "⚠ " + e }
                return "(no response)"
            }()
            await MainActor.run {
                self.messages.append(ChatMsg(role: "parent", text: reply, ts: Date().timeIntervalSince1970))
                self.sending = false
                self.refresh()
            }
        }
    }

    /// Start a worker from the GUI (spawn waits for it to boot, then optionally
    /// sends a first task). Selects it so Tom sees its screen immediately.
    func spawnWorker(name: String, dir: String, task: String) {
        let n = name.trimmingCharacters(in: .whitespaces)
        // names go to the CLI as argv + become tmux session names — keep them tame
        guard !n.isEmpty, n.range(of: "^[A-Za-z0-9._-]+$", options: .regularExpression) != nil else { return }
        let d = dir.trimmingCharacters(in: .whitespaces)
        let t = task.trimmingCharacters(in: .whitespacesAndNewlines)
        Task.detached {
            _ = Self.run(d.isEmpty ? ["spawn", n] : ["spawn", n, d])  // cmd_spawn waits for ready
            if !t.isEmpty { _ = Self.run(["send", n, t]) }
            await MainActor.run {
                self.refresh(); self.selection = .worker(n); self.refreshWorker(n)
            }
        }
    }

    func killWorker(_ name: String) {
        Task.detached {
            _ = Self.run(["kill", name])
            await MainActor.run { self.selection = .parent; self.refresh() }
        }
    }

    var counts: String {
        var c: [String: Int] = [:]
        for l in leads { c[l.state, default: 0] += 1 }
        let order = ["working", "waiting", "needs-human", "blocked", "crashed", "done"]
        return order.compactMap { k in (c[k] ?? 0) > 0 ? "\(glyph(k))\(c[k]!)" : nil }.joined(separator: "  ")
    }
}

// MARK: - Views

struct CockpitView: View {
    @StateObject private var c = Cockpit()
    private let tick = Timer.publish(every: 3, on: .main, in: .common).autoconnect()

    var body: some View {
        NavigationSplitView {
            Sidebar(c: c)
                .navigationSplitViewColumnWidth(min: 230, ideal: 250, max: 300)
        } detail: {
            switch c.selection {
            case .parent:          ParentChat(c: c)
            case .worker(let name): WorkerView(c: c, name: name)
            }
        }
        .frame(minWidth: 820, minHeight: 540)
        .onAppear { c.loadHistory(); c.refresh() }
        .onReceive(tick) { _ in
            c.refresh()
            if case .worker(let n) = c.selection { c.refreshWorker(n) }
        }
    }
}

struct Sidebar: View {
    @ObservedObject var c: Cockpit
    @State private var showNew = false

    var body: some View {
        List {
            Section {
                row(title: "Fleet Parent", glyphStr: "🧠", color: .blue,
                    selected: c.selection == .parent) { c.selection = .parent }
            }
            Section {
                if c.workersState.isEmpty {
                    Text("none yet — ask the parent, or tap +")
                        .font(.caption).foregroundColor(.secondary)
                } else {
                    ForEach(c.workersState, id: \.self) { w in
                        row(title: w.name, glyphStr: glyph(w.state), color: stateColor(w.state),
                            selected: c.selection == .worker(w.name)) {
                            c.selection = .worker(w.name); c.refreshWorker(w.name)
                        }
                        .help(w.state + (w.hint.map { " — " + $0 } ?? ""))
                    }
                }
            } header: {
                HStack {
                    Text("Workers you drive")
                    Spacer()
                    Button { showNew = true } label: { Image(systemName: "plus.circle") }
                        .buttonStyle(.plain).help("Start a new worker")
                }
            }
            Section("Fleet (monitored, read-only)") {
                ForEach(c.leads) { l in
                    HStack(spacing: 8) {
                        Text(glyph(l.state)).foregroundColor(stateColor(l.state))
                        Text(l.name).lineLimit(1).truncationMode(.middle)
                        Spacer()
                        Text(l.age).font(.caption2).foregroundColor(.secondary)
                    }
                    .font(.caption)
                    .help(l.state)   // hover shows the state word (⚠ = needs-you, not an error)
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) {
            VStack(spacing: 3) {
                if !c.awaySummary.isEmpty && c.awaySummary != "no owned workers" {
                    Text(c.awaySummary).font(.caption2).foregroundColor(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading).lineLimit(1)
                }
                HStack {
                    Text(c.counts.isEmpty ? "—" : c.counts)
                        .font(.caption.monospaced()).foregroundColor(.secondary)
                    Spacer()
                    Toggle(isOn: $c.notify) {
                        Image(systemName: c.notify ? "bell.fill" : "bell.slash")
                    }
                    .toggleStyle(.button).font(.caption)
                    .help("Notify on needs-you / crashed / stuck")
                }
            }
            .padding(6).background(.thinMaterial)
        }
        .sheet(isPresented: $showNew) { NewWorkerSheet(c: c, show: $showNew) }
    }

    @ViewBuilder
    func row(title: String, glyphStr: String, color: Color, selected: Bool, tap: @escaping () -> Void) -> some View {
        Button(action: tap) {
            HStack(spacing: 8) {
                Text(glyphStr).foregroundColor(color)
                Text(title).fontWeight(selected ? .semibold : .regular).lineLimit(1)
                Spacer()
            }
            .contentShape(Rectangle())
            .padding(.vertical, 2)
        }
        .buttonStyle(.plain)
        .listRowBackground(selected ? Color.accentColor.opacity(0.18) : Color.clear)
    }
}

struct ParentChat: View {
    @ObservedObject var c: Cockpit

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        if c.messages.isEmpty {
                            Text("Talk to the parent in plain language — \u{300c}Helix \u{306e}\u{30c6}\u{30b9}\u{30c8}\u{76f4}\u{3057}\u{3066}\u{300d}\u{300c}note \u{898b}\u{3066}\u{300d}\u{300c}OK\u{300d}.\nIt spawns and drives workers for you (never your own sessions).")
                                .font(.callout).foregroundColor(.secondary).padding()
                        }
                        ForEach(c.messages) { m in Bubble(msg: m) }
                        if c.sending {
                            HStack(spacing: 8) { ProgressView().scaleEffect(0.7); Text("parent is working\u{2026}").foregroundColor(.secondary) }
                                .padding(.vertical, 4).id("spinner")
                        }
                        Color.clear.frame(height: 1).id("bottom")
                    }.padding()
                }
                .onChange(of: c.messages.count) { _ in withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } }
                .onChange(of: c.sending) { _ in withAnimation { proxy.scrollTo("bottom", anchor: .bottom) } }
            }
            Divider()
            HStack(spacing: 8) {
                TextField("Message the parent\u{2026}", text: $c.input, axis: .vertical)
                    .textFieldStyle(.plain).lineLimit(1...5)
                    .padding(8).background(Color(NSColor.textBackgroundColor)).cornerRadius(8)
                    .onSubmit { c.send() }
                Button(action: { c.send() }) { Image(systemName: "arrow.up.circle.fill").font(.title2) }
                    .buttonStyle(.plain).disabled(c.sending || c.input.trimmingCharacters(in: .whitespaces).isEmpty)
            }.padding(10)
        }
        .navigationTitle("Fleet Parent")
    }
}

struct Bubble: View {
    let msg: ChatMsg
    var isUser: Bool { msg.role == "user" }
    var body: some View {
        HStack {
            if isUser { Spacer(minLength: 60) }
            Text(msg.text)
                .textSelection(.enabled)
                .padding(10)
                .background(isUser ? Color.accentColor.opacity(0.85) : Color(NSColor.controlBackgroundColor))
                .foregroundColor(isUser ? .white : .primary)
                .cornerRadius(12)
            if !isUser { Spacer(minLength: 60) }
        }
    }
}

struct WorkerView: View {
    @ObservedObject var c: Cockpit
    let name: String
    @State private var directInput = ""

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                Text(c.workerScreen.isEmpty ? "(reading\u{2026})" : cleanedScreen(c.workerScreen))
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding()
            }
            Divider()
            HStack(spacing: 8) {
                TextField("Send directly to \(name)\u{2026}", text: $directInput)
                    .textFieldStyle(.plain)
                    .padding(8).background(Color(NSColor.textBackgroundColor)).cornerRadius(8)
                    .onSubmit { sendDirect() }
                Button("Send") { sendDirect() }.disabled(directInput.trimmingCharacters(in: .whitespaces).isEmpty)
            }.padding(10)
        }
        .navigationTitle("worker: \(name)")
        .toolbar {
            ToolbarItem {
                Button { c.killWorker(name) } label: { Label("Kill", systemImage: "xmark.circle") }
                    .help("Stop this worker")
            }
        }
        .onAppear { c.refreshWorker(name) }
    }

    func sendDirect() {
        let t = directInput.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        directInput = ""
        Task.detached {
            _ = Cockpit.run(["send", name, t])
            await MainActor.run { c.refreshWorker(name) }
        }
    }
}

struct NewWorkerSheet: View {
    @ObservedObject var c: Cockpit
    @Binding var show: Bool
    @State private var name = ""
    @State private var dir = NSHomeDirectory()
    @State private var task = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Start a worker").font(.headline)
            TextField("name (e.g. helix)", text: $name).textFieldStyle(.roundedBorder)
            TextField("working dir", text: $dir).textFieldStyle(.roundedBorder)
            TextField("first task (optional)", text: $task, axis: .vertical)
                .lineLimit(1...4).textFieldStyle(.roundedBorder)
            Text("Spawns a NEW child Claude in that dir. Never touches your own sessions.")
                .font(.caption2).foregroundColor(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { show = false }
                Button("Start") {
                    c.spawnWorker(name: name, dir: dir, task: task); show = false
                }
                .keyboardShortcut(.defaultAction)
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20).frame(width: 440)
    }
}

/// Strip the Claude TUI chrome (box-drawing, welcome banner, bottom status bar)
/// from a captured pane so the worker view shows just the conversation.
func cleanedScreen(_ raw: String) -> String {
    let box = CharacterSet(charactersIn: "\u{2502}\u{256d}\u{256e}\u{2570}\u{256f}\u{2500}\u{2501}\u{2504}\u{2508} \u{2590}\u{259b}\u{259c}\u{258c}\u{259d}\u{2598}\u{2580}\t")
    let noise = ["bypass permissions", "Welcome back", "Tips for getting started",
                 "/release-notes", "MCP server", "What's new", "Opus 4.8 is here",
                 "Claude Code v", "for agents", "shift+tab"]
    var out: [String] = []
    var blanks = 0
    for line in raw.split(separator: "\n", omittingEmptySubsequences: false).map(String.init) {
        let s = line.trimmingCharacters(in: box)
        if s.isEmpty { blanks += 1; if blanks <= 1 { out.append("") }; continue }
        blanks = 0
        if s.hasPrefix("Weekly ") || s.hasPrefix("Code ") { continue }
        if noise.contains(where: { s.contains($0) }) { continue }
        out.append(s)
    }
    return out.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
}
