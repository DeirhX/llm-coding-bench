#!/usr/bin/env python3
"""Anthropic Messages API in front, OpenAI chat completions behind, and a log that names the caller.

Two things this buys, both of which the notes in `LOCAL_AGENT_OPS.md` say are missing:

* **A llama.cpp path for Claude Code.** `llama-server` speaks OpenAI and nothing else; Claude Code
  speaks Anthropic and nothing else. Everything measured on the llama.cpp side -- `--swa-full` KV
  snapshots, `ngram-mod` speculation, `--slot-save-path` restores -- is unreachable from the client
  until something translates. Ollama's own Anthropic endpoint is why the MLX runner is the only
  backend that has ever been driven from Claude Code here.
* **Per-client attribution, which no log has ever had.** Ollama logs a request when it *completes*
  and never records who sent it, so "which agent evicted the cache" has only ever been answered by
  reconstruction after the fact. A proxy sees the request as it arrives, with its `user-agent` and
  its first tokens, and can write that down before the runner has done anything.

Deliberately stdlib-only and single-file: it has to be startable next to a benchmark run without a
virtualenv, and its failure modes have to be readable in one sitting.

Translation notes, which is where the bodies are buried:

* Anthropic puts `system` beside `messages`; OpenAI puts it first *in* them. A system given as
  content blocks is joined, because the ordering of blocks is the prompt head and prefix caching
  makes byte-identical heads worth real money (§8).
* A `tool_use` block becomes an assistant `tool_calls` entry with the arguments re-serialised as a
  JSON *string*; a `tool_result` block becomes a `role: "tool"` message keyed by `tool_call_id`.
  Anthropic allows several results in one user turn, OpenAI wants one message each, so they are
  split -- getting this wrong looks like a model that ignores tool output.
* Streaming is not optional. Claude Code sets `stream: true` and reads Anthropic's event grammar:
  `message_start`, then per block `content_block_start` / `content_block_delta` /
  `content_block_stop`, then `message_delta` carrying `stop_reason`, then `message_stop`. Tool calls
  arrive as `input_json_delta` fragments that must be re-emitted in order, since the client
  concatenates them blindly.
* `stop_reason` has to be mapped or the client will not run the tool: OpenAI's `tool_calls` finish
  reason is Anthropic's `tool_use`, and `length` is `max_tokens`.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Iterator

DEFAULT_UPSTREAM = "http://127.0.0.1:8080/v1/chat/completions"
# `length` is deliberately not mapped to `max_tokens`. Handed that, Claude Code ends the session
# with "Claude's response exceeded the 32000 output token maximum" and there is no way back: the
# flow it was in the middle of is simply over. A cut answer is a turn like any other, so it is
# reported as one, with a note in place of the truncated tail.
STOP_REASON = {"stop": "end_turn", "length": "end_turn", "tool_calls": "tool_use",
               "function_call": "tool_use", "content_filter": "end_turn"}

CUT_NOTE = ("\n\n[This answer was cut off at %d tokens by the proxy, so what is above is "
            "incomplete and any tool call it was about to make was dropped. Do not repeat it from "
            "the start: write the short version now -- for a ledger, the CLAIM/EVIDENCE/QUOTE "
            "blocks and nothing else.]")


# --- request translation ---------------------------------------------------------------------

def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


# The most a single answer may be. The client asks for 32,000 and a stage took 24,847 of them in
# one response at 334 t/s -- three times this model's measured rate, which is what near-total
# speculative acceptance looks like when the output has gone round in a circle. Nothing else bounds
# it: the parent was blocked on the report, the gate only sees answers that finish, and the GPU was
# busy the whole time. A ledger that needs more than this is not a ledger.
# Raised from 8,192 after two live runs were cut mid-ledger: this model writes its reasoning into
# the same budget, and a ten-claim ledger with quotes ran past it twice. Still far under the client's
# 32,000, which is the number that matters -- a cut answer is recoverable, but only because the cut
# is reported as an ordinary turn rather than as the max_tokens stop that ends a session.
MAX_OUTPUT = 16384


def to_openai(body: dict, ceiling: int = 0) -> dict:
    """An Anthropic Messages request as OpenAI chat completions."""
    messages: list[dict] = []
    system = _text_of(body.get("system"))
    if system:
        messages.append({"role": "system", "content": system})

    for message in body.get("messages", []):
        role = message.get("role", "user")
        content = message.get("content")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        results: list[dict] = []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                text_parts.append(block.get("text", ""))
            elif kind == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""), "type": "function",
                    "function": {"name": block.get("name", ""),
                                 "arguments": json.dumps(block.get("input") or {})},
                })
            elif kind == "tool_result":
                # One OpenAI message per result: a turn carrying two results is legal Anthropic and
                # illegal OpenAI, and merging them silently attributes output to the wrong call.
                results.append({"role": "tool", "tool_call_id": block.get("tool_use_id", ""),
                                "content": _text_of(block.get("content")) or ""})
            elif kind == "image":
                text_parts.append("[image omitted: this proxy is text-only]")

        if role == "user" and results:
            messages += results
            if any(t.strip() for t in text_parts):
                messages.append({"role": "user", "content": "\n".join(text_parts)})
            continue
        entry: dict[str, Any] = {"role": role, "content": "\n".join(text_parts)}
        if tool_calls:
            entry["tool_calls"] = tool_calls
            entry["content"] = entry["content"] or None
        messages.append(entry)

    out: dict[str, Any] = {
        "model": body.get("model", ""),
        "messages": messages,
        "stream": bool(body.get("stream")),
    }
    for src, dst in (("max_tokens", "max_tokens"), ("temperature", "temperature"),
                     ("top_p", "top_p"), ("stop_sequences", "stop")):
        if body.get(src) is not None:
            out[dst] = body[src]
    limit = ceiling or MAX_OUTPUT
    if limit:
        asked = out.get("max_tokens")
        out["max_tokens"] = min(int(asked), limit) if isinstance(asked, int) else limit
    if body.get("tools"):
        out["tools"] = [{"type": "function",
                         "function": {"name": t.get("name", ""),
                                      "description": t.get("description", ""),
                                      "parameters": t.get("input_schema") or {"type": "object"}}}
                        for t in body["tools"]]
    choice = body.get("tool_choice") or {}
    if choice.get("type") == "any":
        out["tool_choice"] = "required"
    elif choice.get("type") == "tool" and choice.get("name"):
        out["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
    elif choice.get("type") == "none":
        out["tool_choice"] = "none"
    if body.get("stream"):
        out["stream_options"] = {"include_usage": True}
    return out


# --- response translation --------------------------------------------------------------------

def usage_of(reply: dict) -> dict:
    return reply.get("usage") or {}


def to_anthropic(reply: dict, model: str) -> dict:
    """A non-streamed OpenAI completion as an Anthropic message."""
    choice = (reply.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks: list[dict] = []
    # Ollama returns a reasoning model's thinking in `reasoning`, leaving `content` empty -- so a
    # translation that reads only `content` hands the client a blank answer and looks like a dead
    # model. Anthropic's own shape for this is a `thinking` block, which is what Ollama's native
    # endpoint emits, so mirror it rather than inventing a convention.
    thinking = message.get("reasoning") or message.get("reasoning_content")
    if thinking:
        blocks.append({"type": "thinking", "thinking": thinking})
    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})
    cut = (choice.get("finish_reason") or "") == "length"
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            # Unparseable because the answer was cut: a call in half is not a call. Passed through,
            # the client answers "ReportFindings was called with input that could not be parsed as
            # JSON" and the model sends the same oversized call again. Unparseable on a response
            # that finished normally is a fact about the model and is still surfaced.
            if cut:
                continue
            args = {"_unparsed": fn.get("arguments", "")}
        blocks.append({"type": "tool_use", "id": call.get("id") or "toolu_%s" % uuid.uuid4().hex[:16],
                       "name": fn.get("name", ""), "input": args})
    if cut:
        blocks.append({"type": "text",
                       "text": CUT_NOTE % (usage_of(reply).get("completion_tokens", 0))})
    usage = reply.get("usage") or {}
    return {
        "id": reply.get("id") or "msg_%s" % uuid.uuid4().hex[:24],
        "type": "message", "role": "assistant", "model": model,
        "content": blocks or [{"type": "text", "text": ""}],
        "stop_reason": STOP_REASON.get(choice.get("finish_reason") or "stop", "end_turn"),
        "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                  "output_tokens": usage.get("completion_tokens", 0)},
    }


def _sse(event: str, data: dict) -> bytes:
    return ("event: %s\ndata: %s\n\n" % (event, json.dumps(data))).encode()


def stream_anthropic(chunks: Iterator[dict], model: str) -> Iterator[bytes]:
    """Re-emit an OpenAI delta stream in Anthropic's event grammar.

    The client concatenates `input_json_delta` fragments without re-parsing, so a tool call's
    arguments must go out in arrival order and its block must be closed before the next opens.
    """
    message_id = "msg_%s" % uuid.uuid4().hex[:24]
    yield _sse("message_start", {"type": "message_start", "message": {
        "id": message_id, "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0}}})

    index = -1
    open_kind: str | None = None
    tool_slot: dict[int, int] = {}          # upstream tool index -> our block index
    pending: dict[int, dict] = {}           # upstream tool index -> the call being assembled
    finish, usage = "stop", {}

    def close() -> Iterator[bytes]:
        nonlocal open_kind
        if open_kind is not None:
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": index})
            open_kind = None

    for chunk in chunks:
        usage = chunk.get("usage") or usage
        choice = (chunk.get("choices") or [{}])[0]
        finish = choice.get("finish_reason") or finish
        delta = choice.get("delta") or {}

        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
        if reasoning:
            if open_kind != "thinking":
                yield from close()
                index += 1
                open_kind = "thinking"
                yield _sse("content_block_start", {"type": "content_block_start", "index": index,
                                                   "content_block": {"type": "thinking",
                                                                     "thinking": ""}})
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": index,
                                               "delta": {"type": "thinking_delta",
                                                         "thinking": reasoning}})

        if delta.get("content"):
            if open_kind != "text":
                yield from close()
                index += 1
                open_kind = "text"
                yield _sse("content_block_start", {"type": "content_block_start", "index": index,
                                                   "content_block": {"type": "text", "text": ""}})
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": index,
                                               "delta": {"type": "text_delta",
                                                         "text": delta["content"]}})

        for call in delta.get("tool_calls") or []:
            slot = call.get("index", 0)
            fn = call.get("function") or {}
            held = pending.setdefault(slot, {"id": "", "name": "", "args": ""})
            held["id"] = held["id"] or call.get("id") or "toolu_%s" % uuid.uuid4().hex[:16]
            held["name"] = held["name"] or fn.get("name", "")
            held["args"] += fn.get("arguments") or ""

    yield from close()

    # Held back rather than streamed, so that a call the answer was cut in the middle of can be
    # dropped instead of arriving as half a JSON object. Streamed straight through, one became
    # `ReportFindings was called with input that could not be parsed as JSON` and the model sent the
    # same oversized call again. The client concatenates argument fragments without parsing them, so
    # one delta carrying the whole string is as good as many carrying pieces.
    dropped = False
    for slot in sorted(pending):
        held = pending[slot]
        try:
            json.loads(held["args"] or "{}")
        except ValueError:
            dropped = True
            continue
        index += 1
        tool_slot[slot] = index
        yield _sse("content_block_start", {
            "type": "content_block_start", "index": index,
            "content_block": {"type": "tool_use", "id": held["id"],
                              "name": held["name"], "input": {}}})
        if held["args"]:
            yield _sse("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "input_json_delta", "partial_json": held["args"]}})
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": index})

    if finish == "length" or dropped:
        index += 1
        yield _sse("content_block_start", {"type": "content_block_start", "index": index,
                                           "content_block": {"type": "text", "text": ""}})
        yield _sse("content_block_delta", {
            "type": "content_block_delta", "index": index,
            "delta": {"type": "text_delta",
                      "text": CUT_NOTE % usage.get("completion_tokens", 0)}})
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": index})

    settled = STOP_REASON.get(finish, "end_turn")
    if tool_slot and settled == "end_turn" and not dropped:
        settled = "tool_use"        # a complete call still has to be run, cut answer or not
    yield _sse("message_delta", {"type": "message_delta",
                                 "delta": {"stop_reason": settled, "stop_sequence": None},
                                 "usage": {"output_tokens": usage.get("completion_tokens", 0)}})
    yield _sse("message_stop", {"type": "message_stop"})


def iter_openai_sse(stream) -> Iterator[dict]:
    for raw in stream:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except ValueError:
            continue


# --- server ----------------------------------------------------------------------------------

class Proxy(http.server.BaseHTTPRequestHandler):
    upstream = DEFAULT_UPSTREAM
    force_model = ""     # rewrite every request to this model, whatever the client asked for
    ceiling = 0          # and cap what a single answer may cost, whatever the client asked for
    logfile: Any = None
    lock = threading.Lock()
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:      # the access log goes to our own JSONL instead
        pass

    def note(self, **fields) -> None:
        if not self.logfile:
            return
        fields["t"] = time.time()
        with self.lock:
            self.logfile.write(json.dumps(fields) + "\n")
            self.logfile.flush()

    def do_GET(self) -> None:                   # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            # An empty list here is not "no opinion", it is "no such model": the client refuses to
            # start with `the selected model may not exist`, which reads like a configuration
            # problem miles from the actual cause. Advertising the model we force every request to
            # is both truthful and the only answer that lets a session begin.
            named = self.force_model or "local"
            self._json(200, {"object": "list", "has_more": False,
                             "data": [{"type": "model", "id": named, "display_name": named,
                                       "created_at": "2020-01-01T00:00:00Z"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:                  # noqa: N802
        if "/messages" not in self.path:
            self._json(404, {"type": "error", "error": {"type": "not_found_error",
                                                        "message": self.path}})
            return
        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as exc:
            self._json(400, {"type": "error", "error": {"type": "invalid_request_error",
                                                        "message": str(exc)}})
            return

        started = time.time()
        # Claude Code does not only send the model you configured: this request arrived asking for
        # `claude-opus-4-8`, a name no local runner has. On Ollama a name it *does* have but is not
        # the resident variant is worse than an error -- `-64k`, `-96k` and `-128k` are separate
        # models, and naming the wrong one evicts a live session's weights and its prefix cache.
        # So the port, not the client, decides which model answers.
        if self.force_model and body.get("model") != self.force_model:
            self.note(event="model_override", asked=body.get("model"), used=self.force_model)
            body["model"] = self.force_model
        # Written before the upstream is touched: the whole point is knowing who caused a reload,
        # and a record made on completion cannot establish that.
        self.note(event="request", model=body.get("model"), stream=bool(body.get("stream")),
                  agent=self.headers.get("user-agent", ""),
                  messages=len(body.get("messages") or []),
                  system_chars=len(_text_of(body.get("system"))),
                  tools=len(body.get("tools") or []))

        request = urllib.request.Request(
            self.upstream, data=json.dumps(to_openai(body, self.ceiling)).encode(),
            headers={"content-type": "application/json",
                     "authorization": self.headers.get("authorization", "Bearer local")})
        try:
            reply = urllib.request.urlopen(request, timeout=1800)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            self.note(event="upstream_error", status=exc.code, detail=detail)
            self._json(502, {"type": "error", "error": {"type": "api_error", "message": detail}})
            return
        except OSError as exc:
            self.note(event="upstream_unreachable", detail=str(exc))
            self._json(502, {"type": "error", "error": {"type": "api_error", "message": str(exc)}})
            return

        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.send_header("connection", "close")
            self.end_headers()
            sent = 0
            try:
                for piece in stream_anthropic(iter_openai_sse(reply), body.get("model", "")):
                    self.wfile.write(piece)
                    sent += len(piece)
            except (BrokenPipeError, ConnectionResetError):
                self.note(event="client_gone", ms=int((time.time() - started) * 1000))
                return
            self.note(event="done", stream=True, bytes=sent,
                      ms=int((time.time() - started) * 1000))
            self.close_connection = True
            return

        payload = json.loads(reply.read().decode("utf-8", "replace"))
        out = to_anthropic(payload, body.get("model", ""))
        self.note(event="done", stream=False, ms=int((time.time() - started) * 1000),
                  input_tokens=out["usage"]["input_tokens"],
                  output_tokens=out["usage"]["output_tokens"])
        self._json(200, out)

    def _json(self, status: int, payload: dict) -> None:
        blob = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)


class Threaded(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port: int, upstream: str, log_path: str, force_model: str = "",
          ceiling: int = MAX_OUTPUT) -> None:
    Proxy.upstream = upstream
    Proxy.force_model = force_model
    Proxy.ceiling = ceiling
    Proxy.logfile = open(os.path.expanduser(log_path), "a", encoding="utf-8") if log_path else None
    try:
        server = Threaded(("127.0.0.1", port), Proxy)
    except OSError as exc:
        # Worth its own exit path: started detached, a bind failure is invisible, and the request
        # then reaches whatever else holds the port. That happened here three times -- 11434, 11435
        # and 11436 are all taken by earlier probes -- and each time the reply looked like ours.
        raise SystemExit("cannot listen on 127.0.0.1:%d (%s). Something else holds it: "
                         "lsof -nP -iTCP:%d -sTCP:LISTEN" % (port, exc, port))
    print("anthropic -> openai on 127.0.0.1:%d, upstream %s%s"
          % (port, upstream, ", forcing model %s" % force_model if force_model else ""), flush=True)
    server.serve_forever()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=11435)
    ap.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    ap.add_argument("--log", default="/tmp/anthropic-proxy.jsonl")
    ap.add_argument("--max-output", type=int, default=MAX_OUTPUT,
                    help="cap on tokens in one answer; 0 to pass the client's own limit through")
    ap.add_argument("--force-model", default="",
                    help="answer every request with this model, whatever the client asked for")
    args = ap.parse_args()
    try:
        serve(args.port, args.upstream, args.log, args.force_model, args.max_output)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
