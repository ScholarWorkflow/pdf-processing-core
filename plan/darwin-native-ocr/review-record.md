# Darwin native OCR 独立复核记录

审核时间：2026-10-23 UTC。以下为独立只读复核者提供的结论，不是实现作者自授。
Requirement revision：2026-10-23 用户授权 core 云端分支、独立执行/测试两计划、实现、推送与新 PR；不操作用户 Mac。此为首次云端审核的历史边界；本轮本机测试授权及结果以 execution-plan.md 的“本轮执行环境授权”和 test-plan.md 的“本轮 Darwin 执行结果”为准。
Target repository：ScholarWorkflow/pdf-processing-core。
Base SHA：2be1bf850f29b7ccf7bd1b066ae7292aac33fc87。
Implementation candidate：candidate.patch SHA-256 134c69b0dfd13630876575bfafebdfaad1ead9649cafe3120292fa8d8fe56ae8。该标识固定审核时的完整七文件候选；之后仅追加本记录与测试结果说明，不影响代码结论。

## 执行计划

Canonical Plan：OCR-EP1；execution-plan.md SHA-256 14222e0bd1f9724da9d0f25713eb132e051fcb525793b00d2dd31da0fa85ef19。
P1 Requirement & Reality Grounding：PASS。
P2 Solution Validity & Ownership：PASS。
P3 Impact, Safety & Counterexample：PASS。
P4 Executability：PASS。
Gate review completeness：COMPLETE。
Critical assumptions：N/A。Unresolved product impacts：N/A；Darwin 构建与行为证据尚未执行，归测试结果限制，不等同已证实方案缺陷。
Plan conclusion：APPROVED。

关键依据已独立读取：锁定 Nixpkgs 6510408 的 pyobjc-core、Quartz、AVFoundation 定义；PyObjC v11.1 CoreML/Vision/Cocoa setup.py；producer 当前 python/CLI 工厂及 Study 73fbf608 的 Python 3.12 复用方式。CoreML 依赖 core/Cocoa，Vision 依赖 core/Cocoa/Quartz/CoreML，Cocoa 提供 CoreFoundation。修改归公共 producer，无 consumer 补偿、运行时 pip 或全局配置变化。

## 测试计划

Canonical Test Plan：OCR-TP1；最终步骤版 SHA-256 aedef044787c45dfa022a8b6bff8999bf29d393a463dd2a67bea63347b2265ec。
Gate 1（必测清单）：PASS。
Gate 2（步骤与范围）：PASS。
初审后三行以内的 bash 使用说明属于可执行性澄清，不改变目标、输入、检查方法或范围，原结论沿用。官方 Nix 语法支持 --file 的 attrpath 与 --arg/--argstr。

## 实现与已有结果

M2 Implementation Conformance：PASS（本候选静态实现符合性）。
已检查完整候选 diff、真实调用入口与依赖传递。Darwin 绑定进入 pdfxDistribution.dependencies，再进入既有固定 Python；两个新包继承同一 pythonPackages 中 core 的 src/version。Linux 条件不引入 Darwin 包。flake.lock 仅修改 agent-env 的 original 来源，locked 对象、依赖边与 follows 保留。未发现代码或范围必须修改项。

T1：已有 Nix 2.35.3 parse、三平台/ABI 求值与结构化 derivation 结果可用于当前候选，结论通过。Darwin 默认 Python 3.14.7 与 Python 3.12.15 各包含同 ABI 的 core/Cocoa/Quartz/CoreML/Vision；Linux Python 3.14.7 未含这些绑定。审核采用既定方法的已有结果，未重新执行测试。
T2：未执行（当前云端 Linux，无获准 Darwin 执行环境）。
T3：未执行（同上）。
Test Gate 3：尚未全部通过，不能声称完整修复已获运行验收。

Review completeness：COMPLETE（当前候选、计划和已有结果的可判断范围）。
Merge conclusion：NOT_READY，原因仅为计划内 Darwin 构建/导入及原生 OCR 必测结果未完成；不是额外要求全工作流回归。可按用户授权发布 draft PR，不代表自动合并授权。
下一步：由获准的 Darwin 执行者按 TP1 完成 T2/T3；未取得环境授权前保持未执行。只复核实际变化及相关结果，不因本记录或文档 SHA 变化重跑 T1。

