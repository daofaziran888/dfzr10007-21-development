"""目录发布方案业务规则。

围绕「可发布的数据目录清单」补齐三块能力：

1. 发布前校验：字段缺失、负责人缺失、等级冲突都要在发布前给出可读解释；
2. 版本治理：同一数据集的字段清单同一时刻只有一个生效版本，并发更新借助
   期望版本号做乐观锁，冲突时只允许一个版本生效；
3. 发布留痕：同一条目重复发布只保留一份发布方案（upsert），每次操作都写入
   发布历史；负责人调整后，旧发布记录仍保留当时的操作人。

数据等级冲突的取舍规则：数据等级按敏感度 L1 < L2 < L3 < L4 递增，
当字段清单中出现比条目登记等级更敏感的字段时，以「就高不就低」为准——
发布方案采用字段清单里的最高等级，并保留冲突说明，避免敏感数据被低等级发布。
"""
from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.store import store

CATALOG_MODULE = "catalog"
VERSION_MODULE = "catalog_field_versions"
PLAN_MODULE = "catalog_publish_plans"
HISTORY_MODULE = "catalog_publish_history"

# 等级按敏感度递增，冲突时就高不就低。
LEVEL_ORDER = ["L1", "L2", "L3", "L4"]
# 只有这些状态的条目允许发布；已下线/已归档只保留历史引用。
PUBLISHABLE_STATUS = {"草稿", "已发布"}

# 全局锁只负责把「读-判断-写」串成原子区间，跨请求的并发互斥靠它；
# 期望版本号（乐观锁）负责识别过期写入，两者配合保证只有一个新版本生效。
_lock = threading.RLock()


class PublishError(Exception):
    """业务规则被违反时抛出，路由层统一翻译成可读的失败结果。"""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _find_catalog_entry(entry_code: str) -> dict[str, Any] | None:
    for row in store.rows(CATALOG_MODULE):
        if str(row.get("条目编号", "")).strip() == entry_code:
            return row
    return None


def _active_version(entry_code: str) -> dict[str, Any] | None:
    for row in store.rows(VERSION_MODULE):
        if row.get("条目编号") == entry_code and row.get("active"):
            return row
    return None


def _versions(entry_code: str) -> list[dict[str, Any]]:
    return [
        row
        for row in store.rows(VERSION_MODULE)
        if row.get("条目编号") == entry_code
    ]


def _plan_of(entry_code: str) -> dict[str, Any] | None:
    for row in store.rows(PLAN_MODULE):
        if row.get("条目编号") == entry_code:
            return row
    return None


def _ensure_owner(entry: dict[str, Any], operator: str, action: str) -> None:
    """负责人归属匹配：非负责人越权操作直接拒绝（平台管理员可调整负责人）。"""
    owner = str(entry.get("负责人") or "").strip()
    if not operator or not owner:
        raise PublishError(
            f"负责人归属缺失，无法执行「{action}」，请先补全负责人",
            "owner_missing",
        )
    if operator != owner:
        raise PublishError(
            f"当前操作人「{operator}」不是条目「{entry.get('条目编号')}」的负责人"
            f"（负责人：{owner}），无权执行「{action}」",
            "forbidden",
        )


