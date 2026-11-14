"""数据采样管理业务规则：状态流转、字段校验与筛选口径都收在这里。

采样侧不允许自行维护字段清单，只能引用目录发布方案冻结的同一份只读快照，
从而明确「采样输出表引用了哪份数据集」。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.catalog_publish import PublishError, publish_service
from app.store import store

MODULE = "sample"
REQUIRED_FIELDS = ["配置编号", "数据源名称", "采样算法"]
STATUS_ORDER = ["草稿", "已启用", "已暂停", "已关闭"]
ACTION_RULES = {"启用采样": "草稿", "暂停采样": "已启用", "关闭采样": "已暂停"}
NEGATIVE_ACTIONS = []

# 采样配置上承载目录引用的扩展字段。
SNAPSHOT_FIELDS = [
    "引用条目编号",
    "引用发布方案",
    "引用字段版本",
    "只读字段快照",
    "引用性质",
]


class SampleService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("配置编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        for field in SNAPSHOT_FIELDS:
            entry[field] = [] if field == "只读字段快照" else ""
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"采样配置 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据采样管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"采样配置已{action}"

    # ---- 目录快照引用 -----------------------------------------------
    def bind_catalog(self, sample_id: int, entry_code: str) -> tuple[dict[str, Any] | None, str]:
        """把采样输出表绑定到某条目录发布方案冻结的只读字段快照。

        已下线的数据集不允许新增引用（只留历史引用）；快照以深拷贝落库，
        后续字段清单再发新版本也不会改动采样侧已引用的内容。
        """
        entry = store.find(MODULE, sample_id)
        if entry is None:
            return None, f"采样配置 {sample_id} 不存在或已归档"
        target = next(
            (row for row in store.rows("catalog") if row.get("条目编号") == entry_code),
            None,
        )
        if target is None:
            return None, f"目录条目 {entry_code} 不存在"
        if target.get("status") == "已下线":
            return None, f"条目 {entry_code} 已下线，采样配置只能保留历史引用，不能新增绑定"
        try:
            shared = publish_service.shared_snapshot(entry_code)
        except PublishError as exc:
            return None, exc.message

        entry["引用条目编号"] = entry_code
        entry["引用发布方案"] = entry_code
        entry["引用字段版本"] = str(shared["字段版本"])
        # 深拷贝：采样侧拿到的是只读共享版本，任何改动都不会回写到目录侧。
        entry["只读字段快照"] = deepcopy(shared["字段快照"])
        entry["引用性质"] = "发布快照引用"
        return entry, (
            f"采样输出表已绑定 {entry_code} 发布方案（字段版本 v{shared['字段版本']}），"
            f"共冻结 {len(shared['字段快照'])} 个只读字段"
        )

    def read_snapshot(self, sample_id: int) -> dict[str, Any] | None:
        """查看采样配置引用的只读字段快照；返回副本，保证调用方无法改写源快照。"""
        entry = store.find(MODULE, sample_id)
        if entry is None:
            return None
        if not entry.get("引用条目编号"):
            return {
                "配置编号": entry.get("配置编号"),
                "已引用": False,
                "提示": "该采样配置尚未绑定目录发布方案，无法确认输出表引用的数据集",
                "字段快照": [],
            }
        return {
            "配置编号": entry.get("配置编号"),
            "已引用": True,
            "引用条目编号": entry.get("引用条目编号"),
            "引用字段版本": entry.get("引用字段版本"),
            "引用性质": entry.get("引用性质"),
            "只读": True,
            "字段快照": deepcopy(entry.get("只读字段快照", [])),
        }
