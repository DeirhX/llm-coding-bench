#!/usr/bin/env ruby
# frozen_string_literal: true

# Harder coding bench — same Ollama chat settings as the 64k retest,
# but tasks that actually separate strong models.
#
# Usage:
#   BENCH_MODEL='qwen3-coder-next:q8_0' BENCH_TAG='next_hard' ruby hard_bench.rb
#   BENCH_SELFTEST=1 ruby hard_bench.rb   # grade reference solutions only

require 'json'
require 'net/http'
require 'uri'
require 'fileutils'
require 'set'

OUT_DIR = File.expand_path('~/.ollama/bench/results')
FileUtils.mkdir_p(OUT_DIR)

SELFTEST = ENV['BENCH_SELFTEST'] == '1'
MODEL = SELFTEST ? 'selftest' : ENV.fetch('BENCH_MODEL')
TAG = ENV.fetch('BENCH_TAG', SELFTEST ? 'selftest_hard' : MODEL.gsub(/[^a-zA-Z0-9._-]/, '_'))

OPTIONS = {
  'temperature' => 0.1,
  'num_ctx' => 65_536,
  'num_predict' => 16_384,
}.freeze

TASKS = [
  {
    id: 'regex_match',
    title: 'Hard: regex match with . and *',
    max_score: 12,
    prompt: <<~P,
      Write a Ruby function with this exact signature:

      def is_match(s, p)

      Implement full-string matching where pattern p supports only:
      - letters matching themselves
      - '.' matching any single character
      - '*' meaning "zero or more of the preceding element"

      The match must cover the entire string s (not a substring).
      s and p contain only lowercase letters, '.', and '*'.
      Assume every '*' has a valid preceding element.

      After any reasoning, output ONE fenced ruby code block containing the function.
    P
    grade: :regex_match,
  },
  {
    id: 'lru_cache',
    title: 'Hard: LRU cache class',
    max_score: 14,
    prompt: <<~P,
      Write a Ruby class with this exact API:

      class LRUCache
        def initialize(capacity)
        def get(key)   # return value, or -1 if missing
        def put(key, value)
      end

      Semantics:
      - capacity is a positive Integer
      - get/put of an existing key makes it most-recently used
      - put of a new key when at capacity evicts the least-recently used key
      - put may update an existing key's value (and mark it most-recently used)
      - keys and values are Integers

      After any reasoning, output ONE fenced ruby code block containing the full class.
    P
    grade: :lru_cache,
  },
  {
    id: 'alien_order',
    title: 'Hard: alien dictionary order',
    max_score: 10,
    prompt: <<~P,
      Write a Ruby function with this exact signature:

      def alien_order(words)

      words is an Array of Strings in sorted order for an unknown alphabet.
      Derive a valid character order for that alphabet.

      Return:
      - a String containing each distinct letter from words exactly once, in a valid order
      - "" if the input implies no valid order (cycle, or a longer word placed before its prefix)

      If multiple orders are valid, any one is accepted.
      Letters are lowercase a-z only.

      After any reasoning, output ONE fenced ruby code block containing the function.
    P
    grade: :alien_order,
  },
  {
    id: 'eval_expr',
    title: 'Hard: arithmetic expression evaluator',
    max_score: 12,
    prompt: <<~P,
      Write a Ruby function with this exact signature:

      def eval_expr(expr)

      Evaluate a string arithmetic expression and return an Integer.

      Grammar / rules:
      - Integers may be negative (unary minus) and multi-digit
      - Binary operators: + - * /
      - Parentheses ( ) for grouping
      - No exponentiation
      - Spaces may appear anywhere and must be ignored
      - Operator precedence: * and / bind tighter than + and -
      - Same-precedence operators associate left-to-right
      - Division truncates toward zero (like C / Java / Ruby integer division for positives;
        for negatives, truncate toward zero, e.g. (-7)/2 == -3)
      - expr is always syntactically valid and fits in 64-bit intermediate math for these tests

      After any reasoning, output ONE fenced ruby code block containing the function.
    P
    grade: :eval_expr,
  },
  {
    id: 'fix_vm',
    title: 'Hard: fix buggy stack VM (3 bugs)',
    max_score: 10,
    prompt: <<~P,
      This Ruby stack VM is supposed to evaluate a tiny bytecode language, but it has bugs.

      Instruction format: an Array of ops. Each op is [opname, *args].
      Stack holds Integers. Inputs come from an Array consumed left-to-right by IN.

      Ops:
      - ["IN"]           push next input value
      - ["PUSH", n]      push integer n
      - ["ADD"]          pop b, pop a, push a+b
      - ["SUB"]          pop b, pop a, push a-b
      - ["MUL"]          pop b, pop a, push a*b
      - ["DUP"]          duplicate top of stack
      - ["SWAP"]         swap top two stack values
      - ["JZ", offset]   pop v; if v == 0, add offset to the instruction pointer
                         (IP has already been advanced past this instruction; offset is relative)
      - ["JMP", offset]  add offset to IP (same relative rule as JZ)
      - ["HALT"]         stop; return top of stack (or 0 if empty)

      Buggy implementation:

      ```ruby
      def run_vm(code, inputs)
        ip = 0
        stack = []
        in_i = 0
        while ip < code.length
          op, *args = code[ip]
          ip += 1
          case op
          when "IN"
            stack << inputs[in_i]
          when "PUSH"
            stack << args[0]
          when "ADD"
            a = stack.pop
            b = stack.pop
            stack << (a + b)
          when "SUB"
            a = stack.pop
            b = stack.pop
            stack << (a - b)
          when "MUL"
            a = stack.pop
            b = stack.pop
            stack << (a * b)
          when "DUP"
            stack << stack[-1]
          when "SWAP"
            stack[-1], stack[-2] = stack[-2], stack[-1]
          when "JZ"
            v = stack.pop
            ip += args[0] if v != 0
          when "JMP"
            ip += args[0]
          when "HALT"
            return stack[-1] || 0
          else
            raise "unknown op \#{op}"
          end
        end
        stack[-1] || 0
      end
      ```

      Find and fix ALL bugs. Keep the same function signature and op names.
      Do not leave bug comments in your solution.
      After any reasoning, output ONE fenced ruby code block containing the corrected function.
    P
    grade: :fix_vm,
  },
].freeze

