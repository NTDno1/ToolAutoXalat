"""
Database module - SQLite logging cho StreakLog và BetLog.
Dùng chung cho cả Emulator và RealPhone plane.
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Dict, Any


class Database:
    """SQLite database cho logging game data."""

    def __init__(self, db_path: str = "data/game_log.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """Tạo bảng nếu chưa tồn tại."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()

        # Bảng ghi nhận chuỗi bệt Rau/Thịt
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS StreakLog (
                Id INTEGER PRIMARY KEY AUTOINCREMENT,
                StreakLength INTEGER NOT NULL,
                StreakType TEXT NOT NULL,
                StartTime DATETIME NOT NULL,
                EndTime DATETIME NOT NULL,
                DayOfWeek INTEGER,
                HourSlot INTEGER
            )
        """)

        # Bảng ghi nhận kết quả cược
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS BetLog (
                Id INTEGER PRIMARY KEY AUTOINCREMENT,
                StrategyName TEXT,
                RoundNumber INTEGER,
                BetBo INTEGER,
                BetGa INTEGER,
                BetXien INTEGER,
                BetXuc INTEGER,
                BetAmount INTEGER,
                TotalCostSoFar INTEGER,
                Result TEXT,
                BalanceBefore INTEGER,
                BalanceAfter INTEGER,
                ProfitLoss INTEGER,
                Timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Bảng session log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS SessionLog (
                Id INTEGER PRIMARY KEY AUTOINCREMENT,
                SessionStart DATETIME NOT NULL,
                SessionEnd DATETIME,
                Mode TEXT,
                TotalBets INTEGER DEFAULT 0,
                TotalWins INTEGER DEFAULT 0,
                TotalLosses INTEGER DEFAULT 0,
                NetProfit INTEGER DEFAULT 0,
                StartBalance INTEGER,
                EndBalance INTEGER
            )
        """)

        self.conn.commit()

    def log_streak(self, streak_length: int, streak_type: str,
                   start_time: datetime, end_time: datetime):
        """Ghi nhận một chuỗi bệt."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO StreakLog (StreakLength, StreakType, StartTime, EndTime,
                                  DayOfWeek, HourSlot)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            streak_length, streak_type,
            start_time.isoformat(), end_time.isoformat(),
            start_time.weekday(), start_time.hour
        ))
        self.conn.commit()
        return cursor.lastrowid

    def log_bet(self, strategy_name: str, round_number: int,
                bet_bo: int, bet_ga: int, bet_xien: int, bet_xuc: int,
                total_cost: int, result: str,
                balance_before: int, balance_after: int):
        """Ghi nhận một kết quả cược."""
        bet_amount = bet_bo + bet_ga + bet_xien + bet_xuc
        profit_loss = balance_after - balance_before
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO BetLog (StrategyName, RoundNumber,
                               BetBo, BetGa, BetXien, BetXuc,
                               BetAmount, TotalCostSoFar, Result,
                               BalanceBefore, BalanceAfter, ProfitLoss)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            strategy_name, round_number,
            bet_bo, bet_ga, bet_xien, bet_xuc,
            bet_amount, total_cost, result,
            balance_before, balance_after, profit_loss
        ))
        self.conn.commit()
        return cursor.lastrowid

    def start_session(self, mode: str, start_balance: int) -> int:
        """Bắt đầu session mới, trả về session ID."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO SessionLog (SessionStart, Mode, StartBalance)
            VALUES (?, ?, ?)
        """, (datetime.now().isoformat(), mode, start_balance))
        self.conn.commit()
        return cursor.lastrowid

    def end_session(self, session_id: int, total_bets: int, total_wins: int,
                    total_losses: int, net_profit: int, end_balance: int):
        """Kết thúc session."""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE SessionLog SET
                SessionEnd = ?, TotalBets = ?, TotalWins = ?,
                TotalLosses = ?, NetProfit = ?, EndBalance = ?
            WHERE Id = ?
        """, (
            datetime.now().isoformat(), total_bets, total_wins,
            total_losses, net_profit, end_balance, session_id
        ))
        self.conn.commit()

    def get_streak_stats(self, streak_type: str = "RAU",
                         days: int = 7) -> List[Dict[str, Any]]:
        """Thống kê phân phối chuỗi bệt."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT StreakLength, COUNT(*) as Count
            FROM StreakLog
            WHERE StreakType = ?
              AND StartTime >= datetime('now', ? || ' days')
            GROUP BY StreakLength
            ORDER BY StreakLength
        """, (streak_type, f"-{days}"))
        return [dict(row) for row in cursor.fetchall()]

    def get_bet_summary(self, days: int = 1) -> Dict[str, Any]:
        """Tóm tắt kết quả cược."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as TotalBets,
                SUM(CASE WHEN Result = 'WIN' THEN 1 ELSE 0 END) as Wins,
                SUM(CASE WHEN Result = 'LOSE' THEN 1 ELSE 0 END) as Losses,
                SUM(ProfitLoss) as NetProfit,
                AVG(ProfitLoss) as AvgProfit
            FROM BetLog
            WHERE Timestamp >= datetime('now', ? || ' days')
        """, (f"-{days}",))
        row = cursor.fetchone()
        return dict(row) if row else {}

    def close(self):
        """Đóng kết nối database."""
        if self.conn:
            self.conn.close()
            self.conn = None
