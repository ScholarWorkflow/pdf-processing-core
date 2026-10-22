# 公共 PDF agent 可组合交付测试计划

- Test revision: PDF-COMPOSE-T3 / 2026-10-22。
- 产品基线 main@3444dca0e0b84f46d1839ab5c707afa78e23d3a7；实现产品为 7ac7bca10206c5481f47ccd89617566cf8a5a065。
- 对应 [执行计划](execution-plan.md) PDF-COMPOSE-P1。状态：Gate1 已由独立 R1 通过；本次 Gate2 Recipe 待独立审核，全部本轮执行未执行。
- 规则及需求来源见执行计划，不将旧 issue9 全清单重新打开。

## 唯一必测清单（上限）

1. 公共实例与资源交付。输入为同一 pkgs/共享输入/options 两次引用新工厂的 pdfxAgent，并用已有 mkProject 选择该实例。预期得到一份同 ID 的两技能/三角色，完整相邻资源保留，固定入口指向公共包装器；默认 codex 和已有 override 仍可表达。检查方式：一次受影响 Nix 求值/构建及生成资源检查，解析结构化输出，不增加共享安装器冲突/卸载全回归。
2. 已改固定启动可用。输入为现有最小 PDF/修复准备资产和隔离目录；从公开包装器调用必要入口，确认已改路径实际可启动、读取相邻资源并返回原约定结果，不借 uv 下载。两个包装器使用其既有最小非视觉操作，pdf-to-text 由 exam 的正式 caller 集成检查覆盖，不在上游伪造新 batch。检查方式：原有最小入口命令及对应输出；不为本项运行 OCR/provider 全链或重复算法回归。
3. 兼容边界。输入为最终 diff 和三角色投影。预期 APM/PEP 723 源、业务字段、CLI 参数保持；Nix 条件式 JSON 协议仅承接既有角色要求，pdf-to-text 正常摘要仍允许。检查方式：静态差异与完整角色读审，无需重跑未改 APM 全业务。

## Recipe 与实际执行状态

以下为原 Gate1 冻结的执行约束；本版已在后文补齐 pdfcore 云端 Recipe 并待 Gate2 审核，不增加业务测试：
- 固定已产生的产品 SHA、Nix inputs、实际可运行平台。旧首批验收是 aarch64-darwin；当前作者仅 Linux，未获用户 Mac 执行授权。记录平台限制，不用 Linux 结果冒称 Darwin。
- 引用仍适用的既有 fixture/最小输入及既有检查命令，填写输入绝对路径、预期字段、公开输出路径、准备/运行命令和 /tmp 独占输出目录。只补目标 1–2 所需值，不另建判定器或测试设施。
- 如需正式安装，本次改变安装输出，只建一个固定 SHA 的仓外干净 consumer 并复用，不手补产品文件。无关未改部分引用旧结果。
- 若实际步骤涉及 Codex，使用既有 eval 服务和版本化 fixture 配置文件，允许在隔离 Recipe 中编辑生成配置；不改产品定义或服务配置。以 --cd 与 --sandbox workspace-write 调用，不添加 projects 信任证明，不用请求级 --config 重复配置。模型与配置遵循有效 fixtures。
- 失败：产品错误保留原结果，停止受影响项；外部不可用记无法判断。默认每项一次，确认外部条件恢复后最多一次同参数重试，保留两次记录；未恢复不连续重试。业务失败不采样求通过。
- 不运行 Mac、不安装工具、不执行正式测试，直到环境/步骤和授权落实；计划编写不等于执行批准。

## 结果与职责

当前 1–3 均未执行，无 PASS。计划作者提交候选；独立审核者负责 Gate1/Gate2；执行负责人补齐 Recipe 并运行获准项目，记录实际版本/命令/必要输出；审核者按已有方法判断 Gate3。文档 SHA 变化本身不使结果失效。后续 exam 负责真实 batch handoff，最终 Contact+Study 组合只验证一次，不成为本仓重复全业务门槛。


