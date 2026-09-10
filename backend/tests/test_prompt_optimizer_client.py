from backend.app.integrations.prompt_optimizer_client import (
    PROMPT_OPTIMIZER_SYSTEM_PROMPT,
    _build_user_prompt,
)


def test_prompt_optimizer_system_prompt_uses_selected_image_model():
    assert 'image model specified in the request context' in PROMPT_OPTIMIZER_SYSTEM_PROMPT
    assert '"gpt-image-2"' not in PROMPT_OPTIMIZER_SYSTEM_PROMPT
    assert "Follow the user's original intent" in PROMPT_OPTIMIZER_SYSTEM_PROMPT


def test_build_user_prompt_maps_same_language_to_user_input_language():
    built = _build_user_prompt(
        "tiny robot making coffee",
        target_language="same",
        image_api_path="/v1/responses",
        image_model="gpt-image-2",
        size="1024x1024",
        quality="high",
    )

    assert 'Target language: same as user\'s input language' in built
    assert "User image idea:\ntiny robot making coffee" in built


def test_build_user_prompt_includes_structured_intent():
    built = _build_user_prompt(
        "tiny robot making coffee",
        intent="make it rainy at dusk",
        target_language="zh-CN",
        image_api_path="/v1/responses",
        image_model="gpt-image-2",
        size="1024x1024",
        quality="high",
    )

    assert "Original prompt:\ntiny robot making coffee" in built
    assert "Modification intent:\nmake it rainy at dusk" in built
    assert "Return only the revised prompt." in built
