# WhatsApp Group-Based Profile Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `group_profile_map` routing primitive so WhatsApp groups can be exclusively bound to a profile worker. A bound group routes only to its target — never falls back to default — and drops the message when the target's worker is unavailable.

**Architecture:** Extend the existing `ProfileRoutingConfig` schema, the `WhatsAppRouter`, and `ProfileWorkerManager`. Modify the dispatch decision in `gateway/run.py:_handle_message` to consult group routing first when `is_group=true`. No new processes, no new IPC. Workers are still spawned per profile in `profiles`; group routing only changes which existing worker receives a given event.

**Tech Stack:** Python 3.11+, pytest with pytest-asyncio, dataclasses for schema, YAML for config.

**Spec:** `docs/superpowers/specs/2026-05-10-whatsapp-group-profile-routing-design.md`

---

## File Structure

**Modified:**
- `gateway/profile_routing_config.py` — add `group_profile_map: dict[str, str]` to `ProfileRoutingConfig`; extend `parse_profile_routing` to read and validate it.
- `gateway/whatsapp_router.py` — add `resolve_group(chat_id) -> Optional[str]`.
- `gateway/profile_worker_manager.py` — add `has_worker(name) -> bool` for the dispatcher's availability check.
- `gateway/run.py` — modify the routing branch in `_handle_message` (~lines 4611–4628) to consult group routing first.
- `docs/whatsapp_profile_routing.md` — add a "Group routing" section.

**Created:**
- `tests/gateway/test_whatsapp_router.py` — unit tests for `resolve_group`.

**Modified tests:**
- `tests/gateway/test_profile_routing_config.py` — schema parser tests for `group_profile_map`.
- `tests/gateway/test_handle_message_routing.py` — dispatch precedence tests.
- `tests/gateway/test_gateway_routing_boot.py` — regression for invalid `group_profile_map` aborts boot.
- `tests/gateway/test_profile_worker_manager.py` — test for `has_worker`.

---

## Task 1: Add `group_profile_map` field to `ProfileRoutingConfig`

**Files:**
- Modify: `gateway/profile_routing_config.py:28-34`
- Test: `tests/gateway/test_profile_routing_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/gateway/test_profile_routing_config.py`:

```python
def test_dataclass_has_group_profile_map_default_empty():
    """ProfileRoutingConfig accepts group_profile_map and defaults to empty dict."""
    cfg = ProfileRoutingConfig(
        profiles=("main",),
        default_profile="main",
        sender_profile_map={},
    )
    assert cfg.group_profile_map == {}


def test_dataclass_accepts_group_profile_map():
    cfg = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={},
        group_profile_map={"120363409860032836@g.us": "family"},
    )
    assert cfg.group_profile_map == {"120363409860032836@g.us": "family"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_profile_routing_config.py::test_dataclass_has_group_profile_map_default_empty tests/gateway/test_profile_routing_config.py::test_dataclass_accepts_group_profile_map -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'group_profile_map'` (or AttributeError on the assertion line for the first test, depending on which Python validates first).

- [ ] **Step 3: Add the field to the dataclass**

In `gateway/profile_routing_config.py`, change:

```python
@dataclass(frozen=True)
class ProfileRoutingConfig:
    """Validated, canonicalised routing config."""

    profiles: tuple[str, ...]
    default_profile: str
    sender_profile_map: dict[str, str] = field(default_factory=dict)
```

to:

```python
@dataclass(frozen=True)
class ProfileRoutingConfig:
    """Validated, canonicalised routing config."""

    profiles: tuple[str, ...]
    default_profile: str
    sender_profile_map: dict[str, str] = field(default_factory=dict)
    group_profile_map: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_profile_routing_config.py -v`
Expected: PASS for the two new tests, all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add gateway/profile_routing_config.py tests/gateway/test_profile_routing_config.py
git commit -m "feat(gateway): add group_profile_map field to ProfileRoutingConfig"
```

---

## Task 2: Parse `group_profile_map` from YAML

**Files:**
- Modify: `gateway/profile_routing_config.py` (extend `parse_profile_routing`, after the `sender_profile_map` block, before the `return`)
- Test: `tests/gateway/test_profile_routing_config.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/gateway/test_profile_routing_config.py`:

```python
def test_parser_populates_group_profile_map():
    cfg = parse_profile_routing(
        {
            "profiles": ["main", "family"],
            "default_profile": "main",
            "sender_profile_map": {},
            "group_profile_map": {
                "120363409860032836@g.us": "family",
                "120363409999999999@g.us": "main",
            },
        }
    )
    assert cfg is not None
    assert cfg.group_profile_map == {
        "120363409860032836@g.us": "family",
        "120363409999999999@g.us": "main",
    }


