from __future__ import annotations
from store import db

def ok() -> dict:
    return {"db": True, "tables": len(db._TABLES)}
