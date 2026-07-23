#!/bin/zsh
# Run hard bench for one or more models.
# Usage:
#   ~/.ollama/bench/run_hard_bench.sh
#   ~/.ollama/bench/run_hard_bench.sh 'qwen3-coder-next:q8_0' 'qwen3-coder:30b-a3b-fp16'
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

models=("$@")
if (( $# == 0 )); then
  models=(
    'qwen3-coder-next:q8_0'
    'qwen3-coder:30b-a3b-fp16'
    'deepseek-r1:70b-llama-distill-q8_0'
  )
fi

OUT="$HOME/.ollama/bench/results"
mkdir -p "$OUT"
LOG="$OUT/hard_bench_runner.log"
exec > >(tee -a "$LOG") 2>&1

echo "==== hard bench start $(date) ===="
for model in "${models[@]}"; do
  tag="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_hard"
  echo "---- $model (tag=$tag) ----"
  BENCH_MODEL="$model" BENCH_TAG="$tag" /usr/bin/ruby "$HOME/.ollama/bench/hard_bench.rb"
done

/usr/bin/ruby <<'RUBY'
require 'json'
out = File.expand_path('~/.ollama/bench/results')
tags = {
  'Qwen3-Coder-Next Q8' => 'qwen3-coder-next_q8_0_hard',
  'Qwen3-Coder 30B-A3B FP16' => 'qwen3-coder_30b-a3b-fp16_hard',
  'DeepSeek-R1 70B Q8' => 'deepseek-r1_70b-llama-distill-q8_0_hard',
}
rows = {}
tags.each do |name, tag|
  path = File.join(out, "#{tag}_hard_latest.json")
  rows[name] = File.exist?(path) ? JSON.parse(File.read(path)) : nil
end
present = rows.select { |_, v| v }
abort 'no hard results yet' if present.empty?

tasks = present.values.first.map { |r| r['task'] }
puts
puts '# Hard bench compare @ 64k ctx / 16k predict'
puts
printf '| Task |'
present.each_key { |k| printf ' %s |' % k }
puts
printf '|------|'
present.each_key { printf '------|' }
puts
tasks.each do |t|
  printf '| %s |' % t
  present.each_value do |rs|
    r = rs.find { |x| x['task'] == t }
    if r
      printf ' %s %s/%s (%.1fs, %.1f t/s) |' % [r['ok'] ? 'PASS' : 'FAIL', r['score'], r['max_score'], r['wall_s'], r['toks_per_s']]
    else
      printf ' - |'
    end
  end
  puts
end
puts
present.each do |name, rs|
  score = rs.sum { |r| r['score'].to_i }
  max = rs.sum { |r| r['max_score'].to_i }
  wall = rs.sum { |r| r['wall_s'].to_f }
  tps = rs.map { |r| r['toks_per_s'].to_f }.sum / rs.size
  puts "- **#{name}**: #{score}/#{max}, wall #{wall.round(1)}s, ~#{tps.round(1)} tok/s, tasks #{rs.count { |r| r['ok'] }}/#{rs.size}"
end

md = File.join(out, 'compare_hard_64k.md')
File.open(md, 'w') do |f|
  f.puts '# Hard bench compare @ 64k ctx / 16k predict'
  f.puts
  f.printf '| Task |'
  present.each_key { |k| f.printf ' %s |' % k }
  f.puts
  f.printf '|------|'
  present.each_key { f.printf '------|' }
  f.puts
  tasks.each do |t|
    f.printf '| %s |' % t
    present.each_value do |rs|
      r = rs.find { |x| x['task'] == t }
      if r
        f.printf ' %s %s/%s (%.1fs, %.1f t/s) |' % [r['ok'] ? 'PASS' : 'FAIL', r['score'], r['max_score'], r['wall_s'], r['toks_per_s']]
      else
        f.printf ' - |'
      end
    end
    f.puts
  end
  f.puts
  present.each do |name, rs|
    score = rs.sum { |r| r['score'].to_i }
    max = rs.sum { |r| r['max_score'].to_i }
    wall = rs.sum { |r| r['wall_s'].to_f }
    tps = rs.map { |r| r['toks_per_s'].to_f }.sum / rs.size
    f.puts "- **#{name}**: #{score}/#{max}, wall #{wall.round(1)}s, ~#{tps.round(1)} tok/s, tasks #{rs.count { |r| r['ok'] }}/#{rs.size}"
  end
end
puts "Wrote #{md}"
RUBY

echo "==== hard bench done $(date) ===="