def test_parser_group_profile_map_optional():
    """A config without group_profile_map parses with an empty group_profile_map."""
    cfg = parse_profile_routing(
        {
            "profiles": ["main"],
            "default_profile": "main",
            "sender_profile_map": {},
        }
    )
    assert cfg is not None
    assert cfg.group_profile_map == {}


def test_parser_allows_multiple_groups_to_same_profile():
    cfg = parse_profile_routing(
        {
            "profiles": ["main", "family"],
            "default_profile": "main",
            "sender_profile_map": {},
            "group_profile_map": {
                "g1@g.us": "family",
                "g2@g.us": "family",
            },
        }
    )
    assert cfg is not None
    assert cfg.group_profile_map == {"g1@g.us": "family", "g2@g.us": "family"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_profile_routing_config.py::test_parser_populates_group_profile_map tests/gateway/test_profile_routing_config.py::test_parser_group_profile_map_optional tests/gateway/test_profile_routing_config.py::test_parser_allows_multiple_groups_to_same_profile -v`
Expected: FAIL with `AssertionError` (the parser ignores `group_profile_map` and returns the default empty dict only when explicitly set).

- [ ] **Step 3: Implement parsing**

In `gateway/profile_routing_config.py`, in `parse_profile_routing`, immediately after the existing `sender_profile_map` loop completes (after the last `canonical_map[canonical] = target` line, just before the final `return ProfileRoutingConfig(...)`), insert:

```python
    raw_group_map = raw.get("group_profile_map")
    if raw_group_map is None:
        raw_group_map = {}
    if not isinstance(raw_group_map, dict):
        raise ProfileRoutingConfigError(
            "profile_routing.group_profile_map must be a mapping"
        )

    group_map: dict[str, str] = {}
    for raw_chat_id, target in raw_group_map.items():
        if not isinstance(raw_chat_id, str) or not isinstance(target, str):
            raise ProfileRoutingConfigError(
                "group_profile_map keys and values must be strings"
            )
        chat_id = raw_chat_id.strip()
        if not chat_id:
            raise ProfileRoutingConfigError(
                f"group_profile_map key {raw_chat_id!r} is empty after stripping"
            )
        if target not in profiles_tuple:
            raise ProfileRoutingConfigError(
                f"group_profile_map maps {raw_chat_id!r} to unknown profile "
                f"{target!r} (not in profiles list)"
            )
        group_map[chat_id] = target
```

Then change the final return from:

```python
    return ProfileRoutingConfig(
        profiles=profiles_tuple,
        default_profile=default_profile,
        sender_profile_map=canonical_map,
    )
```

to:

```python
    return ProfileRoutingConfig(
        profiles=profiles_tuple,
        default_profile=default_profile,
        sender_profile_map=canonical_map,
        group_profile_map=group_map,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_profile_routing_config.py -v`
Expected: PASS for the three new tests, all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add gateway/profile_routing_config.py tests/gateway/test_profile_routing_config.py
git commit -m "feat(gateway): parse group_profile_map in profile_routing"
```

---

## Task 3: Validation rules for `group_profile_map`

**Files:**
- Test only: `tests/gateway/test_profile_routing_config.py` (parser code from Task 2 already enforces these — this task locks the contract with regression tests).

- [ ] **Step 1: Write the failing tests**

Append to `tests/gateway/test_profile_routing_config.py`:

```python
def test_group_profile_map_unknown_target_raises():
    with pytest.raises(ProfileRoutingConfigError, match="unknown profile"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": {"g1@g.us": "ghost"},
            }
        )


def test_group_profile_map_non_string_key_raises():
    with pytest.raises(ProfileRoutingConfigError, match="must be strings"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": {123: "main"},
            }
        )


def test_group_profile_map_non_string_value_raises():
    with pytest.raises(ProfileRoutingConfigError, match="must be strings"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": {"g1@g.us": 42},
            }
        )


def test_group_profile_map_empty_key_raises():
    with pytest.raises(ProfileRoutingConfigError, match="empty"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": {"   ": "main"},
            }
        )


def test_group_profile_map_not_a_dict_raises():
    with pytest.raises(ProfileRoutingConfigError, match="must be a mapping"):
        parse_profile_routing(
            {
                "profiles": ["main"],
                "default_profile": "main",
                "sender_profile_map": {},
                "group_profile_map": ["g1@g.us", "main"],
            }
        )
```

- [ ] **Step 2: Run tests to verify they pass**

Since the parser code from Task 2 already enforces these rules, the tests should pass on the first run. This task is the regression contract.

Run: `pytest tests/gateway/test_profile_routing_config.py -v -k group`
Expected: PASS for all `group_profile_map` tests.

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/test_profile_routing_config.py
git commit -m "test(gateway): regression suite for group_profile_map validation"
```

---

## Task 4: Boot fail-closed regression for invalid `group_profile_map`

**Files:**
- Test: `tests/gateway/test_gateway_routing_boot.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/gateway/test_gateway_routing_boot.py`:

```python
def test_invalid_group_profile_map_fails_closed(tmp_path, monkeypatch):
    """A malformed ``group_profile_map`` block must abort gateway boot.

    Mirrors test_invalid_profile_routing_fails_closed: the existing
    fail-closed pass-through in gateway/config.py:979 catches
    ProfileRoutingConfigError raised by either map.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        """
whatsapp:
  profile_routing:
    profiles: ["default", "main"]
    default_profile: "default"
    group_profile_map:
      "g1@g.us": "ghost-profile"
"""
    )
    from gateway.config import load_gateway_config

    with pytest.raises(ProfileRoutingConfigError):
        load_gateway_config()


def test_valid_group_profile_map_loads_from_yaml(tmp_path, monkeypatch):
    """A valid group_profile_map block survives load_gateway_config."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        """
whatsapp:
  profile_routing:
    profiles: ["default", "family"]
    default_profile: "default"
    group_profile_map:
      "120363409860032836@g.us": "family"
"""
    )
    from gateway.config import load_gateway_config

    cfg = load_gateway_config()
    assert cfg.whatsapp_profile_routing is not None
    assert cfg.whatsapp_profile_routing.group_profile_map == {
        "120363409860032836@g.us": "family"
    }
```

- [ ] **Step 2: Run tests to verify they pass**

Both tests should pass — the validator from Task 2 raises `ProfileRoutingConfigError`, which is already re-raised past the broad fallback by the previous fail-closed commit (`c2d29658b`).

Run: `pytest tests/gateway/test_gateway_routing_boot.py -v`
Expected: PASS for the two new tests, all existing tests still pass.

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/test_gateway_routing_boot.py
git commit -m "test(gateway): boot regression for group_profile_map fail-closed"
```

---

## Task 5: Add `has_worker` to `ProfileWorkerManager`

**Files:**
- Modify: `gateway/profile_worker_manager.py` (after the `names` property at line 76-78)
- Test: `tests/gateway/test_profile_worker_manager.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/gateway/test_profile_worker_manager.py`:

```python
def test_has_worker_returns_false_when_empty():
    from gateway.profile_worker_manager import ProfileWorkerManager

    mgr = ProfileWorkerManager()
    assert mgr.has_worker("anything") is False


def test_has_worker_returns_true_for_registered():
    from gateway.profile_worker_manager import ProfileWorkerManager

    mgr = ProfileWorkerManager()
    # Bypass start() and inject a sentinel — has_worker is a pure registry check.
    mgr._workers["family"] = object()
    assert mgr.has_worker("family") is True
    assert mgr.has_worker("ghost") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_profile_worker_manager.py::test_has_worker_returns_false_when_empty tests/gateway/test_profile_worker_manager.py::test_has_worker_returns_true_for_registered -v`
Expected: FAIL with `AttributeError: 'ProfileWorkerManager' object has no attribute 'has_worker'`.

- [ ] **Step 3: Add `has_worker`**

In `gateway/profile_worker_manager.py`, after the `names` property (currently at line 77), add:

```python
    def has_worker(self, profile_name: str) -> bool:
        """True iff a worker is currently registered under ``profile_name``."""
        return profile_name in self._workers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_profile_worker_manager.py -v`
Expected: PASS for the two new tests, all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add gateway/profile_worker_manager.py tests/gateway/test_profile_worker_manager.py
git commit -m "feat(gateway): ProfileWorkerManager.has_worker(name)"
```

---

## Task 6: Add `resolve_group` to `WhatsAppRouter`

**Files:**
- Modify: `gateway/whatsapp_router.py`
- Create: `tests/gateway/test_whatsapp_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_whatsapp_router.py`:

```python
"""Unit tests for WhatsAppRouter."""

from __future__ import annotations

from gateway.profile_routing_config import ProfileRoutingConfig
from gateway.whatsapp_router import WhatsAppRouter


def _cfg(**overrides) -> ProfileRoutingConfig:
    base = dict(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60123": "family"},
        group_profile_map={"g1@g.us": "family"},
    )
    base.update(overrides)
    return ProfileRoutingConfig(**base)


def test_resolve_group_returns_mapped_profile():
    router = WhatsAppRouter(_cfg())
    assert router.resolve_group("g1@g.us") == "family"


def test_resolve_group_returns_none_for_unmapped_chat():
    router = WhatsAppRouter(_cfg())
    assert router.resolve_group("g2@g.us") is None


def test_resolve_group_returns_none_when_map_empty():
    router = WhatsAppRouter(_cfg(group_profile_map={}))
    assert router.resolve_group("g1@g.us") is None


def test_resolve_profile_unaffected_by_group_map():
    """Existing sender lookup stays exactly as before."""
    router = WhatsAppRouter(_cfg())
    assert router.resolve_profile("60123") == "family"
    assert router.resolve_profile("99999") == "main"  # default fallback
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_whatsapp_router.py -v`
Expected: FAIL with `AttributeError: 'WhatsAppRouter' object has no attribute 'resolve_group'`.

- [ ] **Step 3: Add `resolve_group`**

In `gateway/whatsapp_router.py`, replace the file with:

```python
"""Pure routing function: WhatsApp identity -> target profile name."""

from __future__ import annotations

from typing import Optional

from gateway.profile_routing_config import ProfileRoutingConfig


class WhatsAppRouter:
    """Resolves a target profile name from canonical WhatsApp identifiers.

    The router does NOT canonicalise input — callers must pass the same
    canonical form that was applied to the map keys at config-load time
    (see :func:`gateway.profile_routing_config.parse_profile_routing`).
    Keeping canonicalisation in one well-known place (the WhatsApp adapter)
    avoids drift between load-time and message-time normalisation.

    Two lookups are exposed:

    * :meth:`resolve_profile` — sender-based, falls back to ``default_profile``.
    * :meth:`resolve_group` — group-chat-based, returns ``None`` when the chat
      is not bound to any profile. Group routing is exclusive: callers MUST
      treat a non-``None`` result as the only legitimate target and drop the
      message rather than fall back to sender routing or the default profile.
    """

    def __init__(self, config: ProfileRoutingConfig) -> None:
        self._map = config.sender_profile_map
        self._default = config.default_profile
        self._group_map = config.group_profile_map

    def resolve_profile(self, canonical_sender_id: str) -> str:
        return self._map.get(canonical_sender_id, self._default)

    def resolve_group(self, chat_id: str) -> Optional[str]:
        return self._group_map.get(chat_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_whatsapp_router.py -v`
Expected: PASS for all four tests.

- [ ] **Step 5: Commit**

```bash
git add gateway/whatsapp_router.py tests/gateway/test_whatsapp_router.py
git commit -m "feat(gateway): WhatsAppRouter.resolve_group(chat_id)"
```

---

## Task 7: Dispatch precedence in `_handle_message`

**Files:**
- Modify: `gateway/run.py:4611-4628`
- Test: `tests/gateway/test_handle_message_routing.py`

- [ ] **Step 1: Add a helper to test_handle_message_routing.py for group events**

Add to the top of `tests/gateway/test_handle_message_routing.py` (just below the existing `_whatsapp_event` helper):

```python
def _whatsapp_group_event(
    chat_id: str,
    canonical_sender_id: str,
    text: str = "@bot hi",
) -> MessageEvent:
    src = SessionSource(
        platform=Platform.WHATSAPP,
        chat_id=chat_id,
        chat_type="group",
        user_id=canonical_sender_id,
        user_name="GroupMember",
        message_id="m1",
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=src,
        message_id="m1",
        canonical_sender_id=canonical_sender_id,
    )
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/gateway/test_handle_message_routing.py`:

```python
@pytest.mark.asyncio
async def test_group_mapped_message_dispatches_to_group_target(tmp_path, monkeypatch):
    """A group with a group_profile_map entry routes to that profile."""
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={},
        group_profile_map={"g1@g.us": "family"},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(return_value={"text": "from-family", "error": None})
    mock_mgr.has_worker = MagicMock(return_value=True)
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_group_event("g1@g.us", "60111")
    result = await runner._handle_message(event)

    assert result is None
    mock_mgr.dispatch.assert_awaited_once()
    assert mock_mgr.dispatch.await_args.args[0] == "family"


@pytest.mark.asyncio
async def test_group_binding_beats_sender_mapping(tmp_path, monkeypatch):
    """When a group is mapped, sender_profile_map is ignored even if it would match."""
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    test_home = tmp_path / "profiles" / "test_profile"
    for d in (main_home, family_home, test_home):
        d.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family", "test_profile"),
        default_profile="main",
        sender_profile_map={"60123": "family"},
        group_profile_map={"g1@g.us": "test_profile"},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(return_value={"text": "from-test", "error": None})
    mock_mgr.has_worker = MagicMock(return_value=True)
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    # sender 60123 is mapped to family, but group g1 is mapped to test_profile.
    event = _whatsapp_group_event("g1@g.us", "60123")
    await runner._handle_message(event)

    mock_mgr.dispatch.assert_awaited_once()
    assert mock_mgr.dispatch.await_args.args[0] == "test_profile"


@pytest.mark.asyncio
async def test_group_mapped_drops_when_worker_unavailable(tmp_path, monkeypatch, caplog):
    """If the bound worker is missing, the message is dropped — no fallback."""
    main_home = tmp_path / "profiles" / "main"
    main_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60123": "main"},  # would have matched if we fell through
        group_profile_map={"g1@g.us": "family"},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock()
    mock_mgr.has_worker = MagicMock(return_value=False)  # family worker missing
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_group_event("g1@g.us", "60123")
    with caplog.at_level("ERROR"):
        result = await runner._handle_message(event)

    assert result is None
    mock_mgr.dispatch.assert_not_awaited()           # no dispatch
    mock_wa_adapter.send.assert_not_awaited()        # no reply
    assert any(
        "group_routing" in rec.message and "worker_unavailable" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_unmapped_group_falls_through_to_sender_routing(tmp_path, monkeypatch):
    """A group not in group_profile_map uses the existing sender path."""
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60123": "family"},
        group_profile_map={"g1@g.us": "family"},  # different group than the event
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(return_value={"text": "ok", "error": None})
    mock_mgr.has_worker = MagicMock(return_value=True)
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_group_event("g2@g.us", "60123")  # g2 not mapped, sender is
    await runner._handle_message(event)

    mock_mgr.dispatch.assert_awaited_once()
    assert mock_mgr.dispatch.await_args.args[0] == "family"


@pytest.mark.asyncio
async def test_dm_ignores_group_profile_map(tmp_path, monkeypatch):
    """DMs never consult group_profile_map even if the chat_id string would match."""
    main_home = tmp_path / "profiles" / "main"
    family_home = tmp_path / "profiles" / "family"
    main_home.mkdir(parents=True)
    family_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(main_home))

    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    cfg = GatewayConfig()
    cfg.whatsapp_profile_routing = ProfileRoutingConfig(
        profiles=("main", "family"),
        default_profile="main",
        sender_profile_map={"60123": "family"},
        # Pathological: group_profile_map keyed on the same string as the DM chat_id.
        # The dispatcher must still treat this as a DM and use sender routing.
        group_profile_map={"60123@s.whatsapp.net": "main"},
    )
    runner = GatewayRunner(cfg)

    mock_mgr = MagicMock()
    mock_mgr.dispatch = AsyncMock(return_value={"text": "ok", "error": None})
    mock_mgr.has_worker = MagicMock(return_value=True)
    runner.profile_worker_manager = mock_mgr

    mock_wa_adapter = MagicMock()
    mock_wa_adapter.send = AsyncMock()
    runner.adapters = {Platform.WHATSAPP: mock_wa_adapter}

    event = _whatsapp_event("60123", "hi")  # DM; chat_type="dm"
    await runner._handle_message(event)

    mock_mgr.dispatch.assert_awaited_once()
    # Sender routing wins — "family", not "main" from the spurious group entry.
    assert mock_mgr.dispatch.await_args.args[0] == "family"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/gateway/test_handle_message_routing.py -v -k "group_"`
Expected: FAIL — `test_group_mapped_message_dispatches_to_group_target` and `test_group_binding_beats_sender_mapping` fail because the dispatcher doesn't consult `resolve_group` yet (it falls through to sender routing or default). `test_group_mapped_drops_when_worker_unavailable` fails because nothing checks `has_worker`. `test_unmapped_group_falls_through_to_sender_routing` and `test_dm_ignores_group_profile_map` may already pass since they exercise the existing sender path — confirm they continue to pass after the implementation.

- [ ] **Step 4: Modify the dispatch decision in `gateway/run.py`**

In `gateway/run.py`, find the existing routing block in `_handle_message`:

```python
        # ── WhatsApp sender-based profile routing ──────────────────────
        # Inbound WhatsApp messages whose canonical sender id maps to a
        # non-primary profile are forwarded to that profile's worker
        # subprocess; the worker's reply is delivered back through the
        # in-process WhatsApp adapter.  Unmapped senders fall through to
        # the in-process pipeline below (default_profile == primary).
        if (
            source is not None
            and source.platform == Platform.WHATSAPP
            and self.whatsapp_router is not None
            and self.profile_worker_manager is not None
        ):
            target_profile = self.whatsapp_router.resolve_profile(
                event.canonical_sender_id or ""
            )
            if target_profile != self.primary_profile_name:
                return await self._dispatch_to_worker(target_profile, event)
```

Replace it with:

```python
        # ── WhatsApp profile routing ───────────────────────────────────
        # Group-based routing takes precedence and is *exclusive*: a chat
        # listed in group_profile_map binds to its target profile only —
        # we never fall back to sender routing or default_profile from a
        # bound group.  Sender-based routing (the existing path) handles
        # DMs and unmapped groups.
        if (
            source is not None
            and source.platform == Platform.WHATSAPP
            and self.whatsapp_router is not None
            and self.profile_worker_manager is not None
        ):
            group_target: Optional[str] = None
            if source.chat_type == "group" and source.chat_id:
                group_target = self.whatsapp_router.resolve_group(source.chat_id)

            if group_target is not None:
                # Group is exclusively bound — handle and return without
                # consulting sender routing.
                if group_target == self.primary_profile_name:
                    # Bound to primary; fall through to in-process pipeline below.
                    pass
                elif self.profile_worker_manager.has_worker(group_target):
                    return await self._dispatch_to_worker(group_target, event)
                else:
                    logger.error(
                        "group_routing: chat=%s target=%s worker_unavailable; "
                        "dropping message",
                        source.chat_id,
                        group_target,
                    )
                    return None
            else:
                target_profile = self.whatsapp_router.resolve_profile(
                    event.canonical_sender_id or ""
                )
                if target_profile != self.primary_profile_name:
                    return await self._dispatch_to_worker(target_profile, event)
```

Verify `Optional` is already imported at the top of `gateway/run.py`. If `from typing import ... Optional` is not already present, add it. (Check with `grep -n "^from typing" gateway/run.py`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/gateway/test_handle_message_routing.py -v`
Expected: PASS for all 5 new tests, all 3 existing tests still pass.

- [ ] **Step 6: Run the broader gateway routing test suite**

Run: `pytest tests/gateway/test_handle_message_routing.py tests/gateway/test_profile_routing_config.py tests/gateway/test_gateway_routing_boot.py tests/gateway/test_routing_backcompat.py tests/gateway/test_whatsapp_router.py tests/gateway/test_profile_worker_manager.py -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add gateway/run.py tests/gateway/test_handle_message_routing.py
git commit -m "feat(gateway): group-based WhatsApp profile routing with fail-closed dispatch"
```

---

## Task 8: User docs

**Files:**
- Modify: `docs/whatsapp_profile_routing.md`

- [ ] **Step 1: Append a "Group routing" section**

Open `docs/whatsapp_profile_routing.md`. After the existing "Sender identifier format" section (and any subsequent existing sections, but before any "Limitations" / "Troubleshooting" trailing sections — append at end if no trailing sections exist), add:

```markdown
## Group routing (chat-id based)

In addition to `sender_profile_map`, you can bind a specific WhatsApp
group to a profile via `group_profile_map`. Every message in that group
that survives the inbound mention/free-response gating is handled by the
bound profile, regardless of who sent it.

```yaml
whatsapp:
  profile_routing:
    profiles: ["default", "main", "family"]
    default_profile: "default"
    sender_profile_map:
      "60123456789": "main"
    group_profile_map:
      "120363409860032836@g.us": "family"
```

### Precedence

For a given inbound message:

1. If the message is in a group AND the group's `chat_id` is in
   `group_profile_map`, route to that profile.
2. Otherwise consult `sender_profile_map`.
3. Otherwise route to `default_profile`.

Group binding is **exclusive**: when a group is mapped, the bound profile
is the only legitimate target. If that profile's worker is unavailable
at dispatch time, the message is dropped and an `ERROR` line of the form
`group_routing: chat=<jid> target=<profile> worker_unavailable; dropping
message` is logged. There is no fallback to `sender_profile_map` or
`default_profile` for a bound group — silently degrading would re-create
the security regression the routing feature exists to prevent.

### Group `chat_id` format

`group_profile_map` keys are matched **verbatim** against the bridge's
`chat_id` for inbound messages. Standard WhatsApp groups use the
`<id>@g.us` suffix; community/LID-only groups may use `<id>@lid`. There
is no canonicalisation — copy the JID exactly as it appears in your
gateway logs (`inbound message: ... chat=<jid>`).

### Validation

`group_profile_map` is validated at boot. A target profile that is not
in `profiles`, a non-string key/value, or an empty key all raise
`ProfileRoutingConfigError` and abort gateway start — same fail-closed
posture as `sender_profile_map`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/whatsapp_profile_routing.md
git commit -m "docs: WhatsApp group_profile_map routing"
```

---

## Final verification

- [ ] **Step 1: Run the full gateway test suite**

Run: `pytest tests/gateway/ -q --ignore=tests/gateway/test_dingtalk.py --ignore=tests/gateway/test_approve_deny_commands.py 2>&1 | tail -20`

(`test_dingtalk.py` and `test_approve_deny_commands.py` have pre-existing failures on `main` unrelated to this work — verified by stashing all changes during the previous fix and re-running. They are not introduced or affected by this plan.)

Expected: all routing tests pass; total failures should be ≤ the baseline pre-existing count.

- [ ] **Step 2: Verify the commit graph**

Run: `git log --oneline -10`
Expected: 8 new commits (one per task), all on top of the spec commit `cd989b269`.

- [ ] **Step 3: Push**

```bash
git push
```

This will deploy on the user's normal pipeline.

---

## Self-review notes

**Spec coverage:** every section of `docs/superpowers/specs/2026-05-10-whatsapp-group-profile-routing-design.md` is mapped:

- §1 Goal / §4 Configuration → Tasks 1, 2 (schema + parser).
- §2 Acceptance criteria 1, 7 (group binds, group beats sender) → Task 7 dispatch tests.
- §2 ACs 2, 6 (no entry → existing path; backward compat) → Task 7 fallthrough test, Task 1 default-empty test.
- §2 AC 3 (DMs ignore) → Task 7 DM-ignores test.
- §2 AC 4 (boot fail-closed on bad target) → Task 4.
- §2 AC 5 (drop on worker unavailable) → Task 7 drop-on-unavailable test.
- §2 AC 8 (gating unchanged) → not modified, no test needed; verified by inspection in Task 7 (we don't touch `_should_process_message`).
- §6 Validation rules → Task 3.
- §7 Error handling → Tasks 4 (boot) + 7 (runtime).
- §8 Testing → Tasks 1–7 each have corresponding tests.
- §9 Backward compat → Task 1 default-empty test, Task 2 optional-block test.
- §11 Implementation steps → mirrored 1:1 in Tasks 1–7.

**Type/name consistency:** `resolve_group` (Task 6) is the same name used in Task 7's dispatch code. `has_worker` (Task 5) is the same name used in Task 7's tests and dispatch code. `group_profile_map` is the field name used uniformly. `ProfileRoutingConfigError` is the only exception class referenced.

**Placeholders:** none.
