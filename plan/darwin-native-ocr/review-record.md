# Darwin native OCR 独立复核记录

审核时间：2026-10-23 UTC。以下为独立只读复核者提供的结论，不是实现作者自授。
Requirement revision：2026-10-23 用户授权 core 云端分支、独立执行/测试两计划、实现、推送与新 PR；不操作用户 Mac。
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
