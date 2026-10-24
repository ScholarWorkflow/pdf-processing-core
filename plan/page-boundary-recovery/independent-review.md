# 独立计划审核

2026-10-24，review_core_page_boundary_fix；只读审核源码与候选，未执行测试、未修改产品。

- Requirement revision: R1。
- Plan revision: P1，implementation-plan.md。
- Test revision: T2，test-plan.md；T1 的业务目标与检查方式沿用，仅改用既有 unittest 主入口。
- Target repository revision: e82dd706cdde1156f9b0b8c0218ae6953a0968dc。
- Relevant runtime: 作者准备检查报告 PyMuPDF 1.26.6；标准 unittest，未要求新安装。

## 事实与决定

已读取计划所引三份现行规则全文。已核 runner.prepare_headings 的 3–4 位数字、首连续段、页码前插标题和印刷页充物理页路径；extract.py 的 page_texts 是返回字段、无约定自动 sidecar，fast 仍可能 OCR；scan_math.parse_md_pages、ocr_refresh_jobs.page_sections 与 worker contract 支持 PDF_PAGE，runner.plan/collect 尚只认旧标题。

逐页 native 全文去空白串与 source body 完整一致后，按既定页长映射原位置，足以避免数字候选与 footer 误归属。只插配对 PDF 1..N markers，不替换源文字、不推断原书物理页。不能相等或无文本层时保留源并报告边界核对；不会把人工修改回滚或引入新 OCR。完整既有边界仍兼容，残缺结构不叠加。两处 runner 消费检查是本变更直接影响。

## 执行计划结论

- P1 Requirement & Reality Grounding: PASS。
- P2 Solution Validity & Ownership: PASS。
- P3 Impact, Safety & Counterexample: PASS。
- P4 Executability: PASS。
- Gate review completeness: COMPLETE。
- Critical assumptions: N/A。
- Unresolved impacts: 无阻断项；OCR/手改正文不能自动对齐是明确保守限制，不要求全部遗留源自动恢复。
- Approved behavior changes: 用户本轮要求修复 core 页恢复，来源见 R1；不增加业务流程要求。
- Reused prior Plan Gate PASS: T2 只改测试调用，不触及 P1–P4，沿用本次 P1 审核。
- Required modifications / Missing evidence: N/A。
- Plan conclusion: APPROVED。

## 测试计划结论

设计审核通过；第一关必测清单通过；第二关 T2 执行步骤通过。五项目标覆盖直接变化的正常恢复、坐标/字节保留、已有及残缺边界、不可对齐不写入、两处 runner 消费。通过既有 unittest 和消费者结果断言检查，不增加全仓、OCR、重复层级验收或来源认证。

T2 仅因 pytest 不可用，改用既有文件 unittest.main 的显式类/方法选择；环境、目标及断言不变，第二关复核通过。

第三关与产品 diff 审核尚未进行；作者下一步按 P1/T2 实现并提交已有执行记录，再由本 reviewer 有界审核。方案批准不代表产品正确、测试通过或可合并。

## 首轮产品及结果审核

已完整审核 runner、SKILL.md、既有测试文件的当前 diff，以及 test-results.md 和首次 unittest.log。10 个聚焦测试通过，按 T2 方法直接采用；审核者未执行测试。计划批准维持，现有已测结果有效，但产品审核与第三关整体暂不能通过。

唯一 blocker（实现偏离，负责人：产品实现者）：collect 调用 _page_boundary_mode 未传配对 PDF 总页数，退化为用 marker 自身数量判断 1..len(markers)。两页源只留 marker 1 时仍被判完整，partial result 会缩小成未能定位的页 2。违反 P1 冻结方案第 6 项的完整 1..N 和残缺结构保护。最小修正为从 job.pdf 的现有 audit 取得连续 1..N 页数，缺失/不连续时保守不缩，不新增状态。只复核修正及 T2 第 3/5 项直接影响，其他结果复用。已完成本轮全部有界内容，无其他已发现 blocker。

## 修正复核与最终结论

collect 已使用 job.pdf 对应 audit 的连续 1..N 页数，缺失/不连续按 0 页保守拒绝边界；helper 总页数改为必填，各调用点已核。既有 partial_collect 测试加入残缺 marker 时保留两页成员的检查，定向复验 1 test / OK（/tmp/pdfcore-page-boundary-tests-nm9RuU/unittest.log）。其余 9 项沿用首次有效结果；未重跑且无需重跑无关项。

唯一实现 blocker 已关闭。当前工作树有界产品 diff 审核通过；第三关通过，T2 五项目标取得当前有效结果，无范围内未解决缺陷或未完成项。P1–P4、测试设计、第一/第二关维持通过。仍不声称真实 OCR 链或全仓回归通过；无远端 approve、comment、merge、发布操作。下一负责人为 parent/作者，按已有授权交付或决定发布。
