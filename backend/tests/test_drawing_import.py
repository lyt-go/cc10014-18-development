"""历史图纸批量导入、版次生效与构件登记核验清单测试。"""
from __future__ import annotations

import threading
import time

import pytest

from app.services import drawing_import as di
from app.services.component_registry import ComponentRegistryService
from app.services.historical_drawing import HistoricalDrawingService
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    store._idempotent_log.clear()
    store._resource_locks.clear()
    from app.seed import SEED_ROWS
    for name in list(store._tables.keys()):
        if name in SEED_ROWS:
            store._tables[name] = [dict(row) for row in SEED_ROWS[name]]
        else:
            store._tables[name] = []
    yield


component_service = ComponentRegistryService()
drawing_service = HistoricalDrawingService()

TWO_DRAWINGS = """图纸编号,绘制年代,覆盖部位,附件,绘制内容,版次
R-21,1931年,南廊,R-21-1931-南廊.pdf,南廊梁架图,1
R-21,1952年,北廊,R-21-1952-北廊.pdf,北廊梁架图,1
"""


def import_content(content: str, *, confirm: bool = True):
    pre = di.precheck({"content": content})
    if not confirm:
        return pre, None
    return pre, di.commit({"precheck_id": pre["precheck_id"]})


def component(name: str, **refs):
    entry, missing = component_service.create_entry({
        "构件名称": name, "所属建筑": "大殿", "材质类型": "木", **refs,
    })
    return entry, missing


# ── 批量导入与编号/年代/部位维度 ──
def test_two_same_number_drawings_are_distinct_identities():
    pre, receipt = import_content(TWO_DRAWINGS)
    assert pre["summary"] == {"total": 2, "passed": 2, "failed": 0}
    assert receipt["summary"]["accepted"] == 2
    identities = {line["图纸身份"] for line in receipt["lines"]}
    assert len(identities) == 2, "同编号不同年代/区域应是两份图纸"

    checklist = di.list_checklist()["items"]
    assert len(checklist) == 2
    assert {item["绘制年代"] for item in checklist} == {"1931年", "1952年"}
    assert {item["覆盖部位"] for item in checklist} == {"南廊", "北廊"}


def test_components_reference_each_location_separately():
    _, receipt = import_content(TWO_DRAWINGS)
    south, missing = component(
        "南廊梁", 图纸编号="R-21", 绘制年代="1931年", 覆盖部位="南廊",
        图纸凭据=receipt["lines"][0]["凭据"],
    )
    assert south is not None, missing
    north, missing = component("北廊梁", 图纸编号="R-21", 绘制年代="1952年", 覆盖部位="北廊")
    assert north is not None, missing
    assert south["图纸ID"] != north["图纸ID"]
    assert south["绘制年代"] == "1931年" and north["绘制年代"] == "1952年"
    assert south["覆盖部位"] == "南廊" and north["覆盖部位"] == "北廊"
    # 两份图纸各自只有一个有效来源
    checklist = di.list_checklist()["items"]
    assert len(checklist) == 2
    assert all(row["有效"] is True for row in checklist)
    assert len({row["图纸身份"] for row in checklist}) == 2


def test_number_alone_cannot_locate_a_drawing():
    import_content(TWO_DRAWINGS)
    entry, missing = component("某梁", 图纸编号="R-21")
    assert entry is None
    assert "年代" in missing[0] and "部位" in missing[0]


# ── 预检：不发布、不进入清单 ──
def test_precheck_does_not_publish_or_enter_checklist():
    before = len(store.rows("historical_drawing"))
    pre = di.precheck({"content": TWO_DRAWINGS})
    assert pre["status"] == "预检完成"
    assert di.list_checklist()["total"] == 0
    assert len(store.rows("historical_drawing")) == before
    di.precheck({"content": TWO_DRAWINGS})
    assert len(store.rows("historical_drawing")) == before
    entry, missing = component("预检梁", 图纸编号="R-21", 绘制年代="1931年", 覆盖部位="南廊")
    assert entry is None
    assert "预检" in missing[0]


