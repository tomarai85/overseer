// make-icon.swift — generate Overseer.icns programmatically (no design tool).
//   swift app/tools/make-icon.swift
// Motif: a radar/eye over the fleet — concentric rings + a center dot on a blue
// gradient rounded square. Writes app/Resources/Overseer.{iconset,icns}.
import AppKit
import Foundation

func pngData(px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px,
                              bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                              isPlanar: false, colorSpaceName: .deviceRGB,
                              bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let p = CGFloat(px)
    let inset = p * 0.06
    let body = NSRect(x: inset, y: inset, width: p - inset * 2, height: p - inset * 2)
    let round = NSBezierPath(roundedRect: body, xRadius: p * 0.22, yRadius: p * 0.22)
    NSGradient(colors: [NSColor(calibratedRed: 0.10, green: 0.13, blue: 0.28, alpha: 1),
                        NSColor(calibratedRed: 0.19, green: 0.53, blue: 0.96, alpha: 1)])?
        .draw(in: round, angle: -90)

    let cx = body.midX, cy = body.midY
    NSColor.white.withAlphaComponent(0.92).setStroke()
    for f in [0.30, 0.185] as [CGFloat] {
        let rr = p * f
        let ring = NSBezierPath(ovalIn: NSRect(x: cx - rr, y: cy - rr, width: rr * 2, height: rr * 2))
        ring.lineWidth = p * 0.028
        ring.stroke()
    }
    // sweep line
    NSColor.white.withAlphaComponent(0.85).setStroke()
    let sweep = NSBezierPath()
    sweep.move(to: NSPoint(x: cx, y: cy))
    sweep.line(to: NSPoint(x: cx + p * 0.30 * 0.7, y: cy + p * 0.30 * 0.7))
    sweep.lineWidth = p * 0.025
    sweep.stroke()
    let dotR = p * 0.055
    NSColor.white.setFill()
    NSBezierPath(ovalIn: NSRect(x: cx - dotR, y: cy - dotR, width: dotR * 2, height: dotR * 2)).fill()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

let here = URL(fileURLWithPath: CommandLine.arguments.first ?? "")
    .deletingLastPathComponent().deletingLastPathComponent()   // app/
let iconset = here.appendingPathComponent("Resources/Overseer.iconset")
try? FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

let specs: [(String, Int)] = [
    ("icon_16x16", 16), ("icon_16x16@2x", 32),
    ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256),
    ("icon_256x256", 256), ("icon_256x256@2x", 512),
    ("icon_512x512", 512), ("icon_512x512@2x", 1024),
]
for (name, px) in specs {
    try! pngData(px: px).write(to: iconset.appendingPathComponent("\(name).png"))
}
let icns = here.appendingPathComponent("Resources/Overseer.icns")
let task = Process()
task.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
task.arguments = ["-c", "icns", iconset.path, "-o", icns.path]
try! task.run(); task.waitUntilExit()
print("wrote \(icns.path)")
