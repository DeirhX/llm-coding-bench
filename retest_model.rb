#!/usr/bin/env ruby
# frozen_string_literal: true

# Shared coding retest: 64k ctx, 16k predict (real coding settings).
require 'json'
require 'net/http'
require 'uri'
require 'fileutils'

OUT_DIR = File.expand_path('~/.ollama/bench/results')
FileUtils.mkdir_p(OUT_DIR)

MODEL = ENV.fetch('BENCH_MODEL')
TAG = ENV.fetch('BENCH_TAG', MODEL.gsub(/[^a-zA-Z0-9._-]/, '_'))

OPTIONS = {
  'temperature' => 0.1,
  'num_ctx' => 65_536,
  'num_predict' => 16_384,
}.freeze

TASKS = [
  {
    id: 'merge_intervals',
    title: 'Code gen: merge overlapping intervals',
    prompt: <<~P,
      Write a Ruby function with this exact signature:

      def merge_intervals(intervals)

      It takes an array of [start, end] intervals and returns a new array of merged
      overlapping/touching intervals, sorted by start.

      Rules:
      - Overlapping or touching intervals merge (e.g. [1,4] and [4,5] -> [1,5])
      - Return [] for empty input
      - After any reasoning, output ONE fenced ruby code block containing the function.
    P
    grade: :merge_intervals,
  },
  {
    id: 'bug_binary_search',
    title: 'Bug hunt: off-by-one binary search',
    prompt: <<~P,
      This Ruby binary search has a bug:

      ```ruby
      def binary_search(arr, target)
        lo = 0
        hi = arr.length
        while lo <= hi
          mid = (lo + hi) / 2
          return mid if arr[mid] == target
          if arr[mid] < target
            lo = mid + 1
          else
            hi = mid - 1
          end
        end
        -1
      end
      ```

      Reply in EXACTLY this format at the end (you may reason first):
      BUG: <one sentence>
      FIX: <the corrected single line of code only>
    P
    grade: :bug_binary_search,
  },
  {
    id: 'course_schedule',
    title: 'Code gen: course schedule (cycle detection)',
    prompt: <<~P,
      Write a Ruby function with this exact signature:

      def can_finish(num_courses, prerequisites)

      num_courses is an Integer.
      prerequisites is an Array of [course, prereq] pairs meaning prereq must be taken before course.
      Return true if you can finish all courses, false if there is a cycle.

      After any reasoning, output ONE fenced ruby code block containing the function.
      The code block is mandatory.
    P
    grade: :course_schedule,
  },
].freeze

def chat(model, prompt)
  uri = URI('http://127.0.0.1:11434/api/chat')
  body = {
    model: model,
    stream: false,
    messages: [{ role: 'user', content: prompt }],
    options: OPTIONS,
  }
  http = Net::HTTP.new(uri.host, uri.port)
  http.read_timeout = 3600
  http.open_timeout = 60
  req = Net::HTTP::Post.new(uri)
  req['Content-Type'] = 'application/json'
  req.body = JSON.generate(body)
  t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
  res = http.request(req)
  wall = Process.clock_gettime(Process::CLOCK_MONOTONIC) - t0
  raise "HTTP #{res.code}: #{res.body[0, 500]}" unless res.is_a?(Net::HTTPSuccess)

  data = JSON.parse(res.body)
  msg = data['message'] || {}
  content = msg['content'].to_s
  thinking = msg['thinking'].to_s
  combined = thinking.empty? ? content : "<think>\n#{thinking}\n</think>\n#{content}"
  eval_duration = data['eval_duration'].to_f
  eval_count = data['eval_count'].to_f
  {
    content: content,
    thinking: thinking,
    combined: combined,
    wall_s: wall,
    load_s: (data['load_duration'].to_f / 1e9),
    prompt_tokens: data['prompt_eval_count'].to_i,
    eval_tokens: data['eval_count'].to_i,
    toks_per_s: eval_duration > 0 ? (eval_count / (eval_duration / 1e9)) : 0.0,
    done_reason: data['done_reason'],
  }
