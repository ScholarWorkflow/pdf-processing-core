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

## PR #13 CI 依赖声明修正

- 首次远端 CI：run 40089795952，head 39d933e，95 passed / 9 subtests passed，唯一失败为 test_runtime_boundary.py::test_pdfx_importers_have_bounded_pep723_dependency。保留该历史失败，不据后续修正覆盖它。
- 原因：本次测试直接 import pdfx.scan_math，而既有 runtime boundary 用 AST 检查全部 .apm/skills/**/*.py 的 pdfx import；新增测试文件未附 bounded PEP 723 声明。
- 最小修正：只在该测试文件顶部添加与 runner 相同的 requires-python >=3.11 和 scholar-workflow-pdfx>=0.1.0,<0.2 元数据。产品实现、测试断言与扫描 contract 均不变；原 P1/T2 产品结论沿用。
- 定向验证：当前 Python 无 pytest，runpy 加载现有 test/test_runtime_boundary.py，直接调用原 test_pdfx_importers_have_bounded_pep723_dependency 函数，保留全部原断言，不另写判定器。
- 命令：python -c 'import runpy; runpy.run_path("test/test_runtime_boundary.py")["test_pdfx_importers_have_bounded_pep723_dependency"](); print("PASS: test_pdfx_importers_have_bounded_pep723_dependency")'
- 结果：PASS，exit 0。原始日志：/tmp/pdfcore-pep723-check-8jzahB/check.log。未重跑无关全回归；修正后的远端 CI 尚待 parent 发布后确认。

## P2 / T3：已完成补丁跨部分失败重试

- 触发：用户报告并经源码确认 collect 仅缩 members、claim 重用 RESULT、finalize 仅读末轮会丢早轮已完成页正文。此前成员测试不足以证明最终交付，旧整体通过被撤回；不受影响的分页/坐标/PEP723结果沿用。
- 本轮基线：PR #13 head 05934632e1a406ec2eb33157d2de41aee170d85c，P2/T3 经独立审核后实现；PR 此时仍 open/draft，未合并。
- 修改：collect 先在原 unit_dir 保存已接受 patches 与 SHA 再缩成员；claim 清旧 RESULT；同 SHA plan 保留快照引用，异 SHA/force 清旧进度；finalize 消费全部已接受轮次并统一原子写源。已知旧版 partial 计数却无快照时返回 needs_replan，不伪造完整修复。
- 命令：PYTHONPATH=lib python .apm/skills/formula-repair/tests/test_formula_repair_runner.py FormulaRepairRunnerTest.test_partial_patches_survive_result_overwrite FormulaRepairRunnerTest.test_partial_patches_reset_on_source_change -v
- 正式首次执行：2 tests / OK，exit 0；两轮覆盖测试包含直接 retry 与中间 replan 两子场景，以及已知旧版快照缺失的拒绝假成功断言。
- 业务结果：第1轮 source 未改，下一轮仅页2；新结果前 collect 不消费旧 RESULT；最后 source 同时包含页1与页2修复，markers 保留，返回应用2条patch，成功清理unit目录。用户改源后旧补丁坐标及缩成员进度失效，完整成员恢复且用户正文不变。
- 原始输出：/tmp/pdfcore-partial-patches-ZzWoWV/unittest.log。无失败运行被隐藏；未执行无关全回归、OCR、真实学习数据或付费模型。
- 产品修改与第三关待本轮独立审核；不得以旧 CI 通过代替本次最终正文结果。
