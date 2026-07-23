#!/bin/zsh
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export BENCH_MODEL='qwen3-coder-next:q8_0'
export BENCH_TAG='qwen3coder_next_q8'
LOG="$HOME/.ollama/bench/results/coder_next_retest_wrapper.log"
exec >>"$LOG" 2>&1
echo "==== start $(date) ===="
/usr/bin/ruby "$HOME/.ollama/bench/retest_model.rb"
echo "==== done $(date) ===="
# Compare vs previous two
/usr/bin/ruby <<'RUBY'
# encoding: utf-8
require 'json'
out = File.expand_path('~/.ollama/bench/results')
files = {
  'DeepSeek-R1 70B Q8' => Dir[File.join(out, 'deepseek_retest_20*.json')].sort[-1],
  'Qwen3-Coder 30B-A3B FP16' => Dir[File.join(out, 'qwen3coder_fp16_retest_20*.json')].sort[-1],
  'Qwen3-Coder-Next Q8' => Dir[File.join(out, 'qwen3coder_next_q8_retest_20*.json')].sort[-1],
}
data = files.transform_values { |f| f && File.exist?(f) ? JSON.parse(File.read(f)) : nil }
abort "missing next results" unless data['Qwen3-Coder-Next Q8']

tasks = %w[merge_intervals bug_binary_search course_schedule]
puts "# Compare @ 64k ctx / 16k predict\n"
printf "| Task |"; data.each_key { |k| printf " %s |" % k }; puts
printf "|------|"; data.each_key { printf "------|" }; puts
tasks.each do |t|
  printf "| %s |" % t
  data.each_value do |rows|
    r = rows.find { |x| x['task'] == t }
    if r
      printf " %s %s/%s (%.1fs, %.1f t/s, %s tok) |" % [r['ok'] ? 'PASS' : 'FAIL', r['score'], r['max_score'], r['wall_s'], r['toks_per_s'], r['eval_tokens']]
    else
      printf " - |"
    end
  end
  puts
end
puts
data.each do |name, rows|
  next unless rows
  score = rows.sum { |r| r['score'].to_i }
  max = rows.sum { |r| r['max_score'].to_i }
  wall = rows.sum { |r| r['wall_s'].to_f }
  tps = rows.map { |r| r['toks_per_s'].to_f }.sum / rows.size
  puts "- **#{name}**: #{score}/#{max}, wall #{wall.round(1)}s, ~#{tps.round(1)} tok/s"
end
path = File.join(out, 'compare_three_64k.md')
# rewrite file with same content
File.open(path, 'w') do |f|
  f.puts "# Compare @ 64k ctx / 16k predict"
  f.puts
  f.printf "| Task |"
  data.each_key { |k| f.printf " %s |" % k }
  f.puts
  f.printf "|------|"
  data.each_key { f.printf "------|" }
  f.puts
  tasks.each do |t|
    f.printf "| %s |" % t
    data.each_value do |rows|
      r = rows.find { |x| x['task'] == t }
      if r
        f.printf " %s %s/%s (%.1fs, %.1f t/s, %s tok) |" % [r['ok'] ? 'PASS' : 'FAIL', r['score'], r['max_score'], r['wall_s'], r['toks_per_s'], r['eval_tokens']]
      else
        f.printf " - |"
      end
    end
    f.puts
  end
  f.puts
  data.each do |name, rows|
    next unless rows
    score = rows.sum { |r| r['score'].to_i }
    max = rows.sum { |r| r['max_score'].to_i }
    wall = rows.sum { |r| r['wall_s'].to_f }
    tps = rows.map { |r| r['toks_per_s'].to_f }.sum / rows.size
    f.puts "- **#{name}**: #{score}/#{max}, wall #{wall.round(1)}s, ~#{tps.round(1)} tok/s"
  end
end
puts "Wrote #{path}"
RUBY
