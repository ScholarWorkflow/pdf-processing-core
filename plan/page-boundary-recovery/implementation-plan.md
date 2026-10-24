# prepare-headings 页边界恢复执行计划

- Requirement revision: R2，2026-10-24 用户要求先在云端修复 core 页码恢复；不是把 3–4 位改成任意位后继续猜测。
- Plan revision: P2；Proposed review status: pending。
- Target: ScholarWorkflow/pdf-processing-core，PR #13 head 05934632e1a406ec2eb33157d2de41aee170d85c（2026-10-24 11:54 UTC 核实 open / draft / 未合并）；原分页基线 main e82dd706cdde1156f9b0b8c0218ae6953a0968dc。
- 规则：[Project Consensus](https://app.notion.com/p/3d60101a85f3815a8c43f271f2b4d91d)、[Plan Reviewer Rule](https://app.notion.com/p/3e80101a85f3817a94cbc6eca1a76a5c)、[Test Engineer Rule](https://app.notion.com/p/3d80101a85f380d8a8f5ce528141e253)。审核记录独立于本文件。

## 目标与边界

修复 formula_repair_runner.py 的 prepare-headings：不以独占数字位数、首个连续序列或印刷页起点认定页边界/原 PDF 物理页；保留现有源正文和已知元数据；让配对 PDF 的第 1..N 页正确对应后续局部 repair。页首数字、页尾数字及页码后额外 footer 都必须归属实际页面。

仅改 producer runner、其既有回归测试、技能使用说明与 unit-worker 补丁契约说明。无新公开 CLI 参数/数据格式、OCR、付费调用、用户学习数据运行、consumer 补偿、模糊匹配框架、全局重跑或自动修复旧错误标题。仅处理调用指定的一对 source/PDF，不扫描其他文件。

## 事实、因果与决定

1. runner.prepare_headings（158–216）选首个连续 3–4 位数字序列，在数字前插标题；无物理元数据时以印刷页起点冒充原 PDF 页。这是错误 owner，runner 同时是唯一 source writer，因此在这里修复。
2. lib/pdfx/extract.py:104–211 已有 page_texts 与 PDF_PAGE 输出；cli.py:83–101 只输出返回 JSON/指定正文，未约定 page_texts 持久 sidecar。故不假定该文件存在，不增加 sidecar discovery 或重跑 extract（fast 也可能进入 OCR）。
3. scan_math.parse_md_pages 与 ocr_refresh_jobs.page_sections 已接受 PDF_PAGE；其数字就是配对 PDF 的物理页 1..N。复用这个格式，不改变其消费者。runner.plan:303 与 collect:631 尚只认原书标题，需同步识别完整连续 PDF_PAGE，防止已准备源被误提升成整段 OCR。
4. PyMuPDF 已是正式 runtime 依赖。仅通过 open/get_text 读取配对 PDF 现有文本层；从实际逐页内容恢复边界，不查印刷页数字。仅去空白进行比较，定位时保留原字符偏移，输出仍使用原字节。

## 冻结方案

1. 读取 source UTF-8 原字节，分离开头 YAML frontmatter（不改写）；audit keys 必须为配对 PDF 页序 1..N。保留完整有效的 PDF_PAGE 1..N 或 N 个既有原书标题，返回 already_prepared。只在正文识别行级边界，不能把 metadata 示例或正文提及误当边界；有不完整/冲突结构时原文不变并指出结构需核对，不叠加第二套边界。
2. 无边界时读取 paired PDF 的每页 get_text；确认文档页数与 audit 一致。将每页文本与 source body 各自移除空白比较：只有所有页面非空且逐页拼接与源正文完整一致，才能按各页长度累计值映射回 source 原字符偏移。此为保守的内容对齐，不改变字词、公式或数字。原正文允许不同空白/换行，但不替换为 PDF 文本。
3. 在各页首个非空白字符前插入独占行 <!-- PDF_PAGE: N -->；若原位置不在行首，仅增加必要换行。保留既有所有字节（包括 CRLF、无末尾换行及页脚），不自动补其他结尾换行；用已有同目录临时文件、fsync、replace 完成一次原子写入。
4. PDF_PAGE 永远只表达传入配对 PDF 页 1..N。原 PDF 物理页映射和印刷页文本均原样保留，不推断、不新增原书页数字。返回 prepared/pages/headings_added（兼容计数键）及 boundary_mode=explicit，不再声称 printed_page_start。
5. 不能对齐（例如源公式手改、OCR 与 native text 不同或空文本层）时不写任何部分 markers，以免下游误用残缺分段；返回 needs_boundary_review 和受影响 pages/reason。用每页完整去空白文本在源中的出现情况提供有限诊断；无法缩小则明确是本对 source 的边界，不能声称全书坏了。不启动 OCR、不修改 audit、manifest 或 repair verdict；调用方只核对报告范围并可继续其他已定位源。诊断不是分页依据。
6. runner.plan/collect 复用一个窄的现存边界检查，支持完整顺序 PDF_PAGE 1..N；保留既有标题兼容，region plan 不再因合法 markers 被提升，partial page job 仅在 completed patches 已持久保留后缩小剩余成员（生命周期见下）。无边界旧 fallback 不在本次改写。

## 取舍、影响与顺序

- 现实替代方案是放宽数字 regex 后增加页首/页尾猜测；仍不能证明数字是页码或判断额外 footer 归属，拒绝采用。
- 全文本去空白相等是有意保守的内部实现，不是要求未来所有源必须逐字等于 PDF。已经人工修复的源无法自动定位时，保留它且报告核对，而不是回滚正文或强制 OCR。
- intended_change：用实际页内容恢复明确配对页边界；支持已有 markers；失败不伪造页码。preserved_behavior：原正文、元数据、repair state owner、原子写、现有 CLI/worker 补丁契约及旧标题读取。approved_behavior_change：用户已授权上述 bug 修复，不新增流程要求。
- 执行：独立完整计划审核 → 实现上述窄变更及既有框架测试 → 按测试计划验证 → 独立 diff/结果审核 → parent 决定发布；不 merge、force-push 或外部评论。
- 关键风险：正常 native 页可恢复，OCR/人工修改页会保守报告而非自动修复；不能以缺失元数据误造原 PDF 页。测试观察为 prepare 返回、输出正文、scan_math/page_sections 实际归属及 runner plan/collect 行为。


## R2 / P2：部分失败修复内容的生命周期

用户 2026-10-24 指出两页任务第一页完成、第二页失败后，collect 缩成员，下一轮覆盖 RESULT.json，finalize 只消费最后结果，导致第一页修复丢失。本轮源码已确认，属于 P1 分页后进入局部重试分支的直接缺陷，不是新功能。

事实依据（上述 PR head 的 runner）：collect 638–702 只记录完成计数/缩成员、不保存 patches；claim 404–429 复用同 unit_dir/RESULT；finalize 735–752 只读取 RESULT；plan 375–390 重建 pending job，只保留成员和 attempts。worker 正式契约只允许 runner 写 source，patches 对原 source SHA。因此修复必须位于 runner，不要求 worker 累计历史或让 source 提前写入。

### 唯一核心机制

- collect 接受成功结果或可缩成员的 partial 结果时，先将该轮 patches 与 source SHA 原子保存到现有 unit_dir 内的独立 accepted-<序号>.json。序号按该 job 已保存引用数量递增，不用会回退的 attempts；manifest 只保存 accepted_results 文件名引用，不保存 OCR 正文。文件写成功后才更新成员并一次保存 manifest。中断在保存 manifest 之前时，同序号可由同轮重写，不累加重复补丁。
- partial 结果仅保存 completed_members 的 patches。沿用现有 patch 结构检查，completed/failed 应是已分配成员的无重复、不相交完整划分；不合法不缩成员、不记完成。worker 文档明确 partial patches 只能属于已完成成员，不含失败页的半成品；不新增成员或 patch 字段。无分页 whole-body fallback 不接受局部完成补丁，仍完整重试。
- claim 在把下一轮标为 running 之前清掉旧 RESULT.json，仅保留 accepted 文件；防止新 worker 尚未产出结果时 collect 读到旧轮。worker 继续覆写原 RESULT 路径，公开契约不变。
- 相同 source SHA 下 plan 重建 pending job 必须保留 accepted_results 引用、unit_dir 与完成计数，仍只安排剩余成员。source 改变或 force 时恢复当前 audit 生成的完整成员、清除旧 accepted 引用/临时目录，不把旧坐标复用到新正文；attempt 预算保持既有非 force 策略。source_changed 也不允许沿用已缩小成员。
- finalize 对有 accepted_results 的 job 只消费这些快照（含最后成功轮），检查每个文件存在及 SHA 对应原 source；不得再同时读 RESULT 重复应用最后一轮。历史无引用的完整成功 job 保留现有 RESULT 读取兼容。继续统一做 bounds/UTF-8/overlap 检查，所有 job 完成后才一次原子写源。audit rollback 时保留快照供重试，成功后沿用 unit_dir 清理，manifest 清理 accepted 引用使完成记录不持有已删除文件。
- 已在旧版本被覆盖而丢失的 patches 无法凭空恢复。已知 completed_member_count>0 却无 accepted_results 的历史 job，collect 不接受末轮而标记 partial_results_missing；finalize 返回 needs_replan / partial_results_missing，不得宣称成功。plan 保留该诊断和完成计数，显式 force 或 source 改变重规划才清除旧进度、恢复完整成员；调用方决定是否完整重试，本次不猜补、不自动跑用户材料。正常成功 finalize 清理临时引用与已无意义的 partial 完成计数，避免清理后被误认为这种历史缺失状态。

### 影响与取舍

P2 重新打开 P1–P4 的 partial lifecycle 直接部分；已有 prepare-headings/native 对齐方案、坐标契约、原文保留、无边界 fallback 不变。替代方向是每次部分失败都全员重试，能避开持久化，但重做已成功页且违背本轮“仅失败成员重试”的既有目标，故选择 unit_dir 保存已接受 patches。没有新增状态框架、worker 累积协议或跨源缓存。

实施顺序：P2/T3 独立审核后修改 runner/worker 说明与指定合成测试，真实两轮覆盖同 RESULT 后调用 finalize 检查两页正文，最后独立复核；不外部 push/merge。历史 P1 审核漏查最终交付生命周期，不作为当前批准；原页边界与依赖声明结果只在不受影响范围沿用。
