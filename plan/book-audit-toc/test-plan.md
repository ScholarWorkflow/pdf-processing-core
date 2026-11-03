# PR #14：core 测试计划、已执行方法与结果复用

- 测试计划版本 CT1，2026-11-03：提取下游 TP4 中 core 三个修正相关部分；文档整理候选，作者不自授测试 PASS。
- 需求/实现范围：本目录 implementation-plan.md 的 C1；产品 fd4104986b1c76050e7bc823ecff67fe4542ea4a，原 core 基线 a903e5573d1a5611e0a9016fbabfe7b84a80416a。
- 原文档固定来源 d329e408b28c21c085a78a9184ea26c8a980e7ba。历史结果由原执行负责人产生，原正式审核角色评定；本次未运行任何测试、安装、OCR、模型或服务。
- 当前路径是复用已完成结果，不因增加文档导致重跑。以下命令是原执行方法的归档，不是本次运行授权；不另加全仓回归、重复安装、模型组合或来源认证。

## 1. 必测目标与充分检查入口

### L01：稀疏审计物理页配对

- **业务目标**：小节只审计部分页时，返回的可疑页仍属于正确的原书物理页。
- **输入或场景**：`pdf-processing-core` 的窄修正及其现有测试框架；小节页段为 40–42，第 40 页没有审计结果，第 41 页为可疑，第 42 页为可信。
- **预期结果**：已审计数为 2，可疑页为 `[41]`，可疑比例为 `1/2`；修复预估沿用这些实际结果及原公式，不把第 40 页当作可疑页。
- **检查方式**：上游负责人用一个针对聚合函数的标准断言检查该输入，交付修正提交及结果。本仓直接采用适用于锁定版本的结果；不再次运行此断言，也不增加全部 OCR 算法回归。

这里的“本仓直接采用”指原下游消费者；本次 core 归档采用同一个已完成结果。无额外 OCR 算法回归。

### 缓存修正：原 W01/R6 的受影响部分

- 目标：缓存命中仍为当前章节生成审计，保持已有逐页结果、PDF/章节身份和累计成果，不以 force/full 或重新 OCR 补偿。
- 输入：原两章文字书 A 和 R5 已生成成果，原 source PDF 时间、逐页缓存及仍缺第 2 章的旧报告；下游产品 bc91a3fe2f9ea0798a6ebf7ed5bb9f6c4cc1eb61、core 38b853f1c086cb33ed2195b5a078b3cbb0495825。
- 预期：原缓存请求跳过切分和提取，当前两节重新进入报告；审计 JSON、Markdown、正文已有审计字段一致；保持页段、选择、稳定 ID 和配对 PDF。已读 n/a 不虚报可信，没有已读页的抽样继承不变。
- 检查：只复用原 W01/R6 正式缓存请求的既有业务观察和 P5 实现审核；不新建缓存局部测试或重新运行整个 W01。T1 去重及真实知识库读取属于原下游 W01 的结果背景，不新增为独立 core 的职责或测试。n/a 边界的 P5 源码审核与 R9 unaudited 观察分别保留，不能把它们写成另做了 n/a 缓存实测。

### 同页目录顺序：原 R8 单项回归

- **业务目标**：正确的目录识别条目写回 PDF 后，保持同页章在所属小节之前以及正确父子关系。
- **固定输入**：独立临时三页 PDF；第 1 页为固定纸面目录，第 2、3 页分别为两章正文。结构化识别输入按原纸面顺序固定为 `[1, 第1章 入门, 2]`、`[2, 1.1 观察, 2]`、`[1, 第2章 方法, 3]`、`[2, 2.1 比较, 3]`，其中三列为层级、标题、物理页；印刷页码与物理页相同。模型识别边界返回固定条目，原生 OCR 读取边界采用该临时 PDF 自身文字以避免外部依赖；不替换产品的偏移探测、抽验、合并排序、层级合法化、备份或 PDF 写回。
- **预期结果**：实际注入后重开 PDF，四条完整书签的标题、页码、顺序和层级与上述固定目录完全一致；第 1 章下为 1.1，第 2 章下为 2.1。旧代码若返回子节在父章前的结构，本项失败；不得改变输入或预期迎合它。
- **检查方式**：原仓库 `pytest` 的单项断言，运行实际 `inject_toc.run` 并读取实际写回的 PDF 书签。测试不复制待修排序、构造另一个替代排序器或只检查文案。先在内核 `38b853f1c086cb33ed2195b5a078b3cbb0495825` 上保存失败，再只改变产品排序代码，以同一测试和同一内容输入复验。

这是一项固定识别输入的代码回归，不能证明视觉模型当时返回了什么，也不替代 W02 的真实扫描识别、切分、审计、身份及知识库判定。原 R7 W02 失败作为历史保持；R8 当时既有 11 项有效通过按实际影响复用，当前 TP4/R9 状态另见下文。

