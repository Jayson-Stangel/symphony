# Compare Psych's syntax tree without YAML 1.1 boolean coercion (notably `on`).
# Scalar spelling/quoting is retained; unsupported syntax can never grant omission.
require 'json'
require 'psych'
require 'digest'
Scalar = Struct.new(:value, :plain, :quoted)

def parsed(source)
  tree = Psych.parse_stream(source)
  raise 'one YAML document required' unless tree.children.length == 1
  convert = lambda do |node|
    raise 'custom tags refused' if node.respond_to?(:tag) && node.tag
    raise 'anchors refused' if node.respond_to?(:anchor) && node.anchor
    case node
    when Psych::Nodes::Scalar
      Scalar.new(node.value, node.plain, node.quoted)
    when Psych::Nodes::Mapping
      keys = node.children.each_slice(2).map(&:first)
      raise 'non-scalar or duplicate keys' unless keys.all? { |key| key.is_a?(Psych::Nodes::Scalar) } && keys.map(&:value).uniq.length == keys.length
      node.children.each_slice(2).to_h { |key, value| [convert.call(key), convert.call(value)] }
    when Psych::Nodes::Sequence
      node.children.map { |child| convert.call(child) }
    else
      raise 'unsupported YAML node, including aliases'
    end
  end
  result = convert.call(tree.children.first.root)
  raise 'mapping required' unless result.is_a?(Hash)
  result
end

def key(mapping, name)
  raise 'mapping required' unless mapping.is_a?(Hash)
  mapping.keys.find { |item| item.value == name }
end

def field(mapping, name, default = nil)
  selected = key(mapping, name)
  selected ? mapping[selected] : default
end

def erase_name(mapping)
  selected = key(mapping, 'name')
  raise 'display must be scalar' if selected && !mapping[selected].is_a?(Scalar)
  mapping.delete(selected) if selected
end

def presentation(value, path)
  erase_name(value)
  if path.start_with?('.github/workflows/')
    jobs = field(value, 'jobs')
    raise 'job mapping required' unless jobs.is_a?(Hash)
    jobs.each do |id, job|
      # Evidence addresses and reusable-call prefixes remain behavioral fields.
      sensitive = %w[runner-release.yml native-manager-helpers.yml current-manager-package.yml].include?(File.basename(path)) ||
                  (File.basename(path) == 'package-checks.yml' && id.value == 'native-host-package') || key(job, 'uses')
      erase_name(job) unless sensitive
      steps = field(job, 'steps', [])
      raise 'step sequence required' unless steps.is_a?(Array)
      steps.each { |step| erase_name(step) unless sensitive }
    end
  else
    runs = field(value, 'runs')
    raise 'only composite YAML actions supported' unless field(runs, 'using')&.value == 'composite'
    steps = field(runs, 'steps')
    raise 'step sequence required' unless steps.is_a?(Array)
    steps.each { |step| erase_name(step) }
  end
  value
end

# Finite reviewed job bodies, not a generic exemption for application controls.
def job_identity(value)
  encode = lambda do |item|
    case item
    when Scalar then [item.value, item.plain, item.quoted]
    when Hash then item.map { |key, child| [encode.call(key), encode.call(child)] }
    when Array then item.map { |child| encode.call(child) }
    else item
    end
  end
  Digest::SHA256.hexdigest(JSON.generate(encode.call(value)))
end