## OCR-TP2 受影响步骤复核（取代上文 TP1 的步骤批准）

2026-10-23，独立只读复核者复核 `test-plan.md` OCR-TP2，SHA-256 `a36e7109a4a9cfed4e5f14ab9b0e170ef8987c679cbb5a10a02bc57c38229bae`；产品基线 `ce03be88e397db5165aeb9cfbb921d7b3bae3cf6`，产品代码与 OCR-EP1 未改变。

- REVIEW_DEFECT：原 Gate 2 漏审了 T1 正式步骤未包含实际依赖字段检查，以及 T2/T3 后续命令可能掩盖前序失败。上文 TP1 Gate 2 PASS 仅保留为历史，本节 TP2 结论取代该步骤批准；不能把旧步骤视为无缺陷。
- Gate 1：沿用 PASS，业务目标和必测范围未变。
- Gate 2：PASS。T1 已补 `nix derivation show`、Nix 2.35.3 的 `structuredAttrs.chosenOutputs[].paths[]` / `inputs.drvs` 字段及锁定版本、Python ABI 观察点；复用已有 T1，不重跑。四个独立 bash 入口分别保存 stdout/stderr 与原始退出码；T2 两版本不互相掩盖，T3 文件查看不覆盖 OCR 失败。
- Review completeness：COMPLETE；Required modifications：N/A。本轮仅只读复核修正和直接影响，没有执行测试或脚本。
- EP1 与产品实现结论沿用。Test Gate 3 未整体完成：T2/T3 仍未执行；Merge conclusion 仍为 NOT_READY。下一执行者仅在获准 Darwin 环境按当前 OCR-TP2 完成原有 T2/T3，不因文档提交重跑 T1，不新增回归。

## 本轮本地执行的实现符合性复核（当前）

2026-10-23（北京时间），专用 `implementation_reviewer` 代理在全部测试结果齐备后只读复核，结论为**完整**，当前阶段未发现必须修正或待核实项。此结论仅针对实现符合性，不代表测试第三关或合并批准。

所审产品版本为 `54cb00bd72ad111df17413ff2ebf0056abb0ddc0`，基线为 `2be1bf850f29b7ccf7bd1b066ae7292aac33fc87`；另核对当前工作树中三份计划文档的待提交修改。有效依据为 OCR-EP1、OCR-TP2 及本轮本机执行授权。

已检查整个计划对应产物及必要连接：

- `nix/pyobjc-vision.nix`：CoreML/Vision 使用传入的 Python 包集合，继承同一 pyobjc-core 的源码和版本，依赖关系符合计划。
- `nix/pdfx.nix`、`flake.nix`：Darwin 绑定进入发行包依赖，再进入公共工厂返回的 Python；CLI、开发环境和两个脚本包装器共用该环境。Linux 条件保留，Python 3.12 覆盖未混入默认版本绑定。
- `flake.lock`、`docs/NIX.md`：只调整 agent-env 的来源分支声明；锁定版本和依赖关系保留，文档区分原生文字识别与远端视觉增强。
- 既有 `ocr_apple.py`、提取调用链、发行包入口和脚本连接：原生适配器已接入现有流程，识别算法、远端优先顺序及返回行为未修改。
- 三份计划与审核记录：前次完整审核覆盖及 TP2 步骤替代关系明确；本轮授权和结果未改变测试范围。

必需结果齐备：T1 沿用已接受且仍适用的通过结果；T2 默认 Python 与 Python 3.12 均构建、导入成功，退出码为 0；T3 单次识别得到 `NATIVE OCR CHECK`，退出码为 0，stderr 为空。复核直接采用既定方法的已有执行结果，未重跑测试或重新认证原始材料。

本轮当前结果取代历史记录中的 T2/T3 未执行状态；原计划及步骤批准沿用，不自行更新正式测试关口或合并结论。下一步由顶层提交推送三份文档，后续测试结果审核和合并审核由相应负责人处理。最终文档提交只加入本轮授权、执行结果和审核记录，不改变所审产品或结果适用性。
