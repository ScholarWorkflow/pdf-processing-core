# T2 测试执行记录

- 产品：baseline e82dd706cdde1156f9b0b8c0218ae6953a0968dc 上本次工作树；执行计划 P1，测试计划 T2。
- 环境：云端隔离 TemporaryDirectory，Python 3.11+、PyMuPDF 1.26.6；pytest 准备检查不可用，按已审核 T2 使用原生 unittest。未运行旧 T1 正式测试。
- 命令：`PYTHONPATH=lib python .apm/skills/formula-repair/tests/test_formula_repair_runner.py PrepareHeadingsTest FormulaRepairRunnerTest.test_marker_boundaries_preserve_region_plan FormulaRepairRunnerTest.test_marker_boundaries_allow_partial_collect -v`
- 正式尝试：2026-10-24，首次，10 tests / OK，exit 0。
- 原始日志：`/tmp/pdfcore-page-boundary-tests-FHr8lf/unittest.log`，未提交日志。
- T2 清单 1–5 均通过：恢复页首/页尾真实边界与额外 footer；配对坐标及原字节保留；完整既有结构 no-op/残缺需核对；人工改文及无文本层不覆盖、页数差异不写入；runner 保留 region 与 partial 剩余页。
- 未执行：全仓、安装包认证、真实学习数据、OCR、付费模型；均不在 T2 范围内。不据本结果声称完整真实修复链已通过。
- 产品 diff 与第三关待独立审核；不得自行视作可合并。

## 独立 diff 审核修正与定向复验

- 发现并修复：collect 未传总页数会把仅含 PDF_PAGE:1 的两页源误判完整。现按 job.pdf 对应 audit 的连续 1..N 检查，缺失/不连续不缩小成员；未新增 state。
- 在既有 test_marker_boundaries_allow_partial_collect 中加入同一两页源缺少第二 marker 时保留完整成员的断言，属于 T2 第 3/5 项直接影响，不增加必测上限。
- 复验命令：PYTHONPATH=lib python .apm/skills/formula-repair/tests/test_formula_repair_runner.py FormulaRepairRunnerTest.test_marker_boundaries_allow_partial_collect -v。
- 结果：1 test / OK，exit 0；日志：/tmp/pdfcore-page-boundary-tests-nm9RuU/unittest.log。
- 原 10 项日志保留；其余不受影响结果沿用。该缺陷由静态审核发现，不能把此前全过测试视为此缺陷已验证。