# --- reference solutions (self-test + sanity) ---------------------------------

REF = {}

REF['regex_match'] = <<~'RUBY'
  def is_match(s, p)
    m = s.length
    n = p.length
    dp = Array.new(m + 1) { Array.new(n + 1, false) }
    dp[0][0] = true
    (1..n).each do |j|
      dp[0][j] = true if p[j - 1] == '*' && j >= 2 && dp[0][j - 2]
    end
    (1..m).each do |i|
      (1..n).each do |j|
        if p[j - 1] == '*'
          dp[i][j] = dp[i][j - 2]
          if p[j - 2] == '.' || p[j - 2] == s[i - 1]
            dp[i][j] ||= dp[i - 1][j]
          end
        elsif p[j - 1] == '.' || p[j - 1] == s[i - 1]
          dp[i][j] = dp[i - 1][j - 1]
        end
      end
    end
    dp[m][n]
  end
RUBY

REF['lru_cache'] = <<~'RUBY'
  class LRUCache
    Node = Struct.new(:key, :val, :prev, :next)
    def initialize(capacity)
      @cap = capacity
      @map = {}
      @head = Node.new(nil, nil, nil, nil)
      @tail = Node.new(nil, nil, nil, nil)
      @head.next = @tail
      @tail.prev = @head
    end
    def get(key)
      n = @map[key]
      return -1 unless n
      move_to_front(n)
      n.val
    end
    def put(key, value)
      if (n = @map[key])
        n.val = value
        move_to_front(n)
        return
      end
      if @map.size >= @cap
        lru = @tail.prev
        remove(lru)
        @map.delete(lru.key)
      end
      n = Node.new(key, value, nil, nil)
      @map[key] = n
      insert_after_head(n)
    end
    def remove(n)
      n.prev.next = n.next
      n.next.prev = n.prev
    end
    def insert_after_head(n)
      n.next = @head.next
      n.prev = @head
      @head.next.prev = n
      @head.next = n
    end
    def move_to_front(n)
      remove(n)
      insert_after_head(n)
    end
  end
RUBY

