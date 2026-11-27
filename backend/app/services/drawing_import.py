"""历史图纸批量导入、预检、导入回执与构件登记核验清单联动。

关键业务约定：

* 预检只校验清单行，不创建图纸；因此只在预检中的图纸永远不会出现在构件
  登记的核验清单里。
* 图纸身份由“图纸编号 + 绘制年代 + 覆盖部位”共同确定。两份同编号图纸只要
  年代或覆盖部位不同，就是两份不同图纸，不能仅凭编号合并。
* 预检失败行保留原始行号和错误；再次导入时仍按原始行号返回该错误行。
* 同一行重复导入（或同一批次内重复行）只对应一个图纸身份，已有图纸直接复用。
* 同一身份出现更高版次时按修订发布处理，只有该身份的最高版次为有效版次，
  旧版次标记为已替代但仍可查询。
* 构件登记提交时会在同一把发布锁内重新解析“提交时实际生效的版次”，不依赖
  领图时的快照；因此临近发布修订版时，提交的是实际生效的覆盖版次。
* 领图没有有效凭据不阻断办理，先记为“待补领”；已领用附件始终可查，包括
  后来被修订版替代的附件。
"""
from __future__ import annotations

import csv
import io
import os
import re
import threading
from datetime import datetime
from typing import Any

from app.store import store

BATCH_TABLE = "_drawing_import_batches"
RECEIPT_TABLE = "_drawing_import_receipts"
CHECKLIST_TABLE = "_drawing_checklist"
EVIDENCE_TABLE = "_drawing_evidence"

IDENTITY_SEP = "|"
REQUIRED_IMPORT_FIELDS = ("图纸编号", "绘制年代", "覆盖部位")
ALLOWED_ATTACHMENT_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# 发布图纸、重新解析生效版次、领图、登记提交共用同一把进程内锁。
# 图纸员确认导入、另一人发布修订版、构件员领图办登记因此在同一临界区内
# 看到一致的版次序列。
PUBLICATION_LOCK = threading.RLock()

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "图纸编号": ("图纸编号", "编号", "图号", "drawing_no", "drawingid", "number"),
    "绘制年代": ("绘制年代", "年代", "绘制年份", "era", "year"),
    "绘制内容": ("绘制内容", "内容", "图名", "content"),
    "覆盖部位": ("覆盖部位", "部位", "覆盖区域", "区域", "part", "location"),
    "附件": ("附件", "附件文件", "附件名", "文件名", "attachment", "file"),
    "版次": ("版次", "版本", "图纸版本", "version"),
    "比例尺": ("比例尺", "scale"),
    "保存状况": ("保存状况", "保存状态", "condition"),
    "备注说明": ("备注说明", "备注", "remark", "note"),
    "行号": ("line_no", "line", "行号", "row"),
}

PHYSICAL_FIELDS = ["图纸编号", "绘制年代", "覆盖部位", "附件", "绘制内容", "版次", "比例尺", "保存状况", "备注说明"]


# ── 小工具 ──
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number_key(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value)).casefold()


def _era_key(value: Any) -> str:
    text = _text(value)
    match = re.search(r"(\d{3,4})", text)
    return match.group(1) if match else _number_key(text)


def _part_key(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value)).casefold()


def identity_of(number: Any, era: Any, part: Any) -> str:
    """图纸身份：编号+年代+覆盖部位；只用编号不能判定同一份图纸。"""
    return IDENTITY_SEP.join((_number_key(number), _era_key(era), _part_key(part)))


def _version(value: Any) -> int | None:
    text = _text(value)
    if not text:
        return 1
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _attachment_error(attachment: str) -> str:
    """附件必须是可识别的图纸文件；仅写文件名也算有效。"""
    name = _text(attachment)
    if not name:
        return ""
    if name.lower().endswith(".pdf.gz") or name.lower().endswith(".zip"):
        return f"附件格式无效：{name}，需上传单个图纸文件而非压缩包"
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_ATTACHMENT_EXT:
        allowed = "、".join(sorted(ALLOWED_ATTACHMENT_EXT))
        return f"附件格式无效：{name}，仅支持 {allowed}"
    return ""


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _canonical(source: dict[str, Any]) -> dict[str, str]:
    """把一行导入数据按中文字段别名整理成标准字段。"""
    values: dict[str, str] = {}
    folded = {str(key).strip().casefold(): value for key, value in source.items()}
    for canonical, aliases in FIELD_ALIASES.items():
        if canonical == "行号":
            continue
        for alias in aliases:
            candidate = folded.get(alias.casefold())
            if candidate is not None and _text(candidate):
                values[canonical] = _text(candidate)
                break
    # 已是标准字段但不在别名折叠键里的，直接保留
    for field in PHYSICAL_FIELDS:
        if field not in values and field in source and _text(source[field]):
            values[field] = _text(source[field])
    return values


