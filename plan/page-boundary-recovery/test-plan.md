# prepare-headings 页边界恢复测试计划

Test revision: T2。Requirement R1，Implementation P1，baseline e82dd706cdde1156f9b0b8c0218ae6953a0968dc。Proposed review status: pending。依据与范围同 implementation-plan.md；以下是本次必测上限，不做全仓/付费/真实学习数据/OCR 或安装认证。

## 必测清单与检查方式

使用 .apm/skills/formula-repair/tests/test_formula_repair_runner.py 既有 unittest 框架，增加 PrepareHeadingsTest 独立类；仅临时合成 PDF、source、audit，不伪造准备成功结果。

1. 正常恢复及数字无关：生成两页 native PDF，分别使用短页码和超过四位页码；页首或页尾含数字且页码后有 footer。源文本含容易误选的连续正文数字。调用 prepare_headings，检查恰好两页 markers，scan_math.parse_md_pages 与 refresh.page_sections/section_range 把正文、数字、footer 各归实际页；不要求数字连续。一次成功输出删除新增标记/必要换行后必须等于原 source 字节。
2. 坐标与原文：原书物理页元数据起点非 1，印刷页与其不同；另一源没有物理映射。两者 markers 均为配对 1..N，不新增猜测原书标题或改元数据。保留 UTF-8、CRLF/空白差异及无末尾换行，确认不同空白仍能恢复。
3. 已有边界兼容：完整 PDF_PAGE、完整旧标题返回 already_prepared 且字节不变；frontmatter 中的 marker 示例不算页。缺页/重复 marker 或残缺标题不被宣称已准备，source 不变。
4. 不可对齐：一页人工改公式、另一页可匹配；以及无 native text 的页。返回 needs_boundary_review、可核对页/原因，源字节不变，不生成部分 markers；不发起 extract/OCR。audit/PDF 页数不一致不写源。这些仅覆盖直接变化的失败路径。
5. 下游 runner：合法 markers 的短教材 region plan 不被提升成全页；page partial collect 仅保留未完成页。复用现有 plan/collect 测试辅助函数与结果契约。不新增全状态机回归义务。

## 执行步骤

- 在目标 checkout 中执行；Python 和 PyMuPDF 当前可用（已只读检查 1.26.6），当前 Python 无 pytest，故使用现有测试文件原生 unittest 精确选择；不另装依赖。
- 所有测试可变输入由 TemporaryDirectory 放在 /tmp；运行输出保存在仓库外 /tmp/pdfcore-page-boundary-tests-<run>/unittest.log。不新增采集器或判定器。
- 命令：PYTHONPATH=lib python .apm/skills/formula-repair/tests/test_formula_repair_runner.py PrepareHeadingsTest FormulaRepairRunnerTest.test_marker_boundaries_preserve_region_plan FormulaRepairRunnerTest.test_marker_boundaries_allow_partial_collect -v。原有 unittest 直接加载指定类与两个方法；不运行无关状态机测试。
- 依既有框架直接断言 prepare 输出、原文件字节、消费者解析结果、jobs 成员。小型临时 PDF 用 PyMuPDF 原生 insert_text/save 创建，不调用外部服务。
- 正式运行一次；业务失败先修产品或核对步骤，保留失败日志，仅复验受影响项。不对不变业务失败重复采样。外部依赖问题停止受影响项，修复环境后最多一次同条件重试；不触碰生产状态或扩大权限。
- 结果记录实际代码 commit/工作树、命令、passed/failed/unjudged/not-run 与日志位置。仅有界测试通过不是全仓或真实 OCR 链通过；后续独立审核不重新执行。

T2 变化：准备检查发现当前 Python 无 pytest，改用文件已有 unittest 主入口，测试业务目标及断言不变；此前未执行正式测试。

## CI 直接影响补充：依赖声明单项核对

PR #13 首次 CI 发现本次新增 pdfx import 的测试文件缺少既有 PEP 723 bounded dependency 声明；parent 授权最小修复及仅核对此既有检查。补齐文件头声明，不改产品、原断言或全 skills 扫描规则。现有 P1/T2 产品验证结果不受影响并沿用。

当前 Python 无 pytest，直接执行无参数、无 fixture 的原测试函数即可：
`python -c 'import runpy; runpy.run_path("test/test_runtime_boundary.py")["test_pdfx_importers_have_bounded_pep723_dependency"](); print("PASS: test_pdfx_importers_have_bounded_pep723_dependency")'`

该补充仅核本次修改直接触及的现有 runtime contract，不新增全仓回归或安装测试；输出保存在 /tmp/pdfcore-pep723-check-<run>/check.log，历史 CI 失败记录保留。方法已独立复核。
