from app.api import model_settings
from app.config import settings


def test_llm_profiles_can_be_created_and_switched_without_returning_keys(monkeypatch):
    monkeypatch.setattr(model_settings, "_profiles", {}, raising=False)
    monkeypatch.setattr(model_settings, "_active_profile_id", "", raising=False)
    monkeypatch.setattr(settings, "llm_api_key", "", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", "https://api.deepseek.com", raising=False)
    monkeypatch.setattr(settings, "llm_model", "deepseek-chat", raising=False)

    first = model_settings.update_llm_settings(
        model_settings.LlmSettingsPayload(
            profileName="DeepSeek 测试",
            baseUrl="https://api.deepseek.com",
            model="deepseek-chat",
            apiKey="secret-deepseek",
            activate=True,
        )
    )
    assert first["configured"] is True
    assert first["model"] == "deepseek-chat"
    assert "secret-deepseek" not in str(first)
    deepseek_id = first["activeProfileId"]

    second = model_settings.update_llm_settings(
        model_settings.LlmSettingsPayload(
            profileName="Qwen 免费测试",
            baseUrl="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen-plus",
            apiKey="secret-qwen",
            activate=True,
        )
    )
    assert second["model"] == "qwen-plus"
    qwen_id = second["activeProfileId"]
    assert qwen_id != deepseek_id
    assert len(second["profiles"]) == 2

    switched = model_settings.update_llm_settings(
        model_settings.LlmSettingsPayload(profileId=deepseek_id, activate=True)
    )
    assert switched["activeProfileId"] == deepseek_id
    assert switched["model"] == "deepseek-chat"
    assert settings.llm_api_key == "secret-deepseek"
    assert all("apiKey" not in profile for profile in switched["profiles"])
