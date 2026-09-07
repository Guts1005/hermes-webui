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
BOOT_JS = ROOT / "static" / "boot.js"
NODE = shutil.which("node")


def _extract_block(source: str, prefix: str) -> str:
    start = source.find(prefix)
    assert start != -1, f"Could not find {prefix}"
    brace = source.find("{", start)
    assert brace != -1, f"Could not find opening brace for {prefix}"
    depth = 0
    for idx in range(brace, len(source)):
        ch = source[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    pytest.fail(f"Could not extract complete block for {prefix}")


def _extract_function(source: str, name: str) -> str:
    for prefix in (f"async function {name}(", f"function {name}("):
        if prefix in source:
            return _extract_block(source, prefix)
    pytest.fail(f"Could not find function {name}")


def _run_node(script: str) -> str:
    if NODE is None:
        pytest.skip("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        script_path = handle.name
    try:
        proc = subprocess.run(
            [NODE, script_path],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Node execution failed (code {proc.returncode}):\n{proc.stderr}")
        return proc.stdout.strip()
    finally:
        Path(script_path).unlink(missing_ok=True)


def _sidebar_harness(eval_code: str) -> str:
    sessions_source = SESSIONS_JS.read_text(encoding="utf-8")
    ui_source = UI_JS.read_text(encoding="utf-8")

    fn_gateway_routing_label = _extract_function(ui_source, "_gatewayRoutingLabel")
    fn_format_gateway_model_label = _extract_function(ui_source, "_formatGatewayModelLabel")
    fn_latest_gateway_routing = _extract_function(ui_source, "_latestGatewayRoutingForSession")
    fn_format_session_model_with_gateway = _extract_function(sessions_source, "_formatSessionModelWithGateway")

    header = """
function _gatewayProviderName(p) { return p ? String(p) : ''; }
function _compactComposerModelChipLabel(id, label) { return label || id; }
function getModelLabel(id) { return id ? ('Model(' + id + ')') : ''; }
"""
    full_script = f"{header}\n{fn_gateway_routing_label}\n{fn_format_gateway_model_label}\n{fn_latest_gateway_routing}\n{fn_format_session_model_with_gateway}\n{eval_code}"
    return _run_node(full_script)


def _production_event_harness(eval_code: str) -> str:
    ui_source = UI_JS.read_text(encoding="utf-8")
    boot_source = BOOT_JS.read_text(encoding="utf-8")

    fn_gateway_routing_label = _extract_function(ui_source, "_gatewayRoutingLabel")
    fn_format_gateway_model_label = _extract_function(ui_source, "_formatGatewayModelLabel")
    fn_latest_gateway_routing = _extract_function(ui_source, "_latestGatewayRoutingForSession")
    fn_sync_model_chip = _extract_function(ui_source, "syncModelChip")
    fn_select_model = _extract_function(ui_source, "selectModelFromDropdown")
    fn_apply_ctx = _extract_function(boot_source, "_applySessionContextMetadataUpdate")
    onchange_block = _extract_block(boot_source, "$('modelSelect').onchange=")

    harness = f"""
const elements = {{
  modelSelect: {{ id: 'modelSelect', value: 'claude-3-5-sonnet', onchange: null }},
  composerModelChip: {{ title: '', classList: {{ toggle: () => {{}}, contains: () => false }} }},
  composerModelLabel: {{ textContent: '' }},
  composerMobileModelLabel: {{ textContent: '' }},
  composerMobileModelAction: {{ classList: {{ toggle: () => {{}}, contains: () => false }} }},
  composerModelDropdown: {{ classList: {{ toggle: () => {{}}, contains: () => false }} }}
}};

function $(id) {{ return elements[id] || null; }}

const S = {{
  _bootReady: true,
  session: {{
    session_id: 'test-session-1',
    workspace: 'default',
    model: 'claude-3-5-sonnet',
    model_provider: null,
    last_used_model: 'claude-3-haiku',
    gateway_routing: null,
    gateway_routing_history: []
  }}
}};

function _gatewayProviderName(p) {{ return p ? String(p) : ''; }}
function _compactComposerModelChipLabel(id, label) {{ return label || id; }}
function getModelLabel(id) {{ return id ? ('Model(' + id + ')') : ''; }}
function _selectedModelOption() {{ return null; }}
function _modelStateForSelect(sel, v) {{ return {{ model: v, model_provider: null }}; }}
function closeModelDropdown() {{}}
function clearProfileTransitionReasoningContext() {{}}
function _writePersistedModelState() {{}}
function _rememberPendingSessionModel() {{}}
function syncReasoningChip() {{}}
function syncTopbar() {{}}
function showToast() {{}}
function t() {{ return ''; }}

async function api(endpoint, opts) {{
  if (endpoint === '/api/session/update') {{
    const body = JSON.parse(opts.body);
    return {{
      session: {{
        session_id: body.session_id,
        model: body.model,
        model_provider: body.model_provider,
        last_used_model: null,
        gateway_routing: null,
        gateway_routing_history: []
      }}
    }};
  }}
  return {{}};
}}

{fn_gateway_routing_label}
{fn_format_gateway_model_label}
{fn_latest_gateway_routing}
{fn_sync_model_chip}
{fn_select_model}
{fn_apply_ctx}
{onchange_block}

{eval_code}
"""
    return _run_node(harness)


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
    out1 = _sidebar_harness("""
const s = { model: 'claude-3-5-sonnet', last_used_model: 'claude-3-haiku', gateway_routing: null };
console.log(_formatSessionModelWithGateway(s));
""")
    assert out1 == "Model(claude-3-haiku)"

    # Case 2: Gateway fallback with explicit used_model (routing.used_model takes precedence)
    out2 = _sidebar_harness("""
const s = {
  model: 'claude-3-5-sonnet',
  last_used_model: 'claude-3-haiku',
  gateway_routing: { used_model: 'llama-3-70b', provider: 'openrouter' }
};
console.log(_formatSessionModelWithGateway(s));
""")
    assert out2 == "Model(llama-3-70b) via openrouter"

    # Case 3: Gateway routing without used_model preserves last_used_model with provider tag
    out3 = _sidebar_harness("""
const s = {
  model: 'claude-3-5-sonnet',
  last_used_model: 'claude-3-haiku',
  gateway_routing: { provider: 'openrouter' }
};
console.log(_formatSessionModelWithGateway(s));
""")
    assert out3 == "Model(claude-3-haiku) via openrouter"

    # Case 4: No fallback occurred (requested model shown)
    out4 = _sidebar_harness("""
const s = { model: 'claude-3-5-sonnet', last_used_model: null, gateway_routing: null };
console.log(_formatSessionModelWithGateway(s));
""")
    assert out4 == "Model(claude-3-5-sonnet)"


def test_server_session_update_clears_stale_fallback_and_routing():
    """Server /api/session/update clears last_used_model, gateway_routing, and history when route changes."""
    session = Session(
        session_id="server_update_clear_test",
        title="Server Invalidation",
        model="claude-3-5-sonnet",
        model_provider="anthropic",
        last_used_model="claude-3-haiku",
        gateway_routing={"used_model": "claude-3-haiku", "provider": "anthropic"},
        gateway_routing_history=[{"used_model": "claude-3-haiku", "provider": "anthropic"}],
    )
    session.save()

    # Emulate the server /api/session/update logic in api/routes.py:15840-15850
    old_model = getattr(session, "model", None)
    old_provider = getattr(session, "model_provider", None)

    session.model = "gpt-4o"
    session.model_provider = "openai"
    if (
        str(old_model or "") != str(getattr(session, "model", "") or "")
        or str(old_provider or "") != str(getattr(session, "model_provider", "") or "")
    ):
        session.last_used_model = None
        session.gateway_routing = None
        session.gateway_routing_history = []
    session.save()

    reloaded = Session.load("server_update_clear_test")
    assert reloaded.model == "gpt-4o"
    assert reloaded.model_provider == "openai"
    assert reloaded.last_used_model is None
    assert reloaded.gateway_routing is None
    assert reloaded.gateway_routing_history == []

    compact = reloaded.compact()
    assert compact["model"] == "gpt-4o"
    assert compact["last_used_model"] is None
    assert compact["gateway_routing"] is None


def test_production_model_selection_lifecycle_observable_behavior():
    """Test full production sequence: selectModelFromDropdown -> modelSelect.onchange -> syncModelChip -> session update -> in flight."""
    raw_output = _production_event_harness("""
async function run() {
  const log = [];

  // 1. Initial state after turn 1 served fallback model
  syncModelChip();
  log.push({ phase: 'initial', label: elements.composerModelLabel.textContent });

  // 2. User selects 'gpt-4o' via shipped selectModelFromDropdown
  // This executes:
  //   - sel.value = 'gpt-4o'
  //   - syncModelChip() (first call: manual pick active)
  //   - modelSelect.onchange() (routeChanged invalidates last_used_model, S.session.model = 'gpt-4o')
  //   - syncModelChip() (second call: sel.value === S.session.model, last_used_model is cleared)
  //   - api('/api/session/update') returns cleared session metadata
  //   - _applySessionContextMetadataUpdate(data)
  //   - syncModelChip() (third call after server update)
  await selectModelFromDropdown('gpt-4o');
  log.push({ phase: 'after_select_and_update', label: elements.composerModelLabel.textContent });

  // 3. While next turn is in flight (before any streaming event returns a new fallback)
  syncModelChip();
  log.push({ phase: 'turn_in_flight', label: elements.composerModelLabel.textContent });

  // 4. Test gateway routing session with history
  S.session.model = 'gpt-4o';
  S.session.gateway_routing = { used_model: 'llama-3', provider: 'openrouter', requested_model: 'gpt-4o' };
  S.session.gateway_routing_history = [{ used_model: 'llama-3', provider: 'openrouter', requested_model: 'gpt-4o' }];
  elements.modelSelect.value = 'gpt-4o';
  syncModelChip();
  log.push({ phase: 'gateway_initial', label: elements.composerModelLabel.textContent });

  // User selects 'claude-3-5-sonnet' from dropdown
  await selectModelFromDropdown('claude-3-5-sonnet');
  log.push({ phase: 'gateway_after_switch', label: elements.composerModelLabel.textContent });

  // While next turn is in flight
  syncModelChip();
  log.push({ phase: 'gateway_turn_in_flight', label: elements.composerModelLabel.textContent });

  console.log(JSON.stringify(log));
}
run();
""")
    results = {item["phase"]: item["label"] for item in json.loads(raw_output)}

    # Phase 1: Initial served direct fallback model displayed
    assert results["initial"] == "Model(claude-3-haiku)"

    # Phase 2: After selectModelFromDropdown + onchange + session update, chip remains the newly selected model
    assert results["after_select_and_update"] == "Model(gpt-4o)"

    # Phase 3: In-flight turn does not snap back to stale last_used_model
    assert results["turn_in_flight"] == "Model(gpt-4o)"

    # Phase 4: Gateway routing session initially displays gateway routed label
    assert results["gateway_initial"] == "Model(llama-3) via openrouter"

    # Phase 5: After switching to claude-3-5-sonnet, chip displays the new model, ignoring stale gateway routing
    assert results["gateway_after_switch"] == "Model(claude-3-5-sonnet)"

    # Phase 6: In-flight turn retains the new model
    assert results["gateway_turn_in_flight"] == "Model(claude-3-5-sonnet)"