# ── 清单解析 ──
def _cell_key(cell: str) -> str | None:
    text = _text(cell).casefold()
    for canonical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if text == alias.casefold():
                return canonical
    return None


def _looks_like_header(cells: list[str]) -> bool:
    return any(_cell_key(cell) for cell in cells)


def _parse_content(content: str) -> list[tuple[int, dict[str, Any]]]:
    """把粘贴/上传的 CSV/TSV 文本解析成 (原始物理行号, 原始数据)。

    行号按文件里的实际顺序编号，中间的空行不会让后续错误行的行号前移。
    """
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if not text.strip():
        return []
    sample = "\n".join(line for line in text.split("\n") if line.strip())
    delimiter = "\t" if sample.count("\t") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    parsed = list(reader)
    while parsed and not any(_text(cell) for cell in parsed[-1]):
        parsed.pop()
    if not parsed:
        return []
    column_map: dict[int, str] = {}
    body_start = 0
    if _looks_like_header(parsed[0]):
        for index, cell in enumerate(parsed[0]):
            key = _cell_key(cell)
            if key and key != "行号":
                column_map[index] = key
        body_start = 1
    else:
        for index, field in enumerate(PHYSICAL_FIELDS):
            column_map[index] = field
        # 没有表头、每行又只有一格，多半不是清单表格；交由调用方提示格式问题。
        if max(len(cells) for cells in parsed) <= 1:
            return []
    result: list[tuple[int, dict[str, Any]]] = []
    for offset, cells in enumerate(parsed[body_start:]):
        line_no = body_start + offset + 1
        if not any(_text(cell) for cell in cells):
            continue
        raw: dict[str, Any] = {}
        for index, cell in enumerate(cells):
            field = column_map.get(index)
            if field and field != "行号":
                raw[field] = _text(cell)
        result.append((line_no, raw))
    return result


def _collect_rows(payload: Any) -> list[tuple[int, dict[str, Any]]]:
    """兼容 rows / lines / items / content 多种请求形态，保留原行号。"""
    if payload is None:
        return []
    if isinstance(payload, list):
        payload = {"rows": payload}
    if not isinstance(payload, dict):
        return []

    row_source = (
        payload.get("rows")
        or payload.get("lines")
        or payload.get("items")
        or payload.get("图纸清单")
        or payload.get("清单")
    )
    explicit_start = _to_int(payload.get("line_no") or payload.get("起始行号"))
    if isinstance(row_source, list) and row_source:
        rows: list[tuple[int, dict[str, Any]]] = []
        for index, item in enumerate(row_source):
            if isinstance(item, str):
                # 单行 CSV 文本
                parsed = _parse_content(item)
                if parsed:
                    line_no, raw = parsed[0]
                else:
                    continue
                rows.append(((explicit_start + index) if explicit_start else index + 1, raw))
                continue
            if not isinstance(item, dict):
                continue
            raw = dict(item)
            explicit = item.get("line_no") or item.get("行号") or item.get("row")
            line_no = _to_int(explicit) or index + 1
            rows.append((line_no, raw))
        if rows:
            return rows

    content = (
        payload.get("content")
        or payload.get("csv")
        or payload.get("text")
        or payload.get("data")
        or payload.get("清单文本")
    )
    if content:
        return _parse_content(str(content))
    return []


# ── 行校验 ──
def validate_line(raw: dict[str, Any], line_no: int) -> dict[str, Any]:
    values = _canonical(raw)
    errors: list[str] = []
    for field in REQUIRED_IMPORT_FIELDS:
        if not values.get(field):
            errors.append(f"缺少{field}")
    era = values.get("绘制年代", "")
    if era and not re.search(r"(\d{3,4})", era):
        errors.append("绘制年代无法识别年份")
    attachment = values.get("附件", "")
    if attachment:
        attachment_error = _attachment_error(attachment)
        if attachment_error:
            errors.append(attachment_error)
    version_raw = values.get("版次", "")
    version = _version(version_raw)
    if version_raw and version is None:
        errors.append("版次格式无效")
    if version is None:
        version = 1
    identity = identity_of(
        values.get("图纸编号", ""), values.get("绘制年代", ""), values.get("覆盖部位", "")
    )
    return {
        "line_no": line_no,
        "行号": line_no,
        "values": values,
        "图纸编号": values.get("图纸编号", ""),
        "绘制年代": values.get("绘制年代", ""),
        "覆盖部位": values.get("覆盖部位", ""),
        "附件": attachment,
        "版次": version_raw or str(version),
        "版次序号": version,
        "identity": identity,
        "图纸身份": identity,
        "ok": not errors,
        "passed": not errors,
        "errors": errors,
        "错误": errors,
    }