## 双根交付的唯一记录归属

最终 Contact+Study 联合 roots 的一次组合安装验证由 Study consumer PR 的执行负责人拥有，正式结果写入该 PR 的 plan/pdf-agent-composition/consumer-handoff.md（[Study #16](https://github.com/StudyCoachWorkflow/study-tools/pull/16)；[Contact #131](https://github.com/ScholarWorkflow/professor-contact/pull/131) 引用），Contact PR 只引用同一结果。范围仅为两顶层 roots 经既有共享层合并、公共 PDF 唯一实例及必要资源真实复制，不运行 Contact 邮件或 Study 全业务，不重复 producer 入口验证。这个后续记录不把尚未更新的 consumer 当作本仓独立输出的阻塞项。

## Gate2 Recipe：PDF-COMPOSE-T3（2026-10-22）

目标产品为 [7ac7bca10206c5481f47ccd89617566cf8a5a065](https://github.com/ScholarWorkflow/pdf-processing-core/commit/7ac7bca10206c5481f47ccd89617566cf8a5a065)，独立代码复核见 [R2](code-review-record.md)。本节为待独立审核的具体 Recipe，不是已执行结果。

实际环境：当前云端 `x86_64-linux`，复用已有 `/workspace/shared/nix-bootstrap/bin/nix`（已核验 Nix 2.35.3）、`jq` 与 Python；不安装工具、不用用户 Mac、不访问外部视觉/模型服务。该平台已经存在于本仓 flake 输出；此结果只代表 Linux，不宣称 Darwin 实测。输入锁保持产品提交中的 `flake.lock`，共享层仍为 `760c1bf60e8ec19d1985660ec5e3276a8ef80b5a`，Nixpkgs `6510408d147d8c5e6d76043e899e9251756f6a75`。正常远端获取失败时记录实际错误，不添加凭据、不改权限、不换浮动来源。

### 目标1：一次正式构建与 apply

所有 Nix 和 store 访问均在现有 `bin/run` 的同一个挂载上下文内进行，使用其中已暴露的 `nix` 与 `/usr/bin/python3`（已核验 3.13.5）；仅在 apply 命令处覆盖隔离 HOME/CODEX_HOME，避免宿主没有 `/nix` 的路径错配。不改系统挂载或启动器。采用同一 producer 工厂两次产生的相同公共实例作为 roots，既有共享层合并；只创建一个干净仓外 consumer。默认与空 harness 的检查仅求值元数据，不新增安装矩阵。

```bash
/workspace/shared/nix-bootstrap/bin/run /bin/bash <<'RUN_RECIPE'
set -euo pipefail
NIX=nix
SHA=7ac7bca10206c5481f47ccd89617566cf8a5a065
SRC="github:ScholarWorkflow/pdf-processing-core/$SHA"
RUN=$(mktemp -d /tmp/pdf-compose-XXXXXXXX)
printf '%s\n' "$RUN"
mkdir -p "$RUN/project" "$RUN/home" "$RUN/codex-home"
cd "$RUN/project"

"$NIX" eval --json --expr "
  let producer = builtins.getFlake \"$SRC\";
      pkgs = producer.inputs.nixpkgs.legacyPackages.x86_64-linux;
      pdf = producer.lib.mkPdfPackages { inherit pkgs; };
      disabled = producer.lib.mkPdfPackages { inherit pkgs; enableHarnesses = []; };
  in {
    default = pdf.pdfxAgent.agentEnv.enableHarnesses;
    disabled = disabled.pdfxAgent.agentEnv.enableHarnesses;
  }" > "$RUN/harnesses.json"
jq -e '.default == ["codex"] and .disabled == []' "$RUN/harnesses.json"

ENV_OUT=$("$NIX" build --no-link --print-out-paths --expr "
  let producer = builtins.getFlake \"$SRC\";
      pkgs = producer.inputs.nixpkgs.legacyPackages.x86_64-linux;
      pdf = producer.lib.mkPdfPackages { inherit pkgs; };
  in producer.inputs.agent-env-nix.lib.mkProject {
    inherit pkgs;
    roots = [ pdf.pdfxAgent (producer.lib.mkPdfPackages { inherit pkgs; }).pdfxAgent ];
  }")
printf '%s\n' "$ENV_OUT" > "$RUN/environment-output.txt"
HOME="$RUN/home" CODEX_HOME="$RUN/codex-home" \
  "$ENV_OUT/bin/agent-env" apply --project "$RUN/project"

jq -e '(.skills | keys) == ["formula-repair","llm-ocr-refresh"] and (.agents | keys) == ["formula-repair","llm-ocr-refresh-unit-worker","pdf-to-text"]' "$ENV_OUT/manifest.json"
for skill in formula-repair llm-ocr-refresh; do
  SOURCE=$(jq -r --arg name "$skill" '.skills[$name].source' "$ENV_OUT/manifest.json")
  diff -r "$SOURCE" "$RUN/project/.agents/skills/$skill"
done
for relative in formula-repair/formula_repair_runner.py formula-repair/concurrency-benchmark.json llm-ocr-refresh/ocr_refresh_jobs.py; do
  test -f "$RUN/project/.agents/skills/$relative"
done
/usr/bin/python3 - "$RUN/project" <<'PY'
import pathlib, sys, tomllib
root = pathlib.Path(sys.argv[1])
for name in ("pdf-to-text", "formula-repair", "llm-ocr-refresh-unit-worker"):
    path = root / ".codex" / "agents" / (name + ".toml")
    record = tomllib.loads(path.read_text())
    assert record["name"] == name
    assert record["description"] and record["developer_instructions"]
    print(name, record["developer_instructions"], sep="\n")
PY
RUN_RECIPE
```

最后按实际打印的完整角色与两份 SKILL.md 人工核对：pdf-to-text 的两条 batch 命令直接执行调用方绝对路径，无 `uv run`；默认先 `--check`、缓存不改、业务字段和正常摘要保留；公式/OCR技能与角色使用已存在的 store 包装器。条件 JSON 段不要求所有成功返回一律 JSON。不新增通用扫描器，不以字符串出现数判定功能通过。

实际运行日志及命令退出码写入同一 RUN；若任一步非零，保留输出并停止依赖该步的后续操作。首次失败不触发改权限/重试/手工补产品。只有已确认外部条件恢复才允许原参数一次重试。

### 目标2：复用未改入口及保留后续交接

两公共 store wrappers 的实现、Python 环境组成、脚本和相邻资源未改；本轮仅修改 agent Nix 投影与工厂参数。因此旧 [Issue9 计划 §5 T4](https://github.com/ScholarWorkflow/pdf-processing-core/blob/3444dca0e0b84f46d1839ab5c707afa78e23d3a7/plan/issue-9-nix.md#5-唯一必测清单及本地执行步骤) 所记 `aarch64-darwin` 两脚本 `--help` 结果继续适用，原执行记录归属 [PR #10](https://github.com/ScholarWorkflow/pdf-processing-core/pull/10)。不把旧 Darwin 结果说成新 Linux 实测，也不因源树/文档 SHA 改变重复运行。新 pdf-to-text 到真实 Exam batch 的交接只在 [Exam #34](https://github.com/StudyCoachWorkflow/exam-question-pipeline/pull/34) 的目标2完成，本仓不伪造 batch。

### 目标3：静态兼容结论

按独立代码 R2 阅读实际三文件 diff 与 APM 原文。本轮没有改 `.apm/`、算法、CLI 或业务输出字段；兼容判断不需要运行未改变的 APM/业务回归。执行者只提交事实，最终 Gate3 由独立审核给出。
