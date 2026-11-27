"""构件登记领域服务：登记古建筑构件后联动修缮方案中对应修复计划。

构件登记可从历史图纸导入回执生成的核验清单中选取图纸证据：
* 提交登记时在发布锁内重新解析“提交时实际生效的覆盖版次”，不沿用领图快照；
* 没有取得有效领图凭据不阻断登记，按待补领记录附件；
* 仅在预检中的图纸不会出现在核验清单，新登记也不能引用。
"""
from __future__ import annotations

from typing import Any

from app.services import drawing_import
from app.services.base import BaseModuleService
from app.store import store


class ComponentRegistryService(BaseModuleService):
    MODULE = "component_registry"
    ENTITY = "构件档案"
    OPERATION_TERM = "确认构件"
    LIST_FIELDS = [
        "构件名称", "所属建筑", "材质类型", "年代判定", "保存状态", "修复历史", "登记人", "备注说明",
        "图纸编号", "绘制年代", "覆盖部位", "图纸版次", "图纸证据", "图纸证据ID", "图纸ID",
    ]
    REFERENCE_FIELDS = ["图纸编号", "绘制年代", "覆盖部位", "图纸版次", "图纸证据", "图纸证据ID", "图纸ID"]
    REQUIRED_FIELDS = ["构件名称", "所属建筑", "材质类型"]
    STATUS_ORDER = ["待登记", "已登记", "已鉴定", "待修复"]
    ACTION_RULES = {"确认构件": "已登记", "提交鉴定": "已鉴定", "标记修复": "待修复"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing

        reference_keys = [
            *self.REFERENCE_FIELDS,
            "图纸身份", "证据ID", "证据号", "图纸凭据", "凭据", "drawing_id", "evidence_id",
        ]
        drawing_values = {
            key: values.get(key)
            for key in reference_keys
            if values.get(key) not in (None, "")
        }
        has_reference = bool(drawing_values)
        reference_fields: dict[str, Any] = {}

        with drawing_import.PUBLICATION_LOCK:
            if has_reference:
                selection, reason = drawing_import.resolve_component_selection(drawing_values)
                if selection is None:
                    if reason:
                        return None, [reason]
                else:
                    evidence = drawing_import.create_registration_evidence(
                        selection, {**values, **drawing_values}
                    )
                    # 登记引用的是提交时实际生效版次；领用记录里仍保留当时领到的附件。
                    reference_fields = drawing_import.registration_fields(selection, evidence)

            rows = store.rows(self.MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in self.LIST_FIELDS})
            entry.update(reference_fields)
            entry["status"] = self.STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            rows.append(entry)

        return entry, []

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "提交鉴定":
            # 检查构件的所属建筑是否已有勘查记录
            building = entry.get("所属建筑", "")
            surveys = store.query_by_field("building_survey", "建筑名称", building)
            if not surveys:
                return False, f"建筑「{building}」尚无勘查记录，不能提交鉴定"
            if any(s.get("status") not in ("已提交", "已复核") for s in surveys):
                return False, f"建筑「{building}」勘查未完成"
        if action == "标记修复":
            # 标记修复前须有已通过的修缮方案
            plans = store.rows("restoration_plan")
            comp_name = entry.get("构件名称", "")
            matched = [
                p for p in plans
                if p.get("status") == "已通过" and comp_name in str(p.get("工艺要求", ""))
            ]
            if not matched:
                return False, f"构件「{comp_name}」缺少关联的已通过修缮方案"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> list[dict]:
        effects = []
        if action == "标记修复":
            # 标记后联动更新修缮方案中对应工序
            comp_name = entry.get("构件名称", "")
            for plan in store.rows("restoration_plan"):
                if comp_name in str(plan.get("工艺要求", "")):
                    plan["构件修复已登记"] = True
                    effects.append(dict(plan))
        return effects
