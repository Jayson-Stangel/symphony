// Release-built host utilities. No user machine compiler or scripting runtime is needed.
import Foundation
#if canImport(CryptoKit)
import CryptoKit
#else
import Crypto
#endif
import CoreFoundation
#if canImport(Darwin)
import Darwin
#else
#if canImport(Musl)
import Musl
#else
import Glibc
#endif
// Glibc also exports a struct named flock. Bind the BSD flock syscall explicitly;
// POSIX process locks do not preserve ownership through an inherited descriptor.
@_silgen_name("flock")
func flock(_ descriptor: Int32, _ operation: Int32) -> Int32
#endif

func fail(_ message: String) -> Never { FileHandle.standardError.write(Data(("symphony: " + message + "\n").utf8)); exit(1) }
func read(_ path: String) throws -> Data { try Data(contentsOf: URL(fileURLWithPath: path)) }
func json(_ data: Data) throws -> Any { try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) }
func object(_ value: Any?) -> [String: Any] { value as? [String: Any] ?? [:] }
func matches(_ value: String, _ pattern: String) -> Bool { value.range(of: pattern.replacingOccurrences(of: "$", with: "\\z"), options: .regularExpression) != nil }
func isBool(_ value: Any?, _ expected: Bool) -> Bool { guard let n = value as? NSNumber else { return false }; return CFGetTypeID(n) == CFBooleanGetTypeID() && n.boolValue == expected }
func integer(_ value: Any?) -> Int? { guard let n = value as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), !["d", "f"].contains(String(cString: n.objCType)), n.doubleValue == Double(n.intValue) else { return nil }; return n.intValue }
func checkedInfo(_ path: String, directory: Bool = false) -> stat {
    var info = stat()
    guard lstat(path, &info) == 0, info.st_uid == geteuid(), (info.st_mode & S_IFMT) == (directory ? S_IFDIR : S_IFREG), directory || info.st_nlink == 1 else { fail("unsafe or foreign metadata: \(path)") }
    return info
}
func validateManifestObject(_ manifest: [String: Any]) throws -> [String: Any] {
    let schema = integer(manifest["schema_version"])
    guard schema == 1 || schema == 2,
          let version = manifest["version"] as? String, matches(version, "^[0-9]+\\.[0-9]+\\.[0-9]+(-[A-Za-z0-9.-]+)?$"),
          let revision = manifest["source_revision"] as? String, matches(revision, "^[a-f0-9]{40}$"),
          ["candidate", "stable"].contains(manifest["channel"] as? String ?? ""),
          manifest["host_architectures"] as? [String] == ["arm64", "x86_64"] else { fail("invalid or unsupported release manifest") }
    if schema == 2 {
        let compatibility = object(manifest["compatibility"])
        let protocolOnly = compatibility["selection_mode"] as? String == "runner-protocol-v1"
        guard Set(compatibility.keys) == (protocolOnly ? Set(["runner_protocol", "selection_mode"]) : Set(["runner_protocol"])), integer(compatibility["runner_protocol"]) == 1 else { fail("unsupported manager/runner compatibility") }
    }
    if schema == 2 { validateComponentSelections(manifest, revision: revision) }
    else if manifest["component_selections"] != nil { fail("component selections require schema2") }
    guard Set(object(manifest["runners"]).keys) == Set(["codex", "claude"]) else { fail("both runner providers are required") }
    let bridgeOriginals = object(manifest["original_components"])
    if manifest["original_components"] != nil {
        guard schema == 1, Set(bridgeOriginals.keys) == Set(["codex", "claude"]) else { fail("invalid bridge original components") }
        for provider in ["codex", "claude"] { guard Set(object(bridgeOriginals[provider]).keys) == Set(["arm64", "x86_64"]) else { fail("incomplete bridge original components") } }
    }
    for provider in ["codex", "claude"] {
        guard Set(object(object(manifest["runners"])[provider]).keys) == Set(["arm64", "x86_64"]) else { fail("both runner architectures are required") }
        for arch in ["arm64", "x86_64"] {
            let selected = object(object(object(manifest["runners"])[provider])[arch])
            let runner = bridgeOriginals.isEmpty ? selected : object(object(bridgeOriginals[provider])[arch])
            if !bridgeOriginals.isEmpty {
                guard let originalImage = runner["image"] as? String, let selectedImage = selected["image"] as? String,
                      matches(selectedImage, "^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$"),
                      originalImage.split(separator: "@").last == selectedImage.split(separator: "@").last else { fail("bridge transfer changes original OCI digest") }
                var expected = runner; expected["image"] = selectedImage; expected.removeValue(forKey: "qualification")
                guard NSDictionary(dictionary: expected).isEqual(to: selected) else { fail("bridge selected image differs from original component") }
            }
            let tools = object(runner["tool_versions"])
            for tool in [provider, "git", "node", "playwright"] { guard let value = tools[tool] as? String, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { fail("missing recorded tool version: \(tool)") } }
            guard let runnerRevision = runner["source_revision"] as? String, matches(runnerRevision, "^[a-f0-9]{40}$"),
                  schema != 1 || runnerRevision == revision,
                  runner["platform"] as? String == (arch == "arm64" ? "linux/arm64" : "linux/amd64"),
                  let image = runner["image"] as? String,
                  matches(image, "^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$") else { fail("missing or invalid \(provider)/\(arch) release image") }
            if schema == 2 || !bridgeOriginals.isEmpty {
                let proof = object(runner["qualification"])
                let dependencies = object(runner["dependencies"])
                guard let engine = tools["elixir"] as? String, !engine.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                      dependencies["resolution_policy"] as? String == "retain-original-qualified-resolution",
                      let bases = dependencies["base_images"] as? [String], !bases.isEmpty,
                      bases.allSatisfy({ matches($0, "^[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}$") }),
                      let packages = dependencies["os_packages"] as? [String: String], !packages.isEmpty,
                      packages.allSatisfy({ !$0.key.isEmpty && !$0.value.isEmpty }),
                      let buildOptions = dependencies["build_options"] as? [String: String],
                      buildOptions["TARGETARCH"] == (arch == "arm64" ? "arm64" : "amd64") else { fail("missing original resolved runner dependencies") }
                guard let inputs = runner["input_identity"] as? String, matches(inputs, "^[a-f0-9]{64}$"),
                      integer(runner["protocol"]) == 1,
                      let imageConfig = runner["image_config"] as? String, matches(imageConfig, "^sha256:[a-f0-9]{64}$"),
                      proof["repository"] as? String == "Jayson-Stangel/symphony",
                      let run = integer(proof["run_id"]), run > 0,
                      let attempt = integer(proof["run_attempt"]), attempt > 0,
                      let artifact = integer(proof["artifact_id"]), artifact > 0,
                      let controller = proof["controller_revision"] as? String, matches(controller, "^[a-f0-9]{40}$"),
                      proof["source_revision"] as? String == runnerRevision,
                      proof["input_identity"] as? String == inputs,
                      proof["image"] as? String == image,
                      ["runner-\(provider)-\(arch)", "runners / runner-\(provider)-\(arch)",
                       "Build runner (\(provider), \(arch))", "Qualify runner components / Build runner (\(provider), \(arch))"].contains(proof["job_name"] as? String ?? ""),
                      proof["artifact_name"] as? String == "runner-candidate-\(provider)-\(arch)",
                      let artifactDigest = proof["artifact_digest"] as? String, matches(artifactDigest, "^sha256:[a-f0-9]{64}$"),
                      let archive = proof["archive_filename"] as? String,
                      ["\(provider)-\(arch).docker.tar.gz", "previous/\(provider)-\(arch).docker.tar.gz"].contains(archive),
                      let archiveDigest = proof["archive_sha256"] as? String, matches(archiveDigest, "^[a-f0-9]{64}$") else { fail("invalid original runner qualification or compatibility") }
                if let raw = proof["record_origin"] {
                    let origin = object(raw)
                    guard Set(origin.keys) == Set(["run_id", "run_attempt", "controller_revision", "job_name"]),
                          let recordRun = integer(origin["run_id"]), recordRun > 0,
                          let recordAttempt = integer(origin["run_attempt"]), recordAttempt > 0,
                          let recordController = origin["controller_revision"] as? String, matches(recordController, "^[a-f0-9]{40}$"),
                          ["recover-\(provider)-\(arch)", "native-record-recovery / recover-\(provider)-\(arch)",
                           "runner-\(provider)-\(arch)", "runners / runner-\(provider)-\(arch)",
                           "Recover native records (\(provider), \(arch))", "Build runner (\(provider), \(arch))",
                           "Qualify runner components / Build runner (\(provider), \(arch))"].contains(origin["job_name"] as? String ?? "") else { fail("invalid separate native record origin") }
                }
                if let raw = proof["payload_origin"] {
                    let origin = object(raw)
                    guard Set(origin.keys) == Set(["run_id", "controller_revision"]), integer(origin["run_id"]) == run,
                          origin["controller_revision"] as? String == controller else { fail("invalid original native payload origin") }
                }
                if let raw = proof["native_step_recovery"] {
                    let recovery = object(raw)
                    let keys = Set(["job_id", "original_job_conclusion", "build_step_number", "qualification_step_number", "payload_step_number", "failure_step_number", "cleanup_step_number", "failure_reason", "log_sha256"])
                    guard Set(recovery.keys) == keys, proof["record_origin"] != nil, proof["payload_origin"] != nil,
                          ["job_id", "build_step_number", "qualification_step_number", "payload_step_number", "failure_step_number", "cleanup_step_number"].allSatisfy({ (integer(recovery[$0]) ?? 0) > 0 }),
                          recovery["original_job_conclusion"] as? String == "failure", recovery["failure_reason"] as? String == "bare-upload-artifact-sha256",
                          let logDigest = recovery["log_sha256"] as? String, matches(logDigest, "^[a-f0-9]{64}$") else { fail("invalid successful-native-stage recovery evidence") }
                }
                if let recordFile = proof["record_filename"] {
                    guard let name = recordFile as? String, ["\(provider)-\(arch).json", "previous/\(provider)-\(arch).json"].contains(name) else { fail("invalid original native record filename") }
                }
            }
        }
    }
    if let raw = manifest["distribution"] {
        let distribution = object(raw)
        guard schema == 2, Set(distribution.keys) == Set(["codex", "claude"]) else { fail("invalid independent component distribution") }
        for provider in ["codex", "claude"] {
            let images = object(distribution[provider])
            guard Set(images.keys) == Set(["arm64", "x86_64"]) else { fail("missing distribution platform") }
            for arch in ["arm64", "x86_64"] {
                let original = object(object(object(manifest["runners"])[provider])[arch])["image"] as? String ?? ""
                guard let image = images[arch] as? String,
                      matches(image, "^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$"),
                      image.split(separator: "@").last == original.split(separator: "@").last else { fail("distribution changes original OCI digest") }
            }
        }
    }
    let bundle = object(manifest["bundle"])
    guard let filename = bundle["filename"] as? String, filename == "symphony-\(version)-macos.tar.gz" else { fail("invalid bundle filename") }
    if let digest = bundle["sha256"] { guard let s = digest as? String, matches(s, "^[a-f0-9]{64}$") else { fail("invalid bundle checksum") } }
    if let raw = manifest["bundles"] {
        guard let bundles = raw as? [String: Any], Set(bundles.keys) == Set(["macos", "linux_x86_64"]),
              NSDictionary(dictionary: object(bundles["macos"])) == NSDictionary(dictionary: bundle) else { fail("invalid platform bundles") }
        let linux = object(bundles["linux_x86_64"])
        guard linux["filename"] as? String == "symphony-\(version)-linux_x86_64.tar.gz" else { fail("invalid Linux bundle filename") }
        if let digest = linux["sha256"] { guard let s = digest as? String, matches(s, "^[a-f0-9]{64}$") else { fail("invalid Linux bundle checksum") } }
    }
    if let platform = manifest["package_platform"] {
        guard let name = platform as? String, ["macos", "linux_x86_64"].contains(name),
              name != "linux_x86_64" || manifest["bundles"] != nil else { fail("invalid package platform") }
    }
    if let raw = manifest["supported_hosts"] {
        guard let hosts = raw as? [String: [String]], hosts == ["macos": ["arm64"], "linux_x86_64": ["x86_64"]],
              bundle["architectures"] as? [String] == ["arm64"] else { fail("invalid supported host or macOS bundle architecture") }
        if let bundles = manifest["bundles"] {
            guard object(object(bundles)["linux_x86_64"])["architectures"] as? [String] == ["x86_64"] else { fail("invalid Linux bundle architecture") }
        }
    } else if bundle["architectures"] != nil || object(manifest["bundles"]).values.contains(where: { object($0)["architectures"] != nil }) {
        fail("bundle architecture requires explicit supported host policy")
    }
    return manifest
}
struct ManagerEnvelope {
    let revision: String
    let base: String
    let coreHash: String
}
func managerEnvelope(_ manifest: [String: Any]) -> ManagerEnvelope? {
    let version = manifest["version"] as? String ?? ""
    guard let raw = manifest["manager_envelope"] else {
        guard !version.contains("-manager.") else { fail("bound manager version requires its envelope") }
        return nil
    }
    let envelope = object(raw)
    guard integer(manifest["schema_version"]) == 1,
          Set(envelope.keys) == Set(["schema_version", "manager_source_revision", "base_version", "core_filename", "core_sha256"]),
          integer(envelope["schema_version"]) == 1,
          let revision = envelope["manager_source_revision"] as? String, matches(revision, "^[a-f0-9]{40}$"),
          let base = envelope["base_version"] as? String, base.utf8.count <= 32, matches(base, "^[0-9]+\\.[0-9]+\\.[0-9]+$"),
          envelope["core_filename"] as? String == "manager-core.json",
          let hash = envelope["core_sha256"] as? String, matches(hash, "^[a-f0-9]{64}$"),
          version == "\(base)-manager.\(revision).manifest.\(hash)",
          manifest["original_components"] == nil, manifest["distribution"] == nil else { fail("invalid manager compatibility envelope") }
    return ManagerEnvelope(revision: revision, base: base, coreHash: hash)
}
func validateManifest(_ path: String) throws -> [String: Any] {
    let manifest = object(try json(read(path)))
    _ = managerEnvelope(manifest)
    return try validateManifestObject(manifest)
}
func coreValue(_ value: Any) {
    if let string = value as? String {
        guard string.unicodeScalars.allSatisfy({ $0.value <= 126 && ($0.value >= 32 || [9, 10, 13].contains($0.value)) }) else { fail("manager core contains unsupported text") }
    } else if let values = value as? [String: Any] {
        for (key, item) in values { coreValue(key); coreValue(item) }
    } else if let values = value as? [Any] { for item in values { coreValue(item) } }
    else if let number = value as? NSNumber {
        guard CFGetTypeID(number) == CFBooleanGetTypeID() || (integer(number).map { $0 >= 0 && $0 <= 9007199254740991 } ?? false) else { fail("manager core contains a noninteger number") }
    } else { fail("manager core contains an unsupported value") }
}
// Use the specified ASCII JSON encoding, independent of Foundation's key
// ordering and escape choices. Exact round-trip also rejects duplicate keys.
func canonicalCoreJSON(_ value: Any) -> String {
    if let string = value as? String {
        let encoded = string.unicodeScalars.map { scalar -> String in
            switch scalar.value {
            case 34: return "\\\""
            case 92: return "\\\\"
            case 9: return "\\t"
            case 10: return "\\n"
            case 13: return "\\r"
            default: return String(scalar)
            }
        }.joined()
        return "\"" + encoded + "\""
    }
    if let values = value as? [String: Any] {
        return "{" + values.keys.sorted().map { canonicalCoreJSON($0) + ":" + canonicalCoreJSON(values[$0]!) }.joined(separator: ",") + "}"
    }
    if let values = value as? [Any] { return "[" + values.map(canonicalCoreJSON).joined(separator: ",") + "]" }
    if let number = value as? NSNumber {
        return CFGetTypeID(number) == CFBooleanGetTypeID() ? (number.boolValue ? "true" : "false") : number.stringValue
    }
    fail("manager core contains an unsupported canonical value")
}

