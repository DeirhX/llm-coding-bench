#!/usr/bin/env ruby
# frozen_string_literal: true

require 'tmpdir'
require 'timeout'
require 'json'

OUT = File.expand_path('~/.ollama/bench/results')

def run_with_timeout(code, harness, timeout_s: 2)
  Dir.mktmpdir do |dir|
    path = File.join(dir, 't.rb')
    out_path = File.join(dir, 'out.txt')
    File.write(path, code + "\n" + harness)
    pid = spawn("ruby #{path.inspect}", out: out_path, err: out_path)
    deadline = Time.now + timeout_s
    while Time.now < deadline
      broken = Process.wait(pid, Process::WNOHANG)
      if broken
        return { out: File.read(out_path), status: $?.exitstatus, timed_out: false }
      end
      sleep 0.05
    end
    Process.kill('KILL', pid) rescue nil
    Process.wait(pid) rescue nil
    { out: "TIMEOUT after #{timeout_s}s\n#{File.read(out_path) rescue ''}", status: -1, timed_out: true }
  end
end

def harness_for(task)
  File.read(File.expand_path('~/.ollama/bench/hard_bench.rb')).then do |src|
    m = src.match(/def grade_#{task}\(text\).*?harness = <<~'RUBY'\n(.*?)  RUBY\n  score_cases/m)
    raise "no harness #{task}" unless m
    m[1]
  end
end

# Recover 30B fix_vm from hung tmp if needed
tmp = Dir[File.join(OUT, 'tmp_*_*.rb')].max_by { |p| File.mtime(p) }
if tmp && File.read(tmp).include?('def run_vm') && !File.exist?(File.join(OUT, 'qwen3-coder_30b-a3b-fp16_hard__fix_vm__code.rb'))
  code = File.read(tmp)[/\A[\s\S]*?(?=raise 'missing')/]
  File.write(File.join(OUT, 'qwen3-coder_30b-a3b-fp16_hard__fix_vm__code.rb'), code)
  puts "Recovered 30B fix_vm from #{tmp} (#{code.bytesize} bytes)"
end

models = {
  'Qwen3-Coder-Next Q8' => 'qwen3-coder-next_q8_0_hard',
  'Qwen3-Coder 30B-A3B FP16' => 'qwen3-coder_30b-a3b-fp16_hard',
}
tasks = %w[regex_match lru_cache alien_order eval_expr fix_vm]
harnesses = tasks.to_h { |t| [t, harness_for(t)] }

models.each do |name, prefix|
  puts "\n## #{name}"
  total = 0
  tmax = 0
  tasks.each do |task|
    path = File.join(OUT, "#{prefix}__#{task}__code.rb")
    unless File.exist?(path)
      puts "- #{task}: NO CODE"
      next
    end
    code = File.read(path)
    res = run_with_timeout(code, harnesses[task], timeout_s: 2)
    if res[:timed_out]
      puts "- #{task}: TIMEOUT (likely infinite loop) code=#{code.bytesize}B"
      tmax += { 'regex_match' => 12, 'lru_cache' => 14, 'alien_order' => 10, 'eval_expr' => 12, 'fix_vm' => 10 }[task]
      next
    end
    if res[:out] =~ /SCORE (\d+)\/(\d+)/
      score = Regexp.last_match(1).to_i
      max = Regexp.last_match(2).to_i
      total += score
      tmax += max
      puts "- #{task}: #{score}/#{max}#{score == max ? '' : ' FAIL'} (#{code.bytesize}B)"
    else
      tmax += 10
      puts "- #{task}: ERROR #{res[:out].lines.first.to_s.strip[0, 100]}"
    end
  end
  puts "TOTAL audited: #{total}/#{tmax}"
end

# Specific bug notes for 30B VM
path = File.join(OUT, 'qwen3-coder_30b-a3b-fp16_hard__fix_vm__code.rb')
if File.exist?(path)
  code = File.read(path)
  puts "\n## 30B fix_vm defect analysis"
  puts "- has leading ip+=1: #{!!(code =~ /ip \+= 1/ && code =~ /op, \*args = code\[ip\]\n\s*ip \+= 1/)}"
  puts "- advances ip only inside JZ branches / JMP: #{code.include?('ip += 1') && !code.match?(/op, \*args = code\[ip\]\n\s*ip \+= 1/)}"
  puts "- fixed IN increment: #{code.include?('in_i += 1')}"
  puts "- fixed SUB pop order (b then a): #{code.match?(/when \"SUB\"\n\s*b = stack\.pop\n\s*a = stack\.pop/)}"
  puts "- JZ uses == 0: #{code.include?('if v == 0') || code.include?('v == 0')}"
end
