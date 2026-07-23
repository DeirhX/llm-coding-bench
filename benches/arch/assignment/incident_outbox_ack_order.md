---
id: incident_outbox_ack_order
title: Incident: lost outbox events
family: incident
max_score: 10
required_files: [worker/outbox_worker.py]
---

Task: Symptom: an outbox event can disappear if the publisher crashes mid-batch.
Find the ordering bug.

Return JSON:
{
  "root_cause": "brief",
  "fix_function": "module.func",
  "citations": ["path.py:symbol", ...]
}