def precheck(payload: Any) -> dict[str, Any]:
    """预检：只返回逐行校验结果，不创建任何图纸。"""
    collected = _collect_rows(payload)
    if isinstance(payload, dict) and not collected and payload.get("content"):
        # 有文本却解析不出任何行，说明表头或分隔符不对，直接报错而不是空通过。
        return {
            "ok": False,
            "message": "未能从清单中解析出任何数据行，请检查表头与分隔符",
            "status": "预检有误",
            "summary": {"total": 0, "passed": 0, "failed": 0},
            "lines": [],
            "items": [],
            "errors": [],
            "error_lines": [],
        }
    lines = [validate_line(raw, line_no) for line_no, raw in collected]
    failed = [line for line in lines if not line["ok"]]
    passed = [line for line in lines if line["ok"]]
    batches = store.rows(BATCH_TABLE)
    batch_id = _next_id(batches)
    precheck_id = f"PRE-{_today()}-{batch_id}"
    record = {
        "id": batch_id,
        "预检号": precheck_id,
        "precheck_id": precheck_id,
        "批次号": precheck_id,
        "kind": "预检",
        "status": "预检完成" if not failed else "预检有误",
        "created_at": _now(),
        "operator": _text(payload.get("operator") if isinstance(payload, dict) else "") or "图纸员",
        "summary": {"total": len(lines), "passed": len(passed), "failed": len(failed)},
        "lines": lines,
    }
    batches.append(record)
    return {
        "ok": not failed,
        "message": "预检通过" if not failed else f"预检发现 {len(failed)} 行错误",
        "precheck_id": precheck_id,
        "batch_id": batch_id,
        "批次号": precheck_id,
        "status": record["status"],
        "summary": record["summary"],
        "total": len(lines),
        "passed": len(passed),
        "failed": len(failed),
        "lines": lines,
        "items": lines,
        "errors": failed,
        "error_lines": failed,
    }


# ── 图纸身份与版次发布 ──
def _drawing_rows(identity: str) -> list[dict[str, Any]]:
    return [
        row for row in store.rows("historical_drawing")
        if row.get("图纸身份") == identity
    ]


def effective_drawing(identity: str) -> dict[str, Any] | None:
    """返回该身份当前实际生效的图纸（每个身份最多一个有效版次）。"""
    rows = _drawing_rows(identity)
    effective = [row for row in rows if row.get("有效版次") is True]
    if effective:
        return max(effective, key=lambda row: int(row.get("版次序号", 1)))
    if not rows:
        return None
    return max(rows, key=lambda row: int(row.get("版次序号", 1)))


def _checklist_entry(identity: str, effective_only: bool = True) -> dict[str, Any] | None:
    entries = [row for row in store.rows(CHECKLIST_TABLE) if row.get("图纸身份") == identity]
    if effective_only:
        entries = [row for row in entries if row.get("有效") is True]
    if not entries:
        return None
    return max(entries, key=lambda row: int(row.get("版次序号", 1)))


def _new_checklist_entry(drawing: dict[str, Any], batch_id: Any) -> dict[str, Any]:
    entries = store.rows(CHECKLIST_TABLE)
    entry = {
        "id": _next_id(entries),
        "drawing_id": drawing.get("id"),
        "图纸ID": drawing.get("id"),
        "图纸编号": drawing.get("图纸编号", ""),
        "绘制年代": drawing.get("绘制年代", ""),
        "覆盖部位": drawing.get("覆盖部位", ""),
        "图纸身份": drawing.get("图纸身份", ""),
        "版次": drawing.get("版次", ""),
        "版次序号": drawing.get("版次序号", 1),
        "附件": drawing.get("附件", ""),
        "有效": True,
        "状态": "有效",
        "导入批次": batch_id,
        "发布状态": "已发布",
        "发布时间": _now(),
    }
    entries.append(entry)
    return entry