end

def extract_ruby(text)
  fences = text.scan(/```(?:ruby)?\s*\n([\s\S]*?)```/i)
  return fences[-1][0].strip unless fences.empty?
  return Regexp.last_match(1).strip if text =~ /(def (?:merge_intervals|can_finish)\b[\s\S]*)/i

  text.strip
end

def run_ruby_fragment(code)
  path = File.join(OUT_DIR, "tmp_#{Process.pid}_#{rand(1_000_000)}.rb")
  File.write(path, code)
  out = `ruby #{path.inspect} 2>&1`
  File.delete(path) rescue nil
  out
end

def grade_merge_intervals(text)
  code = extract_ruby(text)
  tests = code + "\n" + <<~'RUBY'
    raise 'missing' unless defined?(merge_intervals)
    cases = [
      [[[1,3],[2,6],[8,10],[15,18]], [[1,6],[8,10],[15,18]]],
      [[[1,4],[4,5]], [[1,5]]],
      [[[1,4],[0,4]], [[0,4]]],
      [[], []],
      [[[1,4],[2,3]], [[1,4]]],
      [[[1,4],[0,0]], [[0,0],[1,4]]],
      [[[2,3],[4,5],[6,7],[8,9],[1,10]], [[1,10]]],
    ]
    pass = 0
    cases.each_with_index do |(inp, exp), i|
      got = merge_intervals(inp.map(&:dup))
      raise "case #{i}: got=#{got.inspect} expected=#{exp.inspect}" unless got == exp
      pass += 1
    end
    puts "PASS #{pass}/#{cases.size}"
  RUBY
  out = run_ruby_fragment(tests)
  ok = out.include?('PASS 7/7')
  { ok: ok, score: ok ? 7 : out[/PASS (\d+)/, 1].to_i, detail: out.strip[0, 500], code: code }
end

def grade_bug_binary_search(text)
  body = text.gsub(%r{<think>[\s\S]*?</think>}i, '')
  has_bug = !!(body =~ /length\s*-\s*1|off[- ]by[- ]one|out of bounds|index (?:error|out)|beyond|arr\.size\s*-\s*1|should be.*length/i)
  has_fix = !!(body =~ /hi\s*=\s*arr\.(?:length|size)\s*-\s*1/)
  fix_line = body[/FIX:\s*(.+)$/i, 1].to_s
  has_fix ||= !!(fix_line =~ /length\s*-\s*1|size\s*-\s*1/)
  score = (has_bug ? 1 : 0) + (has_fix ? 1 : 0)
  { ok: score == 2, score: score, detail: "bug_mentioned=#{has_bug} fix_correct=#{has_fix}", code: fix_line }
end

def grade_course_schedule(text)
  code = extract_ruby(text)
  tests = code + "\n" + <<~'RUBY'
    raise 'missing' unless defined?(can_finish)
    cases = [
      [2, [[1,0]], true],
      [2, [[1,0],[0,1]], false],
      [1, [], true],
      [3, [[1,0],[2,1]], true],
      [3, [[0,1],[1,2],[2,0]], false],
      [4, [[1,0],[2,0],[3,1],[3,2]], true],
      [5, [[1,4],[2,4],[3,1],[3,2]], true],
    ]
    pass = 0
    cases.each_with_index do |(n, pre, exp), i|
      got = can_finish(n, pre.map(&:dup))
      raise "case #{i}: got=#{got.inspect} expected=#{exp.inspect}" unless got == exp
      pass += 1
    end
    puts "PASS #{pass}/#{cases.size}"
  RUBY
  out = run_ruby_fragment(tests)
  ok = out.include?('PASS 7/7')
  { ok: ok, score: ok ? 7 : out[/PASS (\d+)/, 1].to_i, detail: out.strip[0, 500], code: code }