// This is the finite reviewed 68 source-to-host-policy transition, not a path allowlist.
let transitionAuthority = "13d8a16cc765fb4844f4a1269fb0192210d794d2f47802f7a705c86602300f36"
let reviewedTransition = object(try! json(Data(#"{"changes":[{"after":{"blob":"1064cc082cafe6880bf7fbd731fd629f5b2f13eb","mode":"100644","sha256":"40424ff480652b279f75ed64b2bf4efe17020bccb2b0e17be0db7c9c3c2edaf9"},"before":{"blob":"f4ed3fbaaa3854b81b13152ae017d0f198bdb615","mode":"100644","sha256":"f39384f8ea29822f985096f73cc5f564cae09f30bb053a3c0f20ff9a5dc068b3"},"classification":"human-readme-v1","path":"elixir/README.md"},{"after":{"blob":"aad0dfc711d21ec469f07e51f83c8ad4f3a9a219","mode":"100644","sha256":"affa5d0f6498cb924f3b7d0da607592cf81fa6b831a8e3d9c381867c86e5171d"},"before":{"blob":"f2269f266c7359e91a168148da871864e731577a","mode":"100644","sha256":"86332e5f23b77098d6ae89f16444a3705aafd0001227cbbdee956c32df253a3f"},"classification":"standalone-targets-v1","path":"elixir/mix.exs"}],"manager_input_identities":{"claude":{"arm64":"31582b7a9278d96848bc989781650f6cbef1101a4445d21e25e6e79091ce0b8f","x86_64":"c73a52fd00ff0f92ccb0da5d779e7076182c9700d69de6757a8c3d9387af6465"},"codex":{"arm64":"b245620ee7ac849272634298b6ecaad783871e914ee80a825c301162b3c1f830","x86_64":"4e1ba5d3f712f27c759367617cbe041305d92457f50589a7878f939b4419b33e"}},"source_input_identities":{"claude":{"arm64":"cb5b2cdae310d75b083c8152118b76b28ffcbdb5eb8da80069b0612e4fbb3935","x86_64":"5e18d340c018720eb8eb4457deeb9aa6513467660744a507b8814d844870e8aa"},"codex":{"arm64":"bfd2b01487d18d5494244c7fd1204184cfbf2177d579ab7128de386d7762e11a","x86_64":"01613d9baa3a9557d358c2dfbd3bf755a7de127dc1a109a4d8f3a82002affd68"}},"source_revision":"68ae23bb195965b96c53b3532d5fab01d7244453"}"#.utf8)))
func componentSelection(provider: String, arch: String, revision: String) -> [String: Any] {
    return ["schema_version": 1, "authority_sha256": transitionAuthority,
            "source_revision": reviewedTransition["source_revision"]!, "manager_source_revision": revision,
            "provider": provider, "architecture": arch,
            "selected_input_identity": object(object(reviewedTransition["source_input_identities"])[provider])[arch]!,
            "desired_input_identity": object(object(reviewedTransition["manager_input_identities"])[provider])[arch]!,
            "changes": reviewedTransition["changes"]!]
}
func validateComponentSelections(_ manifest: [String: Any], revision: String) {
    if object(manifest["compatibility"])["selection_mode"] as? String == "runner-protocol-v1" {
        guard manifest["component_selections"] == nil else { fail("protocol compatibility must not claim finite input equality") }
        return
    }
    let selections = object(manifest["component_selections"])
    if manifest["component_selections"] != nil {
        guard Set(selections.keys) == Set(["codex", "claude"]) else { fail("invalid finite component provider set") }
        for provider in ["codex", "claude"] {
            guard selections[provider] is [String: Any], Set(object(selections[provider]).keys).isSubset(of: Set(["arm64", "x86_64"])) else { fail("invalid finite component platform set") }
        }
    }
    for provider in ["codex", "claude"] { for arch in ["arm64", "x86_64"] {
        let record = object(object(object(manifest["runners"])[provider])[arch])
        let receipt = object(object(selections[provider])[arch])
        let finite = record["source_revision"] as? String == reviewedTransition["source_revision"] as? String && revision != reviewedTransition["source_revision"] as? String
        if finite {
            let expected = componentSelection(provider: provider, arch: arch, revision: revision)
            guard Set(receipt.keys) == Set(expected.keys), integer(receipt["schema_version"]) == 1,
                  NSDictionary(dictionary: receipt).isEqual(to: expected),
                  record["input_identity"] as? String == expected["selected_input_identity"] as? String else { fail("missing or altered finite original component selection") }
        } else if object(selections[provider])[arch] != nil { fail("orphan finite component selection") }
    } }
}
func validateTransition(_ compatibility: [String: Any], originals: [String: Any], revision: String, legacy: String) {
    if compatibility["selection_mode"] as? String == "runner-protocol-v1" {
        guard Set(compatibility.keys) == Set(["runner_protocol", "selection_mode"]), integer(compatibility["runner_protocol"]) == 1 else { fail("unsupported manager runtime protocol contract") }
        return
    }
    guard Set(compatibility.keys) == Set(["runner_protocol", "selection_mode", "input_transition"]),
          integer(compatibility["runner_protocol"]) == 1,
          compatibility["selection_mode"] as? String == "finite-input-transition-v1" else { fail("unsupported original component selection") }
    let transition = object(compatibility["input_transition"])
    guard Set(transition.keys) == Set(["schema_version", "authority_sha256", "source_revision", "manager_source_revision", "source_input_identities", "manager_input_identities", "changes"]),
          integer(transition["schema_version"]) == 1,
          transition["source_revision"] as? String == legacy,
          transition["manager_source_revision"] as? String == revision,
          let authority = transition["authority_sha256"] as? String, matches(authority, "^[a-f0-9]{64}$"),
          let changes = transition["changes"] as? [[String: Any]], changes.count <= 2 else { fail("invalid finite input transition") }
    let beforeInputs = object(transition["source_input_identities"]), afterInputs = object(transition["manager_input_identities"])
    guard authority == transitionAuthority, legacy == reviewedTransition["source_revision"] as? String,
          NSDictionary(dictionary: beforeInputs).isEqual(to: object(reviewedTransition["source_input_identities"])),
          NSDictionary(dictionary: afterInputs).isEqual(to: object(reviewedTransition["manager_input_identities"])),
          NSArray(array: changes).isEqual(to: reviewedTransition["changes"] as! [Any]) else { fail("input transition differs from finite reviewed authority") }
    for identities in [beforeInputs, afterInputs] {
        guard Set(identities.keys) == Set(["codex", "claude"]) else { fail("incomplete transition input identities") }
        for provider in ["codex", "claude"] {
            let group = object(identities[provider])
            guard Set(group.keys) == Set(["arm64", "x86_64"]) else { fail("incomplete transition input platform") }
            for arch in ["arm64", "x86_64"] {
                guard let identity = group[arch] as? String, matches(identity, "^[a-f0-9]{64}$") else { fail("invalid transition input identity") }
            }
        }
    }
    for provider in ["codex", "claude"] { for arch in ["arm64", "x86_64"] {
        guard object(beforeInputs[provider])[arch] as? String == object(object(originals[provider])[arch])["input_identity"] as? String else { fail("transition source input differs from original component") }
    } }
    var paths = Set<String>()
    for change in changes {
        guard Set(change.keys) == Set(["path", "before", "after", "classification"]),
              let path = change["path"] as? String, paths.insert(path).inserted,
              (path == "elixir/README.md" && change["classification"] as? String == "human-readme-v1") ||
              (path == "elixir/mix.exs" && change["classification"] as? String == "standalone-targets-v1") else { fail("unsupported finite input change") }
        for key in ["before", "after"] {
            let blob = object(change[key])
            guard Set(blob.keys) == Set(["blob", "mode", "sha256"]), blob["mode"] as? String == "100644",
                  let oid = blob["blob"] as? String, matches(oid, "^[a-f0-9]{40}$"),
                  let hash = blob["sha256"] as? String, matches(hash, "^[a-f0-9]{64}$") else { fail("invalid finite input blob") }
        }
    }
}
func validateVerifiedOrigins(_ origins: [String: Any], originals: [String: Any]) {
    guard Set(origins.keys) == Set(["codex", "claude"]) else { fail("missing original verified origins") }
    let keys = Set(["run_id", "artifact_id", "artifact_digest", "record_run_id", "record_run_attempt", "record_controller_revision", "record_job_name", "native_qualification_scope", "original_job_conclusion", "record_artifact_id", "record_artifact_digest", "run_attempt", "controller_revision", "job_name", "archive_filename", "archive_sha256", "record_filename"])
    for provider in ["codex", "claude"] {
        guard Set(object(origins[provider]).keys) == Set(["arm64", "x86_64"]) else { fail("missing original verified platform") }
        for arch in ["arm64", "x86_64"] {
            let origin = object(object(origins[provider])[arch])
            let proof = object(object(object(originals[provider])[arch])["qualification"])
            let recordOrigin = proof["record_origin"] == nil ? proof : object(proof["record_origin"])
            guard Set(origin.keys) == keys,
                  ["run_id", "run_attempt", "artifact_id"].allSatisfy({ integer(origin[$0]) == integer(proof[$0]) && (integer(origin[$0]) ?? 0) > 0 }),
                  ["artifact_digest", "controller_revision", "job_name", "archive_filename", "archive_sha256"].allSatisfy({ origin[$0] as? String == proof[$0] as? String }),
                  integer(origin["record_run_id"]) == integer(recordOrigin["run_id"]),
                  integer(origin["record_run_attempt"]) == integer(recordOrigin["run_attempt"]),
                  origin["record_controller_revision"] as? String == recordOrigin["controller_revision"] as? String,
                  origin["record_job_name"] as? String == recordOrigin["job_name"] as? String,
                  origin["record_filename"] as? String == (proof["record_filename"] as? String ?? (proof["archive_filename"] as? String ?? "").replacingOccurrences(of: ".docker.tar.gz", with: ".json")),
                  let recordArtifact = integer(origin["record_artifact_id"]), recordArtifact > 0,
                  let digest = origin["record_artifact_digest"] as? String, matches(digest, "^sha256:[a-f0-9]{64}$"),
                  origin["native_qualification_scope"] as? String == (proof["native_step_recovery"] == nil ? "successful-native-job" : "successful-native-step-before-seal-failure"),
                  origin["original_job_conclusion"] as? String == (proof["native_step_recovery"] == nil ? "success" : "failure") else { fail("verified origin differs from original native qualification") }
        }
    }
}

func validateRelease(_ path: String) throws -> [String: Any] {
    let manifest = try validateManifest(path)
    guard let envelope = managerEnvelope(manifest) else {
        var result = manifest
        result["storage_revision"] = manifest["source_revision"]
        result["manager_base_version"] = manifest["version"]
        return result
    }
    let filename = URL(fileURLWithPath: path).deletingLastPathComponent().appendingPathComponent("manager-core.json").path
    let info = checkedInfo(filename)
    guard info.st_size > 0 && info.st_size <= 1024 * 1024 else { fail("manager core exceeds size bound") }
    let data = try read(filename)
    guard SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == envelope.coreHash else { fail("manager core checksum differs from bound version") }
    let core = object(try json(data)); coreValue(core)
    let canonical = Data((canonicalCoreJSON(core) + "\n").utf8)
    guard canonical == data else { fail("manager core is not canonical ASCII JSON") }
    guard Set(core.keys) == Set(["schema_version", "manager_source_revision", "base_version", "channel", "legacy_source_revision", "original_components", "verified_origins", "distribution", "supported_hosts", "compatibility"]),
          integer(core["schema_version"]) == 1, core["manager_source_revision"] as? String == envelope.revision,
          core["base_version"] as? String == envelope.base, core["channel"] as? String == manifest["channel"] as? String,
          let legacy = core["legacy_source_revision"] as? String, legacy == manifest["source_revision"] as? String,
          NSDictionary(dictionary: object(core["supported_hosts"])).isEqual(to: ["macos": ["arm64"], "linux_x86_64": ["x86_64"]]) else { fail("manager core identity or host policy differs") }
    let originals = object(core["original_components"]), distribution = object(core["distribution"])
    validateTransition(object(core["compatibility"]), originals: originals, revision: envelope.revision, legacy: legacy)
    validateVerifiedOrigins(object(core["verified_origins"]), originals: originals)
    for provider in ["codex", "claude"] { for arch in ["arm64", "x86_64"] {
        let original = object(object(originals[provider])[arch]), selected = object(object(object(manifest["runners"])[provider])[arch])
        guard Set(selected.keys) == Set(["image", "platform", "source_revision", "tool_versions"]),
              original["source_revision"] as? String == legacy,
              selected["image"] as? String == object(distribution[provider])[arch] as? String,
              selected["source_revision"] as? String == original["source_revision"] as? String,
              selected["platform"] as? String == original["platform"] as? String,
              NSDictionary(dictionary: object(selected["tool_versions"])).isEqual(to: object(original["tool_versions"])) else { fail("legacy component view differs from bound originals") }
    } }
    var normalized = manifest
    normalized.removeValue(forKey: "manager_envelope")
    normalized["schema_version"] = 2
    normalized["source_revision"] = envelope.revision
    normalized["runners"] = originals
    normalized["distribution"] = distribution
    if object(core["compatibility"])["selection_mode"] as? String == "runner-protocol-v1" {
        normalized["compatibility"] = ["runner_protocol": 1, "selection_mode": "runner-protocol-v1"]
        normalized.removeValue(forKey: "component_selections")
    } else {
        normalized["compatibility"] = ["runner_protocol": 1]
        normalized["component_selections"] = Dictionary(uniqueKeysWithValues: ["codex", "claude"].map { provider in
            (provider, Dictionary(uniqueKeysWithValues: ["arm64", "x86_64"].map { arch in
                (arch, componentSelection(provider: provider, arch: arch, revision: envelope.revision))
            }))
        })
    }
    normalized["supported_hosts"] = core["supported_hosts"]
    _ = try validateManifestObject(normalized)
    normalized["storage_revision"] = legacy
    normalized["manager_base_version"] = envelope.base
    return normalized
}

// A qualified OCI digest binds its original config cryptographically. Classic
// daemons expose that config as Id; manifest stores expose the manifest as Id.
// Never confuse an archive transport manifest with the qualified OCI identity.
func validateRuntimeImage(_ manifest: [String: Any], provider: String, arch: String, reference: String, data: Data) throws {
    guard integer(manifest["schema_version"]) == 2, ["codex", "claude"].contains(provider),
          ["arm64", "x86_64"].contains(arch), data.count <= 1024 * 1024,
          let values = try json(data) as? [[String: Any]], values.count == 1 else { fail("invalid qualified runtime image receipt") }
    let original = object(object(object(manifest["runners"])[provider])[arch])
    let selected = object(object(manifest["distribution"])[provider])[arch] as? String ?? original["image"] as? String
    guard reference == selected, let qualified = original["image"] as? String,
          let config = original["image_config"] as? String else { fail("runtime image is outside selected qualified release") }
    let receipt = values[0], labels = object(object(receipt["Config"])["Labels"])
    guard receipt["Os"] as? String == "linux", receipt["Architecture"] as? String == (arch == "arm64" ? "arm64" : "amd64"),
          labels["org.opencontainers.image.revision"] as? String == original["source_revision"] as? String,
          labels["com.symphony.agent"] as? String == provider,
          labels["com.symphony.inputs.sha256"] as? String == original["input_identity"] as? String,
          labels["com.symphony.protocol"] as? String == "1" else { fail("runtime image differs from original qualified source, platform or inputs") }
    guard (receipt["RepoDigests"] as? [String])?.contains(reference) == true else { fail("runtime image lacks the selected qualified registry digest") }
    if receipt["Id"] as? String == config { return }
    let digest = String(qualified.split(separator: "@").last ?? "")
    let descriptor = object(receipt["Descriptor"])
    guard receipt["Id"] as? String == digest, descriptor["digest"] as? String == digest,
          ["application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"].contains(descriptor["mediaType"] as? String ?? ""),
          (receipt["RepoDigests"] as? [String])?.contains(reference) == true else { fail("runtime image config or qualified manifest identity differs") }
}

// Selected environments retain their actual embedded runtime identity independently
// from the host manager. Admission checks the integration layout, not source equality.
func validateSelectedImage(provider: String, arch: String, identity: String, data: Data) throws {
    guard ["codex", "claude"].contains(provider), ["arm64", "x86_64"].contains(arch),
          matches(identity, "^sha256:[a-f0-9]{64}$"), data.count <= 1024 * 1024,
          let receipts = try json(data) as? [[String: Any]], receipts.count == 1 else { fail("invalid selected environment image receipt") }
    let image = receipts[0], config = object(image["Config"]), labels = object(config["Labels"])
    guard image["Id"] as? String == identity else { fail("selected environment immutable image identity differs") }
    guard image["Os"] as? String == "linux", image["Architecture"] as? String == (arch == "arm64" ? "arm64" : "amd64") else { fail("selected environment platform is incompatible with this host") }
    guard labels["com.symphony.agent"] as? String == provider else { fail("selected environment provider differs; select an image for the requested agent") }
    guard labels["com.symphony.protocol"] as? String == "1" else { fail("selected environment runner protocol is unsupported; rebuild from a compatible template") }
    guard let revision = labels["org.opencontainers.image.revision"] as? String, matches(revision, "^[a-f0-9]{40}$") else { fail("selected environment has no truthful embedded runtime source revision") }
    let entrypoint = config["Entrypoint"] as? [String], user = config["User"] as? String ?? ""
    let expected = provider == "codex" ? ["/usr/local/bin/symphony-runner"] : ["/usr/bin/tini", "-g", "--", "/usr/local/bin/symphony-claude-runner"]
    guard entrypoint == expected else { fail("selected environment entrypoint does not meet the runner contract") }
    var environment: [String: String] = [:]
    for entry in config["Env"] as? [String] ?? [] {
        let parts = entry.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
        guard parts.count == 2 else { fail("selected environment contains invalid environment metadata") }
        let key = String(parts[0])
        if ["HOME", "CLAUDE_CONFIG_DIR", "SYMPHONY_CONTAINERIZED"].contains(key), environment[key] != nil { fail("selected environment contains ambiguous runtime environment metadata") }
        environment[key] = String(parts[1])
    }
    if provider == "claude" {
        guard user == "11001:11001", config["WorkingDir"] as? String == "/var/lib/symphony",
              environment["HOME"] == "/home/claude", environment["CLAUDE_CONFIG_DIR"] == "/home/claude/.claude",
              environment["SYMPHONY_CONTAINERIZED"] == "1" else { fail("selected Claude environment must preserve UID/GID 11001 and its native auth/runtime layout") }
    } else {
        guard ["", "0", "0:0", "root", "root:root"].contains(user),
              config["WorkingDir"] as? String == "/opt/symphony/elixir",
              environment["HOME"] == nil || environment["HOME"] == "/root" else { fail("selected Codex environment must preserve root user and its native auth/runtime layout") }
    }
}

func validateEnvironmentSelection(_ path: String) throws {
    let info = checkedInfo(path)
    guard info.st_size <= 16384, info.st_mode & 0o077 == 0 else { fail("selected environment metadata must be private and bounded") }
    let record = object(try json(read(path)))
    guard Set(record.keys) == Set(["schema_version", "provider", "image_id", "reference", "runtime_revision", "platform", "protocol"]),
          integer(record["schema_version"]) == 1, integer(record["protocol"]) == 1,
          ["codex", "claude"].contains(record["provider"] as? String ?? ""),
          matches(record["image_id"] as? String ?? "", "^sha256:[a-f0-9]{64}$"),
          matches(record["runtime_revision"] as? String ?? "", "^[a-f0-9]{40}$"),
          ["linux/arm64", "linux/amd64"].contains(record["platform"] as? String ?? ""),
          matches(record["reference"] as? String ?? "", "^[a-zA-Z0-9][a-zA-Z0-9._/@:+-]{0,511}$") else { fail("invalid selected environment metadata; reselect a compatible image") }
}

func gitValue(_ input: String) -> String {
    var result = "", quoted = false, escaped = false
    for ch in input {
        if escaped {
            switch ch { case "\\", "\"": result.append(ch); case "t": result.append("\t"); case "b": result.append("\u{8}"); case "n": result.append("\n"); default: fail("invalid origin URL escape") }
            escaped = false; continue
        }
        if ch == "\\" { escaped = true; continue }
        if ch == "\"" { quoted.toggle(); continue }
        if !quoted && (ch == "#" || ch == ";") { break }
        result.append(ch)
    }
    guard !quoted, !escaped else { fail("unterminated origin URL quoting") }
    let value = result.trimmingCharacters(in: .whitespaces)
    guard !value.isEmpty, !value.contains("\n"), !value.contains("\r") else { fail("invalid origin URL") }
    return value
}
// Inspect only selected config files. Indirection is refused without reading its
// targets, so identity never silently differs from Git's effective origin.
func checkGitIdentityEnvironment() {
    let env = ProcessInfo.processInfo.environment
    let supported = Set(["GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"])
    let relocation = Set(["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_IMPLICIT_WORK_TREE", "GIT_CONFIG"])
    if let key = env.keys.sorted().first(where: { relocation.contains($0) || ($0.hasPrefix("GIT_CONFIG_") && !supported.contains($0)) }) {
        fail("packaged repository identity does not support \(key); unset that Git environment override")
    }
    var noSystem = false
    if let value = env["GIT_CONFIG_NOSYSTEM"]?.lowercased() {
        if ["true", "yes", "on"].contains(value) { noSystem = true }
        else if ["", "false", "no", "off"].contains(value) { noSystem = false }
        else if let number = Int(value) { noSystem = number != 0 }
        else { fail("GIT_CONFIG_NOSYSTEM must be a Git boolean; unset or correct it") }
    }
    let home = env["HOME"] ?? NSHomeDirectory()
    let xdg = env["XDG_CONFIG_HOME"].flatMap { $0.isEmpty ? nil : $0 } ?? home + "/.config"
    var paths: [String]
    if let global = env["GIT_CONFIG_GLOBAL"] { paths = global.isEmpty ? [] : [global] }
    else { paths = [xdg + "/git/config", home + "/.gitconfig"] }
    if !noSystem {
        if let system = env["GIT_CONFIG_SYSTEM"] { if !system.isEmpty { paths.append(system) } }
        else { paths += ["/etc/gitconfig", "/opt/homebrew/etc/gitconfig", "/usr/local/etc/gitconfig"] }
    }
    for path in Set(paths) {
        if URL(fileURLWithPath: path).resolvingSymlinksInPath().path == "/dev/null" { continue }
        var info = stat()
        if fstatat(AT_FDCWD, path, &info, 0) != 0 {
            if errno == ENOENT { continue }
            fail("cannot inspect selected external Git configuration; check its access before using the packaged manager")
        }
        guard info.st_mode & S_IFMT == S_IFREG,
              let config = try? String(contentsOfFile: path, encoding: .utf8) else { fail("cannot read selected external Git configuration as a regular UTF-8 file") }
        let indirection = matches(config, #"(?im)^[ \t]*\[(include(if)?|url)([ \t\].])"#)
        let remote = matches(config, #"(?im)^[ \t]*\[remote([ \t]+"origin"|\.origin)[ \t]*\]"#)
        if indirection || remote {
            fail("external Git configuration contains identity indirection (include, URL rewrite, or origin); remove it or select explicit non-indirect GIT_CONFIG_GLOBAL/SYSTEM files")
        }
    }
}
func repository(_ input: String) throws -> (String, String) {
    checkGitIdentityEnvironment()
    var root = URL(fileURLWithPath: input).standardizedFileURL.resolvingSymlinksInPath()
    while root.path != "/" {
        let marker = root.appendingPathComponent(".git")
        var directory: ObjCBool = false
        if FileManager.default.fileExists(atPath: marker.path, isDirectory: &directory) {
            var git = marker
            if !directory.boolValue {
                let text = try String(contentsOf: marker, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
                guard text.hasPrefix("gitdir: ") else { fail("invalid repository .git file") }
                git = URL(fileURLWithPath: String(text.dropFirst(8)), relativeTo: root).standardizedFileURL
            }
            let common = git.appendingPathComponent("commondir")
            if FileManager.default.fileExists(atPath: common.path) {
                let value = try String(contentsOf: common, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines)
                git = URL(fileURLWithPath: value, relativeTo: URL(fileURLWithPath: git.path, isDirectory: true)).standardizedFileURL
            }
            let config = try String(contentsOf: git.appendingPathComponent("config"), encoding: .utf8)
            if matches(config, #"(?im)^[ \t]*\[url([ \t\].])"#) { fail("packaged repository identity does not support local URL rewrite sections; use an explicit local origin URL") }
            if matches(config, "(?im)^[ \\t]*(worktreeconfig[ \\t]*=|insteadof[ \\t]*=|pushinsteadof[ \\t]*=)") { fail("packaged repository identity does not support worktree config or URL rewrite rules; use an explicit local origin URL") }
            var origin = false
            var remote: String? = nil
            for raw in config.components(separatedBy: .newlines) {
                let line = raw.trimmingCharacters(in: .whitespaces)
                if matches(line, "^\\[(?i:include|includeIf)([ \\t\\]]).*") { fail("packaged project identity requires an explicit local origin; Git config includes are unsupported") }
                if line.hasPrefix("[") { origin = matches(line, "^\\[(?i:remote)[ \\t]+\"origin\"\\][ \\t]*([#;].*)?$"); continue }
                if origin, matches(line, "^(?i:url)[ \\t]*=") {
                    let value = gitValue(String(line.drop(while: { $0 != "=" }).dropFirst()).trimmingCharacters(in: .whitespaces))
                    if remote == nil { remote = value }
                }
            }
            guard let found = remote else { fail("the repository needs an explicit origin remote in its local Git config") }
            guard let resolved = realpath(root.path, nil) else { fail("cannot resolve repository root") }; defer { free(resolved) }
            return (String(cString: resolved), found)
        }
        root.deleteLastPathComponent()
    }
    fail("run this command inside an existing Git repository")
}
let args = Array(CommandLine.arguments.dropFirst())
// Candidate import consumes the original graph archive, never Docker-save/config-ID conversion.
func streamDigest(_ handle: FileHandle, maximum: UInt64) throws -> (String, UInt64) {
    var hash = SHA256(), count: UInt64 = 0
    while let data = try handle.read(upToCount: 1024 * 1024), !data.isEmpty {
        count += UInt64(data.count)
        guard count <= maximum else { fail("candidate payload exceeds size bound") }
        hash.update(data: data)
    }
    return (hash.finalize().map { String(format: "%02x", $0) }.joined(), count)
}
func importRead(_ handle: FileHandle, _ count: Int) throws -> Data {
    var data = Data()
    while data.count < count {
        guard let part = try handle.read(upToCount: count - data.count), !part.isEmpty else { fail("truncated candidate archive") }
        data.append(part)
    }
    return data
}
func prepareRunnerImport(_ manifest: [String: Any], provider: String, arch: String, archive: String, destination: String) throws {
    guard manifest["channel"] as? String == "candidate", integer(manifest["schema_version"]) == 2,
          ["codex", "claude"].contains(provider), ["arm64", "x86_64"].contains(arch) else { fail("runner import requires a schema2 candidate and supported provider/platform") }
    let record = object(object(object(manifest["runners"])[provider])[arch])
    let image = record["image"] as? String ?? ""
    let selected = object(object(manifest["distribution"])[provider])[arch] as? String ?? image
    guard selected == image, matches(image, "^localhost:5000/symphony-\(provider)@sha256:[a-f0-9]{64}$"),
          let expectedArchive = object(record["qualification"])["archive_sha256"] as? String else { fail("candidate has no supported original loopback graph; use its selected registry distribution") }
    let info = checkedInfo(archive)
    guard info.st_size > 0, UInt64(info.st_size) <= 32 << 30 else { fail("candidate archive size exceeds bound") }
    let input = try FileHandle(forReadingFrom: URL(fileURLWithPath: archive))
    defer { try? input.close() }
    guard try streamDigest(input, maximum: 32 << 30).0 == expectedArchive else { fail("candidate archive checksum differs from selected original qualification") }
    try input.seek(toOffset: 0)
    _ = checkedInfo(destination, directory: true)
    guard try FileManager.default.contentsOfDirectory(atPath: destination).isEmpty else { fail("runner import destination must be empty") }
    let graph = destination + "/graph"
    try FileManager.default.createDirectory(atPath: graph + "/blobs/sha256", withIntermediateDirectories: true)
    let gzip = Process(), pipe = Pipe()
    gzip.executableURL = URL(fileURLWithPath: "/usr/bin/env"); gzip.arguments = ["gzip", "-dc"]
    gzip.standardInput = input; gzip.standardOutput = pipe; gzip.standardError = FileHandle.nullDevice
    try gzip.run()
    defer { if gzip.isRunning { gzip.terminate() }; gzip.waitUntilExit() }
    let stream = pipe.fileHandleForReading
    defer { try? stream.close() }
    var seen = Set<String>(), hashes: [String: (String, UInt64)] = [:], total: UInt64 = 0
    var extended: String? = nil
    for number in 0..<16384 {
        let header = try importRead(stream, 512)
        if header.allSatisfy({ $0 == 0 }) {
            guard extended == nil else { fail("dangling candidate archive path header") }
            var tailCount = 0
            while let tail = try stream.read(upToCount: 65536), !tail.isEmpty {
                tailCount += tail.count
                guard tailCount <= 1024 * 1024, tail.allSatisfy({ $0 == 0 }) else { fail("invalid candidate archive trailer") }
            }
            gzip.waitUntilExit()
            guard gzip.terminationStatus == 0 else { fail("candidate archive decompression failed") }
            break
        }
        guard number < 16383 else { fail("candidate archive member bound exceeded") }
        func field(_ start: Int, _ length: Int) -> String {
            String(data: header.subdata(in: start..<(start + length)).prefix(while: { $0 != 0 }), encoding: .utf8) ?? ""
        }
        func octal(_ start: Int, _ length: Int) -> UInt64 {
            let text = field(start, length).trimmingCharacters(in: .whitespaces)
            guard !text.isEmpty, text.allSatisfy({ "01234567".contains($0) }), let value = UInt64(text, radix: 8) else { fail("invalid candidate archive number") }
            return value
        }
        let expected = octal(148, 8)
        let actual = header.enumerated().reduce(UInt64(0)) { $0 + ((148..<156).contains($1.offset) ? 32 : UInt64($1.element)) }
        guard expected == actual else { fail("candidate archive header checksum differs") }
        let size = octal(124, 12), type = header[156]
        guard size > 0, size <= 8 << 30 else { fail("invalid candidate archive member size") }
        var name = field(0, 100), prefix = field(345, 155)
        if !prefix.isEmpty { name = prefix + "/" + name }
        if type == 120 { // POSIX path extension: accept only the path key emitted by graph export.
            guard extended == nil, size <= 4096 else { fail("unsupported candidate archive extension") }
            let raw = try importRead(stream, Int(size))
            guard let text = String(data: raw, encoding: .utf8), let separator = text.firstIndex(of: " "),
                  let length = Int(text[..<separator]), length == raw.count,
                  text[text.index(after: separator)...].hasPrefix("path="), text.hasSuffix("\n") else { fail("unsupported candidate archive path extension") }
            extended = String(text[text.index(separator, offsetBy: 6)..<text.index(before: text.endIndex)])
        } else {
            guard type == 0 || type == 48 else { fail("candidate archive contains a link or unsupported member") }
            name = extended ?? name; extended = nil
            let namespace = "symphony-qualified-graphs/v1/"
            guard name.hasPrefix(namespace) else { fail("candidate archive contains a foreign namespace") }
            let relative = String(name.dropFirst(namespace.count))
            guard relative == "index.json" || matches(relative, "^blobs/sha256/[a-f0-9]{64}$"), seen.insert(relative).inserted,
                  relative != "index.json" || size <= 1024 * 1024 else { fail("candidate archive has an unknown or duplicate graph member") }
            total += size
            guard total <= 32 << 30 else { fail("candidate graph exceeds size bound") }
            let path = graph + "/" + relative
            guard FileManager.default.createFile(atPath: path, contents: nil, attributes: [.posixPermissions: 0o600]) else { fail("cannot stage candidate graph") }
            let output = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
            var hash = SHA256(), remaining = size
            while remaining > 0 {
                let data = try importRead(stream, Int(min(remaining, 1024 * 1024)))
                try output.write(contentsOf: data); hash.update(data: data); remaining -= UInt64(data.count)
            }
            try output.close()
            let digest = hash.finalize().map { String(format: "%02x", $0) }.joined()
            if relative != "index.json" { guard relative == "blobs/sha256/" + digest else { fail("candidate graph blob checksum differs") } }
            hashes[relative] = (digest, size)
        }
        let padding = Int((512 - size % 512) % 512)
        if padding > 0 { guard try importRead(stream, padding).allSatisfy({ $0 == 0 }) else { fail("invalid candidate archive padding") } }
    }
    var after = stat()
    guard fstat(input.fileDescriptor, &after) == 0, info.st_dev == after.st_dev, info.st_ino == after.st_ino,
          info.st_size == after.st_size else { fail("candidate archive changed during verification") }
    let index = object(try json(read(graph + "/index.json")))
    guard integer(index["schema_version"]) == 1, let entries = index["images"] as? [[String: Any]],
          entries.filter({ $0["image"] as? String == image && $0["image_config"] as? String == record["image_config"] as? String && $0["archive_tag"] as? String == record["archive_tag"] as? String }).count == 1 else { fail("candidate graph index differs from selected original record") }
    let digest = String(image.split(separator: "@").last ?? "")
    let rootPath = "blobs/sha256/" + digest.dropFirst(7)
    guard hashes[rootPath]?.0 == String(digest.dropFirst(7)), (hashes[rootPath]?.1 ?? UInt64.max) <= 1024 * 1024 else { fail("candidate graph is missing the selected original manifest") }
    let root = object(try json(read(graph + "/" + rootPath)))
    guard integer(root["schemaVersion"]) == 2,
          ["application/vnd.docker.distribution.manifest.v2+json", "application/vnd.oci.image.manifest.v1+json"].contains(root["mediaType"] as? String ?? ""),
          let layers = root["layers"] as? [[String: Any]], !layers.isEmpty, layers.count <= 256 else { fail("candidate graph root is not a supported single image") }
    let config = object(root["config"])
    guard config["digest"] as? String == record["image_config"] as? String,
          ["application/vnd.docker.container.image.v1+json", "application/vnd.oci.image.config.v1+json"].contains(config["mediaType"] as? String ?? ""),
          (integer(config["size"]) ?? Int.max) <= 1024 * 1024 else { fail("candidate graph config differs") }
    guard layers.allSatisfy({ ["application/vnd.docker.image.rootfs.diff.tar.gzip", "application/vnd.oci.image.layer.v1.tar+gzip"].contains($0["mediaType"] as? String ?? "") }) else { fail("candidate graph has an unsupported layer format") }
    let descriptors = [config] + layers + [["digest": digest, "size": Int(hashes[rootPath]!.1)]]
    // Complete closure validation precedes even local registry layout creation.
    for descriptor in descriptors {
        guard let identity = descriptor["digest"] as? String, matches(identity, "^sha256:[a-f0-9]{64}$"),
              let size = integer(descriptor["size"]), size > 0,
              let observed = hashes["blobs/sha256/" + identity.dropFirst(7)], observed.1 == UInt64(size) else { fail("candidate graph descriptor or compressed blob closure differs") }
    }
    let repository = "symphony-" + provider
    let storage = destination + "/registry/docker/registry/v2"
    var linked = Set<String>()
    for descriptor in descriptors {
        guard let identity = descriptor["digest"] as? String, matches(identity, "^sha256:[a-f0-9]{64}$"),
              let size = integer(descriptor["size"]), size > 0,
              let observed = hashes["blobs/sha256/" + identity.dropFirst(7)], observed.1 == UInt64(size) else { fail("candidate graph descriptor or compressed blob closure differs") }
        let hex = String(identity.dropFirst(7)), blob = storage + "/blobs/sha256/" + hex.prefix(2) + "/" + hex
        if !linked.insert(identity).inserted { continue }
        try FileManager.default.createDirectory(atPath: blob, withIntermediateDirectories: true)
        try FileManager.default.linkItem(atPath: graph + "/blobs/sha256/" + hex, toPath: blob + "/data")
        let link = storage + "/repositories/" + repository + (identity == digest ? "/_manifests/revisions/sha256/" : "/_layers/sha256/") + hex
        try FileManager.default.createDirectory(atPath: link, withIntermediateDirectories: true)
        try Data(identity.utf8).write(to: URL(fileURLWithPath: link + "/link"))
    }
    print(image)
}

// Manager-held project lock serializes this small content-addressed store.
// Only sealed copies live here; original native home/auth are never inspected.
enum ConfigStoreError: Error { case unsafe }
func storeRequire(_ condition: Bool) throws { if !condition { throw ConfigStoreError.unsafe } }
func storeDigest(_ value: Any?) throws -> String {
    guard let digest = value as? String, matches(digest, "^[a-f0-9]{64}$") else { throw ConfigStoreError.unsafe }
    return digest
}
func storeIdentity(_ info: stat) -> String {
#if canImport(Darwin)
    let times = "\(info.st_mtimespec.tv_sec):\(info.st_mtimespec.tv_nsec):\(info.st_ctimespec.tv_sec):\(info.st_ctimespec.tv_nsec)"
#else
    let times = "\(info.st_mtim.tv_sec):\(info.st_mtim.tv_nsec):\(info.st_ctim.tv_sec):\(info.st_ctim.tv_nsec)"
#endif
    return "\(info.st_dev):\(info.st_ino):\(info.st_uid):\(info.st_gid):\(info.st_mode):\(info.st_nlink):\(info.st_size):" + times
}
func storeInfo(_ path: String, directory: Bool = false, privateMode: Bool = true) throws -> stat {
    var info = stat()
    try storeRequire(lstat(path, &info) == 0)
    let mode = info.st_mode & 0o7777
    try storeRequire(info.st_uid == geteuid() && (info.st_mode & S_IFMT) == (directory ? S_IFDIR : S_IFREG))
    try storeRequire(privateMode ? mode == (directory ? 0o700 : 0o600) : mode & 0o022 == 0)
    try storeRequire(directory || info.st_nlink == 1)
    guard let canonical = realpath(path, nil) else { throw ConfigStoreError.unsafe }
    defer { free(canonical) }
    try storeRequire(String(cString: canonical) == path)
    return info
}
func storeRead(_ path: String, maximum: Int) throws -> Data {
    let before = try storeInfo(path)
    let descriptor = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
    try storeRequire(descriptor >= 0)
    defer { close(descriptor) }
    var current = stat()
    try storeRequire(fstat(descriptor, &current) == 0 && storeIdentity(before) == storeIdentity(current) && before.st_size >= 0 && before.st_size <= maximum)
    var data = Data(), buffer = [UInt8](repeating: 0, count: 65536)
    while data.count <= maximum {
        let count = read(descriptor, &buffer, min(buffer.count, maximum + 1 - data.count))
        try storeRequire(count >= 0)
        if count == 0 { break }
        data.append(contentsOf: buffer.prefix(count))
    }
    try storeRequire(data.count == before.st_size && data.count <= maximum)
    try storeRequire(fstat(descriptor, &current) == 0 && storeIdentity(before) == storeIdentity(current))
    try storeRequire(lstat(path, &current) == 0 && storeIdentity(before) == storeIdentity(current))
    return data
}
func storeReceipt(_ value: Any, full: Bool) throws -> [String: Any] {
    guard let receipt = value as? [String: Any] else { throw ConfigStoreError.unsafe }
    var keys = Set(["schema", "mode", "profile", "sha256", "source"])
    if full { keys.insert("config_base64") }
    try storeRequire(Set(receipt.keys) == keys && integer(receipt["schema"]) == 1 && receipt["mode"] as? String == "codex-config-snapshot" && receipt["profile"] as? String == "trust-ro-v2")
    _ = try storeDigest(receipt["sha256"])
    guard let source = receipt["source"] as? [String: Any] else { throw ConfigStoreError.unsafe }
    if isBool(source["present"], true) {
        let fields = Set(["uid", "gid", "mode", "device", "inode", "size", "mtime_ns", "ctime_ns"])
        try storeRequire(Set(source.keys) == fields.union(["present"]))
        for field in fields { guard let value = integer(source[field]), value >= 0 else { throw ConfigStoreError.unsafe } }
        let mode = integer(source["mode"])!, size = integer(source["size"])!
        try storeRequire(mode <= 0o7777 && mode & 0o022 == 0 && size <= 65536)
    } else { try storeRequire(Set(source.keys) == Set(["present"]) && isBool(source["present"], false)) }
    return receipt
}
func storeLexicalPath(_ path: String) -> Bool {
    if path == "/" { return true }
    if !path.hasPrefix("/") || path.contains("\0") { return false }
    return path.dropFirst().split(separator: "/", omittingEmptySubsequences: false).allSatisfy { !$0.isEmpty && $0 != "." && $0 != ".." }
}
func storeRoot(_ path: String, create: Bool) throws -> String {
    try storeRequire(storeLexicalPath(path))
    let parent = URL(fileURLWithPath: path).deletingLastPathComponent().path
    _ = try storeInfo(parent, directory: true, privateMode: false)
    var info = stat()
    if create && lstat(path, &info) != 0 {
        try storeRequire(errno == ENOENT && mkdir(path, 0o700) == 0)
        try storeSync(parent, privateMode: false)
    }
    _ = try storeInfo(path, directory: true)
    return path
}
func storeVerify(_ root: String, _ digest: String) throws {
    _ = try storeDigest(digest)
    let directory = root + "/" + digest
    _ = try storeInfo(directory, directory: true)
    try storeRequire(Set(try FileManager.default.contentsOfDirectory(atPath: directory)) == Set(["config.toml", "receipt.json"]))
    let data = try storeRead(directory + "/config.toml", maximum: 65536)
    let receipt = try storeReceipt(json(storeRead(directory + "/receipt.json", maximum: 4096)), full: false)
    try storeRequire(receipt["sha256"] as? String == digest && SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() == digest)
    let source = object(receipt["source"])
    try storeRequire(isBool(source["present"], true) ? integer(source["size"]) == data.count : data.isEmpty)
}
func storeSync(_ path: String, privateMode: Bool = true) throws {
    _ = try storeInfo(path, directory: true, privateMode: privateMode)
    let descriptor = open(path, O_RDONLY | O_NOFOLLOW | O_DIRECTORY)
    try storeRequire(descriptor >= 0)
    defer { close(descriptor) }
    try storeRequire(fsync(descriptor) == 0)
}
func storeTransient(_ root: String, _ name: String) throws -> [String] {
    let stage = matches(name, "^\\.stage-[0-9a-f]{32}$")
    let retire = matches(name, "^\\.retire-[0-9a-f]{64}-[0-9a-f]{32}$")
    try storeRequire(stage || retire)
    let directory = root + "/" + name
    _ = try storeInfo(directory, directory: true)
    let names = try FileManager.default.contentsOfDirectory(atPath: directory)
    try storeRequire(Set(names).isSubset(of: Set(["config.toml", "receipt.json"])))
    for child in names {
        let data = try storeRead(directory + "/" + child, maximum: child == "config.toml" ? 65536 : 4096)
        if retire {
            let digest = String(name.dropFirst(8).prefix(64))
            if child == "config.toml" { try storeRequire(SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() == digest) }
            else { try storeRequire(storeReceipt(json(data), full: false)["sha256"] as? String == digest) }
        }
    }
    return names
}
func storeRemoveTransient(_ root: String, _ name: String) throws {
    let directory = root + "/" + name
    for child in try storeTransient(root, name) { try storeRequire(unlink(directory + "/" + child) == 0) }
    try storeSync(directory)
    try storeRequire(rmdir(directory) == 0)
    try storeSync(root)
}
func storeInventory(_ root: String) throws -> [String] {
    let names = try FileManager.default.contentsOfDirectory(atPath: root)
    try storeRequire(names.count <= 128)
    var artifacts: [String] = [], leftovers: [String] = []
    for name in names {
        if matches(name, "^[0-9a-f]{64}$") { try storeVerify(root, name); artifacts.append(name) }
        else { _ = try storeTransient(root, name); leftovers.append(name) }
    }
    try storeRequire(artifacts.count <= 64 && leftovers.count <= 64)
    for name in leftovers { try storeRemoveTransient(root, name) }
    return artifacts
}
func storeRename(_ source: String, _ destination: String) throws {
    var info = stat()
    try storeRequire(lstat(destination, &info) != 0 && errno == ENOENT)
    try storeRequire(rename(source, destination) == 0)
}
func storeWriteFile(_ path: String, _ data: Data) throws {
    let descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
    try storeRequire(descriptor >= 0)
    defer { close(descriptor) }
    try data.withUnsafeBytes { pointer in
        var offset = 0
        while offset < data.count {
            let count = write(descriptor, pointer.baseAddress!.advanced(by: offset), data.count - offset)
            try storeRequire(count > 0)
            offset += count
        }
    }
    try storeRequire(fsync(descriptor) == 0)
}
func storeInput(_ maximum: Int) throws -> Data {
    var input = Data()
    while input.count <= maximum {
        guard let part = try FileHandle.standardInput.read(upToCount: maximum + 1 - input.count), !part.isEmpty else { break }
        input.append(part)
    }
    try storeRequire(input.count <= maximum)
    return input
}
func storeContainer(_ root: String, _ digest: String, _ uid: String, _ authVolume: String, _ hostOS: String, _ data: Data) throws {
    try storeRequire(matches(uid, "^(0|[1-9][0-9]{0,9})$") && (UInt64(uid) ?? UInt64.max) <= 4294967294)
    try storeRequire(matches(authVolume, "^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$") && ["Darwin", "Linux"].contains(hostOS))
    guard let states = try json(data) as? [[String: Any]], states.count == 1,
          let config = states[0]["Config"] as? [String: Any], let mounts = states[0]["Mounts"] as? [[String: Any]],
          let environment = config["Env"] as? [String], let labels = config["Labels"] as? [String: Any] else { throw ConfigStoreError.unsafe }
    let expected = ["HOME": "/root", "CODEX_HOME": "/root/.codex", "SYMPHONY_CODEX_CONFIG_PROFILE": "trust-ro-v2",
                    "SYMPHONY_CODEX_CONFIG_SHA256": digest, "SYMPHONY_CODEX_CONFIG_OWNER": uid, "SYMPHONY_CONTAINERIZED": "1"]
    for (name, value) in expected {
        try storeRequire(environment.filter { $0.components(separatedBy: "=")[0] == name } == [name + "=" + value])
    }
    for (key, value) in ["profile": "trust-ro-v2", "sha256": digest, "owner": uid] {
        try storeRequire(labels["com.symphony.codex.config." + key] as? String == value)
    }
    let source = root + "/" + digest + "/config.toml"
    let sources = hostOS == "Darwin" ? [source, "/host_mnt" + source] : [source]
    var authCount = 0, snapshotCount = 0
    for mount in mounts {
        guard let destination = mount["Destination"] as? String else { throw ConfigStoreError.unsafe }
        try storeRequire(storeLexicalPath(destination))
        if destination == "/root/.codex" {
            authCount += 1
            try storeRequire(mount["Type"] as? String == "volume" && mount["Name"] as? String == authVolume && isBool(mount["RW"], true))
        } else if destination == "/root/.codex/config.toml" {
            snapshotCount += 1
            try storeRequire(mount["Type"] as? String == "bind" && sources.contains(mount["Source"] as? String ?? "") && isBool(mount["RW"], false))
        } else { try storeRequire(!["/", "/root"].contains(destination) && !destination.hasPrefix("/root/.codex/")) }
    }
    try storeRequire(authCount == 1 && snapshotCount == 1)
}

func selectionRecord(_ image: String, _ digest: String, _ owner: String) throws -> Data {
    try storeRequire(matches(image, "^sha256:[0-9a-f]{64}$") && matches(owner, "^(0|[1-9][0-9]{0,9})$") && (UInt64(owner) ?? UInt64.max) <= 4294967294)
    _ = try storeDigest(digest)
    return Data(("codex-config-v2\t" + image + "\t" + digest + "\t" + owner + "\n").utf8)
}
func selectionRead(_ path: String) throws -> Data {
    let data = try storeRead(path, maximum: 256)
    guard let line = String(data: data, encoding: .ascii) else { throw ConfigStoreError.unsafe }
    let fields = line.split(separator: "\t", omittingEmptySubsequences: false)
    try storeRequire(fields.count == 4 && fields[0] == "codex-config-v2" && fields[3].hasSuffix("\n"))
    try storeRequire(selectionRecord(String(fields[1]), String(fields[2]), String(fields[3].dropLast())) == data)
    return data
}
func selectionOperation(_ args: [String]) throws {
    let operation = args[0]
    try storeRequire(["selection-stage", "selection-read", "selection-publish", "selection-abort"].contains(operation))
    try storeRequire(args.count == (operation == "selection-read" ? 2 : 5))
    let path = args[1], url = URL(fileURLWithPath: path), parent = url.deletingLastPathComponent().path
    try storeRequire(storeLexicalPath(path) && (operation == "selection-read" ? ["image", "image.next"] : ["image"]).contains(url.lastPathComponent))
    _ = try storeInfo(parent, directory: true)
    if operation == "selection-read" { FileHandle.standardOutput.write(try selectionRead(path)); return }
    let expected = try selectionRecord(args[2], args[3], args[4]), next = parent + "/image.next"
    var info = stat()
    let current = lstat(path, &info) == 0 ? try selectionRead(path) : nil
    if current == nil { try storeRequire(errno == ENOENT) }
    let proposed = lstat(next, &info) == 0 ? try selectionRead(next) : nil
    if proposed == nil { try storeRequire(errno == ENOENT) }
    var leftovers: [String] = []
    for name in try FileManager.default.contentsOfDirectory(atPath: parent) where name.hasPrefix(".image.stage-") {
        try storeRequire(matches(name, "^\\.image\\.stage-[0-9a-f]{32}$"))
        _ = try storeRead(parent + "/" + name, maximum: 256)
        leftovers.append(parent + "/" + name)
    }
    try storeRequire(leftovers.count <= 64 && (proposed == nil || proposed == expected))
    if operation == "selection-publish" && proposed == nil { try storeRequire(current == expected) }
    for leftover in leftovers { try storeRequire(unlink(leftover) == 0) }
    if !leftovers.isEmpty { try storeSync(parent) }
    if operation == "selection-stage" && proposed == nil {
        let stage = parent + "/.image.stage-" + UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "")
        defer {
            if lstat(stage, &info) == 0, (try? storeRead(stage, maximum: 256)) != nil {
                _ = unlink(stage); try? storeSync(parent)
            }
        }
        try storeWriteFile(stage, expected)
        try storeRename(stage, next)
        try storeSync(parent)
    } else if operation == "selection-publish" && proposed != nil {
        // Both source and replacement were validated under the manager's lock.
        try storeRequire(rename(next, path) == 0)
        try storeSync(parent)
    } else if operation == "selection-abort" && proposed != nil {
        try storeRequire(unlink(next) == 0)
        try storeSync(parent)
    }
    // Reconfirm durability on idempotent retry after an uncertain parent fsync.
    try storeSync(parent)
    FileHandle.standardOutput.write(expected)
}

func codexConfigStore(_ args: [String]) throws {
    if let operation = args.first, operation.hasPrefix("selection-") { try selectionOperation(args); return }
    try storeRequire(args.count >= 2)
    let operation = args[0], path = args[1]
    try storeRequire(["write", "verify", "cleanup", "container"].contains(operation))
    if operation == "write" {
        try storeRequire(args.count == 2)
        let input = try storeInput(98304)
        var receipt = try storeReceipt(json(input), full: true)
        guard let encoded = receipt["config_base64"] as? String, encoded.utf8.count <= 87384,
              let data = Data(base64Encoded: encoded), data.count <= 65536, data.base64EncodedString() == encoded else { throw ConfigStoreError.unsafe }
        let digest = try storeDigest(receipt["sha256"]), source = object(receipt["source"])
        try storeRequire(SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() == digest)
        try storeRequire(isBool(source["present"], true) ? integer(source["size"]) == data.count : data.isEmpty)
        let root = try storeRoot(path, create: true), names = try storeInventory(root)
        if names.contains(digest) { print(digest); return }
        try storeRequire(names.count < 64)
        let stage = root + "/.stage-" + UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "")
        try storeRequire(mkdir(stage, 0o700) == 0)
        defer {
            var info = stat()
            if lstat(stage, &info) == 0 { try? storeRemoveTransient(root, URL(fileURLWithPath: stage).lastPathComponent) }
        }
        receipt.removeValue(forKey: "config_base64")
        for (name, content) in [("config.toml", data), ("receipt.json", try JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys]))] {
            let file = stage + "/" + name
            try storeWriteFile(file, content)
        }
        var existing = stat()
        try storeRequire(lstat(root + "/" + digest, &existing) != 0 && errno == ENOENT)
        try storeSync(stage)
        try storeRename(stage, root + "/" + digest)
        try storeSync(root)
        print(digest)
    } else if operation == "container" {
        try storeRequire(args.count == 6)
        let root = try storeRoot(path, create: false), digest = try storeDigest(args[2])
        _ = try storeInventory(root)
        try storeVerify(root, digest)
        try storeContainer(root, digest, args[3], args[4], args[5], storeInput(1048576))
        print(digest)
    } else if operation == "verify" {
        try storeRequire(args.count == 3)
        let root = try storeRoot(path, create: false)
        _ = try storeInventory(root)
        let digest = try storeDigest(args[2])
        try storeVerify(root, digest)
        print(digest)
    } else {
        try storeRequire(args.count <= 66)
        let keep = try Set(args.dropFirst(2).map { try storeDigest($0) })
        let root = try storeRoot(path, create: false), names = try storeInventory(root)
        try storeRequire(keep.isSubset(of: Set(names)))
        for name in names where !keep.contains(name) {
            let retired = ".retire-" + name + "-" + UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "")
            try storeRename(root + "/" + name, root + "/" + retired)
            try storeSync(root)
            try storeRemoveTransient(root, retired)
        }
    }
}

let arity = ["prepare-runner-import": 6, "selection-storage-directory": 2, "validate-selected-image": 4, "validate-environment-selection": 2, "validate-runtime-image": 5, "validate-release": 2, "validate-manager": 2, "manager-revision": 2, "manager-base-version": 2, "release-get": 3, "validate-manifest": 2, "get": 3, "repository-root": 2, "repository-remote": 2, "hash-blob": 2, "lock-directory": 2, "lock": 3, "legacy-lock": 2, "identity-migration": 1, "verify-files": 2, "json": 2, "replace-link": 3, "owned-directory": 2, "build-revision": 1, "canonical-path": 2, "owned-file": 2, "ensure-link": 3, "sha256": 2, "host-platform": 1, "host-architecture": 1]
guard let command = args.first, args.count >= (arity[command] ?? 1) else { fail("host helper requires an operation") }
do {
    switch command {
    case "codex-config-store":
        do { try codexConfigStore(Array(args.dropFirst())) }
        catch { fail("unsafe Codex config snapshot store") }
    case "prepare-runner-import":
        try prepareRunnerImport(validateRelease(args[1]), provider: args[2], arch: args[3], archive: args[4], destination: args[5])
    case "canonical-path":
        var path = URL(fileURLWithPath: args[1]).standardizedFileURL.path
        var suffix: [String] = []
        while access(path, F_OK) != 0 {
            let url = URL(fileURLWithPath: path); suffix.insert(url.lastPathComponent, at: 0)
            path = url.deletingLastPathComponent().path
            guard path != "/" || access(path, F_OK) == 0 else { fail("cannot resolve path") }
        }
        guard let resolved = realpath(path, nil) else { fail("cannot resolve path") }; defer { free(resolved) }
        print(([String(cString: resolved)] + suffix).joined(separator: "/").replacingOccurrences(of: "//", with: "/"))
    case "build-revision": print(compiledRevision)
    case "host-architecture":
        #if arch(arm64)
        print("arm64")
        #elseif arch(x86_64)
        print("x86_64")
        #else
        fail("unsupported compiled host architecture")
        #endif
    case "host-platform":
        #if os(macOS)
        print("macos")
        #elseif os(Linux) && arch(x86_64)
        print("linux_x86_64")
        #else
        fail("unsupported host helper platform")
        #endif
    case "sha256": print(SHA256.hash(data: try read(args[1])).map { String(format: "%02x", $0) }.joined())
    case "ensure-link":
        // symlink(2) never follows an existing target directory or link.
        if symlink(args[1], args[2]) != 0 {
            guard errno == EEXIST else { fail("cannot create managed link: \(args[2])") }
            var info = stat()
            guard lstat(args[2], &info) == 0, info.st_mode & S_IFMT == S_IFLNK, info.st_uid == geteuid(),
                  try FileManager.default.destinationOfSymbolicLink(atPath: args[2]) == args[1] else { fail("refusing to replace conflicting managed link: \(args[2])") }
        }
    case "replace-link":
        var info = stat()
        guard lstat(args[1], &info) == 0, info.st_mode & S_IFMT == S_IFLNK, info.st_uid == geteuid() else { fail("activation source is not an owned symbolic link") }
        if lstat(args[2], &info) == 0 { guard info.st_mode & S_IFMT == S_IFLNK, info.st_uid == geteuid() else { fail("refusing to replace an unmanaged activation path") } }
        else if errno != ENOENT { fail("cannot inspect activation path") }
        guard rename(args[1], args[2]) == 0 else { fail("cannot activate release link") }
    case "validate-environment-selection": try validateEnvironmentSelection(args[1])
    case "validate-selected-image":
        var data = Data()
        let limit = 1024 * 1024
        while data.count <= limit {
            let chunk = FileHandle.standardInput.readData(ofLength: min(65536, limit + 1 - data.count))
            if chunk.isEmpty { break }
            data.append(chunk)
        }
        guard data.count <= limit else { fail("selected environment receipt exceeds bounded input") }
        try validateSelectedImage(provider: args[1], arch: args[2], identity: args[3], data: data)
    case "validate-runtime-image":
        var data = Data()
        let limit = 1024 * 1024
        while data.count <= limit {
            let chunk = FileHandle.standardInput.readData(ofLength: min(65536, limit + 1 - data.count))
            if chunk.isEmpty { break }
            data.append(chunk)
        }
        guard data.count <= limit else { fail("runtime image receipt exceeds bounded input") }
        try validateRuntimeImage(validateRelease(args[1]), provider: args[2], arch: args[3], reference: args[4], data: data)
    case "validate-manifest": _ = try validateManifest(args[1])
    case "validate-release": _ = try validateRelease(args[1])
    case "validate-manager":
        let manifest = try validateRelease(args[1])
        guard manifest["source_revision"] as? String == compiledRevision else { fail("bundled helper does not match actual manager source revision") }
    case "manager-revision", "manager-base-version":
        let manifest = try validateManifest(args[1]), envelope = managerEnvelope(manifest)
        print(command == "manager-revision" ? (envelope?.revision ?? manifest["source_revision"] as! String) : (envelope?.base ?? manifest["version"] as! String))
    case "get", "release-get":
        var value: Any = command == "release-get" ? try validateRelease(args[1]) : try json(read(args[1]))
        for part in args[2].split(separator: ".") { guard let next = object(value)[String(part)] else { fail("missing release field: \(args[2])") }; value = next }
        if let s = value as? String { print(s) }
        else if let n = value as? NSNumber { print(CFGetTypeID(n) == CFBooleanGetTypeID() ? (n.boolValue ? "true" : "false") : n.stringValue) }
        else { FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])); print() }
    case "repository-root", "repository-remote":
        let (root, remote) = try repository(args[1]); print(command == "repository-root" ? root : remote)
    case "hash-blob":
        let value = Data(args[1].utf8); let header = Data("blob \(value.count)\0".utf8)
        print(Insecure.SHA1.hash(data: header + value).map { String(format: "%02x", $0) }.joined())
    case "owned-file": _ = checkedInfo(args[1])
    case "selection-storage-directory":
        // Existing runner roots may be 0755 from older launchers. Their private
        // child selection directory and records retain stricter 0700/0600 modes.
        let info = checkedInfo(args[1], directory: true)
        guard info.st_mode & 0o022 == 0 else { fail("selected environment storage is writable by other users") }
    case "owned-directory":
        let info = checkedInfo(args[1], directory: true)
        guard info.st_mode & 0o777 == 0o700 else { fail("unsafe owned directory") }
    case "lock-directory":
        if mkdir(args[1], 0o700) != 0 && errno != EEXIST { fail("cannot create runner lock directory") }
        let info = checkedInfo(args[1], directory: true)
        guard info.st_mode & 0o777 == 0o700 else { fail("unsafe runner lock directory") }
    case "lock":
        let path = args[1]; guard let fd = Int32(args[2]) else { fail("invalid lock descriptor") }
        let current = checkedInfo(path); var info = stat()
        guard fstat(fd, &info) == 0, info.st_dev == current.st_dev, info.st_ino == current.st_ino else { fail("updater lock cannot be verified") }
        if args.count > 3 && args[3] == "inherited" {
            let probe = open(path, O_WRONLY | O_NOFOLLOW); guard probe >= 0 else { fail("cannot verify updater ownership") }; defer { close(probe) }
            guard flock(probe, LOCK_EX | LOCK_NB) != 0, errno == EWOULDBLOCK else { fail("update continuation is missing source ownership") }
        }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { fail("another updater is active") }
    case "legacy-lock":
        // Packaged installs have no directory-lock predecessor. Refuse ambiguous old state rather than guessing ownership.
        fail("legacy updater directory needs recovery through its original source installation")
    case "identity-migration": fail("source installation identity migration must be completed before packaged installation")
    case "verify-files":
        let root = URL(fileURLWithPath: args[1]); let lines = try String(contentsOf: root.appendingPathComponent("files.sha256"), encoding: .utf8).split(separator: "\n")
        guard !lines.isEmpty else { fail("missing bundle inventory") }
        for line in lines {
            guard line.count > 66 else { fail("invalid bundle inventory") }
            let digest = String(line.prefix(64)); let path = String(line.dropFirst(66))
            guard matches(digest, "^[a-f0-9]{64}$"), !path.hasPrefix("/"), !path.split(separator: "/").contains("..") else { fail("unsafe bundle inventory path") }
            let components = path.split(separator: "/").filter { $0 != "." }
            var parent = root
            for component in components.dropLast() {
                parent.appendPathComponent(String(component))
                _ = checkedInfo(parent.path, directory: true)
            }
            let url = root.appendingPathComponent(path); _ = checkedInfo(url.path)
            let observed = SHA256.hash(data: try read(url.path)).map { String(format: "%02x", $0) }.joined()
            guard observed == digest else { fail("bundle checksum mismatch: \(path)") }
        }
    case "json":
        let data = FileHandle.standardInput.readDataToEndOfFile(); let state = object(try json(data)); let agent = object(state["agent"]); let mode = args[1]
        switch mode {
        case "profile":
            guard data.count <= 4096, isBool(state["ready"], true), let profile = state["profile"] as? String, matches(profile, "^[a-f0-9]{64}$") else { exit(1) }; print(profile)
        case "legacy-running": guard let running = integer(object(state["counts"])["running"]), running >= 0, state["error"] == nil else { exit(1) }
        case "prior-ready": guard agent["admission"] == nil, isBool(agent["draining"], false), isBool(object(agent["readiness"])["ready"], true), agent["pause"] is NSNull else { exit(1) }
        case "admission":
            let a = state["agent"] == nil ? state : agent; let d = object(a["admission"])
            if isBool(a["draining"], false), isBool(d["ready"], true) { print("operational") }
            else if isBool(a["draining"], false), isBool(d["ready"], false), d["reason"] as? String == "quota_exhausted", integer(d["retry_at"]) != nil { print("quota_paused") }
            else { print("unhealthy") }
        case "idle": guard isBool(agent["draining"], true), integer(object(state["counts"])["running"]) == 0 else { exit(1) }
        case "workflow-mount":
            guard args.count >= 3 else { fail("workflow mount check requires a path") }
            guard let mounts = try json(data) as? [[String: Any]] else { exit(1) }
            let found = mounts.filter { $0["Destination"] as? String == "/etc/symphony/WORKFLOW.md" }
            guard found.count == 1, found[0]["Type"] as? String == "bind", isBool(found[0]["RW"], false), let source = found[0]["Source"] as? String, [args[2], "/host_mnt" + args[2]].contains(source) else { exit(1) }
        default: fail("unknown JSON predicate")
        }
    default: fail("unknown host helper operation: \(command)")
    }
} catch { fail("\(command) failed: \(error.localizedDescription)") }
