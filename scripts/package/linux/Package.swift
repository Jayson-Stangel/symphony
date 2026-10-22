// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SymphonyHost",
    products: [.executable(name: "symphony-host", targets: ["Host"])],
    dependencies: [
        .package(url: "https://github.com/apple/swift-crypto.git", revision: "e8d6eba1fef23ae5b359c46b03f7d94be2f41fed"),
        .package(url: "https://github.com/apple/swift-asn1.git", exact: "1.3.1")
    ],
    targets: [
        .executableTarget(name: "Host", dependencies: [.product(name: "Crypto", package: "swift-crypto")])
    ],
    swiftLanguageModes: [.v5]
)
