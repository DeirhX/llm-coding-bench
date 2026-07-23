---
id: chain_delete_order
title: Call chain: DELETE /orders/{id}
family: call_chain
max_score: 10
required_files: [api/orders.py,service/order_service.py]
---

Task: Trace request handling for DELETE /orders/{id}.

Return JSON:
{
  "chain": ["module.func", ... in call order from HTTP handler to durable effects],
  "side_effects": ["short strings"],
  "citations": ["path.py:symbol", ...]
}
Include service, repo/outbox/cache steps that actually run on success.
Mention auth/middleware only if it appears on that success path.