begin
  input = JSON.parse(STDIN.read)
  if input['before'].nil? || input['after'].nil?
    raise 'both workflow sides absent' if input['before'].nil? && input['after'].nil?
    presentation(parsed(input['before'] || input.fetch('after')), input.fetch('path'))
    puts JSON.generate({'presentation_only' => false})
    exit 0
  end
  before = presentation(parsed(input.fetch('before')), input.fetch('path'))
  after = presentation(parsed(input.fetch('after')), input.fetch('path'))
  result = {'presentation_only' => before == after}
  if input.fetch('path') == '.github/workflows/burrito-release.yml' && !result['presentation_only']
    known = %w[production-source qualification host-policy runners build controller-diagnostics mac-runner-build mac-runner-controller-diagnostics]
    ordinary = [before, after].map do |document|
      events = field(document, 'on')
      raise 'unknown engine workflow trigger' unless events.is_a?(Hash) && events.keys.map(&:value) == ['workflow_dispatch']
      jobs = field(document, 'jobs')
      raise 'unknown engine controller job' unless jobs.keys.all? { |id| known.include?(id.value) }
      document.delete(key(document, 'on'))
      document.delete(key(document, 'jobs'))
      document
    end
    raise 'unknown engine workflow global controls' unless ordinary[0] == ordinary[1]
    result['manual_controller_only'] = true
  end
  if input.fetch('path') == '.github/workflows/runner-integration.yml' && !result['presentation_only']
    # Only these existing jobs are manual composition/signing consumers. Global
    # settings and ordinary classification/browser jobs remain browser controls.
    manual = %w[packaged-host-consent sign-macos portable helpers reference-node-smoke plan runners manager-inputs manager-package listener-and-cleanup]
    ordinary = [before, after].map do |document|
      jobs = field(document, 'jobs')
      raise 'unknown integration job' unless jobs.keys.all? { |id| (manual + %w[changes chromium]).include?(id.value) }
      consent = field(jobs, 'packaged-host-consent')
      if consent && field(consent, 'if')&.value != "github.event_name == 'workflow_dispatch' && !inputs.sign_macos"
        raise 'packaged host consent must remain explicit manual-only control'
      end
      jobs.delete_if { |id, _| manual.include?(id.value) }
      events = field(document, 'on')
      selected = key(events, 'workflow_dispatch')
      events.delete(selected) if selected
      document
    end
    result['browser_control_changed'] = ordinary[0] != ordinary[1]
  end
  if input.fetch('path') == '.github/workflows/package-checks.yml' && !result['presentation_only']
    # Exact reviewed no-consumer selector and strict required joins only.
    # Native commands, callers, triggers and every other job must be identical.
    audited = {
      'native-host-package' => %w[e712d94d26bff453ab2615554e997c9f2c63d51dcebd511213bd84ff04ae87f8 20e6aee0c3746702be256912c5b9699838ae1f5202582adec860cc2782eeebf2],
      'package-qualification' => %w[755c35dccbc66fee57e976447f6d80f8ce1eb94314b3b4a9fc1ba570590c72e0 c24c93c2913592a6b152c0b81cc79af667c5ac3811c3d21e17b77edb77ae74fc],
      'mac-runner' => %w[7c518ede33b18800ba4e3b48ed63c0ecd409373e94827a809763f727d662ecdc 3f0da5690c80fa29b1e3094d4b2669314f7cc6d3085adff7a881aac6c4b460db]
    }
    copies = [before, after].map { |document| Marshal.load(Marshal.dump(document)) }
    finite = copies.all? do |document|
      jobs = field(document, 'jobs')
      jobs.keys.map(&:value).sort == %w[changes portable qualification host-policy native-host-package package-qualification mac-runner].sort &&
      audited.all? { |name, identities| identities.include?(job_identity(field(jobs, name))) }
    end
    if finite
      copies.each { |document| field(document, 'jobs').delete_if { |id, _| audited.key?(id.value) } }
      result['mac_selector_policy_only'] = true if copies[0] == copies[1]
    end
  end
  if input.fetch('path') == '.github/workflows/make-all.yml' && !result['presentation_only']
    # Only these reviewed coverage/selector seams may change required joins.
    # New commands, predicates, triggers, jobs or a different rollup stay ordinary.
    audited = {
      'make-all' => %w[d06cae5c3c6549e7d75b214e8bbe4db2477269bf25a708026cf70835f3495dd1 a0ad8ba9821a0bd840f4af10bc6c5bbaeaf9f28d243eedef2582edbeb9bb9fb6],
      'elixir-checks' => %w[27b324fad2c0b5ff71df71450bada435682dcd0b9370f82ed648f9964d10ffb8 6e500514340d9177b1b16fe6915767b5d31b761b6ec6b8a1b8eefb2baab1f671],
      'mac-runner' => %w[c0d947e5032f85257bf474be76efc3b83db9fb483ea691d3f0f1b5c74c5329b0 87296894c588f3b29e0df6c8bfef65b0bab91efe21d36f159f53c99bafdab11c]
    }
    copies = [before, after].map { |document| Marshal.load(Marshal.dump(document)) }
    finite = copies.all? do |document|
      jobs = field(document, 'jobs')
      jobs.keys.map(&:value).sort == %w[changes platform-contracts make-all elixir-checks claude-checks mac-runner].sort &&
      audited.all? { |name, identities| identities.include?(job_identity(field(jobs, name))) }
    end
    if finite
      copies.each { |document| field(document, 'jobs').delete_if { |id, _| audited.key?(id.value) } }
      result['elixir_profile_only'] = true if copies[0] == copies[1]
    end
    known = {
      'python3 runner/test_codex_readiness.py' => 'readiness',
      'python3 scripts/ci/release_tests.py codex-config' => 'codex_config',
      'python3 runner/test_npm_cache.py' => 'npm',
      'python3 runner/test_npm_cache_lifecycle.py' => 'npm'
    }
    families = []
    compared = [before, after].map do |document|
      jobs = field(document, 'jobs')
      raise 'unknown application job' unless jobs.keys.all? { |id| %w[changes platform-contracts make-all elixir-checks claude-checks mac-runner].include?(id.value) }
      jobs.each_value do |job|
        field(job, 'steps', []).each do |step|
          run = field(step, 'run')
          next unless run.is_a?(Scalar)
          commands = run.value.lines.map(&:strip).reject(&:empty?)
          next if commands.empty? || !commands.all? { |command| known.key?(command) }
          families.concat(commands.map { |command| known.fetch(command) })
          step.delete(key(step, 'run'))
        end
      end
      document
    end
    result['fast_workflow_families'] = families.uniq.sort if compared[0] == compared[1] && !families.empty?
  end
  puts JSON.generate(result)
rescue StandardError => error
  warn "YAML comparison refused: #{error.class}: #{error.message}"
  exit 1
end
