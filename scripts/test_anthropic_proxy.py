#!/usr/bin/env python3
"""Offline checks for the translating proxy: a fake OpenAI upstream, no model, no network.

The translation is where this can go wrong quietly. A dropped `tool_call_id` looks like a model
ignoring tool output; a mis-mapped `finish_reason` looks like a model that will not call tools; a
tool block left open in the stream looks like truncated arguments. All three are cheap to assert and
impossible to notice by reading the code.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic_proxy as ap  # noqa: E402


class FakeUpstream(BaseHTTPRequestHandler):
    """Records what it was sent and replies with whatever the test parked in `script`."""

    seen: list[dict] = []
    script: dict = {}
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeUpstream.seen.append(body)
        if self.script.get("sse"):
            blob = "".join("data: %s\n\n" % json.dumps(c) for c in self.script["sse"])
            blob += "data: [DONE]\n\n"
            payload = blob.encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
        else:
            payload = json.dumps(self.script["json"]).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _serve(handler, port: int):
    server = HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stack(port_up: int, port_proxy: int):
    up = _serve(FakeUpstream, port_up)
    ap.Proxy.upstream = "http://127.0.0.1:%d/v1/chat/completions" % port_up
    ap.Proxy.logfile = None
    proxy = ap.Threaded(("127.0.0.1", port_proxy), ap.Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    time.sleep(0.1)
    return up, proxy


def _post(port: int, body: dict, raw: bool = False):
    req = urllib.request.Request("http://127.0.0.1:%d/v1/messages" % port,
                                 data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as reply:
        data = reply.read()
    return data.decode() if raw else json.loads(data)


def test_tool_result_becomes_its_own_tool_message() -> None:
    """Anthropic packs several results into one user turn; OpenAI wants one message each."""
    out = ap.to_openai({"model": "m", "messages": [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.py"}},
            {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "b.py"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "AAA"},
            {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "BBB"}]},
            {"type": "text", "text": "now compare them"}]},
    ]})
    roles = [m["role"] for m in out["messages"]]
    assert roles == ["assistant", "tool", "tool", "user"], roles
    assert [m["tool_call_id"] for m in out["messages"][1:3]] == ["t1", "t2"]
    assert out["messages"][2]["content"] == "BBB"
    calls = out["messages"][0]["tool_calls"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"file_path": "a.py"}


def test_system_blocks_are_joined_in_order() -> None:
    """The head is the cached prefix; reordering or dropping a block costs a full re-prefill."""
    out = ap.to_openai({"model": "m", "system": [{"type": "text", "text": "one"},
                                                 {"type": "text", "text": "two"}],
                        "messages": [{"role": "user", "content": "hi"}]})
    assert out["messages"][0] == {"role": "system", "content": "one\ntwo"}


def test_tools_and_tool_choice_translate() -> None:
    out = ap.to_openai({"model": "m", "messages": [], "tool_choice": {"type": "any"}, "tools": [
        {"name": "Read", "description": "read a file",
         "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}}}]})
    assert out["tool_choice"] == "required"
    assert out["tools"][0]["function"]["parameters"]["properties"]["file_path"]["type"] == "string"


def test_finish_reason_tool_calls_becomes_tool_use() -> None:
    """Anthropic clients only run a tool when `stop_reason` says `tool_use`."""
    msg = ap.to_anthropic({"choices": [{"finish_reason": "tool_calls", "message": {
        "content": None, "tool_calls": [{"id": "c1", "function": {
            "name": "Read", "arguments": '{"file_path": "x.py"}'}}]}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3}}, "m")
    assert msg["stop_reason"] == "tool_use"
    assert msg["content"][0] == {"type": "tool_use", "id": "c1", "name": "Read",
                                 "input": {"file_path": "x.py"}}
    assert msg["usage"] == {"input_tokens": 11, "output_tokens": 3}


def test_unparseable_tool_arguments_are_kept_not_dropped() -> None:
    """A truncated argument string is a fact worth surfacing, not a reason to invent {}."""
    msg = ap.to_anthropic({"choices": [{"finish_reason": "tool_calls", "message": {
        "tool_calls": [{"id": "c1", "function": {"name": "Read", "arguments": '{"file_pa'}}]}}]}, "m")
    assert msg["content"][0]["input"]["_unparsed"] == '{"file_pa'


def test_stream_grammar_opens_and_closes_every_block() -> None:
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "Read", "arguments": '{"fi'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'le":"a.py"}'}}]}, "finish_reason": "tool_calls"}]},
    ]
    events = [e.decode() for e in ap.stream_anthropic(iter(chunks), "m")]
    kinds = [line.split(": ", 1)[1] for e in events for line in e.split("\n")
             if line.startswith("event: ")]
    assert kinds == ["message_start", "content_block_start", "content_block_delta",
                     "content_block_delta", "content_block_stop", "content_block_start",
                     "content_block_delta", "content_block_delta", "content_block_stop",
                     "message_delta", "message_stop"], kinds
    fragments = [json.loads(line[6:])["delta"]["partial_json"]
                 for e in events for line in e.split("\n")
                 if line.startswith("data: ") and '"input_json_delta"' in line]
    assert "".join(fragments) == '{"file":"a.py"}'
    tail = json.loads([line[6:] for e in events for line in e.split("\n")
                       if line.startswith("data: ") and '"message_delta"' in line][0])
    assert tail["delta"]["stop_reason"] == "tool_use"


def test_end_to_end_through_a_socket() -> None:
    """The handler itself, not just the pure functions: headers, framing and error mapping."""
    _stack(18081, 18082)
    FakeUpstream.script = {"json": {"id": "cmpl-1", "choices": [
        {"finish_reason": "stop", "message": {"content": "42"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1}}}
    out = _post(18082, {"model": "m", "messages": [{"role": "user", "content": "the answer?"}],
                        "max_tokens": 16})
    assert out["content"] == [{"type": "text", "text": "42"}]
    assert out["stop_reason"] == "end_turn" and out["type"] == "message"
    assert FakeUpstream.seen[-1]["max_tokens"] == 16

    FakeUpstream.script = {"sse": [{"choices": [{"delta": {"content": "hi"}}]},
                                   {"choices": [{"delta": {}, "finish_reason": "stop"}]}]}
    raw = _post(18082, {"model": "m", "stream": True,
                        "messages": [{"role": "user", "content": "hi"}]}, raw=True)
    assert raw.startswith("event: message_start")
    assert raw.rstrip().endswith('data: {"type": "message_stop"}')
    assert FakeUpstream.seen[-1]["stream"] is True


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok   %s" % t.__name__)
    print("%d passed" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def test_reasoning_becomes_a_thinking_block() -> None:
    """Ollama puts a reasoning model's output in `reasoning` and leaves `content` empty.

    Reading only `content` yields a blank answer, which is indistinguishable from a dead model --
    and is exactly what the first live smoke test produced.
    """
    msg = ap.to_anthropic({"choices": [{"finish_reason": "length", "message": {
        "content": "", "reasoning": "The user said banana."}}]}, "m")
    assert msg["content"] == [{"type": "thinking", "thinking": "The user said banana."}]
    assert msg["stop_reason"] == "max_tokens"


def test_thinking_then_text_are_separate_streamed_blocks() -> None:
    chunks = [{"choices": [{"delta": {"reasoning": "weighing it up"}}]},
              {"choices": [{"delta": {"content": "banana"}}]},
              {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
    events = [e.decode() for e in ap.stream_anthropic(iter(chunks), "m")]
    starts = [json.loads(line[6:])["content_block"]["type"]
              for e in events for line in e.split("\n")
              if line.startswith("data: ") and '"content_block_start"' in line]
    assert starts == ["thinking", "text"], starts


def test_force_model_rewrites_whatever_the_client_asked_for() -> None:
    """A client's model name must never reach Ollama unchecked.

    Claude Code sent `claude-opus-4-8` unprompted during the first live run. A name the server does
    not have is a 404; a name it *does* have but is not the resident variant unloads 62 GB of
    weights and the prefix cache with them.
    """
    _stack(18083, 18084)
    ap.Proxy.force_model = "resident-31b"
    try:
        FakeUpstream.script = {"json": {"choices": [{"finish_reason": "stop",
                                                     "message": {"content": "ok"}}]}}
        _post(18084, {"model": "claude-opus-4-8", "messages": [{"role": "user", "content": "hi"}]})
        assert FakeUpstream.seen[-1]["model"] == "resident-31b"
    finally:
        ap.Proxy.force_model = ""


def test_the_model_list_names_the_model_we_force() -> None:
    """An empty list reads to the client as "no such model", and it refuses to start.

    The failure it prints -- the selected model may not exist or you may not have access to it --
    points at configuration rather than at an endpoint answering GET /v1/models with nothing.
    """
    _stack(18085, 18086)
    ap.Proxy.force_model = "qwopus"
    try:
        with urllib.request.urlopen("http://127.0.0.1:18086/v1/models", timeout=10) as fh:
            body = json.loads(fh.read())
    finally:
        ap.Proxy.force_model = ""
    assert [m["id"] for m in body["data"]] == ["qwopus"], body


def test_one_answer_is_bounded() -> None:
    """A stage took 24,847 tokens in a single response at 334 t/s -- three times this model's
    measured rate, which is what near-total speculative acceptance looks like when the output has
    gone round in a circle. The parent was blocked on the report and the GPU was busy throughout.
    """
    asked = {"model": "m", "max_tokens": 32000, "messages": [{"role": "user", "content": "hi"}]}
    assert ap.to_openai(asked)["max_tokens"] == ap.MAX_OUTPUT
    assert ap.to_openai(asked, 512)["max_tokens"] == 512


def test_a_modest_request_is_left_alone() -> None:
    asked = {"model": "m", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]}
    assert ap.to_openai(asked)["max_tokens"] == 100


def test_a_client_that_names_no_limit_still_gets_one() -> None:
    asked = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert ap.to_openai(asked)["max_tokens"] == ap.MAX_OUTPUT
