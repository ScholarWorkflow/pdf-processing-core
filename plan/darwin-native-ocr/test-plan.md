# Darwin 原生 OCR Nix 修复测试计划

- Revision: OCR-TP2；对应 [OCR-EP1](execution-plan.md)，基线 `2be1bf850f29b7ccf7bd1b066ae7292aac33fc87`。
- Proposed review status: pending。第一关必测清单、第二关步骤的独立结论见 [review-record.md](review-record.md)；本轮执行结果见“本轮 Darwin 执行结果”，第三关由测试结果审核者另行判断。作者不声明正式关口通过。
- 依据：[Test Engineer Rule](https://app.notion.com/p/3d80101a85f380d8a8f5ce528141e253) 与 [Project Consensus](https://app.notion.com/p/3d60101a85f3815a8c43f271f2b4d91d)。不重复共享安装器、完整 Study/Exam 或历史业务回归。

## 唯一必测清单

| 项目 | 目标与输入 | 预期结果及检查方式 |
| --- | --- | --- |
| T1 | 最终 Nix 候选；Linux 默认 Python 与 Darwin 默认/Python 3.12 公共工厂 | 表达式可求值；Darwin 固定 Python 的真实依赖包含同 ABI 的 Quartz/Cocoa/CoreML/Vision，Linux 不引入这些绑定；检查直接依赖表达式、工厂 python 的 drvPath 及 derivation 的实际依赖字段。agent-env 只改来源声明，locked 节点与 follows 保持不变。 |
| T2 | Darwin 上构建公共工厂的默认 Python 与 Python 3.12 环境 | 两环境实际构建成功，均能导入 Quartz、Vision、CoreFoundation、CoreML；调用现有 adapter 的框架加载入口成功，不借系统 Python。若两入口是同一 derivation，复用一次。 |
| T3 | T2 中默认 Python 的现有 pdfx.ocr_apple；一张自造英文文字 PNG，不配置远端服务 | 既有原生 adapter 实际返回图片中的 `NATIVE OCR CHECK`；只跑一次最小 OCR，不跑真实材料或复杂公式。 |

T1 是打包表达式检查，不代表 T2/T3 通过。仅 T2/T3 在可用、获准的 Darwin 执行环境运行。初次执行环境为 Linux，故下文保留当时未执行的历史记录；本轮用户已授权在本机 `aarch64-darwin` 完成既有测试，见执行计划“本轮执行环境授权”。这项环境授权取代初次云端执行的限制，不改变 OCR-TP2 的测试步骤或通过条件。没有合适 Darwin 时明确未执行，不宣称可合并。

## 准备与执行

以下 recipe 使用 bash 执行。所有命令在目标仓库外的独立运行目录执行，保留输出。使用已安装的 Nix（支持 flakes），不改变全局配置。下面 `SOURCE` 必须是这次已推送的实际产品 commit；记录实际值、Nix 版本和系统，不用未发布 SHA 或把浮动 main 当测试版本。

```sh
export RUN_DIR=$(mktemp -d /tmp/pdfx-native-ocr.XXXXXX)
cd "$RUN_DIR"
export SOURCE='github:ScholarWorkflow/pdf-processing-core/<actual-pushed-commit>'
nix --version > nix-version.txt
```

T1：实现阶段可用本次候选源码做 Nix parse 和求值，记录候选版本；正式发布后的未变化代码不因文档 SHA 改变重复执行。只读比较 flake.lock 的结构字段及完整 diff，不更新锁。

以下同一表达式使用公开 `lib.mkPdfPackages`，返回既有 python 字段；临时文件仅为调用配置，不复制、替换或修补 producer。

```sh
cat > runtime.nix <<'NIX'
{ source, system, python312 ? false }:
let
  producer = builtins.getFlake source;
  base = producer.inputs.nixpkgs.legacyPackages.${system};
  pkgs = if python312 then base // { python3 = base.python312; } else base;
in (producer.lib.mkPdfPackages { inherit pkgs; }).python
NIX
```

将下面每个测试代码块分别保存为 RUN_DIR 中标出的脚本。使用独立的 `bash` 进程运行，不 `source`，不把 T1/T2/T3 拼成单个脚本或用 `&&` 串联。每次运行前确保同名日志不存在；重试先保留旧日志，另用新的 RUN_DIR，引用或复制未变化的 runtime.nix、SOURCE 及已成功的环境路径记录，不重新构建未受影响环境。各脚本在命令运行前打开 stdout/stderr 日志，EXIT trap 保存原始退出码并原样退出；若日志写入本身失败，记为无法判断而非通过。

T1 脚本 `t1.sh`（本轮只补完整 recipe，下面已有 T1 结果直接复用，不重新执行）：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "${RUN_DIR:?}"
trap 'rc=$?; printf "%s\n" "$rc" > t1.exit-status; exit "$rc"' EXIT
exec > t1.stdout.log 2> t1.stderr.log
nix eval --raw --file runtime.nix --argstr source "$SOURCE" --argstr system x86_64-linux drvPath > linux-drv.txt
nix eval --raw --file runtime.nix --argstr source "$SOURCE" --argstr system aarch64-darwin drvPath > darwin-default-drv.txt
nix eval --raw --file runtime.nix --argstr source "$SOURCE" --argstr system aarch64-darwin --arg python312 true drvPath > darwin-312-drv.txt
nix derivation show "$(cat linux-drv.txt)" "$(cat darwin-default-drv.txt)" "$(cat darwin-312-drv.txt)" > python-env-derivations.json
jq '.derivations | to_entries[] | {derivation: .key, system: .value.system, paths: ([.value.structuredAttrs.chosenOutputs[].paths[]] | unique), inputDrvs: (.value.inputs.drvs | keys)}' python-env-derivations.json > environment-paths.json
```

独立入口：`bash "$RUN_DIR/t1.sh"`。上述字段适用于已有记录的 Nix 2.35.3 JSON 格式。人工核对 `environment-paths.json` 中每个 derivation 的 `system`、`paths` 与 `inputDrvs`：Linux 默认 Python 3.14.7 不含 PyObjC；Darwin 默认 Python 3.14.7 的 core/Cocoa/Quartz/CoreML/Vision 都是 Python 3.14 ABI、PyObjC 11.1；Darwin Python 3.12.15 则全部为 Python 3.12 ABI、PyObjC 11.1。以当前锁定 Nixpkgs 的版本声明为预期依据，不把“有同名包”当成版本正确。`paths` 来自 `structuredAttrs.chosenOutputs[].paths[]`，`inputDrvs` 来自 `inputs.drvs`，不对整个 JSON 做关键词扫描。若实际 Nix 版本返回不同结构，保留原 JSON 并暂停受影响的字段检查，不把空字段视为通过。命令退出 0 仍须满足这些观察点；只是求值证据，不代表构建/导入通过。

T2：仅在获准的 aarch64-darwin 环境执行，复用上述 RUN_DIR 和 SOURCE。两个版本分别独立运行，某版本失败即停止该版本的构建/导入链；不阻止另一版本单独诊断，也不能用另一版本成功覆盖失败。未获准时不转到用户电脑。

默认版本脚本 `t2-default.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "${RUN_DIR:?}"
trap 'rc=$?; printf "%s\n" "$rc" > t2-default.exit-status; exit "$rc"' EXIT
exec > t2-default.stdout.log 2> t2-default.stderr.log
nix build --no-link --print-out-paths --file runtime.nix --argstr source "$SOURCE" --argstr system aarch64-darwin > default-path.txt
runtime=$(cat default-path.txt)
"$runtime/bin/python" -c 'import Quartz, Vision, CoreFoundation, CoreML; from pdfx.ocr_apple import _vision_frameworks; _vision_frameworks(); print("native bindings imported")'
```

独立入口：`bash "$RUN_DIR/t2-default.sh"`。

Python 3.12 脚本 `t2-312.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "${RUN_DIR:?}"
trap 'rc=$?; printf "%s\n" "$rc" > t2-312.exit-status; exit "$rc"' EXIT
exec > t2-312.stdout.log 2> t2-312.stderr.log
nix build --no-link --print-out-paths --file runtime.nix --argstr source "$SOURCE" --argstr system aarch64-darwin --arg python312 true > python312-path.txt
runtime=$(cat python312-path.txt)
"$runtime/bin/python" -c 'import Quartz, Vision, CoreFoundation, CoreML; from pdfx.ocr_apple import _vision_frameworks; _vision_frameworks(); print("native bindings imported")'
```

独立入口：`bash "$RUN_DIR/t2-312.sh"`。T2 通过必须两个入口均成功构建、导入且退出码为 0；任一失败不能整体通过。若 T1 已证明两入口为同一 derivation，直接复用一次成功构建/导入结果，另一项注明复用而非伪造第二次执行。

T3：仅当 T2 默认版本成功时单独执行，不依赖 3.12 是否通过。用已有 PyMuPDF 的内置 Helvetica 生成固定英文文字 PNG，调用既有 OCR adapter；不下载字体、模型或访问 provider。脚本 `t3.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "${RUN_DIR:?}"
trap 'rc=$?; printf "%s\n" "$rc" > t3.exit-status; exit "$rc"' EXIT
exec > t3.stdout.log 2> t3.stderr.log
runtime=$(cat default-path.txt)
env -u PDFX_LAYOUT_ENDPOINT -u PDFX_TRANSCRIPTION_ENDPOINT -u PDFX_VISION_ENDPOINT \
  "$runtime/bin/python" - <<'PYTHON' > native-ocr.txt
import fitz
from pdfx.ocr_apple import ocr_png
pdf = fitz.open()
page = pdf.new_page(width=600, height=160)
page.insert_text((40, 90), "NATIVE OCR CHECK", fontsize=36, fontname="helv")
png = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
print(ocr_png(png, langs=["en"]))
PYTHON
```

独立入口：`bash "$RUN_DIR/t3.sh"`。检查 `t3.exit-status` 和 stderr；成功退出后人工查看 `native-ocr.txt` 是否包含预定文字 `NATIVE OCR CHECK`，不以当前输出反推预期。查看文件的命令不属于 OCR 执行结果，不能用其退出码覆盖 T3 的退出码。失败保留原输出，不换字体/输入/断言求通过。

## 故障、重试和结果

- 源或缓存临时下载故障：保留错误，只有服务恢复后可按相同 SOURCE、命令重试一次；仍失败停止受影响项。没有新恢复事实不连续重试。
- 求值、构建、导入或 OCR 结果失败：保留首次错误；定位实现问题后仅重新执行受修改影响项，不能用后续环境故障覆盖原结果。
- 当前 Linux 无法执行 Darwin 构建/运行时，T2/T3 记未执行；无成功结果不得宣布完整修复验收通过。不得临时借用户 Mac、系统 Python 或虚构远端 endpoint。
- 不保存用户材料、完整环境快照或额外来源认证。原始日志放 RUN_DIR；仓库仅记录产品/计划版本、命令、简短结果和限制。文档/记录 SHA 变化不自动重跑未受影响项。

## 本轮 Darwin 执行结果（当前）

2026-10-23（北京时间），本地执行工程师按本轮用户授权完成 OCR-TP2 的剩余 T2/T3。产品来源固定为已推送的 `github:ScholarWorkflow/pdf-processing-core/54cb00bd72ad111df17413ff2ebf0056abb0ddc0`；测试步骤来自该提交的 OCR-TP2，脚本逐字提取执行。系统为 `aarch64-darwin`（Darwin 25.6.0），Nix 2.34.8。没有修改产品、锁文件、测试输入、命令或通过条件。

| 项目 | 当前结果 | 实际命令与观察 |
| --- | --- | --- |
| T1 | 通过，复用 | 复用下文首次云端记录及 `review-record.md` 已接受的结果。本轮仅补运行结果和环境授权说明，未触及打包表达式或依赖声明，未重新执行。 |
| T2 默认 Python | 通过 | `bash "$RUN_DIR/t2-default.sh"`：Python 3.14.7 固定环境实际构建成功；Quartz、Vision、CoreFoundation、CoreML 与 `_vision_frameworks()` 均导入成功；输出 `native bindings imported`，退出码 0。 |
| T2 Python 3.12 | 通过 | `bash "$RUN_DIR/t2-312.sh"`：Python 3.12.15 固定环境实际构建成功；同样四个框架与现有适配器入口均导入成功；输出 `native bindings imported`，退出码 0。两个环境分别执行，没有互相替代。 |
| T3 | 通过 | 默认 T2 成功后，单独运行一次 `bash "$RUN_DIR/t3.sh"`；既定 Helvetica 文字 PNG 经现有 `ocr_png` 返回 `NATIVE OCR CHECK`，退出码 0，stderr 为空。按步骤取消三个远端识别端点变量，没有调用远端识别服务或读取真实材料。 |

本轮 `RUN_DIR=/tmp/pdfx-native-ocr.q7o66lcc`，`SOURCE` 为上述固定提交。准备记录为 `preflight.txt`、`nix-version.txt`、`source.txt`、`runtime.nix`。两个 T2 及 T3 各自保存原始 `*.stdout.log`、`*.stderr.log`、`*.exit-status`；固定环境路径在 `default-path.txt`、`python312-path.txt`，文字结果在 `native-ocr.txt`。原始材料及临时脚本留在仓库外，不推送。

- 默认环境：`/nix/store/asz853r2zacbhdwwq5n8fa7xf9jc9jrd-python3-3.14.7-env`；Python 3.12 环境：`/nix/store/bchcsr0bp4wxklpsna2jjkxr142axjn2-python3-3.12.15-env`。两者均由同一公开工厂构建，没有借用系统 Python 或修补消费者。
- 默认 T2 首次构建的 Nix 缓存下载曾报告低速超时；Nix 在同一次命令内自动重试后恢复，最终成功。原警告保留在 `t2-default.stderr.log`；没有重新发起正式测试，没有覆盖失败记录。
- 计划内必测项全部有当前有效通过结果，产品代码无需修正。此处是执行结果，不自行授予测试第三关或合并批准；下一负责人为测试结果审核者。独立实现符合性复核见 `review-record.md` 本轮记录。

## 首次云端执行记录（历史）

2026-10-23 云端 x86_64-linux，Nix 2.35.3；基线与本轮源码见 OCR-EP1。受测完整候选 diff SHA-256：`134c69b0dfd13630876575bfafebdfaad1ead9649cafe3120292fa8d8fe56ae8`；之后仅补本结果区和独立审核记录，不改变产品代码。

- T1 实际执行：`nix-instantiate --parse` 检查 flake.nix、nix/pdfx.nix、nix/pyobjc-vision.nix，均退出 0。以候选 `path:` 源执行上面的三个工厂求值，均退出 0；这是实现自检，不是从已发布提交安装。
- Linux 默认 Python 3.14.7：`qpn0k3qcrmndzxrq786d3mxmhdq6z32w-python3-3.14.7-env.drv`；Darwin 默认 Python 3.14.7：`abrhyqkqr02xlpjn1l5f7rffrpwchbha-python3-3.14.7-env.drv`；Darwin Python 3.12.15：`sa3r72s7hha2wjx49ibv1nmrrzg8k917-python3-3.12.15-env.drv`。
- `nix derivation show` 的结构化依赖中，两个 Darwin 环境均包含相应 ABI 的 PyObjC core/Cocoa/Quartz/Vision/CoreML 11.1；Linux 环境无这些绑定。依赖声明与求值结果一致，不代表实际构建成功。
- `nix flake metadata --offline --no-update-lock-file --json .` 退出 0。结构化 lock 比较确认只有 agent-env-nix.original.rev 改 ref=main，所有 locked 对象、依赖边和其他节点不变。`git diff --check` 无错误。
- 临时日志位置 `/tmp/pdfx-native-ocr.gZnSe0`：三个 `*-drv.txt`/`*-drv.err`、parse 输出、`python-env-derivations.json`、`environment-paths.json`、`metadata.json`、`lock-check.txt`。首次临时查看 JSON 时按旧版顶层格式读取，出现 AttributeError；随后按 Nix 2.35 的 `derivations` 字段读取同一文件，没有重跑或改动产品。这是诊断脚本格式错误，不是产品测试失败。
- T2/T3：未执行。当前仅 Linux 云端，没有使用或授权操作用户 Mac，未做 Darwin 构建、模块实际导入或原生 OCR。没有 provider 调用、真实试卷或用户项目改动。
- 独立审核记录明确接受 T1 证据，但不替代 T2/T3；整体不声明测试通过或可合并。下一执行负责人在获准的 Darwin 环境按当前 recipe 完成这两项；记录/文档 SHA 更新不重跑无影响项。

## OCR-TP2 修订说明

2026-10-23：仅补齐 T1 已实际采用的 derivation 字段检查，并分离 T1、T2 默认、T2 Python 3.12、T3 的 bash 入口及错误日志/退出码保存。必测目标、输入、产品代码均不变，复用上述 T1 结果；本轮未执行测试，T2/T3 仍未执行。独立复核结论见 review-record.md。