def _publish_checklist(drawing: dict[str, Any], batch_id: Any = "") -> dict[str, Any]:
    """把已发布图纸同步到构件登记核验清单；同身份旧有效项转为已替代。"""
    identity = str(drawing.get("图纸身份", ""))
    for entry in store.rows(CHECKLIST_TABLE):
        if entry.get("图纸身份") == identity and entry.get("有效") is True and entry.get("drawing_id") != drawing.get("id"):
            entry["有效"] = False
            entry["状态"] = "已替代"
            entry["已替代"] = True
            entry["替代图纸ID"] = drawing.get("id")
    existing = _checklist_entry(identity)
    if existing and existing.get("drawing_id") == drawing.get("id"):
        return existing
    return _new_checklist_entry(drawing, batch_id)


def _supersede_evidence(identity: str, new_drawing_id: int) -> None:
    """修订版发布后，旧领用记录保留附件但标记为已替代。"""
    for evidence in store.rows(EVIDENCE_TABLE):
        if evidence.get("图纸身份") != identity:
            continue
        if evidence.get("drawing_id") == new_drawing_id:
            continue
        evidence["已替代"] = True
        evidence["有效附件"] = False
        evidence["替代图纸ID"] = new_drawing_id


def _mark_old_drawings(identity: str, keep_id: int) -> None:
    for row in _drawing_rows(identity):
        if row.get("id") != keep_id and row.get("有效版次") is True:
            row["有效版次"] = False
            row["已替代"] = True
            row["图纸状态"] = "已替代"
            row["替代图纸ID"] = keep_id


