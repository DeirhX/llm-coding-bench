#!/usr/bin/env ruby
# frozen_string_literal: true

require 'json'
require 'net/http'
require 'uri'
require 'fileutils'

OUT_DIR = File.expand_path('~/.ollama/bench/results')
FileUtils.mkdir_p(OUT_DIR)

MODELS = [
  'qwen3-coder:30b-a3b-fp16',
  'qwen2.5-coder:32b-instruct-q8_0',
  'devstral:24b-small-2505-fp16',
  'deepseek-r1:70b-llama-distill-q8_0',
  'llama3.3:70b-instruct-q8_0',
].freeze

OPTIONS = {
  'temperature' => 0.1,
  'num_ctx' => 8192,
  'num_predict' => 1600,
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
      - Do not read stdin. Do not explain.
      - Output ONLY one fenced ruby code block containing the function.
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

      Reply in EXACTLY this format (no extra prose):
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

      Do not explain. Output ONLY one fenced ruby code block containing the function.
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
  http.read_timeout = 1800
  http.open_timeout = 30
  req = Net::HTTP::Post.new(uri)
  req['Content-Type'] = 'application/json'
  req.body = JSON.generate(body)
  t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
  res = http.request(req)
  wall = Process.clock_gettime(Process::CLOCK_MONOTONIC) - t0
  raise "HTTP #{res.code}: #{res.body[0, 400]}" unless res.is_a?(Net::HTTPSuccess)

  data = JSON.parse(res.body)
  content = data.dig('message', 'content').to_s
  eval_duration = data['eval_duration'].to_f
  eval_count = data['eval_count'].to_f
  {
    content: content,
    wall_s: wall,
    total_s: (data['total_duration'].to_f / 1e9),
    load_s: (data['load_duration'].to_f / 1e9),
    prompt_tokens: data['prompt_eval_count'].to_i,
    eval_tokens: data['eval_count'].to_i,
    eval_s: (eval_duration / 1e9),
    toks_per_s: eval_duration > 0 ? (eval_count / (eval_duration / 1e9)) : 0.0,
  }
end

def extract_ruby(text)
  if text =~ /```(?:ruby)?\s*\n([\s\S]*?)```/i
    return Regexp.last_match(1).strip
  end

  text.strip
end

def run_ruby_fragment(code)
  path = File.join(OUT_DIR, "tmp_#{Process.pid}_#{rand(1_000_000)}.rb")
  File.write(path, code)
  out = `ruby #{path.inspect} 2>&1`
  File.delete(path) rescue nil
  out
end

def grade_merge_intervals(content)
  code = extract_ruby(content)
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
  partial = out[/PASS (\d+)/, 1].to_i
  { ok: ok, score: ok ? 7 : partial, detail: out.strip[0, 500] }
end

def grade_bug_binary_search(content)
  text = content.gsub(%r{<think>[\s\S]*?</think>}i, '')
  has_bug = !!(text =~ /length\s*-\s*1|off[- ]by[- ]one|out of bounds|index (?:error|out)|beyond|arr\.size\s*-\s*1|should be.*length/i)
  has_fix = !!(text =~ /hi\s*=\s*arr\.(?:length|size)\s*-\s*1/)
  fix_line = text[/FIX:\s*(.+)$/i, 1].to_s
  has_fix ||= !!(fix_line =~ /length\s*-\s*1|size\s*-\s*1/)
  score = (has_bug ? 1 : 0) + (has_fix ? 1 : 0)
  { ok: score == 2, score: score, detail: "bug_mentioned=#{has_bug} fix_correct=#{has_fix}" }
end

def grade_course_schedule(content)
  code = extract_ruby(content)
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
  partial = out[/PASS (\d+)/, 1].to_i
  { ok: ok, score: ok ? 7 : partial, detail: out.strip[0, 500] }
end

def grade(task, content)
  case task[:grade]
  when :merge_intervals then grade_merge_intervals(content)
  when :bug_binary_search then grade_bug_binary_search(content)
  when :course_schedule then grade_course_schedule(content)
  else { ok: false, score: 0, detail: 'unknown grader' }
  end
end

def max_score_for(task_id)
  task_id == 'bug_binary_search' ? 2 : 7
end

def run_benchmark!
  results = []
  summary_path = File.join(OUT_DIR, "summary_#{Time.now.strftime('%Y%m%d_%H%M%S')}.json")
  log_path = File.join(OUT_DIR, 'bench.log')
  File.write(log_path, '')

  MODELS.each do |model|
    line = "\n========== #{model} ==========\n"
    print line
    File.open(log_path, 'a') { |f| f.write(line) }

    TASKS.each do |task|
      msg = "-- #{task[:id]} ...\n"
      print msg
      File.open(log_path, 'a') { |f| f.write(msg) }
      begin
        resp = chat(model, task[:prompt])
        g = grade(task, resp[:content])
        row = {
          model: model,
          task: task[:id],
          title: task[:title],
          ok: g[:ok],
          score: g[:score],
          max_score: max_score_for(task[:id]),
          grade_detail: g[:detail],
          wall_s: resp[:wall_s].round(2),
          load_s: resp[:load_s].round(2),
          eval_tokens: resp[:eval_tokens],
          toks_per_s: resp[:toks_per_s].round(2),
          prompt_tokens: resp[:prompt_tokens],
        }
        results << row
        safe = model.gsub(/[^a-zA-Z0-9._-]/, '_')
        File.write(File.join(OUT_DIR, "#{safe}__#{task[:id]}.txt"), resp[:content])
        dumped = JSON.pretty_generate(row) + "\n"
        print dumped
        File.open(log_path, 'a') { |f| f.write(dumped) }
      rescue StandardError => e
        row = { model: model, task: task[:id], ok: false, score: 0, max_score: max_score_for(task[:id]), error: e.message }
        results << row
        err = "ERROR: #{e.message}\n"
        print err
        File.open(log_path, 'a') { |f| f.write(err) }
      end
      File.write(summary_path, JSON.pretty_generate(results))
    end
  end

  puts "\n\n===== LEADERBOARD ====="
  File.open(log_path, 'a') { |f| f.write("\n===== LEADERBOARD =====\n") }
  results.group_by { |r| r[:model] }.each do |model, rows|
    total_score = rows.sum { |r| r[:score].to_i }
    max_score = rows.sum { |r| r[:max_score].to_i }
    avg_tps = rows.map { |r| r[:toks_per_s].to_f }.reject(&:zero?)
    avg_tps = avg_tps.empty? ? 0.0 : (avg_tps.sum / avg_tps.size)
    passed = rows.count { |r| r[:ok] }
    line = format("%-40s  pass %d/%d  score %d/%d  avg tok/s %.1f\n", model, passed, rows.size, total_score, max_score, avg_tps)
    print line
    File.open(log_path, 'a') { |f| f.write(line) }
  end
  puts "\nWrote #{summary_path}"
  File.open(log_path, 'a') { |f| f.write("Wrote #{summary_path}\n") }
end

run_benchmark! if $PROGRAM_NAME == __FILE__