REF['alien_order'] = <<~'RUBY'
  require 'set'
  def alien_order(words)
    chars = {}
    words.each { |w| w.each_char { |c| chars[c] = true } }
    graph = Hash.new { |h, k| h[k] = Set.new }
    indeg = Hash.new(0)
    chars.each_key { |c| indeg[c] = 0 }
    (0...words.length - 1).each do |i|
      a = words[i]
      b = words[i + 1]
      return "" if a.length > b.length && a.start_with?(b)
      found = false
      [a.length, b.length].min.times do |j|
        if a[j] != b[j]
          unless graph[a[j]].include?(b[j])
            graph[a[j]] << b[j]
            indeg[b[j]] += 1
          end
          found = true
          break
        end
      end
    end
    q = indeg.select { |_, d| d.zero? }.map(&:first).sort
    order = []
    until q.empty?
      c = q.shift
      order << c
      graph[c].to_a.sort.each do |nxt|
        indeg[nxt] -= 1
        q << nxt if indeg[nxt].zero?
        q.sort!
      end
    end
    return "" if order.length != chars.length
    order.join
  end
RUBY

REF['eval_expr'] = <<~'RUBY'
  def eval_expr(expr)
    s = expr.gsub(/\s+/, '')
    i = 0
    factor = nil
    term = nil
    expression = nil
    factor = lambda do
      if s[i] == '+'
        i += 1
        return factor.call
      end
      if s[i] == '-'
        i += 1
        return -factor.call
      end
      if s[i] == '('
        i += 1
        v = expression.call
        i += 1
        return v
      end
      start = i
      i += 1 while i < s.length && s[i] =~ /\d/
      s[start...i].to_i
    end
    term = lambda do
      v = factor.call
      while i < s.length && (s[i] == '*' || s[i] == '/')
        op = s[i]
        i += 1
        r = factor.call
        v = op == '*' ? v * r : (v.to_f / r).truncate
      end
      v
    end
    expression = lambda do
      v = term.call
      while i < s.length && (s[i] == '+' || s[i] == '-')
        op = s[i]
        i += 1
        r = term.call
        v = op == '+' ? v + r : v - r
      end
      v
    end
    expression.call
  end
RUBY

REF['fix_vm'] = <<~'RUBY'
  def run_vm(code, inputs)
    ip = 0
    stack = []
    in_i = 0
    while ip < code.length
      op, *args = code[ip]
      ip += 1
      case op
      when "IN"
        stack << inputs[in_i]
        in_i += 1
      when "PUSH"
        stack << args[0]
      when "ADD"
        b = stack.pop
        a = stack.pop
        stack << (a + b)
      when "SUB"
        b = stack.pop
        a = stack.pop
        stack << (a - b)
      when "MUL"
        b = stack.pop
        a = stack.pop
        stack << (a * b)
      when "DUP"
        stack << stack[-1]
      when "SWAP"
        stack[-1], stack[-2] = stack[-2], stack[-1]
      when "JZ"
        v = stack.pop
        ip += args[0] if v == 0
      when "JMP"
        ip += args[0]
      when "HALT"
        return stack[-1] || 0
      else
        raise "unknown op #{op}"
      end
    end
    stack[-1] || 0
  end
RUBY

# Planted bugs in the fix_vm prompt (not disclosed to the model):
# 1) IN never advances in_i
# 2) binary ops pop operands in reverse order (breaks SUB)
# 3) JZ condition inverted (jumps when nonzero)

# --- helpers -----------------------------------------------------------------

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

  if text =~ /(class LRUCache\b[\s\S]*)/i ||
     text =~ /(def (?:is_match|alien_order|eval_expr|run_vm)\b[\s\S]*)/i
    return Regexp.last_match(1).strip
  end

  text.strip
end

def run_ruby_fragment(code, timeout_s: 5)
  path = File.join(OUT_DIR, "tmp_#{Process.pid}_#{rand(1_000_000)}.rb")
  out_path = "#{path}.out"
  File.write(path, code)
  pid = spawn("ruby #{path.inspect}", out: out_path, err: out_path)
  deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + timeout_s
  while Process.clock_gettime(Process::CLOCK_MONOTONIC) < deadline
    if Process.wait(pid, Process::WNOHANG)
      out = File.read(out_path)
      File.delete(path) rescue nil
      File.delete(out_path) rescue nil
      return { out: out, status: $?.exitstatus }
    end
    sleep 0.05
  end
  Process.kill('KILL', pid) rescue nil
  Process.wait(pid) rescue nil
  out = File.exist?(out_path) ? File.read(out_path) : ''
  File.delete(path) rescue nil
  File.delete(out_path) rescue nil
  { out: "TIMEOUT after #{timeout_s}s\n#{out}", status: -1 }
