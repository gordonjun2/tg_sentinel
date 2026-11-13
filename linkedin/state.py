import json
import os
from typing import Dict


def default_state() -> dict:
    return {"version": 1, "seeded": False, "seen": {}}


# Do not mutate; use default_state() for a fresh dict (avoids shared nested "seen").
DEFAULT_STATE: Dict = {"version": 1, "seeded": False, "seen": {}}


def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            state = json.load(f)
        if not isinstance(state.get("seen"), dict):
            state["seen"] = {}
        if "seeded" not in state:
            state["seeded"] = bool(state["seen"])
        if "version" not in state:
            state["version"] = 1
        return state
    except (FileNotFoundError, json.JSONDecodeError):
        return default_state()


def save_state(path: str, state: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, path)


def mark_seen(state: dict, post_id: str, meta: dict) -> None:
    state.setdefault("seen", {})[post_id] = meta


def prune_seen(state: dict, max_entries: int) -> int:
    seen = state.get("seen") or {}
    if len(seen) <= max_entries:
        return 0
    ordered = sorted(
        seen.items(),
        key=lambda item: item[1].get("first_seen") or "",
        reverse=True,
    )
    keep = dict(ordered[:max_entries])
    removed = len(seen) - len(keep)
    state["seen"] = keep
    return removed
