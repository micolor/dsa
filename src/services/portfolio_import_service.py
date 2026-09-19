# -*- coding: utf-8 -*-
"""Portfolio CSV import service with extensible parser registry."""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from data_provider.base import canonical_stock_code
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_service import (
    PortfolioBusyError,
    PortfolioConflictError,
    PortfolioOversellError,
    PortfolioService,
)

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class CsvParserSpec:
    """CSV parser specification for one broker."""

    broker: str
    aliases: Tuple[str, ...]
    display_name: str
    column_hints: Dict[str, Tuple[str, ...]]


DEFAULT_PARSER_SPECS: Tuple[CsvParserSpec, ...] = (
    CsvParserSpec(
        broker="huatai",
        aliases=(),
        display_name="华泰",
        column_hints={
            "trade_date": ("成交日期", "成交时间", "发生日期", "日期"),
            "symbol": ("证券代码", "股票代码", "代码"),
            "side": ("买卖标志", "买卖方向", "操作"),
            "quantity": ("成交数量", "数量", "成交股数"),
            "price": ("成交均价", "成交价格", "价格", "成交价", "均价"),
            "trade_uid": ("成交编号", "成交序号", "流水号"),
        },
    ),
    CsvParserSpec(
        broker="citic",
        aliases=("zhongxin",),
        display_name="中信",
        column_hints={
            "trade_date": ("发生日期", "成交日期", "日期"),
            "symbol": ("证券代码", "股票代码", "代码"),
            "side": ("买卖方向", "买卖标志", "业务名称"),
            "quantity": ("成交数量", "数量", "成交股数"),
            "price": ("成交价格", "成交均价", "价格", "成交价"),
            "trade_uid": ("合同编号", "成交编号", "委托编号"),
        },
    ),
    CsvParserSpec(
        broker="cmb",
        aliases=("zhaoshang", "cmbchina"),
        display_name="招商",
        column_hints={
            "trade_date": ("日期", "成交日期", "发生日期"),
            "symbol": ("证券代码", "股票代码", "代码"),
            "side": ("交易方向", "买卖方向", "买卖标志"),
            "quantity": ("成交股数", "成交数量", "数量"),
            "price": ("成交价", "成交价格", "成交均价", "均价"),
            "trade_uid": ("流水号", "成交编号", "成交序号"),
        },
    ),
)