def test_failed_line_keeps_line_number_and_error_on_reimport():
    bad = """图纸编号,绘制年代,覆盖部位,附件
R-23,1961年,西廊,R-23.docx
R-24,1962年,西廊,R-24.pdf
"""
    pre, receipt = import_content(bad)
    assert pre["summary"]["failed"] == 1
    assert pre["errors"][0]["行号"] == 2
    assert "附件格式无效" in pre["errors"][0]["错误"][0]
    assert receipt["summary"]["accepted"] == 1
    assert receipt["summary"]["failed"] == 1
    assert receipt["errors"][0]["行号"] == 2
    assert "附件格式无效" in receipt["errors"][0]["错误"][0]

    # 未修正前重复导入，仍显示同一原行号和错误
    _, again = import_content(bad)
    assert again["summary"]["failed"] == 1
    assert again["errors"][0]["行号"] == 2
    assert "附件格式无效" in again["errors"][0]["错误"][0]
    assert again["summary"]["published"] == 0, "已作为 R-24 发布的行不重复建图纸"

    fixed = """图纸编号,绘制年代,覆盖部位,附件
R-23,1961年,西廊,R-23.pdf
R-24,1962年,西廊,R-24.pdf
"""
    _, receipt2 = import_content(fixed)
    assert receipt2["summary"]["accepted"] == 2
    assert receipt2["summary"]["failed"] == 0
    # 每份图纸仍各只有一个有效来源
    checklist = di.list_checklist()["items"]
    identities = {item["图纸身份"] for item in checklist}
    assert len(identities) == len(checklist)
    assert all(item["有效"] is True for item in checklist)
    assert {item["图纸编号"] for item in checklist} == {"R-23", "R-24"}


def test_duplicate_reimport_creates_no_second_identity():
    _, receipt = import_content(TWO_DRAWINGS)
    _, again = import_content(TWO_DRAWINGS)
    assert again["summary"]["accepted"] == 2
    assert again["summary"]["published"] == 0
    drawings = [row for row in store.rows("historical_drawing") if row.get("图纸身份")]
    identities = {row["图纸身份"] for row in drawings}
    assert len(identities) == 2
    assert len([row for row in drawings if row.get("有效版次")]) == 2
    assert len(di.list_checklist()["items"]) == 2


# ── 修订版与提交时生效版次 ──
REVISION = """图纸编号,绘制年代,覆盖部位,附件,绘制内容,版次
R-21,1931年,南廊,R-21-1931-南廊-v2.pdf,南廊梁架修订图,2
"""


def test_revision_supersedes_but_old_attachment_still_queryable():
    _, receipt = import_content(TWO_DRAWINGS)
    evidence = di.claim({
        "drawing_id": receipt["lines"][0]["图纸ID"],
        "凭据": receipt["lines"][0]["凭据"],
        "构件名称": "南廊梁",
    })["evidence"]

    _, rev = import_content(REVISION)
    assert rev["summary"]["published"] == 1
    checklist = di.list_checklist()["items"]
    south = [item for item in checklist if item["覆盖部位"] == "南廊"]
    assert len(south) == 1 and south[0]["版次"] == "2"

    evidence_items = di.list_evidence()["items"]
    assert len(evidence_items) == 1
    old = evidence_items[0]
    assert old["领用附件"] == "R-21-1931-南廊.pdf"
    assert old["已替代"] is True


def test_registration_uses_version_effective_at_submit_time():
    _, receipt = import_content(TWO_DRAWINGS)
    south_line = receipt["lines"][0]
    claim = di.claim({"drawing_id": south_line["图纸ID"], "凭据": south_line["凭据"]})
    assert claim["ok"]

    # 领图之后、登记之前，另一人发布了同编号同覆盖部位的修订版。
    _, rev = import_content(REVISION)

    entry, missing = component(
        "南廊梁", 图纸证据ID=claim["evidence"]["id"], 图纸凭据=south_line["凭据"],
    )
    assert entry is not None, missing
    assert entry["图纸版次"] == "2", "登记应引用提交时实际生效的覆盖版次"
    assert entry["图纸ID"] != south_line["图纸ID"]
    assert entry["图纸证据"] == "R-21-1931-南廊-v2.pdf"
    # 核验清单只剩一个南廊有效来源
    south_items = [i for i in di.list_checklist()["items"] if i["覆盖部位"] == "南廊"]
    assert len(south_items) == 1 and south_items[0]["版次"] == "2"


def test_claim_without_valid_token_is_allowed_and_recorded_pending():
    _, receipt = import_content(TWO_DRAWINGS)
    bad_claim = di.claim({"drawing_id": receipt["lines"][0]["图纸ID"], "凭据": "DWG-BOGUS"})
    assert bad_claim["ok"] is True
    assert bad_claim["evidence"]["状态"] == "待补领"
    assert bad_claim["evidence"]["附件"] == "R-21-1931-南廊.pdf"

    entry, missing = component("南廊梁", 图纸证据ID=bad_claim["evidence"]["id"])
    assert entry is not None, missing
    assert entry["图纸版次"] == "1"