def _precheck(entry: dict[str, Any], version: dict[str, Any] | None) -> dict[str, Any]:
    """发布前解释字段缺失、负责人缺失和等级冲突。

    返回结构里 blocking=True 表示问题必须先处理才能发布；等级冲突只告警，
    发布时按就高规则自动采用更严格的等级。
    """
    issues: list[dict[str, str]] = []

    owner = str(entry.get("负责人") or "").strip()
    if not owner:
        issues.append({
            "类型": "负责人缺失",
            "级别": "阻断",
            "说明": f"条目 {entry.get('条目编号')} 未登记负责人，发布方案无人署名，"
                    "请先由平台管理员补全负责人后再发布",
        })

    declared = [part.strip() for part in str(entry.get("字段清单") or "").split("、") if part.strip()]
    actual_fields = version.get("字段清单", []) if version else []
    actual_names = [str(item.get("字段名", "")).strip() for item in actual_fields]

    missing_fields = [name for name in declared if name not in actual_names]
    if not version:
        issues.append({
            "类型": "字段缺失",
            "级别": "阻断",
            "说明": "该数据集还没有生效的字段清单版本，无法生成发布快照",
        })
    elif missing_fields:
        issues.append({
            "类型": "字段缺失",
            "级别": "阻断",
            "说明": "条目登记字段 "
                    + "、".join(declared)
                    + "，生效版本（v"
                    + str(version.get("版本号"))
                    + "）缺少："
                    + "、".join(missing_fields),
        })

    entry_level = str(entry.get("数据等级") or "").strip()
    entry_rank = LEVEL_ORDER.index(entry_level) if entry_level in LEVEL_ORDER else -1
    field_levels = [
        str(item.get("数据等级", "")).strip()
        for item in actual_fields
        if str(item.get("数据等级", "")).strip() in LEVEL_ORDER
    ]
    conflicts: list[dict[str, str]] = []
    effective_level = entry_level
    if field_levels and entry_rank >= 0:
        max_field = max(field_levels, key=lambda lv: LEVEL_ORDER.index(lv))
        for item in actual_fields:
            field_level = str(item.get("数据等级", "")).strip()
            if field_level in LEVEL_ORDER and LEVEL_ORDER.index(field_level) > entry_rank:
                conflicts.append({
                    "字段名": str(item.get("字段名", "")),
                    "字段等级": field_level,
                    "条目登记等级": entry_level,
                })
        if LEVEL_ORDER.index(max_field) > entry_rank:
            # 就高不就低：发布等级采用字段清单里的最高等级。
            effective_level = max_field
    if conflicts:
        issues.append({
            "类型": "等级冲突",
            "级别": "告警",
            "说明": "字段 "
                    + "、".join(f"{c['字段名']}({c['字段等级']})" for c in conflicts)
                    + " 的等级高于条目登记等级 "
                    + entry_level
                    + "；按就高不就低规则，发布等级取 "
                    + effective_level,
        })

    blocking = any(item["级别"] == "阻断" for item in issues)
    return {
        "条目编号": entry.get("条目编号"),
        "阻断发布": blocking,
        "问题清单": issues,
        "条目登记等级": entry_level,
        "建议发布等级": effective_level,
        "生效字段版本": version.get("版本号") if version else None,
        "字段数": len(actual_fields),
    }


