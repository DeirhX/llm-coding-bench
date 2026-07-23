from __future__ import annotations
from store import db

def apply() -> None:
    db.put("schema_meta", "001_init", {"applied": True})