def test_concurrent_import_same_identity_keeps_one_effective_version():
    _, receipt = import_content(TWO_DRAWINGS)
    south_line = receipt["lines"][0]

    results: list[str] = []

    def registering():
        for _ in range(6):
            claim = di.claim({"drawing_id": south_line["图纸ID"], "凭据": south_line["凭据"]})
            entry, missing = component("南廊梁并发", 图纸证据ID=claim["evidence"]["id"])
            assert entry is not None, missing
            results.append(entry["图纸版次"])

    def publishing():
        time.sleep(0.02)
        import_content(REVISION)

    threads = [threading.Thread(target=registering), threading.Thread(target=publishing)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(results) <= {"1", "2"}
    # 并发登记 + 发布修订版后，南廊仍只有一个有效版次
    south = [item for item in di.list_checklist()["items"] if item["覆盖部位"] == "南廊"]
    assert len(south) == 1 and south[0]["版次"] == "2"
    # 修订版发布后提交的登记不得再指向旧版次；发布前提交的可为第1版
    entry_rows = [row for row in store.rows("component_registry") if row.get("构件名称") == "南廊梁并发"]
    assert entry_rows
    # 关键：落库版次始终指向“提交时实际生效”的那份图纸，不会把领图快照写死
    for row in entry_rows:
        drawing = next(
            r for r in store.rows("historical_drawing") if r["id"] == int(row["图纸ID"])
        )
        assert row["图纸版次"] == drawing["版次"]
    # 修订版确认发布后再登记，必定引用生效的修订版
    final, missing = component("南廊梁并发", 图纸编号="R-21", 绘制年代="1931年", 覆盖部位="南廊")
    assert final is not None, missing
    assert final["图纸版次"] == "2"


def test_individual_registration_joins_same_identity_model():
    entry, missing = drawing_service.create_entry({
        "图纸编号": "R-30", "绘制年代": "1935年", "覆盖部位": "东廊",
        "绘制内容": "东廊平面图", "附件": "R-30-1935-东廊.pdf",
    })
    assert entry is not None, missing
    assert entry["图纸身份"] == di.identity_of("R-30", "1935年", "东廊")
    assert di.list_checklist({"图纸编号": "R-30"})["items"], "逐份登记也应同步核验清单"


def test_registration_with_only_receipt_token_resolves_identity():
    _, receipt = import_content(TWO_DRAWINGS)
    north_line = receipt["lines"][1]
    entry, missing = component("北廊梁", 图纸凭据=north_line["凭据"])
    assert entry is not None, missing
    assert entry["覆盖部位"] == "北廊"
    assert entry["绘制年代"] == "1952年"


def test_fixing_failed_line_keeps_original_drawings_single_source():
    _, receipt = import_content(TWO_DRAWINGS)
    bad = """图纸编号,绘制年代,覆盖部位,附件
R-23,1961年,西廊,R-23.docx
R-24,1962年,西廊,R-24.pdf
"""
    _, bad_receipt = import_content(bad)
    assert bad_receipt["errors"][0]["行号"] == 2
    fixed = """图纸编号,绘制年代,覆盖部位,附件
R-23,1961年,西廊,R-23.pdf
R-24,1962年,西廊,R-24.pdf
"""
    _, fixed_receipt = import_content(fixed)
    assert fixed_receipt["summary"]["failed"] == 0

    checklist = di.list_checklist()["items"]
    by_identity: dict[str, list[dict]] = {}
    for item in checklist:
        by_identity.setdefault(item["图纸身份"], []).append(item)
    # 每份图纸只有一个有效来源，包括最早导入的两份 R-21
    assert all(len(rows) == 1 and rows[0]["有效"] is True for rows in by_identity.values())
    r21 = [item for item in checklist if item["图纸编号"] == "R-21"]
    assert len(r21) == 2
    assert {item["覆盖部位"] for item in r21} == {"南廊", "北廊"}


def test_individual_duplicate_identity_rejected():
    first, missing = drawing_service.create_entry({
        "图纸编号": "R-40", "绘制年代": "1940年", "覆盖部位": "东廊", "绘制内容": "东廊图",
    })
    assert first is not None, missing
    second, missing = drawing_service.create_entry({
        "图纸编号": "R-40", "绘制年代": "1940年", "覆盖部位": "东廊", "绘制内容": "东廊图",
    })
    assert second is None
    assert "不重复登记" in missing[0]
    assert len(di.list_checklist({"图纸编号": "R-40"})["items"]) == 1


def test_precheck_without_parseable_rows_reports_error():
    result = di.precheck({"content": "这不是一张表"})
    assert result["ok"] is False
    assert "未能" in result["message"]
    assert result["summary"]["total"] == 0


def test_two_confirmations_of_same_listing_race_keep_single_version():
    """两个图纸员几乎同时确认同一批导入，同身份只发布一份图纸。"""
    results: list[int] = []

    def confirming():
        pre = di.precheck({"content": TWO_DRAWINGS})
        receipt = di.commit({"precheck_id": pre["precheck_id"], "content": TWO_DRAWINGS})
        results.append(receipt["summary"]["published"])

    threads = [threading.Thread(target=confirming) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    drawings = [row for row in store.rows("historical_drawing") if row.get("图纸身份")]
    assert len(drawings) == 2, "同一清单并发确认不应产生重复图纸"
    assert sum(results) == 2, "两份图纸身份各发布一次"