class CatalogPublishService:
    """目录发布、字段版本治理与发布历史的统一入口。"""

    # ---- 发布前校验 -------------------------------------------------
    def precheck(self, entry_code: str) -> dict[str, Any]:
        with _lock:
            entry = _find_catalog_entry(entry_code)
            if entry is None:
                raise PublishError(f"目录条目 {entry_code} 不存在", "not_found")
            return _precheck(entry, _active_version(entry_code))

    # ---- 字段清单版本 -----------------------------------------------
    def list_versions(self, entry_code: str) -> list[dict[str, Any]]:
        with _lock:
            if _find_catalog_entry(entry_code) is None:
                raise PublishError(f"目录条目 {entry_code} 不存在", "not_found")
            return [dict(row) for row in sorted(_versions(entry_code), key=lambda r: int(r["版本号"]))]

    def create_version(
        self,
        entry_code: str,
        fields: list[dict[str, Any]],
        operator: str,
        expected_version: int | None,
        remark: str = "",
    ) -> dict[str, Any]:
        """新增字段清单版本。

        expected_version 为调用方读过的生效版本号；并发场景下第二个提交会发现
        生效版本已经前移，写入被拒绝，从而保证同一数据集只有一个新版本生效。
        """
        if not isinstance(fields, list) or not fields:
            raise PublishError("字段清单不能为空，至少包含一个字段", "empty_fields")
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in fields:
            name = str((item or {}).get("字段名", "")).strip()
            if not name:
                raise PublishError("字段清单中存在未命名字段", "invalid_field")
            if name in seen:
                raise PublishError(f"字段名「{name}」重复，字段清单不允许重名字段", "duplicate_field")
            seen.add(name)
            level = str(item.get("数据等级", "")).strip()
            if level and level not in LEVEL_ORDER:
                raise PublishError(f"字段「{name}」的数据等级 {level} 非法，可选 {LEVEL_ORDER}", "invalid_level")
            cleaned.append({
                "字段名": name,
                "字段类型": str(item.get("字段类型", "")).strip(),
                "数据等级": level,
                "字段说明": str(item.get("字段说明", "")).strip(),
            })

        with _lock:
            entry = _find_catalog_entry(entry_code)
            if entry is None:
                raise PublishError(f"目录条目 {entry_code} 不存在", "not_found")
            _ensure_owner(entry, operator, "调整字段清单")
            current = _active_version(entry_code)
            current_no = int(current["版本号"]) if current else 0
            if expected_version is not None and expected_version != current_no:
                raise PublishError(
                    f"字段清单已被他人更新：当前生效版本为 v{current_no}，"
                    f"您基于 v{expected_version} 的修改已过期，请拉取最新版本后重试",
                    "version_conflict",
                )
            rows = store.rows(VERSION_MODULE)
            new_no = max((int(r["版本号"]) for r in _versions(entry_code)), default=0) + 1
            if current is not None:
                current["active"] = False
            version = {
                "id": _next_id(rows),
                "条目编号": entry_code,
                "版本号": new_no,
                "active": True,
                "字段清单": cleaned,
                "更新人": operator,
                "更新时间": _now(),
                "备注": remark or f"字段清单第 {new_no} 版",
            }
            rows.append(version)
            return deepcopy(version)

    # ---- 发布方案 ---------------------------------------------------
    def publish(self, entry_code: str, operator: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """生成发布结果；返回 (发布方案, 发布前校验结果)。"""
        with _lock:
            entry = _find_catalog_entry(entry_code)
            if entry is None:
                raise PublishError(f"目录条目 {entry_code} 不存在", "not_found")
            if entry.get("status") not in PUBLISHABLE_STATUS:
                raise PublishError(
                    f"条目当前状态为「{entry.get('status')}」，不可发布；"
                    "已下线条目只保留历史引用",
                    "not_publishable",
                )
            _ensure_owner(entry, operator, "发布条目")

            version = _active_version(entry_code)
            check = _precheck(entry, version)
            if check["阻断发布"]:
                raise PublishError(
                    "发布前校验未通过："
                    + "；".join(item["说明"] for item in check["问题清单"] if item["级别"] == "阻断"),
                    "precheck_failed",
                )

            snapshot = deepcopy(version["字段清单"]) if version else []
            plan_rows = store.rows(PLAN_MODULE)
            existing = _plan_of(entry_code)
            action = "重新发布" if existing is not None else "发布"
            if existing is not None:
                # 重复发布同一条目只保留一份方案：原地更新而不是新增。
                existing.update({
                    "数据集名称": entry.get("数据集名称"),
                    "业务域": entry.get("业务域"),
                    "更新频率": entry.get("更新频率"),
                    "数据等级": check["建议发布等级"],
                    "负责人": entry.get("负责人"),
                    "字段版本": version.get("版本号") if version else None,
                    "字段快照": snapshot,
                    "发布操作人": operator,
                    "发布时间": _now(),
                    "发布状态": "已发布",
                    "等级冲突说明": [
                        item["说明"] for item in check["问题清单"] if item["类型"] == "等级冲突"
                    ],
                })
                plan = existing
                action = "重新发布"
            else:
                plan = {
                    "id": _next_id(plan_rows),
                    "条目编号": entry_code,
                    "数据集名称": entry.get("数据集名称"),
                    "业务域": entry.get("业务域"),
                    "更新频率": entry.get("更新频率"),
                    "数据等级": check["建议发布等级"],
                    "负责人": entry.get("负责人"),
                    "字段版本": version.get("版本号") if version else None,
                    "字段快照": snapshot,
                    "发布操作人": operator,
                    "发布时间": _now(),
                    "发布状态": "已发布",
                    "等级冲突说明": [
                        item["说明"] for item in check["问题清单"] if item["类型"] == "等级冲突"
                    ],
                }
                plan_rows.append(plan)

            entry["status"] = "已发布"
            entry["条目状态"] = "已发布"
            entry["pending"] = False

            history_rows = store.rows(HISTORY_MODULE)
            history_rows.append({
                "id": _next_id(history_rows),
                "条目编号": entry_code,
                "操作类型": action,
                "操作人": operator,
                "操作时间": plan["发布时间"],
                "字段版本": plan["字段版本"],
                "发布等级": plan["数据等级"],
                "说明": "发布方案已生成/覆盖，字段快照已冻结"
                        + ("；" + "；".join(plan["等级冲突说明"]) if plan["等级冲突说明"] else ""),
            })
            return deepcopy(plan), check

    def list_plans(self, include_offline: bool = False) -> list[dict[str, Any]]:
        with _lock:
            rows = store.rows(PLAN_MODULE)
            if include_offline:
                return [dict(row) for row in rows]
            live_codes = {
                row["条目编号"]
                for row in store.rows(CATALOG_MODULE)
                if row.get("status") != "已下线"
            }
            return [dict(row) for row in rows if row.get("条目编号") in live_codes]

    def list_history(self, entry_code: str | None = None) -> list[dict[str, Any]]:
        with _lock:
            rows = store.rows(HISTORY_MODULE)
            if entry_code:
                rows = [row for row in rows if row.get("条目编号") == entry_code]
            return [dict(row) for row in rows]

    # ---- 负责人调整 -------------------------------------------------
    def change_owner(self, entry_code: str, new_owner: str, operator: str) -> dict[str, Any]:
        """负责人调整只允许平台管理员或现任负责人操作；历史记录保留原操作人。"""
        new_owner = str(new_owner or "").strip()
        if not new_owner:
            raise PublishError("新负责人不能为空", "owner_missing")
        with _lock:
            entry = _find_catalog_entry(entry_code)
            if entry is None:
                raise PublishError(f"目录条目 {entry_code} 不存在", "not_found")
            old_owner = str(entry.get("负责人") or "").strip()
            if operator != "平台管理员" and operator != old_owner:
                raise PublishError(
                    f"操作人「{operator}」既不是平台管理员也不是现任负责人（{old_owner or '空缺'}），"
                    "无权调整负责人",
                    "forbidden",
                )
            entry["负责人"] = new_owner
            plan = _plan_of(entry_code)
            if plan is not None:
                # 方案署名跟随新负责人，但历史记录里的操作人保持不变。
                plan["负责人"] = new_owner
            history_rows = store.rows(HISTORY_MODULE)
            history_rows.append({
                "id": _next_id(history_rows),
                "条目编号": entry_code,
                "操作类型": "负责人调整",
                "操作人": operator,
                "操作时间": _now(),
                "字段版本": plan.get("字段版本") if plan else None,
                "发布等级": plan.get("数据等级") if plan else None,
                "说明": f"负责人由「{old_owner or '空缺'}」调整为「{new_owner}」，历史发布记录操作人不变",
            })
            return dict(entry)

    # ---- 给数据采样侧使用的只读快照 ---------------------------------
    def shared_snapshot(self, entry_code: str) -> dict[str, Any]:
        """数据采样管理读取同一份字段清单快照：只读、深拷贝，调用方改不到源数据。"""
        with _lock:
            entry = _find_catalog_entry(entry_code)
            if entry is None:
                raise PublishError(f"目录条目 {entry_code} 不存在", "not_found")
            plan = _plan_of(entry_code)
            if plan is None:
                raise PublishError(
                    f"条目 {entry_code} 尚未发布，没有可共享的只读快照",
                    "not_published",
                )
            return {
                "条目编号": entry_code,
                "数据集名称": entry.get("数据集名称"),
                "条目状态": entry.get("status"),
                "字段版本": plan.get("字段版本"),
                "数据等级": plan.get("数据等级"),
                "只读": True,
                "字段快照": deepcopy(plan.get("字段快照", [])),
            }


publish_service = CatalogPublishService()
