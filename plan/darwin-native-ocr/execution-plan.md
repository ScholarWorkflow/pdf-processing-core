# Darwin 原生 OCR 的 Nix 运行依赖修复

- Requirement revision: 2026-10-23 用户报告 Nix 安装的 Study 无法导入 Quartz；随后授权云端分支、双计划、实现、推送和新 PR，并要求本轮自有 Nix 输入跟踪主分支。
- Plan revision: OCR-EP1，唯一完整执行计划。
- Target repository revision: `2be1bf850f29b7ccf7bd1b066ae7292aac33fc87`（main）。
- Relevant upstream: Nixpkgs `6510408d147d8c5e6d76043e899e9251756f6a75`；该版本 PyObjC 11.1。
- Proposed review status: pending。作者不声明 APPROVED；独立计划审核后实施核心方案。
- 独立测试计划：[test-plan.md](test-plan.md)。本计划不增加其清单外测试。
- 规则：[Plan Reviewer Rule](https://app.notion.com/p/3e80101a85f3817a94cbc6eca1a76a5c)、[Test Engineer Rule](https://app.notion.com/p/3d80101a85f380d8a8f5ce528141e253)、[Project Consensus](https://app.notion.com/p/3d60101a85f3815a8c43f271f2b4d91d)。

## 目标与边界

让既有 Darwin 原生文字 OCR 在本仓提供的固定 Nix Python 环境中获得其真实直接运行依赖，不要求用户给系统 Python 手装模块或填写远端识别网址。公共工厂返回的 Python、pdfx CLI 及其脚本包装器继续共用同一环境，调用方无需另造 OCR 环境。

本次不改 OCR 算法、选页、fallback 顺序、返回字段、远端 provider 协议/鉴权、模型权重、APM/uv/PEP723 发行依赖、agent-env 安装器或用户全局配置。不把原生文字 OCR 描述成公式/复杂版面视觉增强。只在云端工作，不操作用户 Mac。

## 决策与事实

所有本仓事实固定在上述目标 revision：

1. **依赖归 PDF producer，而非 Study/Exam 补偿。** [nix/pdfx.nix](https://github.com/ScholarWorkflow/pdf-processing-core/blob/2be1bf850f29b7ccf7bd1b066ae7292aac33fc87/nix/pdfx.nix) 的 `pdfxDistribution` 只声明 pymupdf，`pythonEnvironment` 只有 pymupdf、pillow、distribution；同文件的 CLI/脚本包装器和 `flake.lib.mkPdfPackages` 返回的 python 使用它。[ocr_apple.py](https://github.com/ScholarWorkflow/pdf-processing-core/blob/2be1bf850f29b7ccf7bd1b066ae7292aac33fc87/lib/pdfx/ocr_apple.py#L29-L38) 真实导入 Quartz、Vision、CoreFoundation。缺失来自固定环境，而非用户电脑没有识别能力。
2. **复用匹配的 PyObjC 源，不升级整套 Nixpkgs。** 锁定 Nixpkgs [python-packages.nix](https://github.com/NixOS/nixpkgs/blob/6510408d147d8c5e6d76043e899e9251756f6a75/pkgs/top-level/python-packages.nix#L15707-L15745) 已导出 core、Cocoa、Quartz，但这一完整 PyObjC 声明段没有 CoreML/Vision。其 [pyobjc-core](https://github.com/NixOS/nixpkgs/blob/6510408d147d8c5e6d76043e899e9251756f6a75/pkgs/development/python-modules/pyobjc-core/default.nix) 固定 11.1，源为整个 PyObjC 树；[AVFoundation](https://github.com/NixOS/nixpkgs/blob/6510408d147d8c5e6d76043e899e9251756f6a75/pkgs/development/python-modules/pyobjc-framework-AVFoundation/default.nix) 提供可复用的子目录构建、libffi 和 SDK 工具路径适配方式。
3. **仅补真实闭包。** PyObjC 11.1 [CoreML/setup.py](https://github.com/ronaldoussoren/pyobjc/blob/v11.1/pyobjc-framework-CoreML/setup.py) 依赖 core、Cocoa；[Vision/setup.py](https://github.com/ronaldoussoren/pyobjc/blob/v11.1/pyobjc-framework-Vision/setup.py) 依赖 core、Cocoa、Quartz、CoreML。CoreFoundation 由 Cocoa 分发提供。不拉整个 PyObjC 元包。
4. **依赖跟随调用方 Python ABI。** `mkPdfPackages` 接收 pkgs，并在内部使用 pkgs.python3。新声明使用该 python.pkgs 构建，不硬编码另一个 Python。这样保留调用方默认 Python 与 Python 3.12 覆盖的现有用途，不新增公开 Python 输出或新的工厂参数。
5. **分支来源不等于自动升级。** flake 中 agent-env-nix 的 URL 改 `main`，lock 对应 original 从 rev 改 ref；保留其 locked rev/narHash/time、所有其他锁节点与 follows。更新文档中“禁止浮动分支”的旧说明，明确 main 是下次 update 的来源，锁文件仍可复现，不把本次来源变化说成升级共享实现。

## 冻结方案、所有权与顺序

1. 同 PR 新增一个小型 `nix/pyobjc-vision.nix`，在传入 pkgs/pythonPackages 下构造 CoreML 和 Vision，版本和源码继承 pyobjc-core；采用现有 Nixpkgs PyObjC 构建方式。包声明使用各自 upstream 真实 dependencies 与 import checks，不增加通用打包框架。
2. 在 `nix/pdfx.nix` 用 `lib.optionals pkgs.stdenv.hostPlatform.isDarwin` 将所需绑定接入 pdfxDistribution 的 dependencies。Python 环境继续包含该 distribution，闭包自动包含绑定；Linux 不求值或引入 Darwin 绑定。全部 CLI/工厂入口自然复用，不增 wrapper 或运行时 pip/uv。
3. 修改 `flake.nix`/`flake.lock` 的上述 agent-env 来源声明，并在 `docs/NIX.md` 更新主分支/lock 说明及原生文字 OCR 与远端增强边界。既有普通 CLI、agent、工厂字段及 enableHarnesses 接口不变。
4. 固定候选交独立计划与代码复核。实际验证和未执行项写入测试计划的简短结果区；仅已执行的检查记录观察结果，不自授正式测试关口通过。
5. 正常提交、推送新分支、开 draft PR。发布前复读 main/分支，使用正常提交和并发保护，不 force、不覆盖别人提交，不自动合并。调用方之后更新其 lock 才会取得修复；不在本 PR 修改各消费者。

## 影响、安全与反例

- intended_change / approved_behavior_change：Darwin 固定 Python 闭包补齐既有原生 OCR 所需绑定；agent-env 来源可跟踪 main。批准来源为本轮用户明确开发与发布授权。
- preserved_behavior：Linux 依赖、现有 API/CLI、库角色语义、远端优先与原生 fallback 顺序、错误原样报告、用户指定输出/缓存位置、非 Nix 入口、运行闭包不自动注入 PATH/PYTHONPATH。
- prohibited_side_effect：系统 Python 安装、用户 Mac 操作、真实试卷上传、模型下载、全局安全/配置改变、在 consumer 填补缺失依赖、静默吞掉原生 OCR 错误。
- Invariant：所有 PyObjC 绑定来自同一 Python package set 和同一 core 源；代表性反例是 Python 3.12 环境混入默认 Python 的扩展，因此声明必须使用传入 pythonPackages。Linux 强制求值 Darwin 包会破坏原支持，故条件在依赖构造外层。
- 版本/兼容：复用现有 core 源避免独立升级绑定；构建失败或模块导入失败按实际错误处理，不退回系统 Python。真实 Darwin 构建与行为结果仍待执行，不能由 Linux 求值推定。
- 状态/并发/lifecycle：不新增运行态状态或写入器；现有线程局部识别 request、失败语义、缓存和清理不变。无需历史数据迁移，回退为原仓库/lock 修订。
- Unresolved impact：无未决产品或 owner 选择；平台构建/运行尚未验证，作为测试结果限制记录，不冒充已交付可运行结论。
