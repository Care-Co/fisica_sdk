// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "FisicaScale", platforms: [.macOS(.v11), .iOS(.v13)],
    products: [.library(name: "FisicaScale", targets: ["FisicaScale"])],
    targets: [.binaryTarget(name: "CFisica", path: "CFisica.xcframework"),
              .target(name: "FisicaScale", dependencies: ["CFisica"], linkerSettings: [.linkedLibrary("c++"), .linkedFramework("CoreFoundation")]),
              .testTarget(name: "FisicaScaleTests", dependencies: ["FisicaScale"])])
