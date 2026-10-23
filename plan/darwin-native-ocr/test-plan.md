# Darwin 原生 OCR Nix 修复测试计划

- Revision: OCR-TP1；对应 [OCR-EP1](execution-plan.md)，基线 `2be1bf850f29b7ccf7bd1b066ae7292aac33fc87`。
- Proposed review status: pending。第一关必测清单、第二关步骤的独立结论见 [review-record.md](review-record.md)；第三关尚未整体完成。作者不声明正式 PASS。
- 依据：[Test Engineer Rule](https://app.notion.com/p/3d80101a85f380d8a8f5ce528141e253) 与 [Project Consensus](https://app.notion.com/p/3d60101a85f3815a8c43f271f2b4d91d)。不重复共享安装器、完整 Study/Exam 或历史业务回归。

## 唯一必测清单

| 项目 | 目标与输入 | 预期结果及检查方式 |
| --- | --- | --- |
| T1 | 最终 Nix 候选；Linux 默认 Python 与 Darwin 默认/Python 3.12 公共工厂 | 表达式可求值；Darwin 固定 Python 的真实依赖包含同 ABI 的 Quartz/Cocoa/CoreML/Vision，Linux 不引入这些绑定；检查直接依赖表达式及工厂 python 的 drvPath。agent-env 只改来源声明，locked 节点与 follows 保持不变。 |
| T2 | Darwin 上构建公共工厂的默认 Python 与 Python 3.12 环境 | 两环境实际构建成功，均能导入 Quartz、Vision、CoreFoundation、CoreML；调用现有 adapter 的框架加载入口成功，不借系统 Python。若两入口是同一 derivation，复用一次。 |
| T3 | T2 中默认 Python 的现有 pdfx.ocr_apple；一张自造英文文字 PNG，不配置远端服务 | 既有原生 adapter 实际返回图片中的 `NATIVE OCR CHECK`；只跑一次最小 OCR，不跑真实材料或复杂公式。 |

T1 是打包表达式检查，不代表 T2/T3 通过。仅 T2/T3 在可用、获准的 Darwin 执行环境运行；当前云端为 Linux，不操作用户 Mac。没有合适 Darwin 时明确未执行，仍可提交实现与 draft PR，不宣称可合并。

## 准备与执行

以下 recipe 使用 bash 执行。所有命令在目标仓库外的独立运行目录执行，保留输出。使用已安装的 Nix（支持 flakes），不改变全局配置。下面 `SOURCE` 必须是这次已推送的实际产品 commit；记录实际值、Nix 版本和系统，不用未发布 SHA 或把浮动 main 当测试版本。

```sh
RUN_DIR=$(mktemp -d /tmp/pdfx-native-ocr.XXXXXX)
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
nix eval --raw --file runtime.nix --argstr source "$SOURCE" --argstr system x86_64-linux drvPath
nix eval --raw --file runtime.nix --argstr source "$SOURCE" --argstr system aarch64-darwin drvPath
nix eval --raw --file runtime.nix --argstr source "$SOURCE" --argstr system aarch64-darwin --arg python312 true drvPath
```

T2：仅在获准的 aarch64-darwin 环境执行，复用上述 RUN_DIR 和 SOURCE；构建默认与 3.12，随后对返回的两个环境各导入一次。未获准时不转到用户电脑。

```sh
nix build --no-link --print-out-paths --file runtime.nix --argstr source "$SOURCE" --argstr system aarch64-darwin > default-path.txt
nix build --no-link --print-out-paths --file runtime.nix --argstr source "$SOURCE" --argstr system aarch64-darwin --arg python312 true > python312-path.txt
while IFS= read -r runtime; do
  "$runtime/bin/python" -c 'import Quartz, Vision, CoreFoundation, CoreML; from pdfx.ocr_apple import _vision_frameworks; _vision_frameworks(); print("native bindings imported")'
done < <(cat default-path.txt python312-path.txt | sort -u)
```

T3：在 T2 默认环境里，用已有 PyMuPDF 的内置 Helvetica 生成一页固定英文文字并渲染为 PNG，调用既有 OCR adapter；不下载字体、模型或访问任何 provider。清除远端 endpoint 以明确该最小检查不依赖它们。

```sh
runtime=$(cat default-path.txt)
env -u PDFX_LAYOUT_ENDPOINT -u PDFX_TRANSCRIPTION_ENDPOINT -u PDFX_VISION_ENDPOINT \
  "$runtime/bin/python" - <<'PY' > native-ocr.txt
import fitz
from pdfx.ocr_apple import ocr_png
pdf = fitz.open()
page = pdf.new_page(width=600, height=160)
page.insert_text((40, 90), "NATIVE OCR CHECK", fontsize=36, fontname="helv")
png = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
print(ocr_png(png, langs=["en"]))
PY
cat native-ocr.txt
```

人工检查输出包含预定文字，不以当前输出反推预期。失败保留原输出，不换字体/输入/断言求通过。

## 故障、重试和结果

- 源或缓存临时下载故障：保留错误，只有服务恢复后可按相同 SOURCE、命令重试一次；仍失败停止受影响项。没有新恢复事实不连续重试。
- 求值、构建、导入或 OCR 结果失败：保留首次错误；定位实现问题后仅重新执行受修改影响项，不能用后续环境故障覆盖原结果。
- 当前 Linux 无法执行 Darwin 构建/运行时，T2/T3 记未执行；无成功结果不得宣布完整修复验收通过。不得临时借用户 Mac、系统 Python 或虚构远端 endpoint。
- 不保存用户材料、完整环境快照或额外来源认证。原始日志放 RUN_DIR；仓库仅记录产品/计划版本、命令、简短结果和限制。文档/记录 SHA 变化不自动重跑未受影响项。

## 当前记录

2026-10-23 云端 x86_64-linux，Nix 2.35.3；基线与本轮源码见 OCR-EP1。受测完整候选 diff SHA-256：`134c69b0dfd13630876575bfafebdfaad1ead9649cafe3120292fa8d8fe56ae8`；之后仅补本结果区和独立审核记录，不改变产品代码。

- T1 实际执行：`nix-instantiate --parse` 检查 flake.nix、nix/pdfx.nix、nix/pyobjc-vision.nix，均退出 0。以候选 `path:` 源执行上面的三个工厂求值，均退出 0；这是实现自检，不是从已发布提交安装。
- Linux 默认 Python 3.14.7：`qpn0k3qcrmndzxrq786d3mxmhdq6z32w-python3-3.14.7-env.drv`；Darwin 默认 Python 3.14.7：`abrhyqkqr02xlpjn1l5f7rffrpwchbha-python3-3.14.7-env.drv`；Darwin Python 3.12.15：`sa3r72s7hha2wjx49ibv1nmrrzg8k917-python3-3.12.15-env.drv`。
- `nix derivation show` 的结构化依赖中，两个 Darwin 环境均包含相应 ABI 的 PyObjC core/Cocoa/Quartz/Vision/CoreML 11.1；Linux 环境无这些绑定。依赖声明与求值结果一致，不代表实际构建成功。
- `nix flake metadata --offline --no-update-lock-file --json .` 退出 0。结构化 lock 比较确认只有 agent-env-nix.original.rev 改 ref=main，所有 locked 对象、依赖边和其他节点不变。`git diff --check` 无错误。
- 临时日志位置 `/tmp/pdfx-native-ocr.gZnSe0`：三个 `*-drv.txt`/`*-drv.err`、parse 输出、`python-env-derivations.json`、`environment-paths.json`、`metadata.json`、`lock-check.txt`。首次临时查看 JSON 时按旧版顶层格式读取，出现 AttributeError；随后按 Nix 2.35 的 `derivations` 字段读取同一文件，没有重跑或改动产品。这是诊断脚本格式错误，不是产品测试失败。
- T2/T3：未执行。当前仅 Linux 云端，没有使用或授权操作用户 Mac，未做 Darwin 构建、模块实际导入或原生 OCR。没有 provider 调用、真实试卷或用户项目改动。
- 独立审核记录明确接受 T1 证据，但不替代 T2/T3；整体不声明测试通过或可合并。下一执行负责人在获准的 Darwin 环境按原 recipe 完成这两项；记录/文档 SHA 更新不重跑无影响项。