### 真实消费者影响：原 W02/R9 已有结果

- 使用原冻结 B：三页扫描书、纸面目录但无 PDF 书签，两个章/节对分别位于物理页 2、3；预期目录父子、顺序和两章页覆盖正确。
- R9 用下游 1524bde68a9fe80e65d23fa2ae0decbed5ad0237 正式安装，其 core 固定 fd4104986b1c76050e7bc823ecff67fe4542ea4a。
- 按原 W02 方法读取实际 PDF 书签和业务成果，确认页 2/3 覆盖；本地 R8 不能替代真实识别。TP4 接受可读正文中的孤立单字偏差为质量警告，未降低目录、缺页、串页、不可读正文的要求。
- 本次只复用，既不重新请求 R9，也不将 W02 全套 Nix/身份/知识库行为扩成 core 新的测试集合。

## 2. 已执行步骤与配置

### 2.1 L01 历史命令

在原 core 工作树，使用现有 uv 与固定 pymupdf；测试由 pytest 临时数据构造，不连接模型/服务：

```bash
uv run --no-project --with pytest --with pymupdf==1.26.7   python -m pytest -q test/test_texlayer_audit.py::test_sparse_section_audit_keeps_physical_pages
```

日志为原 PDF_BOOK_RUN 下 logs/l01-attempt-1.log 与 .exit。当前归档不重新准备环境；原受测提交 1740fafb31ca16701805693dd4669876386f2c54。

### 2.2 R8 固定输入、命令与失败处理

测试代码由原内核负责人放在 `pdf-processing-core/test/test_inject_toc.py`，准确入口为 `test_same_page_entries_keep_toc_reading_order`。执行者使用现有 core 工作树和原运行根；`PDF_BOOK_RUN` 沿用当前本地交接的私有目录，`UV_CACHE_DIR` 复用其 `uv-cache`，原始日志放在新增 `revision-r8/logs`，不提交 Git。实际运行前由执行负责人确认测试文件已保存、与原 TP4 第 8.1 节（本文件 §1 同页目录顺序）一致；主对话完成第二关口后执行。

在 core 工作树分别对旧代码和修正代码运行完全相同的命令：

```bash
UV_CACHE_DIR="$PDF_BOOK_RUN/uv-cache" \
  uv run --no-project --with pytest --with pymupdf==1.26.7 \
  python -m pytest -q \
  test/test_inject_toc.py::test_same_page_entries_keep_toc_reading_order
```

两次标准输出、错误和真实退出状态分别保存为 `revision-r8/logs/toc-before.{log,exit}` 与 `toc-after.{log,exit}`。首次预期是业务断言失败；收集失败或依赖错误不冒充复现成功。修正前没有复现时先解释原因，不修改正确预期求失败；修正后仍失败则沿已确认问题继续修正并保留每次结果。只有环境或记录故障时才修正对应运行步骤，既有失败不覆盖。

测试使用独立临时 PDF，不操作原 B、R7 失败产物或知识库；不启动网关、eval、视觉服务，不调用真实模型或 OCR，不运行构建或安装。已有目录测试不自动全量执行，本轮没有新增第二套输入或额外测试方法。核心修正审查后可提交并更新本仓依赖绑定，版本变化本身不使已通过项目失效。

上段“执行/运行”均为已完成 R8 的历史方法。PDF_BOOK_RUN 指原仓库外运行根，其实际路径由原本地交接记录保存，不上传用户配置。结果见下节；本次不回退工作树或重复修前失败。

### 2.3 R6/R9 下游方法的必要交接

R6 沿用原 A 已写入成果和知识库六项，不清空重来；正常 Nix 安装受影响产品后，仅发送原 w01-cache，成功后发送原 w01-read，各一次，失败则保留原结果并停止依赖步骤。配置加载 paths.env、runtime.env、pdfx.env，再加载 revision-r6/prepared-paths.env，原消费者和失败成果不动。方法、已执行请求和判断见固定源的 R6 审核及 execution-results.md，不能把其中“待执行”历史标记当作当前状态。

R9 仅从原冻结 B 复制业务输入到新消费者，正式构建 book-environment 并 activate --project；不拷贝产品或修补消费者。加载同三份 env 后用 revision-r9/prepared-paths.env 绑定产品及路径；使用固定 skills-test-fixtures 5a31ae0095952cc359f7fa826730f14e7ee4f5b2 的 prepare_codex_project_config.py 和 codex-w02-recipe.toml。原配置是 gpt-6-luna/low、workspace-write/on-request/auto_review，PDFX 配置通过原配置文件加载。既有 eval 请求只发一次，预算1200秒/curl1260秒；等待同一会话终态，不用新请求替代等待。失败、拒绝或超时均保留真实状态，不自动追加。只停止自有网关，不启停用户 eval 服务。

