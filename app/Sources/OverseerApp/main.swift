// Overseer — menu-bar shell (native AppKit).
//
// The "eyes" of the cockpit as a real Mac app: a status-bar item showing live
// fleet counts at a glance, with a dropdown listing each session + state.
// State comes from the proven Python engine via `overseer leads` (JSON), so the
// hard logic (state-truth) is shared, not reimplemented.
//
// This is the minimal first slice. The send box + expandable window + the
// parent-AI cockpit come next; this proves "real Mac app, not a browser tab".

import AppKit

struct Lead: Decodable {
    let session_id: String
    let name: String
    let state: String
    let age: String
}

final class FleetController: NSObject {
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    var timer: Timer?

    func start() {
        statusItem.button?.title = "OV …"
        refreshNow()
        let t = Timer.scheduledTimer(timeInterval: 5, target: self,
                                     selector: #selector(refreshNow), userInfo: nil, repeats: true)
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

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

    @objc func refreshNow() {
        DispatchQueue.global().async {
            let leads = Self.fetchLeads()
            DispatchQueue.main.async { self.render(leads) }
        }
    }

    func render(_ leads: [Lead]) {
        var counts: [String: Int] = [:]
        for l in leads { counts[l.state, default: 0] += 1 }
        let working = counts["working"] ?? 0
        let waiting = counts["waiting"] ?? 0
        let needs   = counts["needs-human"] ?? 0
        let crashed = counts["crashed"] ?? 0

        // glanceable menu-bar title: working count, then anything that wants Tom
        var title = "▶\(working)"
        if waiting > 0 { title += " ⏸\(waiting)" }
        if needs   > 0 { title += " ⚠\(needs)" }
        if crashed > 0 { title += " ✗\(crashed)" }
        statusItem.button?.title = title

        let menu = NSMenu()
        let header = NSMenuItem(title: "Overseer — \(leads.count) live", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        menu.addItem(.separator())
        if leads.isEmpty {
            menu.addItem(NSMenuItem(title: "no live sessions", action: nil, keyEquivalent: ""))
        } else {
            for l in leads {
                menu.addItem(NSMenuItem(title: "\(glyph(l.state))  \(l.name)    \(l.age)",
                                        action: nil, keyEquivalent: ""))
            }
        }
        menu.addItem(.separator())
        let r = NSMenuItem(title: "Refresh now", action: #selector(refreshNow), keyEquivalent: "r")
        r.target = self
        menu.addItem(r)
        let q = NSMenuItem(title: "Quit Overseer", action: #selector(quit), keyEquivalent: "q")
        q.target = self
        menu.addItem(q)
        statusItem.menu = menu
    }

    @objc func quit() { NSApp.terminate(nil) }

    static func fetchLeads() -> [Lead] {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-lc", "\(NSHomeDirectory())/overseer/bin/overseer leads"]
        let outPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = Pipe()
        do { try p.run(); p.waitUntilExit() } catch { return [] }
        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        return (try? JSONDecoder().decode([Lead].self, from: data)) ?? []
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)  // menu-bar only, no dock icon
let controller = FleetController()
controller.start()
app.run()
