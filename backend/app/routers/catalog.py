"""数据目录检索接口：维护目录条目，覆盖发布条目、下线条目、归档条目等动作。

目录发布方案相关接口（发布前校验、字段清单版本、发布方案、发布历史、
负责人调整与只读共享快照）统一挂在 /api/catalog 下。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.catalog import CatalogService
from app.services.catalog_publish import PublishError, publish_service

router = APIRouter(prefix="/api/catalog", tags=["数据目录检索"])

service = CatalogService()

LIST_FIELDS = ["条目编号", "数据集名称", "业务域", "字段清单", "更新频率", "数据等级", "负责人", "条目状态"]
STATUSES = ["草稿", "已发布", "已下线", "已归档"]


def _raise_publish(exc: PublishError) -> HTTPException:
    status_map = {
        "not_found": 404,
        "forbidden": 403,
        "version_conflict": 409,
        "precheck_failed": 422,
        "owner_missing": 422,
        "not_publishable": 422,
        "not_published": 422,
    }
    return HTTPException(status_code=status_map.get(exc.code, 400), detail=exc.message)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按条目编号检索"),
    status: str | None = Query(default=None, description="草稿、已发布、已下线、已归档"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按条目编号与状态过滤数据目录检索列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/publish/plans")
def list_publish_plans(include_offline: bool = Query(default=False, description="是否包含已下线条目的历史方案")) -> dict[str, Any]:
    """可发布的数据目录清单：按字段清单、负责人、数据等级、更新频率生成的发布结果。"""
    plans = publish_service.list_plans(include_offline=include_offline)
    return {"total": len(plans), "items": plans}


@router.get("/publish/history")
def list_publish_history(entry_code: str | None = Query(default=None, description="按条目编号过滤历史")) -> dict[str, Any]:
    """发布历史：负责人调整后旧记录仍保留原操作人。"""
    items = publish_service.list_history(entry_code)
    return {"total": len(items), "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条目录条目明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"目录条目 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条目录条目，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="目录条目已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条目录条目执行发布条目、下线条目、归档条目；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


# ---- 目录发布方案 ----------------------------------------------------

@router.get("/entry/{entry_code}/precheck")
def precheck_entry(entry_code: str) -> dict[str, Any]:
    """发布前解释字段缺失、负责人缺失和等级冲突，不产生任何写入。"""
    try:
        return publish_service.precheck(entry_code)
    except PublishError as exc:
        raise _raise_publish(exc) from exc


@router.get("/entry/{entry_code}/field-versions")
def list_field_versions(entry_code: str) -> dict[str, Any]:
    """查看字段清单的全部版本及当前唯一生效版本。"""
    try:
        items = publish_service.list_versions(entry_code)
    except PublishError as exc:
        raise _raise_publish(exc) from exc
    active = next((item["版本号"] for item in items if item.get("active")), None)
    return {"条目编号": entry_code, "生效版本": active, "total": len(items), "items": items}


@router.post("/entry/{entry_code}/field-versions")
def create_field_version(entry_code: str, payload: EntryPayload) -> ActionResult:
    """负责人调整字段清单：期望版本号不匹配时拒绝并发写入，只允许一个新版本生效。"""
    values = payload.values
    operator = str(values.get("operator") or "").strip()
    expected = values.get("expected_version")
    expected_version = int(expected) if expected is not None and str(expected) != "" else None
    try:
        version = publish_service.create_version(
            entry_code,
            values.get("字段清单") or [],
            operator=operator,
            expected_version=expected_version,
            remark=str(values.get("remark") or ""),
        )
    except PublishError as exc:
        raise _raise_publish(exc) from exc
    return ActionResult(ok=True, message=f"字段清单 v{version['版本号']} 已生效，旧版本同时失效", entry=version)


@router.post("/entry/{entry_code}/publish")
def publish_entry(entry_code: str, payload: EntryPayload) -> ActionResult:
    """按字段清单、负责人、数据等级、更新频率生成条目发布结果；重复发布只保留一份方案。"""
    operator = str(payload.values.get("operator") or "").strip()
    try:
        plan, check = publish_service.publish(entry_code, operator)
    except PublishError as exc:
        raise _raise_publish(exc) from exc
    warning = ""
    if check["问题清单"]:
        warning = "；发布前提示：" + "；".join(item["说明"] for item in check["问题清单"])
    return ActionResult(
        ok=True,
        message=f"条目 {entry_code} 的发布方案已生成（发布等级 {plan['数据等级']}）{warning}",
        entry=plan,
    )


@router.post("/entry/{entry_code}/owner")
def change_owner(entry_code: str, payload: EntryPayload) -> ActionResult:
    """调整负责人；平台管理员或现任负责人可操作，旧发布记录仍保留原操作人。"""
    values = payload.values
    operator = str(values.get("operator") or "").strip()
    try:
        entry = publish_service.change_owner(
            entry_code,
            new_owner=str(values.get("new_owner") or ""),
            operator=operator,
        )
    except PublishError as exc:
        raise _raise_publish(exc) from exc
    return ActionResult(ok=True, message=f"条目 {entry_code} 负责人已调整为「{entry['负责人']}」", entry=entry)


@router.get("/entry/{entry_code}/shared-snapshot")
def shared_snapshot(entry_code: str) -> dict[str, Any]:
    """数据采样管理读取的同一份只读共享字段清单快照。"""
    try:
        return publish_service.shared_snapshot(entry_code)
    except PublishError as exc:
        raise _raise_publish(exc) from exc


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据目录检索清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "catalog", "total": total, "items": items}
