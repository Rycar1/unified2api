from core.converter import apply_model_defaults


def test_v41_flash_defaults_to_high_reasoning():
    body = {"model": "deepseek-v4.1-flash", "messages": []}
    assert apply_model_defaults(body) is body
    assert body["reasoning_effort"] == "high"


def test_explicit_reasoning_effort_is_preserved():
    body = {"model": "deepseek-v4.1-flash", "reasoning_effort": "low"}
    apply_model_defaults(body)
    assert body["reasoning_effort"] == "low"


def test_other_models_are_unchanged():
    body = {"model": "deepseek-v4-flash"}
    apply_model_defaults(body)
    assert "reasoning_effort" not in body