rescue StandardError => e
  { out: e.message, status: 1 }
end

def score_cases(code, harness)
  res = run_ruby_fragment(code + "\n" + harness)
  detail = res[:out].strip[0, 800]
  if detail =~ /SCORE (\d+)\/(\d+)/
    score = Regexp.last_match(1).to_i
    max = Regexp.last_match(2).to_i
    { ok: score == max, score: score, max_score: max, detail: detail, code: code }
  elsif detail.include?('TIMEOUT')
    { ok: false, score: 0, max_score: 0, detail: detail, code: code }
  else
    { ok: false, score: 0, max_score: 0, detail: detail, code: code }
  end
end

# --- graders -----------------------------------------------------------------

def grade_regex_match(text)
  code = extract_ruby(text)
  harness = <<~'RUBY'
    raise 'missing' unless defined?(is_match)
    cases = [
      ["aa", "a", false],
      ["aa", "a*", true],
      ["ab", ".*", true],
      ["aab", "c*a*b", true],
      ["mississippi", "mis*is*p*.", false],
      ["", "", true],
      ["", "a*", true],
      ["a", "ab*", true],
      ["bbbba", ".*a*a", true],
      ["ab", ".*c", false],
      ["aaa", "a*a", true],
      ["aaa", "aaaa", false],
    ]
    pass = 0
    cases.each_with_index do |(s, p, exp), i|
      got = is_match(s, p)
      raise "case #{i}: is_match(#{s.inspect}, #{p.inspect}) => #{got.inspect}, want #{exp.inspect}" unless !!got == !!exp
      pass += 1
    end
    puts "SCORE #{pass}/#{cases.size}"
  RUBY
  score_cases(code, harness)
end

def grade_lru_cache(text)
  code = extract_ruby(text)
  harness = <<~'RUBY'
    raise 'missing' unless defined?(LRUCache)
    pass = 0
    checks = []

    c = LRUCache.new(2)
    c.put(1, 1)
    c.put(2, 2)
    checks << (c.get(1) == 1)                         # 1
    c.put(3, 3)                                       # evicts 2
    checks << (c.get(2) == -1)                        # 2
    checks << (c.get(3) == 3)                         # 3
    c.put(4, 4)                                       # evicts 1
    checks << (c.get(1) == -1)                        # 4
    checks << (c.get(3) == 3)                         # 5
    checks << (c.get(4) == 4)                         # 6

    c2 = LRUCache.new(2)
    c2.put(2, 1)
    c2.put(2, 2)                                      # update
    checks << (c2.get(2) == 2)                        # 7
    c2.put(1, 1)
    c2.put(4, 1)                                      # evicts 2 (1 more recent after get? wait: put 1, then put 4 -> 2 is LRU)
    checks << (c2.get(2) == -1)                       # 8

    c3 = LRUCache.new(1)
    c3.put(2, 1)
    checks << (c3.get(2) == 1)                        # 9
    c3.put(3, 2)
    checks << (c3.get(2) == -1)                       # 10
    checks << (c3.get(3) == 2)                        # 11

    c4 = LRUCache.new(2)
    c4.put(1, 1)
    c4.put(2, 2)
    c4.get(1)
    c4.put(3, 3)                                      # evicts 2
    checks << (c4.get(2) == -1)                       # 12
    checks << (c4.get(3) == 3)                        # 13
    checks << (c4.get(1) == 1)                        # 14

    checks.each_with_index do |ok, i|
      raise "check #{i + 1} failed" unless ok
      pass += 1
    end
    puts "SCORE #{pass}/#{checks.size}"
  RUBY
  score_cases(code, harness)
end

