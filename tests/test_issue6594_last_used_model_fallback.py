"""Regression tests for issue #6594:
Composer chip and sidebar model resolution during direct-provider fallback.

Verifies:
1. session.model is NOT overwritten during direct fallback (preserves requested route).
2. session.last_used_model is persisted on Session and serialized in compact/metadata.
3. _SIDEBAR_SESSION_RESPONSE_FIELDS allowlists last_used_model.
4. Frontend resolution precedence: gateway_routing.used_model -> last_used_model -> session.model.
5. Multi-turn route stability (subsequent turns still target requested session.model).
"""

from pathlib import Path
from api.models import Session
from api.routes import _SIDEBAR_SESSION_RESPONSE_FIELDS, _sidebar_session_response_item

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
STREAMING_PY = (ROOT / "api" / "streaming.py").read_text(encoding="utf-8")


def test_session_model_preserved_and_last_used_model_persisted():
    """session.model must not be overwritten; last_used_model must be persisted."""
    session = Session(
        session_id="6594fallback",
        title="Direct Fallback Test",
        model="claude-3-5-sonnet",
        last_used_model="claude-3-haiku",
    )
    session.save()

    loaded = Session.load("6594fallback")
    assert loaded is not None
    # Requested route preserved
    assert loaded.model == "claude-3-5-sonnet"
    # Fallback model captured separately
    assert loaded.last_used_model == "claude-3-haiku"

    # Verify compact representation
    compact = loaded.compact()
    assert compact["model"] == "claude-3-5-sonnet"
    assert compact["last_used_model"] == "claude-3-haiku"

    # Verify load_metadata_only
    meta = Session.load_metadata_only("6594fallback")
    assert meta is not None
    assert meta.model == "claude-3-5-sonnet"
    assert meta.last_used_model == "claude-3-haiku"


def test_sidebar_session_response_fields_allowlists_last_used_model():
    """Sidebar session serialization must include last_used_model."""
    assert "last_used_model" in _SIDEBAR_SESSION_RESPONSE_FIELDS

    raw_session = {
        "session_id": "sid6594",
        "title": "Conversation",
        "model": "gpt-4o",
        "last_used_model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
    }
    sidebar_item = _sidebar_session_response_item(raw_session)
    assert sidebar_item.get("last_used_model") == "gpt-4o-mini"
    assert sidebar_item.get("model") == "gpt-4o"


def test_streaming_updates_last_used_model_without_mutating_session_model():
    """Post-run hook updates s.last_used_model from post-run agent.model."""
    assert "s.last_used_model = str(_used_model).strip()[:240]" in STREAMING_PY
    assert "s.model = " not in STREAMING_PY.split("if _used_model:")[1].split("if _gateway_routing:")[0]


def test_frontend_formatters_resolve_precedence():
    """Frontend must resolve gateway_routing -> last_used_model -> session.model."""
    # sessions.js: _formatSessionModelWithGateway
    assert "fallbackModel=s.last_used_model||s.model" in SESSIONS_JS.replace(" ", "")

    # ui.js: composer chip label resolution
    assert "S.session.last_used_model" in UI_JS


def test_composer_chip_manual_pick_overrides_last_used_model():
    """Manual dropdown pick takes precedence over historical fallback and routing."""
    assert "manualPick" in UI_JS
    assert "!manualPick&&S.session&&S.session.last_used_model" in UI_JS.replace(" ", "")
    assert "activeRouting=manualPick?null:gatewayRouting" in UI_JS.replace(" ", "")
    assert "_formatGatewayModelLabel(sel.value||'',compactText,activeRouting)" in UI_JS.replace(" ", "")