def _new_drawing_row(
    line: dict[str, Any],
    *,
    version: int,
    batch_id: Any,
    source: str,
    supersedes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    values = line["values"]
    rows = store.rows("historical_drawing")
    drawing: dict[str, Any] = {
        "id": _next_id(rows),
        "status": "已登记",
        "pending": True,
        "abnormal": False,
        "图纸编号": values.get("图纸编号", ""),
        "绘制年代": values.get("绘制年代", ""),
        "绘制内容": values.get("绘制内容", "")
        or f"{values.get('图纸编号', '')} {values.get('覆盖部位', '')}图纸",
        "比例尺": values.get("比例尺", ""),
        "保存状况": values.get("保存状况", "待核验"),
        "数字化状态": values.get("数字化状态", "未数字化"),
        "归档位置": values.get("归档位置", ""),
        "备注说明": values.get("备注说明", ""),
        "覆盖部位": values.get("覆盖部位", ""),
        "附件": values.get("附件", ""),
        "图纸身份": line["identity"],
        "版次": str(version),
        "版次序号": version,
        "有效版次": True,
        "图纸状态": "有效",
        "图纸来源": source,
        "发布状态": "已发布",
        "导入批次": batch_id,
        "导入行号": line["line_no"],
    }
    if supersedes is not None:
        drawing["前版图纸ID"] = supersedes.get("id")
    rows.append(drawing)
    return drawing


def _register_drawing(
    line: dict[str, Any],
    *,
    batch_id: Any,
    receipt_id: int,
    source: str = "批量导入",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """为通过校验的一行登记图纸身份；返回 (回执行, 本次新建图纸或 None)。

    图纸身份只看“编号+年代+覆盖部位”，所以同身份重复导入绝不会多建一份图纸。
    """
    identity = line["identity"]
    requested_version = int(line.get("版次序号") or 1)
    current = effective_drawing(identity)
    new_drawing: dict[str, Any] | None = None

    if current is None:
        drawing = _new_drawing_row(line, version=requested_version, batch_id=batch_id, source=source)
        _publish_checklist(drawing, batch_id)
        new_drawing = drawing
        outcome = "受理并发布"
        drawing_id = drawing["id"]
        version = requested_version
    else:
        current_version = int(current.get("版次序号", 1))
        if requested_version > current_version:
            drawing = _new_drawing_row(
                line,
                version=requested_version,
                batch_id=batch_id,
                source=source,
                supersedes=current,
            )
            _mark_old_drawings(identity, drawing["id"])
            _publish_checklist(drawing, batch_id)
            _supersede_evidence(identity, drawing["id"])
            new_drawing = drawing
            outcome = f"修订发布，版次{requested_version}生效"
            drawing_id = drawing["id"]
            version = requested_version
        else:
            # 同身份同版次或旧版次：复用现有图纸，不新增图纸身份，
            # 也不把已有修订版回退成旧版次。
            outcome = "重复行复用，仅记一个图纸身份"
            drawing_id = current["id"]
            version = current_version

    values = line["values"]
    identity = line["identity"]
    token = f"DWG-{_today()}-{receipt_id}-L{line['line_no']}"
    receipt_line = {
        "line_no": line["line_no"],
        "行号": line["line_no"],
        "ok": True,
        "passed": True,
        "outcome": outcome,
        "结果": outcome,
        "values": values,
        "图纸编号": values.get("图纸编号", ""),
        "绘制年代": values.get("绘制年代", ""),
        "覆盖部位": values.get("覆盖部位", ""),
        "附件": values.get("附件", ""),
        "identity": identity,
        "图纸身份": identity,
        "drawing_id": drawing_id,
        "图纸ID": drawing_id,
        "version": version,
        "版次": str(version),
        "token": token,
        "凭据": token,
        "errors": [],
        "错误": [],
    }
    return receipt_line, new_drawing


def _find_precheck(precheck_id: Any) -> dict[str, Any] | None:
    if precheck_id in (None, ""):
        return None
    for record in store.rows(BATCH_TABLE):
        if str(record.get("precheck_id")) == str(precheck_id) or str(record.get("id")) == str(precheck_id):
            if record.get("kind") == "预检":
                return record
    return None


def commit(payload: Any) -> dict[str, Any]:
    """确认导入：重新校验每一行，失败行保留原行号和错误，通过行发布图纸。"""
    if not isinstance(payload, dict):
        payload = {"rows": payload}
    precheck_id = payload.get("precheck_id") or payload.get("预检号") or payload.get("batch_id")
    precheck_record = _find_precheck(precheck_id)
    collected = _collect_rows(payload)
    if not collected and precheck_record is not None:
        collected = [
            (int(line.get("line_no", index + 1)), dict(line.get("values") or line))
            for index, line in enumerate(precheck_record.get("lines", []))
        ]

    # 确认导入、发布修订版与登记提交共用同一把锁：其他图纸员同时发布的同身份
    # 修订版不会插到本次逐行处理中间，所以每份图纸只有一个生效版次。
    with PUBLICATION_LOCK:
        receipts = store.rows(RECEIPT_TABLE)
        receipt_id = _next_id(receipts)
        receipt_no = f"RCP-{_today()}-{receipt_id}"
        batch_id = precheck_record.get("precheck_id") if precheck_record else f"IMP-{_today()}-{receipt_id}"
        lines: list[dict[str, Any]] = []
        accepted: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        new_drawings: list[dict[str, Any]] = []

        for line_no, raw in collected:
            validated = validate_line(raw, line_no)
            if not validated["ok"]:
                validated["outcome"] = "预检失败，保留原行号与错误"
                validated["结果"] = validated["outcome"]
                validated["drawing_id"] = None
                validated["图纸ID"] = None
                validated["token"] = ""
                validated["凭据"] = ""
                lines.append(validated)
                failed.append(validated)
                continue
            receipt_line, drawing = _register_drawing(
                validated, batch_id=batch_id, receipt_id=receipt_id
            )
            lines.append(receipt_line)
            accepted.append(receipt_line)
            if drawing is not None:
                new_drawings.append(drawing)

        receipt = {
            "id": receipt_id,
            "receipt_id": receipt_id,
            "回执号": receipt_no,
            "receipt_no": receipt_no,
            "batch_id": batch_id,
            "批次号": batch_id,
            "precheck_id": precheck_id,
            "status": "全部受理" if not failed else ("部分受理" if accepted else "未受理"),
            "created_at": _now(),
            "summary": {
                "total": len(lines),
                "accepted": len(accepted),
                "failed": len(failed),
                "published": len(new_drawings),
            },
            "total": len(lines),
            "accepted": len(accepted),
            "failed": len(failed),
            "lines": lines,
            "items": lines,
            "errors": failed,
            "error_lines": failed,
        }
        receipts.append(receipt)

        if precheck_record is not None:
            precheck_record["status"] = "已确认" if not failed else "已确认（含错误行）"
            precheck_record["receipt_id"] = receipt_id

        batches = store.rows(BATCH_TABLE)
        batches.append({
            "id": _next_id(batches),
            "kind": "导入",
            "precheck_id": precheck_record.get("precheck_id") if precheck_record else "",
            "receipt_id": receipt_id,
            "回执号": receipt_no,
            "status": receipt["status"],
            "created_at": receipt["created_at"],
            "summary": receipt["summary"],
        })

    result = {
        "ok": not failed,
        "message": (
            "导入完成" if not failed
            else f"导入完成，{len(accepted)} 行受理、{len(failed)} 行保留错误"
        ),
        "receipt": receipt,
        "entry": receipt,
    }
    result.update(receipt)
    return result


# ── 核验清单与领图 ──
def _matches(item: dict[str, Any], filters: dict[str, str]) -> bool:
    for field, expected in filters.items():
        if not expected:
            continue
        if field in ("图纸编号", "绘制年代", "覆盖部位", "图纸身份", "附件", "状态"):
            if expected.casefold() not in str(item.get(field, "")).casefold():
                return False
    return True


def batch_summary() -> dict[str, Any]:
    """给核验清单页面用的批次/回执统计。"""
    batches = [dict(record) for record in store.rows(BATCH_TABLE)]
    prechecks = [record for record in batches if record.get("kind") == "预检"]
    receipts = [dict(record) for record in store.rows(RECEIPT_TABLE)]
    return {
        "prechecks": len(prechecks),
        "receipts": len(receipts),
        "latest_precheck": prechecks[-1].get("precheck_id") if prechecks else "",
        "latest_receipt": receipts[-1].get("回执号") if receipts else "",
        "error_lines": sum(
            int((receipt.get("summary") or {}).get("failed", 0))
            for receipt in receipts
        ),
    }


def list_checklist(filters: dict[str, str] | None = None, include_superseded: bool = False) -> dict[str, Any]:
    """构件登记核验清单：只含已提交导入并发布的图纸，绝不包含仅在预检中的图。"""
    filters = {key: value for key, value in (filters or {}).items() if _text(value)}
    items = []
    for entry in store.rows(CHECKLIST_TABLE):
        if not include_superseded and entry.get("有效") is not True:
            continue
        if _matches(entry, filters):
            items.append(dict(entry))
    return {"ok": True, "items": items, "total": len(items), "checklist": items}


def list_evidence(filters: dict[str, str] | None = None) -> dict[str, Any]:
    """已领用附件查询：包含被修订版替代的旧附件，附件始终可查。"""
    filters = {key: value for key, value in (filters or {}).items() if _text(value)}
    items = []
    for evidence in store.rows(EVIDENCE_TABLE):
        if _matches(evidence, filters):
            if "构件名称" in filters and filters["构件名称"].casefold() not in str(evidence.get("构件名称", "")).casefold():
                continue
            items.append(dict(evidence))
    return {"ok": True, "items": items, "total": len(items), "evidence": items}


def _find_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    for receipt in store.rows(RECEIPT_TABLE):
        for line in receipt.get("lines", []):
            if line.get("凭据") == token or line.get("token") == token:
                return line
    return None


def _find_drawing(drawing_id: Any) -> dict[str, Any] | None:
    drawing_id = _to_int(drawing_id)
    if drawing_id is None:
        return None
    for row in store.rows("historical_drawing"):
        if _to_int(row.get("id")) == drawing_id:
            return row
    return None


def claim(payload: Any) -> dict[str, Any]:
    """领图：无有效凭据也可办理（记待补领），附件一并记录可查。"""
    if not isinstance(payload, dict):
        payload = {}
    token = _text(payload.get("凭据") or payload.get("token") or payload.get("凭证"))
    drawing_id = payload.get("drawing_id") or payload.get("图纸ID") or payload.get("drawingId")
    token_line = _find_token(token)
    token_valid = False
    if token_line is not None:
        token_drawing_id = token_line.get("drawing_id")
        if drawing_id in (None, ""):
            drawing_id = token_drawing_id
        token_valid = token_line.get("ok") is True and _to_int(token_drawing_id) == _to_int(drawing_id)

    drawing = _find_drawing(drawing_id)
    if drawing is None and token_line is not None:
        drawing = _find_drawing(token_line.get("drawing_id"))
    if drawing is None:
        message = (
            "领图失败：该图纸仅在预检中或尚未发布，不能领用"
            if drawing_id not in (None, "")
            else "领图失败：未指定已发布图纸"
        )
        return {"ok": False, "message": message, "evidence": None}

    identity = str(drawing.get("图纸身份") or "")
    if not identity:
        identity = identity_of(drawing.get("图纸编号"), drawing.get("绘制年代"), drawing.get("覆盖部位"))

    # 与发布修订版共用一把锁：领用到的是此刻实际生效的版次，而不是更早的快照。
    with PUBLICATION_LOCK:
        current = effective_drawing(identity) or drawing
        checklist = _checklist_entry(identity)
        if checklist is None:
            return {
                "ok": False,
                "message": "领图失败：该图纸仅在预检中或尚未发布，不能领用",
                "evidence": None,
            }
        evidence_rows = store.rows(EVIDENCE_TABLE)
        evidence_id = _next_id(evidence_rows)
        evidence = {
            "id": evidence_id,
            "证据号": f"EV-{_today()}-{evidence_id}",
            "evidence_id": evidence_id,
            "凭据": token,
            "token": token,
            "凭据有效": token_valid,
            "领用人": _text(payload.get("领用人") or payload.get("operator")) or "构件员",
            "构件名称": _text(payload.get("构件名称")) or _text(payload.get("component")) or "",
            "drawing_id": current.get("id"),
            "图纸ID": current.get("id"),
            "领用图纸ID": drawing.get("id"),
            "图纸编号": current.get("图纸编号", ""),
            "绘制年代": current.get("绘制年代", ""),
            "覆盖部位": current.get("覆盖部位", ""),
            "图纸身份": identity,
            "领用版次": drawing.get("版次", ""),
            "实际生效版次": current.get("版次", ""),
            "附件": drawing.get("附件", ""),
            "领用附件": drawing.get("附件", ""),
            "状态": "已领用" if token_valid else "待补领",
            "已替代": drawing.get("id") != current.get("id"),
            "有效附件": drawing.get("id") == current.get("id"),
            "created_at": _now(),
        }
        evidence_rows.append(evidence)
    message = "已凭有效凭据领图" if token_valid else "未取得有效凭据，已记为待补领，可继续办理登记"
    return {"ok": True, "message": message, "evidence": evidence, "entry": evidence}


def _resolve_evidence(values: dict[str, Any]) -> dict[str, Any] | None:
    evidence_id = (
        values.get("图纸证据ID")
        or values.get("证据ID")
        or values.get("证据号")
        or values.get("evidence_id")
    )
    evidence_id = _to_int(evidence_id)
    if evidence_id is None:
        return None
    for row in store.rows(EVIDENCE_TABLE):
        if _to_int(row.get("id")) == evidence_id:
            return row
    return None


def resolve_component_selection(values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """解析构件登记所选取的图纸证据，并返回提交时实际生效的版次。"""
    values = values or {}
    evidence = _resolve_evidence(values)
    identity = ""
    source_drawing: dict[str, Any] | None = None
    token = ""

    explicit_identity = ""
    if _text(values.get("图纸编号") or values.get("drawing_no")):
        number = _text(values.get("图纸编号") or values.get("drawing_no"))
        era = _text(values.get("绘制年代") or values.get("era"))
        part = _text(values.get("覆盖部位") or values.get("part"))
        if era and part:
            explicit_identity = identity_of(number, era, part)

    if evidence is not None:
        identity = str(evidence.get("图纸身份") or "")
        token = str(evidence.get("凭据") or "")
        source_drawing = _find_drawing(evidence.get("领用图纸ID") or evidence.get("drawing_id"))
        if explicit_identity and identity and explicit_identity != identity:
            return None, (
                "所选图纸证据与填写的图纸编号/年代/覆盖部位不一致，"
                "请按核验清单中的同一份图纸登记"
            )

    if not identity:
        drawing_id = (
            values.get("图纸ID")
            or values.get("drawing_id")
            or values.get("引用图纸ID")
        )
        if drawing_id not in (None, ""):
            source_drawing = _find_drawing(drawing_id)
            if source_drawing is None:
                return None, "所选图纸不存在，可能尚未导入发布或仅在预检中"
            identity = str(source_drawing.get("图纸身份") or "")

    if not identity:
        token_value = _text(values.get("图纸凭据") or values.get("凭据") or values.get("token"))
        token_line = _find_token(token_value)
        if token_line is not None:
            token = token_value
            source_drawing = _find_drawing(token_line.get("drawing_id"))
            if source_drawing is not None:
                identity = str(source_drawing.get("图纸身份") or "")

    if not identity and _text(values.get("图纸身份") or values.get("identity")):
        identity = _text(values.get("图纸身份") or values.get("identity"))

    if not identity and _text(values.get("图纸编号") or values.get("drawing_no")):
        number = _text(values.get("图纸编号") or values.get("drawing_no"))
        era = _text(values.get("绘制年代") or values.get("era"))
        part = _text(values.get("覆盖部位") or values.get("part"))
        if not era or not part:
            return None, (
                "图纸编号不能单独确定同一份图纸：同编号图纸可能属于不同年代或区域，"
                "请同时提供绘制年代和覆盖部位"
            )
        identity = identity_of(number, era, part)

    if not identity:
        return None, ""

    current = effective_drawing(identity)
    if current is None:
        return None, "所选图纸仅在预检中或尚未发布，不能用于新的构件登记"
    checklist = _checklist_entry(identity)
    if checklist is None or checklist.get("drawing_id") != current.get("id"):
        return None, "所选图纸尚未同步到构件登记核验清单，不能用于新的构件登记"

    evidence = evidence or None
    selection = {
        "identity": identity,
        "source_drawing": source_drawing or current,
        "current_drawing": current,
        "evidence": evidence,
        "token": token,
        "checklist": checklist,
    }
    return selection, ""


def create_registration_evidence(
    selection: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    """构件登记选取图纸时记录一次领用；没有凭据也继续办理。"""
    existing = _resolve_evidence(values)
    if existing is not None:
        return existing
    current = selection["current_drawing"]
    token = _text(values.get("图纸凭据") or values.get("凭据") or selection.get("token") or "")
    token_line = _find_token(token)
    token_valid = bool(
        token_line
        and token_line.get("ok") is True
        and _to_int(token_line.get("drawing_id")) == _to_int(selection["source_drawing"].get("id"))
    )
    evidence_rows = store.rows(EVIDENCE_TABLE)
    evidence_id = _next_id(evidence_rows)
    evidence = {
        "id": evidence_id,
        "证据号": f"EV-{_today()}-{evidence_id}",
        "evidence_id": evidence_id,
        "凭据": token,
        "token": token,
        "凭据有效": token_valid,
        "领用人": _text(values.get("登记人")) or "构件员",
        "构件名称": _text(values.get("构件名称")),
        "drawing_id": current.get("id"),
        "图纸ID": current.get("id"),
        "领用图纸ID": selection["source_drawing"].get("id"),
        "图纸编号": current.get("图纸编号", ""),
        "绘制年代": current.get("绘制年代", ""),
        "覆盖部位": current.get("覆盖部位", ""),
        "图纸身份": selection["identity"],
        "领用版次": selection["source_drawing"].get("版次", ""),
        "实际生效版次": current.get("版次", ""),
        "附件": selection["source_drawing"].get("附件", ""),
        "领用附件": selection["source_drawing"].get("附件", ""),
        "状态": "已领用" if token_valid else "待补领",
        "已替代": selection["source_drawing"].get("id") != current.get("id"),
        "有效附件": selection["source_drawing"].get("id") == current.get("id"),
        "created_at": _now(),
    }
    evidence_rows.append(evidence)
    return evidence


def registration_fields(selection: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """登记引用字段：附件取提交时实际生效版次，领用记录仍保留领到的那份。"""
    current = selection["current_drawing"]
    return {
        "图纸证据ID": evidence.get("id"),
        "图纸证据": current.get("附件", "") or evidence.get("附件", ""),
        "图纸ID": current.get("id"),
        "图纸编号": current.get("图纸编号", ""),
        "绘制年代": current.get("绘制年代", ""),
        "覆盖部位": current.get("覆盖部位", ""),
        "图纸身份": selection["identity"],
        "图纸版次": current.get("版次", ""),
        "实际生效图纸ID": current.get("id"),
    }


# ── 逐份登记入口（历史图纸原有 create 路径也能同步清单） ──
def register_individual(entry: dict[str, Any]) -> dict[str, Any] | None:
    number = _text(entry.get("图纸编号"))
    era = _text(entry.get("绘制年代"))
    part = _text(entry.get("覆盖部位"))
    if not (number and era and part):
        return None
    identity = identity_of(number, era, part)
    entry["图纸身份"] = identity
    version = _version(entry.get("版次"))
    if version is None:
        version = 1
    entry.setdefault("版次", str(version))
    entry.setdefault("版次序号", version)
    existing = effective_drawing(identity)
    if existing is not None and existing.get("id") != entry.get("id"):
        if int(existing.get("版次序号", 1)) >= version:
            entry["有效版次"] = False
            entry["图纸状态"] = "已替代"
            entry["替代图纸ID"] = existing["id"]
            return None
        _mark_old_drawings(identity, entry["id"])
    entry["有效版次"] = True
    entry["图纸状态"] = "有效"
    entry["发布状态"] = "已发布"
    return _publish_checklist(entry, entry.get("导入批次", "逐份登记"))


def find_receipt(identifier: Any) -> dict[str, Any] | None:
    if identifier is None:
        return None
    text = str(identifier)
    for receipt in store.rows(RECEIPT_TABLE):
        if str(receipt.get("id")) == text or str(receipt.get("回执号")) == text:
            return receipt
        if str(receipt.get("batch_id")) == text or str(receipt.get("precheck_id")) == text:
            return receipt
    return None


def list_receipts() -> dict[str, Any]:
    receipts = [dict(receipt) for receipt in store.rows(RECEIPT_TABLE)]
    return {"ok": True, "items": receipts, "total": len(receipts), "receipts": receipts}