def grade_alien_order(text)
  code = extract_ruby(text)
  # Validator runs in-process with the candidate function.
  harness = <<~'RUBY'
    require 'set'
    raise 'missing' unless defined?(alien_order)

    def implied_edges(words)
      chars = Set.new
      words.each { |w| w.each_char { |c| chars << c } }
      edges = Set.new
      invalid = false
      (0...words.length - 1).each do |i|
        a = words[i]
        b = words[i + 1]
        if a.length > b.length && a.start_with?(b)
          invalid = true
          break
        end
        [a.length, b.length].min.times do |j|
          if a[j] != b[j]
            edges << [a[j], b[j]]
            break
          end
        end
      end
      [chars, edges, invalid]
    end

    def valid_order?(order, words)
      return false unless order.is_a?(String)
      chars, edges, invalid = implied_edges(words)
      return order == "" if invalid
      return false if order == ""
      return false unless order.chars.sort == chars.to_a.sort
      return false unless order.chars.uniq.length == order.length
      pos = {}
      order.chars.each_with_index { |c, i| pos[c] = i }
      edges.all? { |a, b| pos[a] < pos[b] }
    end

    cases = [
      [["wrt", "wrf", "er", "ett", "rftt"], :valid],
      [["z", "x"], :valid],
      [["z", "x", "z"], :invalid],
      [["abc", "ab"], :invalid],
      [["a", "b", "ca", "cc"], :valid],
      [["ac", "ab", "bc"], :valid],
      [["a"], :valid],
      [["ab", "adc"], :valid],
      [["abc", "abx", "ag"], :valid],
      [["z", "z"], :valid],
    ]
    pass = 0
    cases.each_with_index do |(words, kind), i|
      got = alien_order(words.map(&:dup))
      ok = if kind == :invalid
             got == ""
           else
             valid_order?(got, words)
           end
      raise "case #{i}: words=#{words.inspect} got=#{got.inspect}" unless ok
      pass += 1
    end
    puts "SCORE #{pass}/#{cases.size}"
  RUBY
  score_cases(code, harness)
end

def grade_eval_expr(text)
  code = extract_ruby(text)
  harness = <<~'RUBY'
    raise 'missing' unless defined?(eval_expr)
    cases = [
      ["3+2*2", 7],
      [" 3+5 / 2 ", 5],
      ["(1+(4+5+2)-3)+(6+8)", 23],
      ["2-1+2", 3],
      ["-2+3", 1],
      ["1-(2+3)", -4],
      ["14/3*2", 8],
      ["(-7)/2", -3],
      ["2*(3+4)*5", 70],
      ["10-2-3", 5],
      ["8/2/2", 2],
      ["-((2+3)*4)", -20],
    ]
    pass = 0
    cases.each_with_index do |(expr, exp), i|
      got = eval_expr(expr)
      raise "case #{i}: eval_expr(#{expr.inspect}) => #{got.inspect}, want #{exp.inspect}" unless got == exp
      pass += 1
    end
    puts "SCORE #{pass}/#{cases.size}"
  RUBY
  score_cases(code, harness)
end

def grade_fix_vm(text)
  code = extract_ruby(text)
  harness = <<~'RUBY'
    raise 'missing' unless defined?(run_vm)
    cases = [
      # 5 - 3 = 2  (catches reversed SUB operands)
      [[["IN"], ["IN"], ["SUB"], ["HALT"]], [5, 3], 2],
      # 2 * (7 - 4) via stack discipline
      [[["PUSH", 2], ["PUSH", 7], ["PUSH", 4], ["SUB"], ["MUL"], ["HALT"]], [], 6],
      # DUP then ADD: x + x
      [[["IN"], ["DUP"], ["ADD"], ["HALT"]], [9], 18],
      # SWAP then SUB: inputs a,b -> b-a after swap? IN a, IN b, SWAP -> b,a on stack top a; SUB pops b'=a,a'=b -> b-a
      [[["IN"], ["IN"], ["SWAP"], ["SUB"], ["HALT"]], [3, 10], 7],
      # JZ skip PUSH 99 when top is 0
      [[["PUSH", 0], ["JZ", 1], ["PUSH", 99], ["PUSH", 7], ["HALT"]], [], 7],
      # JZ do not skip when nonzero
      [[["PUSH", 1], ["JZ", 1], ["PUSH", 99], ["HALT"]], [], 99],
      # JMP skip
      [[["JMP", 1], ["PUSH", 1], ["PUSH", 2], ["HALT"]], [], 2],
      # accumulate until 0 sentinel; SWAP drops sentinel under HALT
      [[
        ["PUSH", 0],
        ["IN"],
        ["DUP"],
        ["JZ", 2],
        ["ADD"],
        ["JMP", -5],
        ["SWAP"],
        ["HALT"],
      ], [3, 4, 5, 0], 12],
      # empty halt
      [[["HALT"]], [], 0],
      # ADD after two pushes
      [[["PUSH", 40], ["PUSH", 2], ["ADD"], ["HALT"]], [], 42],
    ]
    pass = 0
    cases.each_with_index do |(prog, inputs, exp), i|
      got = run_vm(prog.map { |op| op.map(&:itself) }, inputs.dup)
      raise "case #{i}: got=#{got.inspect} want=#{exp.inspect}" unless got == exp
      pass += 1
    end
    puts "SCORE #{pass}/#{cases.size}"
  RUBY
  score_cases(code, harness)
