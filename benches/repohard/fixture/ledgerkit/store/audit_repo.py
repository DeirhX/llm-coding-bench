from __future__ import annotations
from typing import Any
import uuid
from store import db

def write(tenant_id: str, action: str, detail: dict[str, Any]) -> str:
    eid = str(uuid.uuid4())
    db.put("audit", eid, {"id": eid, "tenant_id": tenant_id, "action": action, "detail": detail})
    return eid