class PortfolioImportService:
    """Parse broker CSV and commit normalized trade records with dedup."""
    _shared_parser_registry: Dict[str, CsvParserSpec] = {}
    _shared_broker_alias_map: Dict[str, str] = {}
    _shared_registry_initialized: bool = False

    def __init__(
        self,
        *,
        portfolio_service: Optional[PortfolioService] = None,
        repo: Optional[PortfolioRepository] = None,
    ):
        self.portfolio_service = portfolio_service or PortfolioService()
        self.repo = repo or PortfolioRepository()
        self._parser_registry = self.__class__._shared_parser_registry
        self._broker_alias_map = self.__class__._shared_broker_alias_map
        if not self.__class__._shared_registry_initialized:
            self._init_default_parsers()
            self.__class__._shared_registry_initialized = True

    def _init_default_parsers(self) -> None:
        for spec in DEFAULT_PARSER_SPECS:
            self.register_parser(spec)

    def register_parser(self, spec: CsvParserSpec) -> None:
        """Register or replace one broker parser spec."""
        broker = (spec.broker or "").strip().lower()
        if not broker:
            raise ValueError("broker is required")
        new_aliases = tuple(sorted({alias.strip().lower() for alias in spec.aliases if alias}))
        for alias in new_aliases:
            if alias == broker:
                raise ValueError(f"alias '{alias}' cannot be the same as broker id")
            existing_target = self._broker_alias_map.get(alias)
            if existing_target and existing_target != broker:
                raise ValueError(
                    f"alias '{alias}' already registered by broker '{existing_target}'"
                )
        for alias, target in list(self._broker_alias_map.items()):
            if target == broker and alias not in new_aliases:
                self._broker_alias_map.pop(alias, None)
        self._parser_registry[broker] = CsvParserSpec(
            broker=broker,
            aliases=new_aliases,
            display_name=spec.display_name or broker,
            column_hints=dict(spec.column_hints or {}),
        )
        for alias in self._parser_registry[broker].aliases:
            self._broker_alias_map[alias] = broker

    def list_supported_brokers(self) -> List[Dict[str, Any]]:
        """List canonical broker ids and aliases for frontend selector."""
        items: List[Dict[str, Any]] = []
        for broker in sorted(self._parser_registry.keys()):
            aliases = sorted(alias for alias, target in self._broker_alias_map.items() if target == broker)
            items.append(
                {
                    "broker": broker,
                    "aliases": aliases,
                    "display_name": self._parser_registry[broker].display_name,
                }
            )
        return items

    def parse_trade_csv(
        self,
        *,
        broker: str,
        content: bytes,
    ) -> Dict[str, Any]:
        broker_norm = self._normalize_broker(broker)
        parser_spec = self._parser_registry[broker_norm]
        df = self._read_csv(content)

        records: List[Dict[str, Any]] = []
        skipped = 0
        errors: List[str] = []
        content_occurrences: Dict[str, int] = {}

        for idx, row in df.iterrows():
            normalized = self._normalize_trade_row(row=row, parser_spec=parser_spec)
            if normalized is None:
                skipped += 1
                continue
            try:
                # 区分因子是「该内容在本文件内第几次出现」，不是绝对行号。行号对
                # 「同一文件原样重导」稳定，但券商重新导出必然改变行位置（顶部多一行
                # 查询区间说明、删掉一行已撤销成交、按日期重排、只导出其中一段），
                # 整份文件的 hash 会一起变，`has_trade_dedup_hash` 判不出重复，同一批
                # 成交被重复入账。序号对上述所有重排稳定，且仍能区分同字段的合法分笔。
                normalized["_source_line_number"] = int(idx) + 2
                content_key = self._dedup_content_key(normalized)
                ordinal = content_occurrences.get(content_key, 0) + 1
                content_occurrences[content_key] = ordinal
                normalized["_duplicate_ordinal"] = ordinal
                normalized["dedup_hash"] = self._build_dedup_hash(normalized)
                records.append(normalized)
            except Exception as exc:  # pragma: no cover - defensive path
                skipped += 1
                errors.append(f"row={idx + 1}: {exc}")

        return {
            "broker": broker_norm,
            "record_count": len(records),
            "skipped_count": skipped,
            "error_count": len(errors),
            "records": records,
            "errors": errors[:20],
        }

    def commit_trade_records(
        self,
        *,
        account_id: int,
        broker: str,
        records: List[Dict[str, Any]],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        broker_norm = self._normalize_broker(broker)

        inserted_count = 0
        duplicate_count = 0
        failed_count = 0
        errors: List[str] = []
        seen_trade_uids: set[str] = set()
        seen_dedup_hashes: set[str] = set()

        for i, record in enumerate(records):
            try:
                trade_uid = (record.get("trade_uid") or "").strip() or None
                dedup_hash = (record.get("dedup_hash") or "").strip()
                if not dedup_hash:
                    dedup_hash = self._build_dedup_hash(record)

                if trade_uid and self.repo.has_trade_uid(account_id, trade_uid):
                    duplicate_count += 1
                    continue
                dedup_hash_to_use: Optional[str] = dedup_hash or None
                if dedup_hash_to_use and self.repo.has_trade_dedup_hash(account_id, dedup_hash_to_use):
                    duplicate_count += 1
                    continue

                if dry_run:
                    if trade_uid and trade_uid in seen_trade_uids:
                        duplicate_count += 1
                        continue
                    if dedup_hash_to_use and dedup_hash_to_use in seen_dedup_hashes:
                        duplicate_count += 1
                        continue
                    inserted_count += 1
                    if trade_uid:
                        seen_trade_uids.add(trade_uid)
                    if dedup_hash_to_use:
                        seen_dedup_hashes.add(dedup_hash_to_use)
                    continue

                trade_date_value = record.get("trade_date")
                if isinstance(trade_date_value, date):
                    trade_date_obj = trade_date_value
                else:
                    trade_date_obj = date.fromisoformat(str(trade_date_value))

                self.portfolio_service.record_trade(
                    account_id=account_id,
                    symbol=str(record["symbol"]),
                    trade_date=trade_date_obj,
                    side=str(record["side"]),
                    quantity=float(record["quantity"]),
                    price=float(record["price"]),
                    fee=float(record.get("fee", 0.0) or 0.0),
                    tax=float(record.get("tax", 0.0) or 0.0),
                    market=record.get("market"),
                    currency=record.get("currency"),
                    trade_uid=trade_uid,
                    dedup_hash=dedup_hash_to_use,
                    note=(record.get("note") or "").strip() or f"csv_import:{broker_norm}",
                )
                inserted_count += 1
            except PortfolioConflictError:
                duplicate_count += 1
            except PortfolioOversellError as exc:
                failed_count += 1
                errors.append(f"idx={i}: {exc}")
            except PortfolioBusyError as exc:
                failed_count += 1
                errors.append(f"idx={i}: portfolio_busy: {exc}")
            except Exception as exc:
                failed_count += 1
                errors.append(f"idx={i}: {exc}")

        return {
            "account_id": account_id,
            "record_count": len(records),
            "inserted_count": inserted_count,
            "duplicate_count": duplicate_count,
            "failed_count": failed_count,
            "dry_run": bool(dry_run),
            "errors": errors[:20],
        }

    def _normalize_broker(self, value: str) -> str:
        broker = (value or "").strip().lower()
        broker = self._broker_alias_map.get(broker, broker)
        if broker not in self._parser_registry:
            supported = ", ".join(sorted(self._parser_registry.keys()))
            raise ValueError(f"broker must be one of: {supported}")
        return broker

    @staticmethod
    def _read_csv(content: bytes) -> pd.DataFrame:
        for encoding in ("utf-8-sig", "gbk", "gb18030"):
            try:
                return pd.read_csv(
                    io.BytesIO(content),
                    encoding=encoding,
                    dtype=str,
                    keep_default_na=False,
                )
            except UnicodeDecodeError:
                continue
        return pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)

    def _normalize_trade_row(
        self,
        *,
        row: Any,
        parser_spec: CsvParserSpec,
    ) -> Optional[Dict[str, Any]]:
        broker_hints = parser_spec.column_hints

        trade_date_raw = self._pick(
            row,
            *(broker_hints.get("trade_date") or ()),
            "成交日期",
            "发生日期",
            "日期",
            "成交时间",
        )
        trade_date_obj = self._parse_date(trade_date_raw)
        if trade_date_obj is None:
            return None

        symbol_raw = self._pick(
            row,
            *(broker_hints.get("symbol") or ()),
            "证券代码",
            "股票代码",
            "代码",
        )
        symbol = canonical_stock_code(str(symbol_raw or "").strip())
        if not symbol:
            return None

        side_raw = self._pick(
            row,
            *(broker_hints.get("side") or ()),
            "买卖标志",
            "买卖方向",
            "交易方向",
            "业务名称",
            "操作",
        )
        side = self._normalize_side(side_raw)
        if side is None:
            return None

        quantity = self._parse_float(
            self._pick(row, *(broker_hints.get("quantity") or ()), "成交数量", "数量", "成交股数")
        )
        price = self._parse_float(
            self._pick(row, *(broker_hints.get("price") or ()), "成交均价", "成交价格", "价格", "成交价", "均价")
        )
        if quantity is None or quantity <= 0 or price is None or price <= 0:
            return None

        fee = 0.0
        for col in ("手续费", "佣金", "交易费", "规费", "过户费"):
            value = self._parse_float(self._pick(row, col))
            if value is not None:
                fee += value

        tax = 0.0
        for col in ("印花税", "税费", "其他税费"):
            value = self._parse_float(self._pick(row, col))
            if value is not None:
                tax += value

        trade_uid = self._pick(
            row,
            *(broker_hints.get("trade_uid") or ()),
            "成交编号",
            "成交序号",
            "合同编号",
            "委托编号",
            "流水号",
        )
        currency = self._pick(row, "币种", "货币")

        return {
            "trade_date": trade_date_obj,
            "symbol": symbol,
            "side": side,
            "quantity": float(quantity),
            "price": float(price),
            "fee": float(fee),
            "tax": float(tax),
            "trade_uid": (str(trade_uid).strip() if trade_uid is not None else None) or None,
            "currency": (str(currency).strip().upper() if currency is not None else None) or None,
        }

    @staticmethod
    def _pick(row: Any, *candidates: str) -> Any:
        for name in candidates:
            if name in row.index:
                value = row.get(name)
                if value is not None and str(value).strip() != "" and str(value).strip().lower() != "nan":
                    return value
        return None

    @staticmethod
    def _parse_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text or text.lower() == "nan":
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() == "nan":
            return None
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()

    @staticmethod
    def _normalize_side(value: Any) -> Optional[str]:
        text = str(value or "").strip().lower()
        if not text:
            return None
        compact = text.replace(" ", "")
        buy_exact = {"buy", "b", "买", "买入", "证券买入", "普通买入"}
        sell_exact = {"sell", "s", "卖", "卖出", "证券卖出", "普通卖出"}
        if compact in buy_exact:
            return "buy"
        if compact in sell_exact:
            return "sell"
        if "买入" in compact or compact.startswith("买"):
            return "buy"
        if "卖出" in compact or compact.startswith("卖"):
            return "sell"
        return None

    @staticmethod
    def _dedup_content_key(record: Dict[str, Any]) -> str:
        """成交的业务内容，不含任何位置因子。"""
        return "|".join(
            [
                str(record.get("trade_date") or ""),
                str(record.get("symbol") or ""),
                str(record.get("side") or ""),
                f"{float(record.get('quantity', 0.0)):.8f}",
                f"{float(record.get('price', 0.0)):.8f}",
                f"{float(record.get('fee', 0.0)):.8f}",
                f"{float(record.get('tax', 0.0)):.8f}",
                str(record.get("currency") or ""),
            ]
        )

    @classmethod
    def _build_dedup_hash(cls, record: Dict[str, Any]) -> str:
        # 位置因子用「本文件内第几次出现」：同一文件原样重导、以及重排后的重导
        # 都得到同一个 hash；同字段的合法分笔成交仍不会被折叠成一条。
        occurrence = record.get("_duplicate_ordinal")
        if occurrence is None:
            occurrence = record.get("_source_line_number") or record.get("source_line_number") or ""
        payload = f"{cls._dedup_content_key(record)}|{occurrence}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
