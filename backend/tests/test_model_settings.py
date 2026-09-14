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


def test_multiple_keys_require_an_explicit_manual_selection(monkeypatch):
    monkeypatch.setattr(model_settings, "_profiles", {}, raising=False)
    monkeypatch.setattr(model_settings, "_active_profile_id", "", raising=False)
    monkeypatch.setattr(settings, "llm_api_key", "", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", "https://provider.example/v1", raising=False)
    monkeypatch.setattr(settings, "llm_model", "model-a", raising=False)

    created = model_settings.update_llm_settings(model_settings.LlmSettingsPayload(
        profileName="共享服务", baseUrl="https://provider.example/v1", model="model-a",
        apiKeys=["secret-one", "secret-two"], activate=True,
    ))
    profile = created["profiles"][0]
    assert profile["keyCount"] == 2
    assert sum(bool(item["active"]) for item in profile["keys"]) == 1
    assert "secret-one" not in str(created) and "secret-two" not in str(created)
    assert settings.llm_api_key == "secret-one"

    second_id = profile["keys"][1]["id"]
    switched = model_settings.update_llm_settings(model_settings.LlmSettingsPayload(
        profileId=profile["id"], activeKeyId=second_id, activate=True,
    ))
    assert switched["activeKeyId"] == second_id
    assert settings.llm_api_key == "secret-two"


def test_model_discovery_uses_the_manually_selected_key(monkeypatch):
    monkeypatch.setattr(model_settings, "_profiles", {
        "p1": {
            "id": "p1", "name": "P1", "baseUrl": "https://provider.example/v1", "model": "a",
            "apiKeys": ["key-one", "key-two"], "activeKeyId": model_settings._key_id("key-one"),
        },
    }, raising=False)
    monkeypatch.setattr(model_settings, "_active_profile_id", "p1", raising=False)
    captured = {}

    def fake_discover(base_url, api_key, protocol, auth_mode):
        captured.update(base_url=base_url, api_key=api_key, protocol=protocol, auth_mode=auth_mode)
        return ["model-a", "model-b"]

    monkeypatch.setattr(model_settings, "_discover_models", fake_discover)
    result = model_settings.discover_llm_models(model_settings.ModelDiscoveryPayload(
        baseUrl="https://provider.example/v1", profileId="p1",
        activeKeyId=model_settings._key_id("key-two"),
    ))
    assert result["models"] == ["model-a", "model-b"]
    assert captured == {
        "base_url": "https://provider.example/v1", "api_key": "key-two",
        "protocol": "openai_compatible", "auth_mode": "auto",
    }


def test_runtime_profile_is_resolved_by_id_without_exposing_key(monkeypatch):
    monkeypatch.setattr(model_settings, "_profiles", {
        "deepseek": {
            "id": "deepseek", "name": "DeepSeek", "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-chat", "apiKeys": ["secret-deepseek"],
            "activeKeyId": model_settings._key_id("secret-deepseek"),
        },
        "qwen": {
            "id": "qwen", "name": "Qwen", "baseUrl": "https://provider.example/v1",
            "model": "qwen-plus", "apiKeys": ["secret-qwen"],
            "activeKeyId": model_settings._key_id("secret-qwen"),
        },
    }, raising=False)
    monkeypatch.setattr(model_settings, "_active_profile_id", "deepseek", raising=False)

    runtime = model_settings.resolve_runtime_profile("qwen")
    public = model_settings.get_llm_settings()

    assert runtime == {
        "id": "qwen", "name": "Qwen", "protocol": "openai_compatible",
        "authMode": "auto",
        "baseUrl": "https://provider.example/v1", "model": "qwen-plus", "apiKey": "secret-qwen",
    }
    assert "secret-qwen" not in str(public)


def test_anthropic_profile_preserves_protocol_and_auth_mode(monkeypatch):
    monkeypatch.setattr(model_settings, "_profiles", {}, raising=False)
    monkeypatch.setattr(model_settings, "_active_profile_id", "", raising=False)
    monkeypatch.setattr(settings, "llm_api_key", "", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", "https://api.deepseek.com", raising=False)
    monkeypatch.setattr(settings, "llm_model", "deepseek-chat", raising=False)

    created = model_settings.update_llm_settings(model_settings.LlmSettingsPayload(
        profileName="Claude 生产",
        protocol="anthropic_messages",
        authMode="x_api_key",
        baseUrl="https://api.anthropic.com",
        model="claude-sonnet-test",
        apiKey="secret-anthropic",
        activate=True,
    ))
    profile_id = created["activeProfileId"]
    public_profile = created["profiles"][0]
    runtime = model_settings.resolve_runtime_profile(profile_id)

    assert public_profile["protocol"] == "anthropic_messages"
    assert public_profile["authMode"] == "x_api_key"
    assert runtime["protocol"] == "anthropic_messages"
    assert runtime["authMode"] == "x_api_key"
    assert runtime["apiKey"] == "secret-anthropic"
    assert settings.llm_protocol == "anthropic_messages"
    assert settings.llm_auth_mode == "x_api_key"


def test_anthropic_model_discovery_uses_native_endpoint_and_headers():
    assert model_settings._discovery_endpoint("https://api.anthropic.com", "anthropic_messages") == "https://api.anthropic.com/v1/models"
    headers = model_settings._discovery_headers("secret", "anthropic_messages", "auto")
    assert headers["x-api-key"] == "secret"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers
