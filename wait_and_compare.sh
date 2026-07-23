#!/bin/zsh
set -e
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
OUT="$HOME/.ollama/bench/results"
LOG="$OUT/wait_and_compare.log"
exec >>"$LOG" 2>&1
echo "==== start $(date) ===="

# Wait for DeepSeek retest process to finish (up to ~3h)
echo "Waiting for retest_deepseek.rb ..."
for i in {1..360}; do
  if ! pgrep -f '/Users/deirh/.ollama/bench/retest_deepseek.rb' >/dev/null; then
    if grep -q 'DEEPSEEK RETEST' "$OUT/deepseek_retest.log" 2>/dev/null; then
      echo "DeepSeek finished at $(date)"
      break
    fi
    # process gone but maybe crashed before marker
    if [ "$i" -gt 3 ] && ! pgrep -f retest_deepseek >/dev/null; then
      echo "DeepSeek process gone; checking log..."
      tail -20 "$OUT/deepseek_retest.log" || true
      break
    fi
  fi
  sleep 30
done

echo "==== DeepSeek summary ===="
tail -30 "$OUT/deepseek_retest.log" || true

echo "==== Starting Qwen3-Coder (fastest) retest $(date) ===="
export BENCH_MODEL='qwen3-coder:30b-a3b-fp16'
export BENCH_TAG='qwen3coder_fp16'
/usr/bin/ruby "$HOME/.ollama/bench/retest_model.rb"

echo "==== Building comparison $(date) ===="
/usr/bin/ruby <<'RUBY'
require 'json'
require 'time'

out = File.expand_path('~/.ollama/bench/results')
ds_files = Dir[File.join(out, 'deepseek_retest_20*.json')].sort
qw_files = Dir[File.join(out, 'qwen3coder_fp16_retest_20*.json')].sort
abort "missing deepseek results" if ds_files.empty?
abort "missing qwen results" if qw_files.empty?

ds = JSON.parse(File.read(ds_files[-1]))
qw = JSON.parse(File.read(qw_files[-1]))

def idx(rows)
  rows.map { |r| [r['task'], r] }.to_h
end

di = idx(ds)
qi = idx(qw)
tasks = (di.keys | qi.keys)

lines = []
lines << "# Quality compare @ 64k ctx / 16k predict"
lines << "Generated: #{Time.now}"
lines << ""
lines << "| Task | DeepSeek-R1 70B Q8 | Qwen3-Coder 30B-A3B FP16 | Winner |"
lines << "|------|--------------------|---------------------------|--------|"

ds_score = qw_score = 0
ds_wall = qw_wall = 0.0

tasks.each do |t|
  a = di[t] || {}
  b = qi[t] || {}
  ds_score += a['score'].to_i
  qw_score += b['score'].to_i
  ds_wall += a['wall_s'].to_f
  qw_wall += b['wall_s'].to_f
  as = "#{a['ok'] ? 'PASS' : 'FAIL'} #{a['score']}/#{a['max_score']} (#{a['wall_s']}s, #{a['toks_per_s']} t/s, #{a['eval_tokens']} tok)"
  bs = "#{b['ok'] ? 'PASS' : 'FAIL'} #{b['score']}/#{b['max_score']} (#{b['wall_s']}s, #{b['toks_per_s']} t/s, #{b['eval_tokens']} tok)"
  win = if a['score'].to_i > b['score'].to_i
          'DeepSeek'
        elsif b['score'].to_i > a['score'].to_i
          'Qwen3-Coder'
        else
          # tie on correctness: prefer fewer tokens / faster wall for quality-efficiency
          'TIE (correctness)'
        end
  lines << "| #{t} | #{as} | #{bs} | #{win} |"
end

lines << ""
lines << "## Totals"
lines << "- DeepSeek score: **#{ds_score}** / wall **#{ds_wall.round(1)}s**"
lines << "- Qwen3-Coder score: **#{qw_score}** / wall **#{qw_wall.round(1)}s**"
lines << ""
lines << "## Quality notes (code artifacts)"
tasks.each do |t|
  next if t == 'bug_binary_search'
  ds_code = File.join(out, "deepseek_retest__#{t}__code.rb")
  # deepseek script used different naming
  ds_code = File.join(out, "deepseek_retest__#{t}.txt") unless File.exist?(File.join(out, "deepseek_retest__#{t}__code.rb"))
  qw_code = File.join(out, "qwen3coder_fp16__#{t}__code.rb")
  lines << "### #{t}"
  if File.exist?(qw_code)
    qc = File.read(qw_code)
    lines << "- Qwen code (#{qc.lines.size} lines):"
    lines << "```ruby"
    lines << qc.strip
    lines << "```"
  end
  # Extract from deepseek combined if needed
  ds_txt = File.join(out, "deepseek_retest__#{t}.txt")
  if File.exist?(ds_txt)
    body = File.read(ds_txt)
    code = body.scan(/```(?:ruby)?\s*\n([\s\S]*?)```/i)[-1]
    snippet = code ? code[0].strip : body.strip[0, 800]
    lines << "- DeepSeek final code/answer excerpt:"
    lines << "```ruby"
    lines << snippet
    lines << "```"
  end
  lines << ""
end

# Qualitative: verbosity / instruction following
lines << "## Behavioral quality"
tasks.each do |t|
  a = di[t] || {}
  b = qi[t] || {}
  lines << "- **#{t}**: DeepSeek thinking_chars=#{a['thinking_chars']} content_chars=#{a['content_chars']} eval_tokens=#{a['eval_tokens']}; " \
           "Qwen thinking_chars=#{b['thinking_chars']} content_chars=#{b['content_chars']} eval_tokens=#{b['eval_tokens']}"
end

overall = if qw_score > ds_score
            'Qwen3-Coder wins on correctness score.'
          elsif ds_score > qw_score
            'DeepSeek wins on correctness score.'
          else
            'Correctness tied; Qwen3-Coder wins on latency/efficiency unless DeepSeek’s reasoning adds value you can see in harder tasks.'
          end
lines << ""
lines << "## Verdict"
lines << overall

path = File.join(out, 'compare_deepseek_vs_qwen3coder.md')
File.write(path, lines.join("\n") + "\n")
puts lines.join("\n")
puts "Wrote #{path}"
RUBY

echo "==== done $(date) ===="
