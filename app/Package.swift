// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "OverseerApp",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "OverseerApp",
            path: "Sources/OverseerApp"
        )
    ]
)
