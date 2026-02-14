from src.core.config import Config


def test_mcp_forward_env_collects_ranking_vars(monkeypatch):
    keys = {
        "LILITH_SCORE_CALIBRATION_PATH": "/tmp/calib.json",
        "LILITH_SCORE_WINDOW_SIZE": "777",
        "LILITH_SCORE_DRIFT_Z": "2.1",
        "LILITH_SCORE_RECENCY_HALF_LIFE_DAYS": "90",
        "LILITH_ENABLE_LEARNED_RANKING": "true",
        "LILITH_SOURCE_RELIABILITY_PRIORS": '{"email":1.0}',
    }
    for key, value in keys.items():
        monkeypatch.setenv(key, value)

    cfg = Config.load()
    for key, value in keys.items():
        assert cfg.mcp_forward_env[key] == value


def test_mcp_forward_env_omits_empty_values(monkeypatch):
    monkeypatch.setenv("LILITH_SCORE_WINDOW_SIZE", "")
    monkeypatch.delenv("LILITH_SCORE_DRIFT_Z", raising=False)
    cfg = Config.load()
    assert "LILITH_SCORE_WINDOW_SIZE" not in cfg.mcp_forward_env
    assert "LILITH_SCORE_DRIFT_Z" not in cfg.mcp_forward_env