end

def grade(task, text)
  case task[:grade]
  when :regex_match then grade_regex_match(text)
  when :lru_cache then grade_lru_cache(text)
  when :alien_order then grade_alien_order(text)
  when :eval_expr then grade_eval_expr(text)
  when :fix_vm then grade_fix_vm(text)
  else { ok: false, score: 0, max_score: task[:max_score], detail: 'unknown', code: '' }
  end
end

def log!(path, s)
  print s
  File.open(path, 'a') { |f| f.write(s) }
end

# --- main --------------------------------------------------------------------

if SELFTEST
  puts "Self-test hard bench graders @ #{Time.now}"
  all_ok = true
  TASKS.each do |task|
    g = grade(task, "```ruby\n#{REF[task[:id]]}\n```")
    status = g[:ok] ? 'PASS' : 'FAIL'
    puts "#{status} #{task[:id]} #{g[:score]}/#{g[:max_score].nonzero? || task[:max_score]} — #{g[:detail].to_s.lines.first}"
    all_ok &&= g[:ok]
    puts g[:detail] unless g[:ok]
  end

  # Ensure buggy prompt code actually fails
  buggy = <<~'RUBY'
    def run_vm(code, inputs)
      ip = 0
      stack = []
      in_i = 0
      while ip < code.length
        op, *args = code[ip]
        ip += 1
        case op
        when "IN"
          stack << inputs[in_i]
        when "PUSH"
          stack << args[0]
        when "ADD"
          a = stack.pop
          b = stack.pop
          stack << (a + b)
        when "SUB"
          a = stack.pop
          b = stack.pop
          stack << (a - b)
        when "MUL"
          a = stack.pop
          b = stack.pop
          stack << (a * b)
        when "DUP"
          stack << stack[-1]
        when "SWAP"
          stack[-1], stack[-2] = stack[-2], stack[-1]
        when "JZ"
          v = stack.pop
          ip += args[0] if v != 0
        when "JMP"
          ip += args[0]
        when "HALT"
          return stack[-1] || 0
        else
          raise "unknown op #{op}"
        end
      end
      stack[-1] || 0
    end
  RUBY
  bg = grade_fix_vm("```ruby\n#{buggy}\n```")
  puts "buggy VM should fail: score=#{bg[:score]} ok=#{bg[:ok]} (want ok=false)"
  all_ok &&= !bg[:ok]
  abort 'SELFTEST FAILED' unless all_ok
  puts 'SELFTEST OK'
  exit 0
end

log_path = File.join(OUT_DIR, "#{TAG}_hard.log")
summary_path = File.join(OUT_DIR, "#{TAG}_hard_#{Time.now.strftime('%Y%m%d_%H%M%S')}.json")
File.write(log_path, '')
log!(log_path, "Hard bench #{Time.now}\nmodel=#{MODEL}\noptions=#{OPTIONS.inspect}\n")

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
    max = g[:max_score].nonzero? || task[:max_score]
    row = {
      model: MODEL,
      task: task[:id],
      title: task[:title],
      ok: g[:ok],
      score: g[:score],
      max_score: max,
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
    row = { model: MODEL, task: task[:id], ok: false, score: 0, max_score: task[:max_score], error: e.message }
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
log!(log_path, format("\n===== HARD #{MODEL} =====\npass %d/%d  score %d/%d  avg tok/s %.1f\nWrote %s\n",
                      passed, results.size, total, max, avg_tps, summary_path))
File.write(File.join(OUT_DIR, "#{TAG}_hard_latest.json"), JSON.pretty_generate(results))
