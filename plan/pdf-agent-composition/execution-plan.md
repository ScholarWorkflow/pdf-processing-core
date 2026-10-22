# 公共 PDF agent 可组合交付执行计划

- Requirement revision: 用户 2026-10-22 通用包声明/递归依赖要求及创建四个 PR、先做前两个的授权。
- Plan revision: PDF-COMPOSE-P1 / 2026-10-22，完整候选。
- Target revision: main@3444dca0e0b84f46d1839ab5c707afa78e23d3a7。
- Proposed review status: pending。作者未给出 APPROVED；正式 P1–P4 通过后才实施核心方案。
- 唯一测试计划：[test-plan.md](test-plan.md)。本文件不新增测试义务。
- 规范：[Project Consensus](https://app.notion.com/p/3d60101a85f3815a8c43f271f2b4d91d)（2026-10-19）、[Plan Reviewer Rule](https://app.notion.com/p/3e80101a85f3817a94cbc6eca1a76a5c)、[Test Engineer Rule](https://app.notion.com/p/3d80101a85f380d8a8f5ce528141e253)（后二者 2026-10-09）。

## 需求与非目标

包自行声明完整资源、原生角色和真实直接 agentDependencies，由已有共享层递归计算所选 roots 的资源。用户可同时选择多个顶层包，不应要求为 Contact/Study 特制相互覆盖的 PDF 包。Nix 入口使用固定程序闭包，不借 APM/uv 临时安装；原 APM/PEP 723 入口保持。

本 PR 只统一 PDF producer 的公开实例与固定入口，不改 PDF/OCR/公式算法、业务字段、源 APM 内容、视觉提供者、共享安装器冲突/保护逻辑或 symlink 行为。不建 registry，不引入新 harness 或完整下游业务。

## 事实、因果与所有权

所有源链接固定基线：
- [nix/pdfx.nix](https://github.com/ScholarWorkflow/pdf-processing-core/blob/3444dca0e0b84f46d1839ab5c707afa78e23d3a7/nix/pdfx.nix) 已返回 pdfx、pdfxAgent 和内部 Python 环境，打包两技能、三角色，runtimePackages 仅为 pdfx；角色 ID 是 scholar-workflow-pdfx-agent。
- 同文件的 pdfToTextInstructions 只补内核绝对路径，未替换源角色的 uv run 消费者命令。两个公共脚本已有 pdfx-formula-repair-runner / pdfx-ocr-refresh-jobs 包装器。
- [exam 的 pdf-adapted.nix](https://github.com/StudyCoachWorkflow/exam-question-pipeline/blob/d57c2c47ac5d3a4dc6579bbcfb2508352e696acd/nix/packages/pdf-adapted.nix) 用同一 ID override 文件和角色，并加技能本地包装器；由此两条依赖路径取得不同 PDF 定义，冲突检查是正确行为。
- [exam agent-from-apm.nix](https://github.com/StudyCoachWorkflow/exam-question-pipeline/blob/d57c2c47ac5d3a4dc6579bbcfb2508352e696acd/nix/lib/agent-from-apm.nix) 追加的单消息协议只在角色既有约定要求 JSON 时要求 JSON。它不是新业务 schema。
- [pdf-to-text](https://github.com/ScholarWorkflow/pdf-processing-core/blob/3444dca0e0b84f46d1839ab5c707afa78e23d3a7/.apm/agents/pdf-to-text.agent.md) 拥有转换协议，但 batch 命令明确由 consumer 拥有；正常路径允许摘要，错误已有 JSON。formula-repair 和 unit worker 已有各自 JSON。
- 2026-10-22 只读检查 main tree 无 AGENTS.md/CLAUDE.md，open PR 列表为空。

决策：固定启动适配属于 PDF producer；exam 自己的 batch/gate/post 仍归 exam。把通用适配上移后，各 caller 引用同一生产者工厂及同一输入，即得到相同 PDF 实例；共享层继续递归与去重，不静默择一。

## 冻结方案

1. 在 flake.lib 暴露 mkPdfPackages { pkgs; enableHarnesses ? [ "codex" ]; }，由现有 nix/pdfx.nix 构造并返回 pdfx、pdfxAgent、python。packages 与 devShell 使用同一工厂。python 是现有内部输出的稳定工厂字段，不新增 packages.<system>.python 或额外 python.withPackages 产品义务。nix/pdfx.nix 新增默认 enableHarnesses 参数，原两参数 import 保持兼容。
2. 公共 Nix pdf-to-text 投影直接执行 caller 传入的已安装固定 wrapper；同步替换 --check/转换示例、允许命令清单和缺依赖措辞。caller 继续负责提供 batch 的绝对路径，PDF 不硬编码 exam 路径、不拥有 batch、不注入 PATH。
3. 公共 formula/refresh 保留现有两个 store 绝对包装器作为统一入口，不复制 exam 的技能本地包装器。完整技能树及相邻 concurrency-benchmark.json 保留。原始 .apm 不改；Nix 派生脚本中若生成可执行操作提示含旧 uv 入口，仅替换为相同公共固定入口，保持参数、数据与错误语义。
4. Nix 三角色追加通用单条最终消息约定：工具阶段不发进度；角色本身要求 JSON 时，最终只含其既有 JSON，错误/受阻沿既有字段。不得把 pdf-to-text 正常摘要强改成新 schema；不得引入 exam 专用业务字段。原 APM/OpenCode 投影不变。
5. docs/NIX.md 说明工厂、直接依赖的声明方式、同一 pkgs/输入及 enableHarnesses 用于共享实例；用户选择顶层 roots，由共享层递归。保留旧 packages 接口及普通 pdfx CLI 无须激活的用法。无需增加中心注册或身份映射。

## 影响与反例

intended_change：所有 Nix caller 可消费同一完整 PDF 实例和固定入口，消除下游同名重写的必要。
preserved_behavior：ID/角色/技能名、原业务字段、batch 所有权、库与 CLI 参数、APM/uv/PEP 723、运行态写入位置、缓存及错误/降级语义。
approved_behavior_change：Nix 固定启动与通用依赖组合；不构成业务算法或新的 JSON schema 授权。
prohibited_side_effect：改用户全局配置、覆盖无关资源、关闭真实冲突、将 runtimePackages 当 PATH、上游反向依赖 exam。
Invariant：相同 producer/input/options 的同一 PDF ID 必须有一致内容。若两个 caller 固定不同 PDF 修订或不同 options，共享层仍可报冲突，不声称任意历史版本自动兼容。
代表性反例：Contact 不传 exam wrapper，故公共角色只接收 caller 路径；同名包通过改 ID 回避会继续撞导出名，故不采取；只把程序留在闭包但指令用 uv 不能实现固定启动，故同步处理实际指令。
并发/状态：没有新增运行态状态或写入器。包装器只读 store 资源，可变数据仍由 caller 指定；真实错误原样停止，不 fallback 到下载。
Unresolved impact：无新增业务语义决策。实际构建/运行结果未取得，不以设计替代结果。

## 实施与交接顺序

S0：冻结两份计划，独立执行计划及测试 Gate1 审核；完整实际 Recipe 由执行负责人补齐后 Gate2。
S1：同 PR 实现工厂和 Nix 投影，仅改上述 producer 文件；保留 APM 源。
S2：独立代码审核与受影响检查，记录产品 SHA/输入、输出接口、实际平台、外部服务限制。
S3：将已发布可获取且有真实验证记录的固定提交交 exam；在此之前 exam 只能准备定义，不能编造 pin。
S4：exam/Contact/Study 后续分别改其真实直接引用；最终联合 roots 验证由下游交接一次完成，不在各 producer 重复。
仅云端操作，不用用户 Mac。当前作者云端 Linux 且 PATH 无 nix，旧首批验收为 aarch64-darwin；这不阻计划/代码审查，但不得声称 Darwin 实跑或擅自将 Linux 替为正式平台。执行负责人须如实报告可用平台及受阻目标。
正常提交，发布前复读 head/open PR，遇并发重新比较，不 force、不覆盖他人；不自动合并。


## 双根交付的唯一记录归属

最终 Contact+Study 联合 roots 的一次组合安装验证由 Study consumer PR 的执行负责人拥有，正式结果写入该 PR 的 plan/pdf-agent-composition/consumer-handoff.md（[Study #16](https://github.com/StudyCoachWorkflow/study-tools/pull/16)；[Contact #131](https://github.com/ScholarWorkflow/professor-contact/pull/131) 引用），Contact PR 只引用同一结果。范围仅为两顶层 roots 经既有共享层合并、公共 PDF 唯一实例及必要资源真实复制，不运行 Contact 邮件或 Study 全业务，不重复 producer 入口验证。这个后续记录不把尚未更新的 consumer 当作本仓独立输出的阻塞项。
