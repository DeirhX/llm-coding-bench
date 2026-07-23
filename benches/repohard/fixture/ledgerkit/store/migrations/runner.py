from __future__ import annotations
from store import db
from store.migrations.registry import MIGRATIONS

def run_all() -> list[str]:
    done = []
    for name, fn in MIGRATIONS:
        if db.get("schema_meta", name):
            continue
        fn()
        done.append(name)
    return done