完整历史命令和实际本地变量接线保留在原 TP4 §9（下方固定链接）；这不是要求 core 复制或重建下游 harness、知识库、密钥/配置或安装器。原始配置/响应在受控的 PDF_BOOK_RUN，仓内只保存必要脱敏记录。没有这些本地文件时不能宣称已重现，也不影响直接采用已有正式记录。

## 3. 既有结果与适用版本（归属原记录，不是本次新跑）

| 目标 | 原执行版本/记录 | 既有结果及复用范围 |
| --- | --- | --- |
| L01 | core 1740faf；execution-results.md L01 | 1 passed, 5 warnings in 2.02s，退出0；audited=2，suspect_pages=[41]，ratio=1/2。后续缓存/排序变更未改变该配对行为，复用。 |
| 缓存恢复 | core 38b853f + 下游 bc91a3f；R6 | cache_hit=true、sampled、sections=2、4页可信；JSON/Markdown/两章正文一致；未切分、提取或重OCR，选择[2]、ID与PDF保持。原 R5 缓存遗漏失败保留。后续仅目录排序改变，不使结果失效。 |
| 同页排序 | R8：旧core 38b853f → fd410498 | 同一输入旧码1 failed/退出1，修后1 passed/退出0；实际重开PDF四书签正确。5 warnings为PyMuPDF SWIG弃用提示；不证明R7的识别分支。 |
| 真实目录/页覆盖 | R9：core fd410498 + 下游1524bde | 实际父子目录、顺序、物理页2/3成果正确；TP4复核同一次R9后W02通过。孤立“的→昀”、unaudited、目录抽验两处警告及诊断限制仍保留。 |

R9 最初按 TP3 的逐字要求判断失败，随后用户澄清后 TP4 复核通过；不能写成 R9 当时按 TP4 执行。下游“12/12通过”是其工作流整体历史结论，不是本 PR 新增十二项测试，也不是 Merge READY。原实现审核者在 TP3 下的意见仍按当时范围归属，不冒充其批准 TP4。

## 4. 关口、未完成项和下一负责者

- 原第一关口：TP2 既有清单、TP3 的唯一 R8 回归及 TP4 用户澄清均在下游 test-review-record.md 有正式记录。
- 原第二关口：R6/R8/R9 方法已获批准并执行；TP4 无新增运行步骤，R9 批准继续有效。
- 原第三关口：采用下游正式记录的 L01、R6、R8 与 TP4/R9 结论，不由本文件作者重新授予 PASS。
- 此次候选仅整理 core 相关内容，独立复核见 review-record.md。尚未完成的是 PR14 正常合并审核，不是要求重跑历史测试。若发现具体事实使结果失效，仅交原负责者处理受影响目标；材料缺失标为待补，不伪造失败/通过。

## 5. 固定引用（仅上述 core 相关范围）

- [原测试计划 TP4：L01、§8 R8、§9 R9、§10范围澄清](https://github.com/ScholarWorkflow/pdf-book-pipeline/blob/d329e408b28c21c085a78a9184ea26c8a980e7ba/plan/nix-full-workflow/test-plan.md)
- [原R6缓存准备/步骤审批与R8/R9关口](https://github.com/ScholarWorkflow/pdf-book-pipeline/blob/d329e408b28c21c085a78a9184ea26c8a980e7ba/plan/nix-full-workflow/test-review-record.md)
- [原执行结果及全部历史失败](https://github.com/ScholarWorkflow/pdf-book-pipeline/blob/d329e408b28c21c085a78a9184ea26c8a980e7ba/plan/nix-full-workflow/execution-results.md)
- [原P5/P6实现审核](https://github.com/ScholarWorkflow/pdf-book-pipeline/blob/d329e408b28c21c085a78a9184ea26c8a980e7ba/plan/nix-full-workflow/implementation-review.md)
- [原准备审核](https://github.com/ScholarWorkflow/pdf-book-pipeline/blob/d329e408b28c21c085a78a9184ea26c8a980e7ba/plan/nix-full-workflow/test-preparation-review.md)
- [R6使用的既有Codex Recipe](https://github.com/ScholarWorkflow/pdf-book-pipeline/blob/d329e408b28c21c085a78a9184ea26c8a980e7ba/plan/nix-full-workflow/codex-recipe.toml)
- [R9使用的既有W02 Recipe](https://github.com/ScholarWorkflow/pdf-book-pipeline/blob/d329e408b28c21c085a78a9184ea26c8a980e7ba/plan/nix-full-workflow/codex-w02-recipe.toml)
