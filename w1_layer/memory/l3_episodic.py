# -*- coding: utf-8 -*-
"""
l3_episodic.py — L3 情景记忆（任务轨迹 + 反思）
================================================
使用 SQLite 存储任务执行轨迹、反思记录、决策日志，
支持时间范围查询和事件类型筛选。
"""
import os
import json
import uuid
import sqlite3
import threading
import logging
from typing import List, Dict, Any, Optional

from w1_layer.memory.schemas import L3EpisodicMemory

logger = logging.getLogger("scu2.w1.memory.l3")


class L3EpisodicStore:
    """L3 情景记忆：长期任务轨迹与反思"""

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            db_path = os.path.join(base, "scu2_data", "memory", "memory_l3.db")
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS l3_episodic (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT DEFAULT 'task',
                    task_desc TEXT DEFAULT '',
                    steps TEXT DEFAULT '[]',
                    result TEXT DEFAULT '',
                    success INTEGER DEFAULT 1,
                    reflection TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_l3_ts ON l3_episodic(timestamp)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_l3_type ON l3_episodic(event_type)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_l3_success ON l3_episodic(success)")

    def add(self, event_type: str, task_desc: str = "",
            steps: List[Dict] = None, result: str = "",
            success: bool = True, reflection: str = "",
            metadata: Dict = None) -> L3EpisodicMemory:
        """添加情景记忆"""
        item = L3EpisodicMemory(
            id=str(uuid.uuid4())[:12],
            event_type=event_type,
            task_desc=task_desc,
            steps=steps or [],
            result=result,
            success=success,
            reflection=reflection,
            metadata=metadata or {},
        )
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO l3_episodic (id, timestamp, event_type, task_desc, steps, result, success, reflection, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item.id, item.timestamp, event_type, task_desc,
                 json.dumps(steps or [], ensure_ascii=False),
                 result, 1 if success else 0, reflection,
                 json.dumps(metadata or {}, ensure_ascii=False))
            )
        return item

    def search(self, keyword: str = "", top_k: int = 10,
               event_type: Optional[str] = None,
               time_start: Optional[str] = None,
               time_end: Optional[str] = None) -> List[Dict[str, Any]]:
        """多条件检索"""
        sql = ("SELECT id, timestamp, event_type, task_desc, steps, result, success, reflection, metadata "
               "FROM l3_episodic WHERE 1=1")
        params = []
        if keyword:
            sql += " AND (task_desc LIKE ? OR result LIKE ? OR reflection LIKE ?)"
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        if event_type:
            sql += " AND event_type=?"
            params.append(event_type)
        if time_start:
            sql += " AND timestamp >= ?"
            params.append(time_start)
        if time_end:
            sql += " AND timestamp <= ?"
            params.append(time_end)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(top_k)

        with self._lock, self._conn() as c:
            rows = c.execute(sql, params).fetchall()

        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "timestamp": row[1],
            "event_type": row[2],
            "task_desc": row[3],
            "steps": json.loads(row[4]) if row[4] else [],
            "result": row[5],
            "success": bool(row[6]),
            "reflection": row[7],
            "metadata": json.loads(row[8]) if row[8] else {},
        }

    def forget(self, item_id: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM l3_episodic WHERE id=?", (item_id,))
            return cur.rowcount > 0

    def recent_reflections(self, n: int = 10) -> List[Dict]:
        """最近的反思记录"""
        return self.search(event_type="reflection", top_k=n)

    def failure_analysis(self, n: int = 20) -> List[Dict]:
        """失败事件分析"""
        with self._lock, self._conn() as c:
            rows = c.execute(
                "SELECT id, timestamp, event_type, task_desc, steps, result, success, reflection, metadata "
                "FROM l3_episodic WHERE success=0 ORDER BY timestamp DESC LIMIT ?",
                (n,)
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def stats(self) -> Dict[str, Any]:
        with self._lock, self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM l3_episodic").fetchone()[0]
            types = c.execute(
                "SELECT event_type, COUNT(*) FROM l3_episodic GROUP BY event_type"
            ).fetchall()
            succ = c.execute(
                "SELECT success, COUNT(*) FROM l3_episodic GROUP BY success"
            ).fetchall()
        return {
            "layer": "L3",
            "items": total,
            "event_types": dict(types),
            "success_rate": {
                "success": dict(succ).get(1, 0),
                "failure": dict(succ).get(0, 0),
            },
        }
