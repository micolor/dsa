# -*- coding: utf-8 -*-
"""
===================================
股票数据服务层
===================================

职责：
1. 封装股票数据获取逻辑
2. 提供实时行情和历史数据接口
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from src.repositories.stock_repo import StockRepository

# DB 快路径新鲜度窗口：取到的最新一条日线距今超过 N 个日历日则视为陈旧，
# 不直接返回（避免 K 线长时间停在旧数据），回退到网络路径再取一次。
_DAILY_CACHE_FRESH_DAYS = 4

logger = logging.getLogger(__name__)


class StockService:
    """
    股票数据服务
    
    封装股票数据获取的业务逻辑
    """
    
    def __init__(self):
        """初始化股票数据服务"""
        self.repo = StockRepository()
    
    def get_realtime_quote(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        获取股票实时行情
        
        Args:
            stock_code: 股票代码
            
        Returns:
            实时行情数据字典
        """
        try:
            # 调用数据获取器获取实时行情
            from data_provider.base import DataFetcherManager
            
            manager = DataFetcherManager()
            quote = manager.get_realtime_quote(stock_code)
            
            if quote is None:
                logger.warning(f"获取 {stock_code} 实时行情失败")
                return None
            
            # UnifiedRealtimeQuote 是 dataclass，使用 getattr 安全访问字段
            # 字段映射: UnifiedRealtimeQuote -> API 响应
            # - code -> stock_code
            # - name -> stock_name
            # - price -> current_price
            # - change_amount -> change
            # - change_pct -> change_percent
            # - open_price -> open
            # - high -> high
            # - low -> low
            # - pre_close -> prev_close
            # - volume -> volume
            # - amount -> amount
            return {
                "stock_code": getattr(quote, "code", stock_code),
                "stock_name": getattr(quote, "name", None),
                "current_price": getattr(quote, "price", 0.0) or 0.0,
                "change": getattr(quote, "change_amount", None),
                "change_percent": getattr(quote, "change_pct", None),
                "open": getattr(quote, "open_price", None),
                "high": getattr(quote, "high", None),
                "low": getattr(quote, "low", None),
                "prev_close": getattr(quote, "pre_close", None),
                "volume": getattr(quote, "volume", None),
                "amount": getattr(quote, "amount", None),
                "update_time": datetime.now().isoformat(),
            }
            
        except ImportError:
            logger.warning("DataFetcherManager 不可用，无法获取实时行情")
            return None
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}", exc_info=True)
            return None
    
    @staticmethod
    def _daily_rows_to_kline_data(rows) -> List[Dict[str, Any]]:
        """把 StockDaily 行列表组装成 KLineData 结构（date/open/high/low/close/volume/amount/change_percent）。"""
        data = []
        for r in rows:
            pct = getattr(r, "pct_chg", None)
            data.append({
                "date": r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date),
                "open": float(r.open) if r.open is not None else 0.0,
                "high": float(r.high) if r.high is not None else 0.0,
                "low": float(r.low) if r.low is not None else 0.0,
                "close": float(r.close) if r.close is not None else 0.0,
                "volume": float(r.volume) if r.volume is not None else None,
                "amount": float(r.amount) if r.amount is not None else None,
                "change_percent": float(pct) if pct is not None else None,
            })
        return data

    @staticmethod
    def _cached_series_fresh(rows, today: Optional[datetime] = None) -> bool:
        """判断缓存日线是否足够新鲜：最新一条日期落在最近 _DAILY_CACHE_FRESH_DAYS 内。"""
        if not rows:
            return False
        latest = max((r.date for r in rows if getattr(r, "date", None) is not None), default=None)
        if latest is None:
            return False
        ref = (today or datetime.today()).date()
        return (ref - latest).days <= _DAILY_CACHE_FRESH_DAYS

    def _persist_daily_cache(self, df, stock_code: str, data_source: str) -> None:
        """把网络取到的日线落库（按 (code,date) UPSERT，幂等），失败不影响主流程。"""
        try:
            self.repo.save_dataframe(df, stock_code, data_source)
        except Exception as e:  # noqa: BLE001
            logger.warning("落库日线缓存失败 %s: %s", stock_code, e)

    def get_history_data(
        self,
        stock_code: str,
        period: str = "daily",
        days: int = 30
    ) -> Dict[str, Any]:
        """
        获取股票历史行情
        
        Args:
            stock_code: 股票代码
            period: K 线周期 (daily/weekly/monthly)
            days: 获取天数
            
        Returns:
            历史行情数据字典
            
        Raises:
            ValueError: 当 period 不是 daily 时抛出（weekly/monthly 暂未实现）
        """
        # 验证 period 参数，只支持 daily
        if period != "daily":
            raise ValueError(
                f"暂不支持 '{period}' 周期，目前仅支持 'daily'。"
                "weekly/monthly 聚合功能将在后续版本实现。"
            )
        
        try:
            # DB 快路径：若 stock_daily 已有足够新鲜的历史日线，直接返回，不再走网络，
            # 解决首页 K 线重复网络取数导致的加载慢。
            cached = self.repo.get_daily_series(stock_code, days)
            if self._cached_series_fresh(cached):
                logger.info(
                    f"[K线缓存命中] {stock_code} 使用本地日线缓存 rows={len(cached)} (days={days})"
                )
                return {
                    "stock_code": stock_code,
                    "stock_name": None,
                    "period": period,
                    "data": self._daily_rows_to_kline_data(cached),
                }

            # 调用数据获取器获取历史数据
            from data_provider.base import DataFetcherManager

            manager = DataFetcherManager()
            # 交互式历史请求（K 线图）不做跨源一致性对账：
            # 对账只记录告警、不改变返回数据，同步执行会二次取数阻塞加载，故关闭
            df, source = manager.get_daily_data(stock_code, days=days, reconcile=False)

            if df is None or df.empty:
                logger.warning(f"获取 {stock_code} 历史数据失败")
                return {"stock_code": stock_code, "period": period, "data": []}

            # 取到后落库（UPSERT 幂等），下次请求走 DB 快路径，港股同理可被缓存
            self._persist_daily_cache(df, stock_code, source)

            # 获取股票名称
            stock_name = manager.get_stock_name(stock_code)

            # 转换为响应格式
            data = []
            for _, row in df.iterrows():
                date_val = row.get("date")
                if hasattr(date_val, "strftime"):
                    date_str = date_val.strftime("%Y-%m-%d")
                else:
                    date_str = str(date_val)
                
                data.append({
                    "date": date_str,
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0)) if row.get("volume") else None,
                    "amount": float(row.get("amount", 0)) if row.get("amount") else None,
                    "change_percent": float(row.get("pct_chg", 0)) if row.get("pct_chg") else None,
                })
            
            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "period": period,
                "data": data,
            }
            
        except ImportError:
            logger.warning("DataFetcherManager 未找到，返回空数据")
            return {"stock_code": stock_code, "period": period, "data": []}
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}", exc_info=True)
            return {"stock_code": stock_code, "period": period, "data": []}
    
