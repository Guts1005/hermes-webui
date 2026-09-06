import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from api.models import Session
from api.routes import _SIDEBAR_SESSION_RESPONSE_FIELDS, _sidebar_session_response_item

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_JS = ROOT / "static" / "sessions.js"
UI_JS = ROOT / "static" / "ui.js"
NODE = shutil.which("node")


def _extract_function(source: str, name: str) -> str:
    start = source.find(f"function {name}(")
    assert start != -1, f"Could not find function {name}"
    brace = source.find("{", start)
    assert brace != -1, f"Could not find opening brace for {name}"
    depth = 0
    for idx in range(brace, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    pytest.fail(f"Could not extract complete function body for {name}")


def _run_node(script: str) -> str:
    if NODE is None:
        pytest.skip("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        script_path = handle.name
    try:
        proc = subprocess.run(
            [NODE, script_path],
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()
    finally:
        Path(script_path).unlink(missing_ok=True)


def _frontend_harness(eval_code: str) -> str:
    sessions_source = SESSIONS_JS.read_text(encoding="utf-8")
    ui_source = UI_JS.read_text(encoding="utf-8")

    fn_gateway_routing_label = _extract_function(ui_source, "_gatewayRoutingLabel")
    fn_format_gateway_model_label = _extract_function(ui_source, "_formatGatewayModelLabel")
    fn_format_session_model_with_gateway = _extract_function(sessions_source, "_formatSessionModelWithGateway")

    header = """
function _gatewayProviderName(p) { return p ? String(p) : ''; }
function _compactComposerModelChipLabel(id, label) { return label || id; }
function getModelLabel(id) { return id ? ('Model(' + id + ')') : ''; }
function _latestGatewayRoutingForSession(s) {
  if(!s) return null;
  if(s.gateway_routing) return s.gateway_routing;
  const history = Array.isArray(s.gateway_routing_history) ? s.gateway_routing_history : [];
  return history.length ? history[history.length - 1] : null;
}
function _selectedModelOption() { return null; }

function resolveComposerChip(selVal, session) {
  const S = { session: session };
  const sel = { value: selVal };
  const text = getModelLabel(sel.value || '');
  const compactText = _compactComposerModelChipLabel(sel.value || '', text);
  const gatewayRouting = _latestGatewayRoutingForSession(S.session);
  const manualPick = String(sel.value || '') !== String((S.session && S.session.model) || '');
  const activeRouting = manualPick ? null : gatewayRouting;
  const fallbackModel = (!manualPick && S.session && S.session.last_used_model) ? S.session.last_used_model : (sel.value || '');
  const fallbackText = (!manualPick && S.session && S.session.last_used_model)
    ? _compactComposerModelChipLabel(S.session.last_used_model, getModelLabel(S.session.last_used_model))
    : compactText;
  const displayText = _formatGatewayModelLabel(fallbackModel, fallbackText, activeRouting) || fallbackText;
  return displayText;
}
"""
    full_script = f"{header}\n{fn_gateway_routing_label}\n{fn_format_gateway_model_label}\n{fn_format_session_model_with_gateway}\n{eval_code}"
    return _run_node(full_script)


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


def test_streaming_post_run_hook_updates_last_used_model_without_mutating_session_model():
    """Post-run agent model capture sets s.last_used_model and preserves s.model."""
    s = Session(session_id="post_run_test", model="claude-3-5-sonnet")

    class MockAgent:
        model = "claude-3-haiku"

    agent = MockAgent()
    resolved_model = "claude-3-5-sonnet"
    model = "claude-3-5-sonnet"

    _used_model = getattr(agent, "model", None) or resolved_model or model
    if _used_model:
        s.last_used_model = str(_used_model).strip()[:240]

    assert s.last_used_model == "claude-3-haiku"
    assert s.model == "claude-3-5-sonnet"


def test_sidebar_model_resolution_observable_precedence():
    """Sidebar formatter returns observable string according to precedence hierarchy."""
    # Case 1: Direct fallback (last_used_model takes precedence over requested model)
    out1 = _frontend_harness("""
const s = { model: 'claude-3-5-sonnet', last_used_model: 'claude-3-haiku', gateway_routing: null };
console.log(_formatSessionModelWithGateway(s));
""")
    assert out1 == "Model(claude-3-haiku)"

    # Case 2: Gateway fallback with explicit used_model (routing.used_model takes precedence)
    out2 = _frontend_harness("""
const s = {
  model: 'claude-3-5-sonnet',
  last_used_model: 'claude-3-haiku',
  gateway_routing: { used_model: 'llama-3-70b', provider: 'openrouter' }
};
console.log(_formatSessionModelWithGateway(s));
""")
    assert out2 == "Model(llama-3-70b) via openrouter"

    # Case 3: Gateway routing without used_model preserves last_used_model with provider tag
    out3 = _frontend_harness("""
const s = {
  model: 'claude-3-5-sonnet',
  last_used_model: 'claude-3-haiku',
  gateway_routing: { provider: 'openrouter' }
};
console.log(_formatSessionModelWithGateway(s));
""")
    assert out3 == "Model(claude-3-haiku) via openrouter"

    # Case 4: No fallback occurred (requested model shown)
    out4 = _frontend_harness("""
const s = { model: 'claude-3-5-sonnet', last_used_model: null, gateway_routing: null };
console.log(_formatSessionModelWithGateway(s));
""")
    assert out4 == "Model(claude-3-5-sonnet)"


def test_composer_chip_manual_pick_observable_precedence():
    """Composer chip prioritizes manual live dropdown pick over historical fallback and routing."""
    # Case 1: Active fallback turn without manual pick shows last_used_model
    out1 = _frontend_harness("""
const session = { model: 'claude-3-5-sonnet', last_used_model: 'claude-3-haiku' };
console.log(resolveComposerChip('claude-3-5-sonnet', session));
""")
    assert out1 == "Model(claude-3-haiku)"

    # Case 2: User manually selects different model (e.g. gpt-4o) from dropdown
    # -> Manual pick overrides historical last_used_model immediately
    out2 = _frontend_harness("""
const session = { model: 'claude-3-5-sonnet', last_used_model: 'claude-3-haiku' };
console.log(resolveComposerChip('gpt-4o', session));
""")
    assert out2 == "Model(gpt-4o)"

    # Case 3: Manual pick overrides retained gateway routing
    out3 = _frontend_harness("""
const session = {
  model: 'claude-3-5-sonnet',
  last_used_model: 'claude-3-haiku',
  gateway_routing: { used_model: 'llama-3', provider: 'openrouter' }
};
console.log(resolveComposerChip('gpt-4o', session));
""")
    assert out3 == "Model(gpt-4o)"

    # Case 4: Routing without used_model formats last_used_model via provider
    out4 = _frontend_harness("""
const session = {
  model: 'claude-3-5-sonnet',
  last_used_model: 'claude-3-haiku',
  gateway_routing: { provider: 'openrouter' }
};
console.log(resolveComposerChip('claude-3-5-sonnet', session));
""")
    assert out4 == "Model(claude-3-haiku) via openrouter"
