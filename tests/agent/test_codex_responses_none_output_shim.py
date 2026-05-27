"""Tests for the OpenAI SDK ``response.output is None`` shim installed by
``agent.codex_responses_adapter``.

Regression guard: the Codex Responses backend at chatgpt.com/backend-api/codex
emits stream events whose ``response.output`` is ``None``; the unpatched
OpenAI Python SDK iterates that field directly and raises
``TypeError: 'NoneType' object is not iterable``, which Hermes' classifier
then misroutes as a non-retryable client error.
"""

from types import SimpleNamespace

import pytest


def test_shim_is_installed_on_import() -> None:
    # Import for side-effect installation, then verify the marker.
    import agent.codex_responses_adapter  # noqa: F401
    from openai.lib._parsing import _responses as sdk_parsing

    assert getattr(sdk_parsing, "_hermes_none_output_shim_installed", False) is True


def test_shim_coerces_none_output_to_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.codex_responses_adapter  # noqa: F401
    from openai.lib._parsing import _responses as sdk_parsing

    seen = {}

    def _fake_inner(*args, **kwargs):
        # Record what the SDK sees after our wrapper has run.
        seen["response_output"] = kwargs["response"].output
        return "ok"

    # Replace the wrapped inner so we don't depend on a real Response model.
    monkeypatch.setattr(sdk_parsing.parse_response, "__wrapped__", None, raising=False)
    # Reach the closure variable by re-installing with our fake inner.
    # Easiest: monkey-patch the symbol the wrapper closes over indirectly by
    # replacing parse_response and re-running install.
    sdk_parsing._hermes_none_output_shim_installed = False
    sdk_parsing.parse_response = _fake_inner
    from agent.codex_responses_adapter import _install_openai_responses_none_output_shim

    _install_openai_responses_none_output_shim()

    response = SimpleNamespace(output=None)
    result = sdk_parsing.parse_response(response=response)

    assert result == "ok"
    assert seen["response_output"] == []
    # The original object was mutated in place.
    assert response.output == []


def test_shim_leaves_non_none_output_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent.codex_responses_adapter  # noqa: F401
    from openai.lib._parsing import _responses as sdk_parsing

    def _fake_inner(*args, **kwargs):
        return kwargs["response"].output

    sdk_parsing._hermes_none_output_shim_installed = False
    sdk_parsing.parse_response = _fake_inner
    from agent.codex_responses_adapter import _install_openai_responses_none_output_shim

    _install_openai_responses_none_output_shim()

    sentinel = [SimpleNamespace(type="message")]
    response = SimpleNamespace(output=sentinel)
    result = sdk_parsing.parse_response(response=response)

    # Wrapper did not replace a non-None output.
    assert result is sentinel
    assert response.output is sentinel


def test_shim_is_idempotent() -> None:
    import agent.codex_responses_adapter  # noqa: F401
    from openai.lib._parsing import _responses as sdk_parsing
    from agent.codex_responses_adapter import _install_openai_responses_none_output_shim

    before = sdk_parsing.parse_response
    _install_openai_responses_none_output_shim()
    _install_openai_responses_none_output_shim()
    after = sdk_parsing.parse_response

    assert before is after
