"""历史图纸批量导入与构件登记核验清单接口。

预检与导入回执都挂在历史图纸模块下；构件登记新增核验清单、领图和已领用
附件查询。具体业务规则在 app/services/drawing_import.py。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services import drawing_import

router = APIRouter(prefix="/api", tags=["图纸导入"])


def _filters(request: Request, allowed: tuple[str, ...]) -> dict[str, str]:
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in allowed and value.strip()
    }


@router.get("/historical_drawing/import/template")
def import_template() -> StreamingResponse:
    """下载导入清单模板，列顺序与预检解析一致。"""
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(drawing_import.PHYSICAL_FIELDS)
    writer.writerow([
        "R-21", "1931年", "南廊", "R-21-1931-南廊.pdf",
        "南廊梁架立面图", "1", "1:50", "基本完好", "1931年测绘",
    ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="historical_drawing_import_template.csv"'},
    )


@router.post("/historical_drawing/import/precheck")
async def precheck(request: Request) -> dict[str, Any]:
    """预检导入清单：只校验并返回逐行结果，不创建图纸。"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return drawing_import.precheck(payload)


@router.post("/historical_drawing/import/commit")
async def commit(request: Request) -> dict[str, Any]:
    """确认导入：逐行重新校验，成功行发布图纸并同步核验清单。"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return drawing_import.commit(payload)


@router.get("/historical_drawing/import/batches")
def batch_summary() -> dict[str, Any]:
    """导入批次与回执统计，给图纸页与核验清单页共用。"""
    return {"ok": True, **drawing_import.batch_summary()}


@router.get("/historical_drawing/import/receipts")
def list_receipts() -> dict[str, Any]:
    return drawing_import.list_receipts()


@router.get("/historical_drawing/import/receipts/{identifier}")
def get_receipt(identifier: str) -> dict[str, Any]:
    receipt = drawing_import.find_receipt(identifier)
    if receipt is None:
        return {"ok": False, "message": f"导入回执 {identifier} 不存在", "receipt": None}
    return {"ok": True, "message": "导入回执", "receipt": receipt, "entry": receipt}


@router.get("/historical_drawing/checklist")
def drawing_checklist(request: Request) -> dict[str, Any]:
    filters = _filters(request, ("图纸编号", "绘制年代", "覆盖部位", "图纸身份"))
    return drawing_import.list_checklist(filters, include_superseded=False)


@router.get("/component_registry/checklist")
def component_checklist(request: Request) -> dict[str, Any]:
    """构件登记核验清单：只返回已导入发布并当前有效的图纸。"""
    filters = _filters(request, ("图纸编号", "绘制年代", "覆盖部位", "图纸身份"))
    return drawing_import.list_checklist(filters, include_superseded=False)


@router.get("/component_registry/evidence")
def component_evidence(request: Request) -> dict[str, Any]:
    """已领用附件查询：含被修订版替代的旧附件。"""
    filters = _filters(request, ("图纸编号", "绘制年代", "覆盖部位", "图纸身份", "构件名称", "状态"))
    return drawing_import.list_evidence(filters)


@router.post("/component_registry/claim")
async def claim(request: Request) -> dict[str, Any]:
    """按导入回执凭据领图；无有效凭据记待补领但可继续办理。"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return drawing_import.claim(payload)


@router.get("/historical_drawing/import/identity/{identity}")
def drawing_by_identity(identity: str) -> dict[str, Any]:
    current = drawing_import.effective_drawing(identity)
    return {"ok": current is not None, "drawing": current, "checklist": drawing_import.list_checklist({"图纸身份": identity})}
