"""Check, Please backend routes for the Desktop pane's data path (backlog item 11).

Mounts under /api/plugins/check-please/. Reads this plugin's own durable state through
hermes_cli.plugins_state.PluginState -- the exact class ctx.state (used by __init__.py's
hooks) wraps -- never a raw file path and never the renderer. "check-please" here must match
plugin.yaml's `name:` exactly: that string is the plugin_id both ctx.state and this route use
to find the same plugin-data directory.

No network calls, no shell, no credentials. Read-only: this module never writes plugin state,
only the agent-side hooks in __init__.py do that.
"""
from __future__ import annotations

from fastapi import APIRouter

from hermes_cli.plugins_state import PluginState

router = APIRouter()

_PLUGIN_ID = "check-please"
_EMPTY_CLAIMS: dict = {"claims": [], "backed": 0, "total": 0}


def _state() -> PluginState:
    return PluginState(_PLUGIN_ID)


@router.get("/claims")
async def get_claims(session_id: str = "") -> dict:
    """Last-reply claims + tally for one session (the Desktop pane's focused session).

    Shape matches engine.claims_payload() exactly: {"claims": [...], "backed": int, "total": int}.
    An unknown or missing session_id returns the same honest empty shape the pane already
    renders as its empty state -- never a guessed or stale row.
    """
    last_reply = _state().get("last_reply", default={}) or {}
    payload = last_reply.get(session_id) if session_id else None
    return dict(payload) if isinstance(payload, dict) else dict(_EMPTY_CLAIMS)


@router.get("/jobs")
async def get_jobs() -> dict:
    """Per-cron-job tally (backlog item 7's data), for a future Desktop jobs view."""
    tally = _state().get("cron_tally", default={}) or {}
    return {"jobs": tally if isinstance(tally, dict) else {}}
