# -*- coding: utf-8 -*-
"""
===================================
股票数据访问层
===================================

职责：
1. 封装股票数据的数据库操作
2. 提供日线数据查询接口
"""

import logging
from datetime import date
from typing import Optional, List, Dict, Any

import pandas as pd
from sqlalchemy import and_, desc, select

from src.storage import DatabaseManager, StockDaily

logger = logging.getLogger(__name__)


class StockRepository:
    """
    股票数据访问层
    
    封装 StockDaily 表的数据库操作
    """
    
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        初始化数据访问层
        
        Args:
            db_manager: 数据库管理器（可选，默认使用单例）
        """
        self.db = db_manager or DatabaseManager.get_instance()
    
    def get_latest(self, code: str, days: int = 2) -> List[StockDaily]:
        """
        获取最近 N 天的数据
        
        Args:
            code: 股票代码
            days: 获取天数
            
        Returns:
            StockDaily 对象列表（按日期降序）
        """
        try:
            return self.db.get_latest_data(code, days)
        except Exception as e:
            logger.error(f"获取最新数据失败: {e}")
            return []
    
    @staticmethod
    def _code_variants(code: str) -> List[str]:
        """构造给定代码的等价存储变体，用于 stock_daily 侧历史形式不一致的匹配。

        stock_daily 同时存裸码（600519）与带前缀/后缀（SH601166、00700.HK）两种形式；
        这里按常见历史形式生成候选，查询侧按日期合并后取行数最多者。
        """
        code = (code or "").strip()
        if not code:
            return []
        candidates = [code]
        upper = code.upper()
        # 裸数字：可能是 A 股 6 位或港股 5 位 → 补 SH/SZ 前缀与 HK 前缀
        if code.isdigit():
            if len(code) == 6:
                candidates += [f"SH{code}", f"SZ{code}"]
            elif len(code) >= 4:
                candidates.append(f"HK{code}")
        if upper.endswith(".HK"):
            digits = upper[:-3]
            if digits.isdigit():
                candidates.append(f"HK{digits.zfill(5)}")
        if upper.startswith("HK") and upper[2:].isdigit():
            candidates.append(upper)
        # 去重保序
        seen, out = set(), []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def get_daily_series(self, code: str, days: int) -> List[StockDaily]:
        """读取已缓存的最近 `days` 天日线，按日期升序返回。

        为兼容 stock_daily 中裸码/前缀码混合的历史存储，用 _code_variants 生成候选，
        以「最新一行日期最新」优先、再按「行数最多」择优，
        避免仅凭单一代码形式 MISS 掉已落库数据。返回最符合条件的变体结果。
        """
        best: List[StockDaily] = []
        best_latest: Optional[date] = None
        best_rows = -1
        for cand in self._code_variants(code):
            try:
                rows = self.db.get_latest_data(cand, days)
            except Exception as e:  # noqa: BLE001
                logger.warning("读取日线缓存失败 %s: %s", cand, e)
                continue
            if not rows:
                continue
            latest = max((r.date for r in rows if r.date is not None), default=None)
            # 以最新日期为准；若相同再按行数多者优先
            if best_rows < 0 or (latest is not None and (best_latest is None or latest > best_latest)) \
                    or (latest == best_latest and len(rows) > best_rows):
                best = rows
                best_latest = latest
                best_rows = len(rows)
        return list(reversed(best))

    def get_range(
        self,
        code: str,
        start_date: date,
        end_date: date
    ) -> List[StockDaily]:
        """
        获取指定日期范围的数据
        
        Args:
            code: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            StockDaily 对象列表
        """
        try:
            return self.db.get_data_range(code, start_date, end_date)
        except Exception as e:
            logger.error(f"获取日期范围数据失败: {e}")
            return []
    
    def save_dataframe(
        self,
        df: pd.DataFrame,
        code: str,
        data_source: str = "Unknown"
    ) -> int:
        """
        保存 DataFrame 到数据库
        
        Args:
            df: 包含日线数据的 DataFrame
            code: 股票代码
            data_source: 数据来源
            
        Returns:
            保存的记录数
        """
        try:
            return self.db.save_daily_data(df, code, data_source)
        except Exception as e:
            logger.error(f"保存日线数据失败: {e}")
            return 0
    
    def has_today_data(self, code: str, target_date: Optional[date] = None) -> bool:
        """
        检查是否有指定日期的数据
        
        Args:
            code: 股票代码
            target_date: 目标日期（默认今天）
            
        Returns:
            是否存在数据
        """
        try:
            return self.db.has_today_data(code, target_date)
        except Exception as e:
            logger.error(f"检查数据存在失败: {e}")
            return False
    
    def get_analysis_context(
        self, 
        code: str, 
        target_date: Optional[date] = None
    ) -> Optional[Dict[str, Any]]:
        """
        获取分析上下文
        
        Args:
            code: 股票代码
            target_date: 目标日期
            
        Returns:
            分析上下文字典
        """
        try:
            return self.db.get_analysis_context(code, target_date)
        except Exception as e:
            logger.error(f"获取分析上下文失败: {e}")
            return None

    def get_start_daily(self, *, code: str, analysis_date: date) -> Optional[StockDaily]:
        """Return StockDaily for analysis_date (preferred) or nearest previous date."""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date <= analysis_date))
                .order_by(desc(StockDaily.date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def get_daily_on_date(self, *, code: str, target_date: date) -> Optional[StockDaily]:
        """Return StockDaily for the exact target_date without trading-day fallback."""
        with self.db.get_session() as session:
            row = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date == target_date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def get_forward_bars(self, *, code: str, analysis_date: date, eval_window_days: int) -> List[StockDaily]:
        """Return forward daily bars after analysis_date, up to eval_window_days."""
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockDaily)
                .where(and_(StockDaily.code == code, StockDaily.date > analysis_date))
                .order_by(StockDaily.date)
                .limit(eval_window_days)
            ).scalars().all()
            return list(rows)
