# -*- coding: utf-8 -*-
from src.config import Config


def test_data_quality_defaults():
    cfg = Config()
    assert cfg.data_quality_reconciliation_enabled is True
    assert cfg.data_quality_price_diff_threshold_pct == 1.0
    assert cfg.data_quality_date_mismatch_tolerance_seconds == 3600