end

def grade(task, text)
  case task[:grade]
  when :merge_intervals then grade_merge_intervals(text)
  when :bug_binary_search then grade_bug_binary_search(text)
  when :course_schedule then grade_course_schedule(text)
  else { ok: false, score: 0, detail: 'unknown', code: '' }
  end
end

def max_score_for(task_id)
  task_id == 'bug_binary_search' ? 2 : 7
end

def log!(path, s)
  print s
  File.open(path, 'a') { |f| f.write(s) }
end

log_path = File.join(OUT_DIR, "#{TAG}_retest.log")
summary_path = File.join(OUT_DIR, "#{TAG}_retest_#{Time.now.strftime('%Y%m%d_%H%M%S')}.json")
File.write(log_path, '')
log!(log_path, "Retest #{Time.now}\nmodel=#{MODEL}\noptions=#{OPTIONS.inspect}\n")

begin
  warm = chat(MODEL, 'Reply with exactly: OK')
  ps = JSON.parse(Net::HTTP.get(URI('http://127.0.0.1:11434/api/ps')))
  m = (ps['models'] || []).find { |x| x['name'] == MODEL || x['model'] == MODEL }
  size = m ? (m['size'].to_f / 2**30) : 0
  ctx = m ? m['context_length'] : '?'
  log!(log_path, format("warmup ok wall=%.1fs load=%.1fs eval_tokens=%d ctx=%s size_gib=%.1f\n",
                        warm[:wall_s], warm[:load_s], warm[:eval_tokens], ctx, size))
rescue StandardError => e
  log!(log_path, "warmup ERROR: #{e.message}\n")
end

results = []
TASKS.each do |task|
  log!(log_path, "\n-- #{task[:id]} ...\n")
  begin
    resp = chat(MODEL, task[:prompt])
    g = grade(task, resp[:combined])
    row = {
      model: MODEL,
      task: task[:id],
      title: task[:title],
      ok: g[:ok],
      score: g[:score],
      max_score: max_score_for(task[:id]),
      grade_detail: g[:detail],
      wall_s: resp[:wall_s].round(2),
      load_s: resp[:load_s].round(2),
      eval_tokens: resp[:eval_tokens],
      prompt_tokens: resp[:prompt_tokens],
      toks_per_s: resp[:toks_per_s].round(2),
      done_reason: resp[:done_reason],
      content_chars: resp[:content].length,
      thinking_chars: resp[:thinking].length,
      code_chars: g[:code].to_s.length,
      num_ctx: OPTIONS['num_ctx'],
      num_predict: OPTIONS['num_predict'],
    }
    results << row
    File.write(File.join(OUT_DIR, "#{TAG}__#{task[:id]}.txt"), resp[:combined])
    File.write(File.join(OUT_DIR, "#{TAG}__#{task[:id]}__code.rb"), g[:code].to_s)
    log!(log_path, JSON.pretty_generate(row) + "\n")
  rescue StandardError => e
    row = { model: MODEL, task: task[:id], ok: false, score: 0, max_score: max_score_for(task[:id]), error: e.message }
    results << row
    log!(log_path, "ERROR: #{e.message}\n")
  end
  File.write(summary_path, JSON.pretty_generate(results))
end

total = results.sum { |r| r[:score].to_i }
max = results.sum { |r| r[:max_score].to_i }
passed = results.count { |r| r[:ok] }
avg_tps = results.map { |r| r[:toks_per_s].to_f }.reject(&:zero?)
avg_tps = avg_tps.empty? ? 0.0 : avg_tps.sum / avg_tps.size
log!(log_path, format("\n===== RETEST #{MODEL} =====\npass %d/%d  score %d/%d  avg tok/s %.1f\nWrote %s\n",
                      passed, results.size, total, max, avg_tps, summary_path))
File.write(File.join(OUT_DIR, "#{TAG}_retest_latest.json"), JSON.pretty_generate(results))
