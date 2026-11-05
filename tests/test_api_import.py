def test_fastapi_module_imports_cleanly():
    from triconsensus.api import app

    assert app.title == "TriConsensus AI API"
