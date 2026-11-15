"""确认已删除的第三方桌宠与本地模型集成没有重新注册。"""


def test_removed_routes_are_not_in_openapi():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/v1/models" not in paths
    assert "/v1/chat/completions" not in paths
    assert "/api/v1/pet/state" not in paths


def test_removed_provider_module_is_not_importable():
    from pathlib import Path
    import app

    app_dir = Path(app.__file__).parent
    assert not (app_dir / "llm" / "ollama.py").exists()
    assert not (app_dir / "api" / "openai_compat.py").exists()
    assert not (app_dir / "workflows" / "pet.py").exists()
    assert not (app_dir / "schemas" / "pet.py").exists()
