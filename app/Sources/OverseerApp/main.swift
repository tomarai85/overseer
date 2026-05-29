// Overseer — the OPERATE cockpit (native macOS window).
//
// You talk to one Fleet Parent (left: "Parent"); it spawns and drives child
// workers for you. The sidebar lists your workers + the whole fleet's true
// state; the main pane is the parent chat, or a selected worker's live screen.
// All logic is the proven Python core, called via `~/overseer/bin/overseer`.
//
// AppKit bootstrap (not bundled .app yet) so the window reliably shows + a
// menu-bar item stays for at-a-glance. SwiftUI content lives in Cockpit.swift.

import AppKit
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var statusItem: NSStatusItem!

    func applicationDidFinishLaunching(_ note: Notification) {
        let hosting = NSHostingController(rootView: CockpitView())
        window = NSWindow(contentViewController: hosting)
        window.title = "Overseer — Fleet Cockpit"
        window.styleMask = [.titled, .closable, .miniaturizable, .resizable]
        window.setContentSize(NSSize(width: 960, height: 640))
        window.center()
        window.makeKeyAndOrderFront(nil)

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "OV"
        let menu = NSMenu()
        let open = NSMenuItem(title: "Open Cockpit", action: #selector(showWindow), keyEquivalent: "o")
        open.target = self
        menu.addItem(open)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit Overseer", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem.menu = menu

        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
