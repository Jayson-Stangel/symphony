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
func validateManifest(_ path: String) throws -> [String: Any] {
    let manifest = object(try json(read(path)))
    let schema = integer(manifest["schema_version"])
    guard schema == 1 || schema == 2,
          let version = manifest["version"] as? String, matches(version, "^[0-9]+\\.[0-9]+\\.[0-9]+(-[A-Za-z0-9.-]+)?$"),
          let revision = manifest["source_revision"] as? String, matches(revision, "^[a-f0-9]{40}$"),
          ["candidate", "stable"].contains(manifest["channel"] as? String ?? ""),
          manifest["host_architectures"] as? [String] == ["arm64", "x86_64"] else { fail("invalid or unsupported release manifest") }
    if schema == 2 {
        let compatibility = object(manifest["compatibility"])
        guard Set(compatibility.keys) == Set(["runner_protocol"]), integer(compatibility["runner_protocol"]) == 1 else { fail("unsupported manager/runner compatibility") }
    }
    guard Set(object(manifest["runners"]).keys) == Set(["codex", "claude"]) else { fail("both runner providers are required") }
    for provider in ["codex", "claude"] {
        guard Set(object(object(manifest["runners"])[provider]).keys) == Set(["arm64", "x86_64"]) else { fail("both runner architectures are required") }
        for arch in ["arm64", "x86_64"] {
            let runner = object(object(object(manifest["runners"])[provider])[arch])
            let tools = object(runner["tool_versions"])
            for tool in [provider, "git", "node", "playwright"] { guard let value = tools[tool] as? String, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { fail("missing recorded tool version: \(tool)") } }
            guard let runnerRevision = runner["source_revision"] as? String, matches(runnerRevision, "^[a-f0-9]{40}$"),
                  schema != 1 || runnerRevision == revision,
                  runner["platform"] as? String == (arch == "arm64" ? "linux/arm64" : "linux/amd64"),
                  let image = runner["image"] as? String,
                  matches(image, "^[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}$") else { fail("missing or invalid \(provider)/\(arch) release image") }
            if schema == 2 {
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
                      proof["repository"] as? String == "Jayson-Stangel/symphony",
                      let run = integer(proof["run_id"]), run > 0,
                      let attempt = integer(proof["run_attempt"]), attempt > 0,
                      let artifact = integer(proof["artifact_id"]), artifact > 0,
                      let controller = proof["controller_revision"] as? String, matches(controller, "^[a-f0-9]{40}$"),
                      proof["source_revision"] as? String == runnerRevision,
                      proof["input_identity"] as? String == inputs,
                      proof["image"] as? String == image,
                      ["runner-\(provider)-\(arch)", "runners / runner-\(provider)-\(arch)"].contains(proof["job_name"] as? String ?? ""),
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
                           "runner-\(provider)-\(arch)", "runners / runner-\(provider)-\(arch)"].contains(origin["job_name"] as? String ?? "") else { fail("invalid separate native record origin") }
                }
                if let raw = proof["payload_origin"] {
                    let origin = object(raw)
                    guard Set(origin.keys) == Set(["run_id", "controller_revision"]), integer(origin["run_id"]) == run,
                          origin["controller_revision"] as? String == controller else { fail("invalid original native payload origin") }
                }
                if let raw = proof["native_step_recovery"] {
                    let recovery = object(raw)
                    let keys = Set(["job_id", "original_job_conclusion", "qualification_step_number", "payload_step_number", "failure_step_number", "cleanup_step_number", "failure_reason", "log_sha256"])
                    guard Set(recovery.keys) == keys, proof["record_origin"] != nil, proof["payload_origin"] != nil,
                          ["job_id", "qualification_step_number", "payload_step_number", "failure_step_number", "cleanup_step_number"].allSatisfy({ (integer(recovery[$0]) ?? 0) > 0 }),
                          recovery["original_job_conclusion"] as? String == "failure", recovery["failure_reason"] as? String == "bare-upload-artifact-sha256",
                          let logDigest = recovery["log_sha256"] as? String, matches(logDigest, "^[a-f0-9]{64}$") else { fail("invalid successful-native-stage recovery evidence") }
                }
                if let recordFile = proof["record_filename"] {
                    guard let name = recordFile as? String, ["\(provider)-\(arch).json", "previous/\(provider)-\(arch).json"].contains(name) else { fail("invalid original native record filename") }
                }
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
    return manifest
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
let arity = ["validate-manifest": 2, "get": 3, "repository-root": 2, "repository-remote": 2, "hash-blob": 2, "lock-directory": 2, "lock": 3, "legacy-lock": 2, "identity-migration": 1, "verify-files": 2, "json": 2, "replace-link": 3, "owned-directory": 2, "build-revision": 1, "canonical-path": 2, "owned-file": 2, "ensure-link": 3, "sha256": 2, "host-platform": 1]
guard let command = args.first, args.count >= (arity[command] ?? 1) else { fail("host helper requires an operation") }
do {
    switch command {
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
    case "validate-manifest": _ = try validateManifest(args[1])
    case "get":
        var value: Any = try json(read(args[1]))
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
