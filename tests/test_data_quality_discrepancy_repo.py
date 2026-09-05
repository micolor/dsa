# -*- coding: utf-8 -*-
"""Repository tests for DataQualityDiscrepancy P1."""

from __future__ import annotations

import os

import pytest

from src.config import Config
from src.repositories.data_quality_discrepancy_repo import DataQualityDiscrepancyRepository
from src.storage import DatabaseManager


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    db_path = tmp_path / "data_quality_discrepancy_repo.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


@pytest.fixture
def repo(isolated_db):
    return DataQualityDiscrepancyRepository(isolated_db)


def _seed(repo):
    repo.record_discrepancy({
        "market": "cn",
        "stock_code": "600519.SH",
        "issue_type": "price_discrepancy",
        "primary_source": "tencent",
        "secondary_source": "akshare_sina",
        "primary_price": 1500.0,
        "secondary_price": 1450.0,
        "price_diff_pct": 3.33,
        "primary_ts": "2026-09-05T10:00:00",
        "secondary_ts": "2026-09-05T10:00:00",
        "detail": "price mismatch",
    })
    repo.record_discrepancy({
        "market": "cn",
        "stock_code": "000001.SZ",
        "issue_type": "date_mismatch",
        "primary_source": "tencent",
        "secondary_source": "akshare_sina",
        "primary_price": None,
        "secondary_price": None,
        "price_diff_pct": None,
        "primary_ts": "2026-09-05T09:30:00",
        "secondary_ts": "2026-09-05T09:00:00",
        "detail": "date mismatch",
    })


def test_record_discrepancy_persists(repo):
    row = repo.record_discrepancy({
        "market": "cn",
        "stock_code": "600519.SH",
        "issue_type": "price_discrepancy",
        "primary_source": "tencent",
        "secondary_source": "akshare_sina",
        "primary_price": 1500.0,
        "secondary_price": 1450.0,
        "price_diff_pct": 3.33,
        "primary_ts": "2026-09-05T10:00:00",
        "secondary_ts": "2026-09-05T10:00:00",
        "detail": "price mismatch",
    })
    assert row.id is not None
    assert row.market == "cn"
    assert row.stock_code == "600519.SH"
    assert row.issue_type == "price_discrepancy"
    assert row.price_diff_pct == 3.33


def test_record_and_list_basic(repo):
    _seed(repo)
    rows, total = repo.list_discrepancies(page=1, page_size=20)
    assert total >= 2
    assert isinstance(rows, list)
    assert len(rows) == total
    assert rows[0].issue_type in {"price_discrepancy", "date_mismatch"}


def test_list_filters_by_issue_type(repo):
    _seed(repo)
    rows, total = repo.list_discrepancies(issue_type="price_discrepancy", page=1, page_size=20)
    assert all(r.issue_type == "price_discrepancy" for r in rows)
    assert total >= 1
    assert total == len(rows)


def test_list_filters_by_market_and_stock(repo):
    _seed(repo)
    rows, total = repo.list_discrepancies(market="cn", stock_code="600519.SH", page=1, page_size=20)
    assert all(r.market == "cn" and r.stock_code == "600519.SH" for r in rows)
    assert total >= 1


def test_list_pagination_ordering_no_duplicates(repo):
    # seed enough distinct rows to exercise >1 page
    for i in range(5):
        repo.record_discrepancy({
            "market": "cn",
            "stock_code": f"60051{i}.SH",
            "issue_type": "price_discrepancy",
            "primary_source": "tencent",
            "secondary_source": "akshare_sina",
            "primary_price": 100.0 + i,
            "secondary_price": 90.0 + i,
            "price_diff_pct": 10.0,
            "primary_ts": "2026-09-05T10:00:00",
            "secondary_ts": "2026-09-05T10:00:00",
            "detail": "price mismatch",
        })
    page1, total = repo.list_discrepancies(page=1, page_size=1)
    page2, _ = repo.list_discrepancies(page=2, page_size=1)
    assert total == 5
    assert len(page1) == 1 and len(page2) == 1
    # 按 created_at desc, id desc 排序：任意两页不应重复同一行
    assert page1[0].id != page2[0].id
