# Compare Psych's syntax tree without YAML 1.1 boolean coercion (notably `on`).
# Scalar spelling/quoting is retained; unsupported syntax can never grant omission.
require 'json'
require 'psych'
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
  if input.fetch('path') == '.github/workflows/runner-integration.yml' && !result['presentation_only']
    # Only these existing jobs are manual composition/signing consumers. Global
    # settings and ordinary classification/browser jobs remain browser controls.
    manual = %w[sign-macos portable helpers reference-node-smoke plan runners manager-inputs manager-package listener-and-cleanup]
    ordinary = [before, after].map do |document|
      jobs = field(document, 'jobs')
      raise 'unknown integration job' unless jobs.keys.all? { |id| (manual + %w[changes chromium]).include?(id.value) }
      jobs.delete_if { |id, _| manual.include?(id.value) }
      events = field(document, 'on')
      selected = key(events, 'workflow_dispatch')
      events.delete(selected) if selected
      document
    end
    result['browser_control_changed'] = ordinary[0] != ordinary[1]
  end
  if input.fetch('path') == '.github/workflows/make-all.yml' && !result['presentation_only']
    known = {
      'python3 runner/test_codex_readiness.py' => 'readiness',
      'python3 scripts/ci/release_tests.py codex-config' => 'codex_config',
      'python3 runner/test_npm_cache.py' => 'npm',
      'python3 runner/test_npm_cache_lifecycle.py' => 'npm'
    }
    families = []
    compared = [before, after].map do |document|
      jobs = field(document, 'jobs')
      raise 'unknown application job' unless jobs.keys.all? { |id| %w[changes platform-contracts make-all].include?(id.value) }
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
