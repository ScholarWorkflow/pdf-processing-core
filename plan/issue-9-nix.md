# Issue #9：Nix 安装支持执行计划

- 计划版本：`issue9-nix-plan-v7-2026-10-15`
- 需求版本：[Issue #9](https://github.com/ScholarWorkflow/pdf-processing-core/issues/9)，2026-10-16 当前版本；保留 2026-10-15 对 Nix 入口、PEP 723/uv 兼容、glance 启动、Codex 资源发现和配置保护的已确认要求。
- 仓库基线：`main@9ad523a624d94eb2774a0082fde1f54c3ff02256`。实施沿用本计划所在拉取请求分支，并在各次实现/验证记录实际提交号。
- 现有兼容记录：`CODEX-COMPATIBILITY.md`（2026-09-07：APM 0.29.0、Codex 0.153.4、OpenCode 1.18.21、uv 0.12.5、Python 3.14.7）。这些是当时的实测版本，不是要求 Nix 强制使用 Python 3.14.7。
- 外部标准：Nixpkgs [依赖与包装说明](https://nixos.org/manual/nixpkgs/stable/)、Nix [锁文件](https://nix.dev/manual/nix/2.28/command-ref/new-cli/nix3-flake-lock)、Codex [技能发现](https://developers.openai.com/codex/skills)和[自定义角色](https://developers.openai.com/codex/subagents)。
- 规范：[Project Consensus](https://app.notion.com/p/rekidunois/Project-Consensus-3d60101a85f3815a8c43f271f2b4d91d)、[Plan Reviewer Rule](https://app.notion.com/p/rekidunois/Plan-Reviewer-Rule-3e80101a85f3817a94cbc6eca1a76a5c)、[Test Engineer Rule](https://app.notion.com/p/rekidunois/Test-Engineer-Rule-3d80101a85f380d8a8f5ce528141e253)；冲突时优先共识中的产品操作约束，测试范围以测试规则为准。
- 共享实现基线：[agent-env-nix PR #1](https://github.com/RekiDunois/agent-env-nix/pull/1)，固定 `760c1bf60e8ec19d1985660ec5e3276a8ef80b5a`；该 PR 尚未合并。
- `Proposed review status: pending`。本文件是待审核计划；未经正式 P1–P4 审核并取得 `Plan conclusion: APPROVED`，不得开始核心实现。

## 1. 需求、事实与决策依据

本任务的产品目标是：同一份 `lib/pdfx` 源码交付可被其他 Nix 项目引用的 Python 库和 `pdfx` 命令；携带现有两个技能、两个脚本、三名角色与调度资源。视觉增强在有明确的 `vision-tools/glance` 依赖时启用；基础包先能独立交付。新增入口不更改现有 APM/Codex/OpenCode 行为。

| 决策 | 正式要求（应怎样） | 基线事实（现在怎样、确切文件） | 改动位置 |
| --- | --- | --- | --- |
| D1：单份源码、发行兼容 | Issue #9 不允许另建业务包；消费者固定版本不得自动升为最新源码 | `lib/pdfx` 是内核；`pyproject.toml` 仍叫 `pdf-processing-core`，`packaging/scholar-workflow-pdfx/pyproject.toml` 叫 `scholar-workflow-pdfx`，两者版本均为 `0.1.0`；`scripts/stage_release_package.py` 从 `lib/pdfx` 暂存独立发行物；`CROSS-REPO-CONSUMER-HANDOFF.md` 要保留旧名称给 `paper-analysis` | 新增 `flake.nix`、`flake.lock`、`nix/pdfx.nix`；调用既有暂存脚本 |
| D2：Nix 运行不临时拉 Python 包 | Issue #9 修订后只要求 Nix 入口移除运行期 `uv` 下载；原 APM 入口必须保留 | 两个技能脚本的 PEP 723 依赖是 `scholar-workflow-pdfx>=0.1.0,<0.2`；`test/test_runtime_boundary.py` 明确约束 `uv run --script` 和 `uvx --from`；`CODEX-COMPATIBILITY.md` 规定保留部署路径 | Nix 构建的 Python 环境和独立运行包装器，不改原技能脚本的 PEP 723 声明 |
| D3：技能与角色不能靠 Nix store 自动注册 | Issue #9 要携带有效资源、保留宿主安装 | `.apm/skills/{formula-repair,llm-ocr-refresh}` 含脚本，`formula-repair/concurrency-benchmark.json` 被脚本按相邻目录读取；`.apm/agents` 恰有三角色；`CODEX-COMPATIBILITY.md` 的 APM 安装产出是 `.agents/skills/<name>/` 和 `.codex/agents/<name>.toml`。Codex 官方文档同样只从规定目录发现资源 | 将原 APM 资源打包进 Nix 输出，另为已有技能/角色提供共享 Codex 适配；不自动迁移 APM |
| D4：Codex 角色约定不等于强制权限 | Issue #9 保留委派约定，不要求改变宿主 | `CODEX-COMPATIBILITY.md` 已验证 APM 产出的 Codex 角色只含 `name`、`description`、`developer_instructions`，不会保留 OpenCode 的 `mode`、`hidden`、`permission`、`task` 限制 | 不加伪造的权限映射，不把强制权限对等作为测试项 |
| D5：视觉能力可选 | Issue #9 要求基础路径不依赖视觉服务；Apple Vision 仅在支持环境启用 | `lib/pdfx/extract.py` 按 `layout → transcription → native` 尝试，远端提供者由运行时配置；`lib/pdfx/ocr_apple.py` 懒加载 `Quartz/Vision/CoreFoundation`；`ocr_refresh_jobs.py` 运行中依赖 PyMuPDF、Pillow，并通过 `--glance` > `OCR_REFRESH_GLANCE` > `PATH` 寻找 `glance`；`run_glance` 当前总是调用 `sys.executable <glance_path>`，不能直接运行 Nix 二进制包装器 | Nix 可选视觉包装及 `ocr_refresh_jobs.py` 的启动分支 |
| D6：秘密及可变状态不入 store | Issue #9 明确禁止秘密、日志和缓存固化 | 运行器会生成 `.ocr_units/`、`.formula-repair-state.json` 及结果文件；`formula_repair_runner.py` 用 `__file__` 旁的基准数据；图像/网络提供者设置由运行环境持有 | Nix 输出仅为只读程序/资源；可写位置全部来自调用方与独立运行目录 |

事实来源均为上述仓库基线；Nixpkgs 的确切包版本、外部视觉包可消费版本要在实施前按所选 `flake.lock` 核实并记录，不能猜测或把缺失的外部服务写成已验证成功。

## 2. 范围和不做的事

首批支持与本轮验收仅面向 Apple Silicon Mac（`aarch64-darwin`）。Linux 及其他平台支持、构建和测试均不属于本轮交付或验收条件，不要求 Linux 宿主、容器、远程构建器或多平台矩阵；已有非 Mac 结果只保留为对应平台的历史记录，不能替代 Mac 结果。 本次修订依据用户 2026-10-15 18:00 UTC 决定，仅将 v6 的“实际可运行平台”明确为 Mac；保留 v6 删除额外配置要求的修订。本次候选待正式复核，未受影响的历史审核与执行结果保留。

**本次要改**：Nix 可消费的内核/命令输出；两个已有脚本的 Nix 启动包装器；两个技能、三角色、基准文件和相对资源路径的只读交付；必要的直接依赖及版本锁定；基础包与增强配置的区分；对应文档和本次安装目标的最小检查。

**保留**：`pdfx` 的公开模块/命令名及既有参数，`pdf-processing-core` 根项目兼容名称，`scholar-workflow-pdfx>=0.1.0,<0.2` 的既有 APM 调用，两个技能的 PEP 723 元数据、源 `SKILL.md`/角色正文、APM 两个 target 和原有委派约定；原有写入、缓存、回退和调用方所有权。

**不做**：修改任何下游仓库、批量升级已有固定依赖、改变 OCR/公式算法、另建公共仓库或重复建设跨仓安装器、自动安装第三方模型/权重/远端服务、修改用户全局 Codex 配置、尝试把 OpenCode 权限映射成 Codex 强制访问控制、增加业务测试或模型评分关卡。

### 2.1 方案选择与替代方向

**选择本仓固定源码构建**：沿用现有 `stage_release_package.py`，以本次目标提交和 Nix 输入锁文件生成内核发行包，辅以 Nix 包装器和 APM 资源输出。优势是同一提交下的源码、脚本和资源可逐一对应；代价是需由本仓维护 Nix 打包定义和锁文件。

**现实替代方案：直接把已发布的 PyPI 轮子放进 Nix**。它可以减少内核构建步骤，适合只需要既有 `0.1.0` 公共发行物的消费者；但 Issue #9 同时要求同一目标仓库修订的两份脚本、三份角色及技能资源。如果轮子和脚本来自不同修订，无法凭相同版本字符串证明兼容，且不便在实现该 PR 时测试尚未发布的新资源，因此本次不选。以后某下游只使用纯 `pdfx` 库时，可自主选择已验证的固定轮子版本，不能视为本任务强制迁移。

## 3. 冻结实现方案

### 3.1 Nix 输出与依赖归属

1. 在本仓新增 `flake.nix`、提交 `flake.lock`。`nix/pdfx.nix` 用 Nixpkgs Python 构建函数及包装函数负责本仓库/CLI和脚本闭包；已有技能/角色的Nix/Codex 资源适配复用固定的 `agent-env-nix.lib.mkAgentPackage`，项目接入复用 `lib.mkProject`，不复制安装器或配置合并器。本仓仍维护第三方源、固定散列及运行依赖，禁止浮动追踪 `main`。普通库/CLI不以共享项目激活为前提，也不强制额外公开 `python.withPackages` 专用属性。
2. 普通基础库/CLI可先发布，不等待资源适配全链或视觉增强；资源适配仍是本任务必须完成的后续交付。基础输出为 `packages.<system>.pdfx`，包含 `bin/pdfx`、`bin/pdfx-formula-repair-runner`、`bin/pdfx-ocr-refresh-jobs`，以及 `share/pdf-processing-core/apm/.apm/` 资源树。另提供 `devShells.<system>.default`，使用与构建相同的 Python 包环境以验证 `import pdfx`。首批实施与本轮验收仅使用 `aarch64-darwin`。flake 中存在某系统的输出，不等于已在该系统验证通过；只对有真实构建与运行结果的平台报告通过，其他平台标为“未验证”。`x86_64-linux` 构建及其专用运行环境不是其他平台通过测试或基础包交付的前提。
3. 由 `scripts/stage_release_package.py` 在 Nix 构建的临时目录准备现有 `packaging/scholar-workflow-pdfx/pyproject.toml` 与 `lib/pdfx`；以 Nixpkgs 的 Python PEP 517 构建能力安装该暂存项目。不得复制出另一份需人工维护的 `pdfx` 源码。构建前调用现有 `validate_release_contract` 核对根项目、发行项目、`pdfx.__version__`、CLI 和依赖是否一致。
4. Nix 中由**同一套** Python/`PyMuPDF>=1.24` 供库、命令及两个运行包装器使用；增强脚本还需要显式安装 `Pillow`。构建时使用的 `hatchling` 是构建依赖；`PyMuPDF` 是库运行依赖；`Pillow` 是图像渲染脚本的运行依赖。基础包不得引入 `vision-tools`、云端服务及权重。若已锁定的 Nixpkgs 缺少符合要求的包，实施者须在本仓加固定源和散列的包构建定义并保持发行兼容；不得默默使用版本不足的系统包。
5. 包名和版本号来自现有发行元数据；同时记录**具体 Nix 输入修订与目标仓库提交号**。同为 `0.1.0` 不代表任意仓库提交与已发行 PyPI 轮子逐字节等价。消费者只可选自己核实过的固定引用，不因新输出存在就无条件迁移。

### 3.1.1 共享输入的正常获取与失败处理

共享`agent-env-nix`当前为私有仓库。针对[复审A1](https://github.com/ScholarWorkflow/pdf-processing-core/pull/10#issuecomment-6193414842)，需求决定人于2026-10-15明确选择保持既定共享输入及`packages.<system>.pdfx-agent`结构：按正常Nix路径请求，成功即继续，失败则保留并报告实际错误，由用户自行处理仓库权限或可见性。不承诺每个消费者都能成功获取。

不添加权限预检查、预先公开仓库、额外认证操作或依赖结构改造要求，不自动修改仓库可见性。A1所建议的获取边界重构不再作为本任务必改阻断项；“纯库/CLI无需宿主激活”仍不被当作“绝不读取共享输入”的证明。其他业务范围、共享接口及测试清单/recipe保持不变。

### 3.2 包装器与资源

1. `bin/pdfx` 调用已安装的 `pdfx.cli:main`。`bin/pdfx-formula-repair-runner` 运行打包后的 `formula_repair_runner.py`；`bin/pdfx-ocr-refresh-jobs` 运行打包后的 `ocr_refresh_jobs.py`。两个脚本包装器使用 Nix 输出中的 Python，并可在任意工作目录启动；不经 `uv`/`uvx`，不注入生产仓库的 `lib` 路径，也不自行推测资源文件位置。
2. `formula_repair_runner.py` 和 `concurrency-benchmark.json` 在只读资源树中保持相邻目录关系；其运行态的状态文件、`.ocr_units/`、缓存、PDF、日志、凭据等按调用方指定目录生成。包装器只提供程序/资源绝对路径，不把 Nix store 设为运行态工作目录。
3. `share/pdf-processing-core/apm/.apm/` 完整保留两份 `SKILL.md`、两个 Python 脚本、基准文件及三份 `*.agent.md`。普通 `packages.<system>.pdfx` 保留上述程序与资源接口；纯库/CLI用户不需要 `mkProject` 或宿主配置。现有 APM 的 Codex/OpenCode 安装和转换路径继续有效。
4. 本仓已有两技能及三角色，必须交付 `packages.<system>.pdfx-agent` 资源适配输出，消费端可选择不激活。该适配由 `mkAgentPackage` 声明：`files` 指向完整技能目录和所需资源，`exports.skills` 声明两技能；`exports.agents` 从三名现有角色显式提供 `description` 和 `developer_instructions`。共享库不解析 APM manifest，不把原始 `*.agent.md` 自动转换为 Codex TOML，不自动转换业务指令；本仓负责保持既有角色语义并将 Nix 专用资源中的运行命令指向包装器。Nix专用资源仅在构建时派生，源APM文件不改，不伪造OpenCode权限等价。
5. `pdfx-agent` 的 `runtimePackages = [ pdfx ]` 引用普通库/CLI derivation，不引用自身；它仅保留程序闭包，不会自动将命令加入使用者 PATH；Nix专用技能/角色使用本仓包装器的绝对store路径，或由包装器显式设置其必要PATH。原APM的PEP 723、`uv run --script`/`uvx --from`和发行版本约束保持，Nix入口不运行期取包。
6. 仅选择 Nix/Codex 接入的消费项目使用 `mkProject { roots = [ packages.<system>.pdfx-agent ]; }` 及共享 apply/remove 接口；普通库/CLI使用与原APM用户都不强制激活或自动迁移。适配包通过共享库投射 Codex 资源；共享库目前不支持 OpenCode，旧 OpenCode/APM 路径保留。

**配置与生命周期边界**：直接复用上述共享实现，不在本仓另写冲突检测、所有权记录或卸载器。接入限于选定项目和选定资源；无关技能、角色、MCP、模型、provider、权限、hooks及未知字段保持。相同已有技能/角色/MCP复用且不认领，不同定义报冲突；父级及全局配置只读检查，不以项目优先级静默遮蔽。

共享库已拥有的技能目录是完整生成产物，升级整体替换，修改应回源仓，不承诺保留对生成技能的直接编辑。已拥有agents/MCP仅在与上次生成状态一致时更新或移除，用户修改保留并报告；共享目录和无关资源不删除。已有APM资源冲突时由用户决定保留或切换该项，不静默接管。

本版替换旧接入实现；v2批准仅对应旧版本，不自动批准本版变化。验证只核查本仓资源映射和必要入口，不在各包重复共享安装器全套回归。

### 3.3 视觉增强的条件交付

1. 基础 `pdfx` 包只宣称文本提取、质量检查、命令及脚本运行环境已就绪。`extract --strategy auto` 等既有选项保留，但未配置视觉提供者时不得宣称扫描 PDF 一定能 OCR；返回的 `vision-failed`/`none` 等既有状态仍交调用方处理。不能把“CLI 能识别选项”写成“增强能力可用”。
2. 增强部分使用显式传入的 `vision-tools/glance` **Nix derivation** 组成包装配置；只在真实依赖可固定、构建和安装时发布增强输出，不能在无该依赖时提供声称完整的 `full` 版本。默认 `PATH` 提供程序，`OCR_REFRESH_GLANCE` 和 `--glance` 依现有优先级覆盖；endpoint/model/凭据、缓存/日志只来自运行时。
3. 修改 `ocr_refresh_jobs.py:run_glance` 的入口选择：普通可执行 `glance`/Nix 包装器直接作为子进程运行；既有 Python 脚本仍可在当前 Python 环境运行。维持 `--glance` > `OCR_REFRESH_GLANCE` > `PATH`、`--ocr` 参数、超时及错误结果的原语义。只处理为新增 Nix 启动方式所必需的差异。
4. `Apple Vision` 仅在确认 PyObjC 绑定和 Apple 系统框架可用的 Darwin 环境声明支持；Linux 上不尝试链接或假装提供 `Vision`。远端 OCR、模型和权重不作为本仓包的隐含依赖。视觉依赖未就绪时标记为“增强未发布/待上游”，不得据此阻止**基础库与 CLI** 的交付，也不得报告增强检查通过。

## 4. 实施顺序、文件责任与停止条件

| 步骤 | 文件/归属 | 具体动作 | 完成条件或停止条件 |
| --- | --- | --- | --- |
| S0 | 本 PR、`plan/issue-9-nix.md` | 先完成 P1–P4 正式审核；固定本计划、需求和基线 SHA；已知的包版本冲突需在此处理 | 没有 `APPROVED` 不开始核心实现 |
| S1 | `flake.nix`、`flake.lock`、`nix/pdfx.nix` | 固定 Nix 输入，用原暂存脚本构建 `scholar-workflow-pdfx`；暴露基础输出与 Python 开发环境 | `pdfx` 由 Nix 输出提供且可引用；PyMuPDF 版本满足约束；不引入视觉包 |
| S2 | Nix 打包文件 | 同一环境包装两个脚本，保留原APM资源，以共享mkAgentPackage声明Nix/Codex适配输出pdfx-agent；显式资源定位，不另写激活器 | 运行包装器可独立于 checkout 启动，资源路径在 Nix 输出中存在且相邻关系正确 |
| S3 | `docs/NIX.md`、必要的 `README.md` 链接 | 写出锁定版本/提交、Nix 构建与干净安装、导入、CLI/脚本调用、原APM/可选共享Codex接入边界、生成技能所有权、外部视觉配置及能力限制 | 新消费端不必猜 Python 位置、Nix store 资源位置或把 store 当可写工作目录 |
| S4 | `ocr_refresh_jobs.py` 和其已存在的测试文件（仅需时） | 允许 Nix 可执行 `glance` 直接调用，保留 Python 脚本和错误/超时行为；独立视觉包装等上游可固定后接线 | 无增强依赖时仅基础包可发布；增强包只有构建并校验入口后才发布 |
| S5 | `plan/issue-9-nix.md`、必要的产品安装检查 | 对本次交付执行第 5 节最小必测项；记录所用 commit、Nix 输入修订、消费目录、命令和结果 | 所有已声明交付的必测项有实际结果；未就绪增强项标记待上游，不伪报 PASS |

**失败与清理**：依赖无法固定/构建时停止对应输出，不能转为运行时联网安装；Nix 失败构建不影响原 APM/PyPI 路径。调用输入缺失继续报原错误；提供者不可用维持原失败/降级状态，不写入虚假完成标记。独立临时运行目录删除前保留必要失败记录；不修改用户状态、不在 store 写入缓存或日志。

**下游交接**：基础输出完成后记录可供 `paper-analysis`、`pdf-book-pipeline`、`exam-preprocess`、`study-tools`、`admissions-data` 固定引用的精确包输出/SHA、已验证命令、尚缺的视觉依赖。消费者在自己的变更中决定引用版本与直接依赖；本 PR 不修改其源码或锁文件。

## 5. 唯一必测清单及本地执行步骤

测试修订：`issue9-nix-tests-v6-2026-10-15`，对应实施方案 v7；相对测试 v5 仅将本轮平台明确为 `aarch64-darwin`，保留已撤销额外配置要求的删除，不新增检查或重复执行。T1–T6 的业务目标、已有命令与检查方法不变。PR 已记录在 `aarch64-darwin` 上 T1–T5 通过，T6 未执行；旧结果仍适用，不因本次计划文字修订重跑，第三关结论仍须按测试规则审核。

范围仅覆盖固定构建、安装、库导入、CLI/脚本、资源交付及直接修改的 glance 启动行为。所有 T1–T6 均不得要求 Linux 专用软件包、Linux 宿主、Linux 容器或远程 Linux 构建器；不得把 `x86_64-linux` 构建设为其他平台通过或本轮交付的条件。本轮测试仅在 `aarch64-darwin` 进行，只对该平台报告通过；未测试的平台标为“未验证”，不作为本轮业务失败或额外阻断。普通库/CLI先交付，不以宿主激活为通过条件；pdfx-agent仍是本任务必须交付的资源包，发布时完成T4的适配部分。不新增模型效果、公式准确率、Codex委派、权限认证、第三方服务可用性或全仓回归；共享安装器的冲突、升级/卸载、GC与配置保护不在本仓重复测试。

| 项 | 业务目标；输入/场景 | 预期结果及足够检查方式 |
| --- | --- | --- |
| T1 | 精确提交和锁定输入构建普通pdfx | 一次nix build成功；输出含pdfx及两脚本包装器。记录输出路径，后续安装与运行复用该产物 |
| T2 | 仓库外干净消费目录使用普通库/CLI；一页临时PDF | 独立profile安装成功；pdfx --help、quality --help成功；quality --json解析成功且pages有一页。只查看退出码与必要JSON字段，不做OCR业务 |
| T3 | 同版本Nix Python环境导入 | pdfx、pdfx.quality、pdfx.extract、pdfx.status导入成功，模块位置来自Nix安装输出，不是checkout。复用该环境生成T2临时输入，无额外公开库属性要求 |
| T4 | 普通包资源/脚本；资源输出pdfx-agent及一次共享项目安装 | 两脚本--help成功；原APM两技能、两脚本、三角色及相邻基准存在。pdfx-agent经mkProject正式apply后，两技能是完整普通文件树，三角色正确映射；Nix专用资源明确调用本仓store包装器，不能仅有store原资源就宣称宿主已发现。已有APM投射结果复用，不启动模型 |
| T5（仅glance调用变更时） | 可执行glance和Python脚本，既有显式参数/环境变量选择，--ocr、超时与失败 | 在已有unittest框架中只选择本次启动兼容断言；输入为临时fake程序与PNG，检查选中程序、参数、timeout标记及非零退出错误。无需真实OCR或全文件业务回归 |
| T6（仅增强输出实际发布时） | 固定真实vision-tools/glance及匹配Python依赖的增强安装 | 构建/安装成功，实际安装的glance一次受控启动成功；不测远端模型质量或服务可用性。未发布增强时记“待上游/未执行”，不阻止基础或已完成资源交付。Apple Vision只在对应Darwin能力实际交付时检查 |

### 5.1 准备与T1–T3、T4基础资源

本地执行者记录待测生产仓库确切SHA、Nix版本和所用输入锁。先确认已有Nix、jq、Python及TOML解析工具yq（支持`-p toml -o json`）可用；工具缺失只处理对应预检，不安装产品外测试平台。远端仓库读取使用已有认证，不把凭据写入flake/store。下面步骤在 Apple Silicon Mac（`aarch64-darwin`）上使用该宿主的默认 Nix 输出，不指定 Linux 平台或交叉构建。默认复用仍有效的安装；下面给出本任务首次安装的完整步骤，复用时引用旧RUN/OUT/profile，不因文档或提交编号变化自动重建。

```bash
set -euo pipefail
# 在待测生产仓库读取SHA；不从worktrees或消费者取版本
SHA=$(git rev-parse HEAD)
RUN=$(mktemp -d /tmp/pdfx-nix9-XXXXXX)
mkdir -p "$RUN/consumer" "$RUN/home" "$RUN/codex-home"
SRC="github:ScholarWorkflow/pdf-processing-core/$SHA"
cd "$RUN/consumer"

# T1：仅构建一次；profile安装复用同一derivation和store产物
OUT=$(nix build --no-link --print-out-paths "$SRC#pdfx")
nix profile install --profile "$RUN/profile" "$SRC#pdfx"
for cmd in pdfx pdfx-formula-repair-runner pdfx-ocr-refresh-jobs; do
  test -x "$OUT/bin/$cmd"
done

# T2/T3：同一构建环境，临时普通PDF，没有宿主注册步骤
"$RUN/profile/bin/pdfx" --help
"$RUN/profile/bin/pdfx" quality --help
nix develop "$SRC" --command python -c 'import pdfx, pdfx.quality, pdfx.extract, pdfx.status; print(pdfx.__file__)'
nix develop "$SRC" --command python -c 'import pymupdf, sys; d=pymupdf.open(); p=d.new_page(); p.insert_text((72,72), "sample"); d.save(sys.argv[1]); d.close()' "$RUN/consumer/sample.pdf"
"$RUN/profile/bin/pdfx" quality "$RUN/consumer/sample.pdf" --json > "$RUN/quality.json"
jq -e '.pdf and (.pages | type == "array") and (.pages | length == 1)' "$RUN/quality.json"

# T4基础部分
"$RUN/profile/bin/pdfx-formula-repair-runner" --help
"$RUN/profile/bin/pdfx-ocr-refresh-jobs" --help
APM="$OUT/share/pdf-processing-core/apm/.apm"
for f in skills/formula-repair/SKILL.md skills/formula-repair/formula_repair_runner.py skills/formula-repair/concurrency-benchmark.json skills/llm-ocr-refresh/SKILL.md skills/llm-ocr-refresh/ocr_refresh_jobs.py agents/pdf-to-text.agent.md agents/formula-repair.agent.md agents/llm-ocr-refresh-unit-worker.agent.md; do
  test -f "$APM/$f"
done
```

直接查看T3打印的模块路径应在Nix安装输出中，不在生产源码或consumer；只记必要结果，不建立额外来源认证。`nix develop`复用同一锁定环境及已有构建缓存，不是再次干净安装。上述普通包检查独立于下一节。

### 5.2 T4资源适配：一次正式mkProject/apply

仅在本次待测SHA已交付pdfx-agent时执行；否则明确记资源适配未交付，不能把整个Issue写成已完成。使用同一RUN下的新项目，不复制产品文件或补改生成配置。下面表达式是消费者正常接入声明，所有产品资源来自固定远端包，不使用生产checkout作为输入。

```bash
SHARED='github:RekiDunois/agent-env-nix/760c1bf60e8ec19d1985660ec5e3276a8ef80b5a'
SYSTEM=$(nix eval --impure --raw --expr builtins.currentSystem)
RESOURCE=$(nix build --no-link --print-out-paths "$SRC#pdfx-agent")
ENV_OUT=$(nix build --no-link --print-out-paths --expr "
  let
    shared = builtins.getFlake \"$SHARED\";
    producer = builtins.getFlake \"$SRC\";
    pkgs = shared.inputs.nixpkgs.legacyPackages.\"$SYSTEM\";
  in shared.lib.mkProject {
    inherit pkgs;
    roots = [ producer.packages.\"$SYSTEM\".pdfx-agent ];
  }")
PROJECT="$RUN/agent-consumer"
mkdir -p "$PROJECT"
HOME="$RUN/home" CODEX_HOME="$RUN/codex-home" \
  "$ENV_OUT/bin/agent-env" apply --project "$PROJECT"

# 本仓预期导出集合，采用共享环境正式manifest中的资源映射
jq -e '(.skills | keys) == ["formula-repair","llm-ocr-refresh"] and (.agents | keys) == ["formula-repair","llm-ocr-refresh-unit-worker","pdf-to-text"]' "$ENV_OUT/manifest.json"
for skill in formula-repair llm-ocr-refresh; do
  SOURCE=$(jq -r --arg name "$skill" '.skills[$name].source' "$ENV_OUT/manifest.json")
  test -f "$PROJECT/.agents/skills/$skill/SKILL.md"
  diff -r "$SOURCE" "$PROJECT/.agents/skills/$skill"
  # 完整普通文件复制；不以符号链接伪装已安装技能
  test -z "$(find "$PROJECT/.agents/skills/$skill" -type l -print)"
done
for name in pdf-to-text formula-repair llm-ocr-refresh-unit-worker; do
  yq -p toml -o json "$PROJECT/.codex/agents/$name.toml" |
    jq -e --arg name "$name" '.name == $name and (.description | type == "string" and length > 0) and (.developer_instructions | type == "string" and length > 0)'
done
```

检查两份安装后的SKILL.md及三角色的developer_instructions：应保持既有角色职责/委派目标，Nix专用调用位置分别指向本版本`$OUT/bin/pdfx`及两个脚本包装器的绝对store路径，不依赖uv下载或仅凭runtimePackages假定PATH可用。此处直接人工核对必要命令和角色内容即可，不建新扫描器；脚本能启动已由5.1两个--help覆盖，不重复执行。安装的formula-repair脚本及concurrency-benchmark.json仍相邻。manifest用于定位本仓导出资源，不额外采集全闭包或环境快照。

若祖先或系统配置有同名冲突，记录该环境阻塞并停止此项，不编辑那些配置、不删除已有资源、不绕过共享保护。该故障不反转已成立的普通库/CLI结果。无需再测apply重复性、冲突矩阵、升级/remove、GC或配置保护；这些属于共享库既有验证。

### 5.3 T5调用兼容与T6增强等待项

T5复用`.apm/skills/llm-ocr-refresh/tests/test_ocr_refresh_jobs.py`的unittest、临时目录和fake glance惯例。实现者在同一文件仅补本次启动兼容所需方法：`RFixture.test_nix_glance_executable`、`test_python_glance_script`、`test_glance_selection_priority`、`test_glance_timeout_and_failure`。这些名称只是recipe的示例选择，可复用或合并等价的现有检查，不强制新增四个方法；若实际选择名称不同，执行前同步下面选择列表。它们不是已有通过结果，不运行整个RFixture已有业务套件。

输入/断言约定：可执行fake和非可执行.py各输出固定短文本，必须收到输入PNG及--ocr；显式--glance、OCR_REFRESH_GLANCE和PATH按既有优先级选择对应fake；超时fake超过传入短timeout，断言run_glance的timed_out及unit_timeout；非零退出fake返回预设退出码，断言原glance_exit错误及无成功文本。复用框架断言和subprocess mock/fake，不新建通用设施、不调用真实provider。只覆盖上述直接改变的启动行为，不扩展排列组合或OCR输出质量。

在实施已补齐这些方法后，本地执行者从生产仓库记录测试目录绝对路径；保持执行工作目录在RUN，不把测试源码安装进消费者。T5针对直接修改的调用函数，按既有单元测试方式加载源码，不能用其结果替代T1–T4正式安装结果。

执行 T5 前，仅确认实际加载的 `ocr_refresh_jobs.py` 和 `test_ocr_refresh_jobs.py` 与本轮记录的 `SHA` 一致。下述 `git diff --quiet` 返回 0 方可继续；非 0 则停止 T5，排查本地文件差异或命令错误。其他文件有未提交修改不影响 T5，无需重新提取或复制源码：

```bash
# TEST_DIR为待测生产仓库的绝对路径加下述现有测试目录；执行前记录实际值
set -e
: "${TEST_DIR:?设置为待测生产仓库/.apm/skills/llm-ocr-refresh/tests的绝对路径}"
TEST_REPO=$(git -C "$TEST_DIR" rev-parse --show-toplevel)
git -C "$TEST_REPO" diff --quiet "$SHA" -- \
  .apm/skills/llm-ocr-refresh/ocr_refresh_jobs.py \
  .apm/skills/llm-ocr-refresh/tests/test_ocr_refresh_jobs.py
cd "$RUN/consumer"
TMPDIR="$RUN" nix develop "$SRC" --command python -c '
import sys, unittest
sys.path.insert(0, sys.argv[1])
names = ["test_nix_glance_executable", "test_python_glance_script", "test_glance_selection_priority", "test_glance_timeout_and_failure"]
suite = unittest.defaultTestLoader.loadTestsFromNames(["test_ocr_refresh_jobs.RFixture." + n for n in names])
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
' "$TEST_DIR"
```

T6沿用条件交付。当前方案尚未固定增强输出属性与vision-tools可消费版本，因此本次不虚构完整可执行命令或记为PASS。增强输出发布前，由本地执行者在本节补齐真实输出属性、固定上游SHA、安装命令和受控启动命令，按既有T6目标复核受影响步骤后执行；不阻断T1–T4。一次启动使用实际安装的glance受支持的无模型入口（如其实际支持的--help），验证包装程序能够启动，不把它当作OCR服务或质量通过。若实际入口不提供无模型启动，先按真实上游用法补明确受控方案，不能自行使用真实凭据或绕过程序。Apple Vision仅在实际Darwin增强交付时核对所声明绑定/系统框架，Linux不记录Apple Vision成功。

### 5.4 结果、复用、重试与清理

记录实际SHA、执行平台、锁定输入、RUN、安装命令及T1–T6逐项结果；不要求额外逐项证据账本。只记录实际测试平台的结论，不要求为了验证其他系统（包括 Linux）额外准备软件包、容器或构建环境。复用有效安装、构建及未受影响PASS，不因文档变更或提交编号改变重复安装。正式首次安装已覆盖本目标，不另建第二套consumer。

外部下载故障先确认属于环境；同一固定SHA/参数最多重试一次，复用已完成store产物。已确认无新信息的故障停止，受影响项记无法判断；业务失败不得通过重试、改输入或放宽预期消除。apply有部分进展或冲突时保留RUN及必要报错，不自动remove或绕过保护；正式重试按共享支持的同项目apply继续，不能借重试改产品行为。保留必要失败输出，临时资料只在独立/tmp；成功且结果已记录后可清理本次独占临时目录，不删除用户配置或其他项目资源。清理不是新增验收项。


## 6. 安全条件、反例和未完成事项

- **不变条件 A**：Nix 核心包不要求 `vision-tools`；反例为某个导入或包装器在普通文本提取时直接启动 `glance`。方案以基础/增强分开及懒加载解决；不改上游 OCR 算法。
- **不变条件 B**：Nix 同一版本的 `pdfx` API/CLI 仍来自 `lib/pdfx`；反例为维护第二份 Nix 专用内核或以任意 `main` 替代固定发行版本。方案用已有暂存脚本和 SHA/依赖锁定。
- **不变条件 C**：Codex/OpenCode 的 APM 入口保持原语义；反例为直接删除脚本 PEP 723、把所有 `uvx` 示例改成 Nix 专用命令，或以 `hidden` 作为 Codex ACL 成功证据。方案保持默认资源原样及双入口。
- **不变条件 D**：所有可写状态、模型配置和秘密在 store 外；反例为 `.ocr_units` 写入只读资源目录或将 API 密钥固定在 Nix derivation。方案由调用方提供可写路径，包只包含程序/只读资源。
- **不变条件 E**：Nix `glance` 包装器是可执行文件时不能交给 Python 当作源码解析；方案只在对应调用点区分可执行文件和既有 Python 脚本。
- **不变条件 F**：原 APM 角色/技能的实际发现仍以合法安装路径为准；反例为只有 `/nix/store/.../.apm` 文件就宣称 Codex 已加载。方案区分普通资源输出、原APM安装与可选共享Codex激活，不声称未执行的注册。

**已知等待项**：`vision-tools/glance` 可固定的 Nix 依赖、各消费者实际固定版本，以及 Darwin 系统框架/绑定可用性由后续实施按对应源记录。它们不阻止**基础** Nix 包设计与交付；实际增强输出是否可以发布取决于依赖和对应最小安装检查。不得把这些未验证事项写成已经通过。

## 7. 交接和拉取请求状态

- 本拉取请求初始仅提交本计划；为 `draft`，正文含 `Closes #9`，但**不能提前合并**。后续 Nix 文件、文档、直接受影响的产品代码和必要测试均提交在同一分支与同一拉取请求。
- 实施前由独立计划审核者按 P1–P4 给出正式记录；测试第一、第二和第三关也须按 `Test Engineer Rule` 的授权角色处理。本文件不得自行把计划写成 `APPROVED`，也不把方案审核通过等同于测试/合并通过。
- 交接时只维护此 `plan/issue-9-nix.md` 最新完整版本，PR/Issue 评论引用路径及版本，避免让本地代理拼接评论。交付记录列明基础包可消费的实际 SHA、包版本、完成的 T1–T4 结果、增强项状态及下游仍需自行核对的依赖。


