# 古建筑修缮管理平台

文保工程师、工匠和监理共同管理古建筑修缮工序，审核修缮方案，签收变更后按确认版施工。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/components/DrawingImportPanel.vue  图纸批量导入/预检/回执面板
│   ├── src/components/DrawingEvidencePicker.vue 构件登记核验清单选图
│   ├── src/components/DrawingEvidenceList.vue 已领用附件查询
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/drawing_import.py  图纸预检/导入回执/核验清单/领图接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/drawing_import.py  图纸身份、版次与发布同步
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 建筑勘查 | `building_survey` | 勘查记录 | 建筑名称、勘查部位、病害类型 |
| 修缮方案 | `restoration_plan` | 修缮方案 | 方案编号、对应勘查、修缮工艺 |
| 材料核验 | `material_verify` | 材料核验单 | 材料名称、规格型号、供应商 |
| 工匠分配 | `craftsman_assign` | 工匠分配单 | 工匠姓名、技艺类别、资质等级 |
| 工序编排 | `work_sequence` | 工序卡 | 工序名称、前置工序、所需工匠 |
| 质量检查 | `quality_inspect` | 检查记录 | 检查工序、检查项目、技术标准 |
| 历史图纸 | `historical_drawing` | 历史图纸 | 图纸编号、绘制年代、覆盖部位、版次、附件 |
| 构件登记 | `component_registry` | 构件档案 | 构件名称、所属建筑、材质类型、图纸证据 |
| 环境监测 | `env_monitor` | 监测数据 | 监测位置、温度读数、湿度读数 |
| 修缮日志 | `restoration_log` | 施工日志 | 施工日期、对应工序、施工内容 |
| 验收记录 | `acceptance_record` | 验收报告 | 验收工序、验收标准、验收结论 |
| 修缮支出 | `funding_use` | 支出记录 | 支出项目、支出类别、预算金额 |
| 档案归档 | `archive_mgmt` | 修缮档案 | 档案名称、档案类别、形成日期 |
| 专家评审 | `expert_review` | 评审意见 | 评审方案、专家姓名、专业领域 |
| 安全交底 | `safety_briefing` | 交底记录 | 交底工序、危险源、防护措施 |
| 施工排期 | `construction_schedule` | 排期计划 | 计划工序、开始日期、结束日期 |
| 变更签证 | `change_order` | 变更签证单 | 变更事项、变更原因、影响工序 |
| 竣工总结 | `completion_summary` | 竣工报告 | 修缮项目、开工日期、竣工日期 |

## 历史图纸批量导入

历史图纸支持逐份登记和批量导入两条路径。批量导入先预检再确认导入：

1. `POST /api/historical_drawing/import/precheck` 预检清单（CSV/TSV 文本或行数组），
   逐行返回结果与错误，**不创建图纸**；失败行保留原始文件行号。
2. `POST /api/historical_drawing/import/commit` 确认导入，通过行发布图纸并生成导入回执
   （`/api/historical_drawing/import/receipts`），同时把发布结果同步到
   `GET /api/component_registry/checklist` 构件登记核验清单。
3. 构件登记从核验清单选用图纸证据（`POST /api/component_registry/claim` 领图，
   `GET /api/component_registry/evidence` 查已领用附件），登记提交时按提交时实际
   生效的版次落库。

关键口径：

- **图纸身份 = 图纸编号 + 绘制年代 + 覆盖部位**。两份同编号图纸若年代或覆盖部位
  不同就是两份图纸，单凭编号不能合并；填编号时须同时给出年代与部位。
- **预检中的图纸不会进入核验清单**，新的构件登记也选不到。
- **重复导入同一行只记一个图纸身份**，已发布的图纸直接复用，不重复建图。
- **同身份更高版次按修订发布**：旧版次转为“已替代”仍可查询，每个身份只有一个
  有效版次；修订版发布后，之前领用的旧附件保留可查（`已替代` 标记）。
- **领图没有有效凭据不阻断办理**，记为“待补领”后可继续登记；凭据来自导入回执。
- 确认导入、修订发布、领图和登记提交共用同一把发布锁，所以构件员提交的是当时
  实际生效的覆盖版次，而不是领图时的快照。
- 附件仅接受单个图纸文件（pdf/jpg/jpeg/png/tif/tiff）；无效附件在预检与导入回执
  中都保留原行号与错误，修正后再次导入即可。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：应用开发
子方向：web前端/全栈开发
