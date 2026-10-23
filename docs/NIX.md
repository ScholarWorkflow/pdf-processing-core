# Nix 安装

本仓库的 Nix flake 提供普通 `pdfx` 包、开发环境和可选的 Codex 资源包。输入来源可以跟踪仓库主分支，实际使用的版本仍由 `flake.lock` 固定；运行时不会自动升级，更新版本需要更新锁文件。

当前发行版本为 `0.1.0`。`agent-env-nix` 的输入来源是 `main`；当前锁文件仍固定其到 `760c1bf60e8ec19d1985660ec5e3276a8ef80b5a`，并通过它固定 Nixpkgs 到 `6510408d147d8c5e6d76043e899e9251756f6a75`。本次来源调整不升级这些锁定版本。消费者可声明本仓 `main` 来源，由自己的锁文件选择实际提交；以下固定提交示例同样有效。

## 普通库和命令

把 `<commit>` 替换为要固定的 `pdf-processing-core` 提交：

```sh
SOURCE='github:ScholarWorkflow/pdf-processing-core/<commit>'
nix build --no-link --print-out-paths "$SOURCE#pdfx"
nix profile install "$SOURCE#pdfx"

pdfx --help
pdfx quality --help
pdfx quality ./paper.pdf --json
pdfx-formula-repair-runner --help
pdfx-ocr-refresh-jobs --help
```

`pdfx` 包含 `pdfx` 命令、公式修复和 OCR 刷新脚本包装器，以及原 APM 资源树。运行依赖由 Nix 闭包提供，不会在运行时调用 `uv` 下载 Python 包。`nix develop "$SOURCE"` 使用与包构建相同的 Python 环境。

Darwin 的固定 Python 环境包含既有 Apple Vision 文字 OCR 适配器需要的 PyObjC 绑定（Quartz、Vision、CoreFoundation 及依赖 CoreML），公共工厂、CLI 和脚本包装器共用这份闭包，不需给系统 Python 手动安装模块。原生文字 OCR 不要求 `PDFX_LAYOUT_ENDPOINT`、`PDFX_TRANSCRIPTION_ENDPOINT` 或 `PDFX_VISION_ENDPOINT`；Linux 不引入这些 macOS 绑定。

基础包不包含远端视觉服务、外部模型或权重。原生文字 OCR 不等于完整的公式/复杂版面视觉增强；这些可选提供者的配置与凭据仍由调用环境管理，原有 provider 优先与本机 fallback 顺序不变。未配置远端服务不再是 Darwin 本机文字识别的前置缺口，但实际平台构建与识别结果应以对应验证记录为准。

Nix store 中的程序和资源是只读文件。脚本应从调用方的工作目录运行，状态文件、缓存和结果由脚本写入调用方指定的位置。

## Codex 资源包

`packages.<system>.pdfx-agent` 使用固定版本的 `agent-env-nix` 导出两个技能和三个角色。消费项目可以选择把资源投射到项目配置：

```nix
let
  shared = builtins.getFlake
    "github:RekiDunois/agent-env-nix/760c1bf60e8ec19d1985660ec5e3276a8ef80b5a";
  producer = builtins.getFlake
    "github:ScholarWorkflow/pdf-processing-core/<commit>";
  system = "aarch64-darwin";
  pkgs = shared.inputs.nixpkgs.legacyPackages.${system};
in
shared.lib.mkProject {
  inherit pkgs;
  roots = [ producer.packages.${system}.pdfx-agent ];
}
```

构建结果中的 `bin/agent-env` 提供项目级 `apply` 和 `remove` 命令。资源包默认只投射 Codex 资源；它不会自动修改用户全局配置。Nix 专用技能和角色调用固定包中的绝对路径包装器。现有 APM、OpenCode 和 PEP 723/uv 入口仍按各自原有方式使用。

项目内生成的技能目录由固定资源包提供；需要修改技能时应回到本仓源码更新后重新生成，不应直接编辑投射后的副本。

该固定版本的 `agent-env-nix` 仓库为私有仓库。Nix 按普通方式获取锁定输入；若获取返回权限错误，保留并处理 Nix 报出的实际错误。pdf-processing-core 不另做权限预检查，也不会自动更改仓库可见性。

## 同一依赖图中的公共 PDF 实例

`lib.mkPdfPackages { pkgs; enableHarnesses ? [ "codex" ]; }` 返回现有的 `pdfx`、`pdfxAgent` 和 `python`。`python` 是固定的库运行环境，供调用方自己的 batch 包装器使用，不是额外的 flake 包输出。普通 `packages.<system>.pdfx-agent` 也使用此工厂。

在同一个项目中，消费者使用相同 producer 修订、`pkgs`、共享层和 harness 选项，让直接与传递依赖引用同一个公共实例。消费者只声明真实 `agentDependencies`，不要以相同 id 重写公共 PDF agent 的 `files` 或 `exports`，也不要依靠安装顺序覆盖它。不同版本/配置的同名冲突仍由共享层如实拒绝。

```nix
let
  pdf = producer.lib.mkPdfPackages { inherit pkgs; };
in
shared.lib.mkProject {
  inherit pkgs;
  roots = [ pdf.pdfxAgent ];
}
```

`enableHarnesses = []` 关闭该 agent 的 Codex 投射，默认仍为 `[ "codex" ]`。Nix 的 `pdf-to-text` 直接执行调用方提供的绝对路径 batch wrapper，保留先 `--check`、缓存与原有结果字段；它不实现调用方的业务 batch。公式修复和 OCR 刷新使用 `pdfx` 包中既有的 store 绝对路径包装器。运行闭包不自动注入 `PATH` 或 `PYTHONPATH`。

只有原角色要求 JSON 的分支使用单个 JSON 最终返回；普通转换的简洁摘要及命令 JSON 明细约定保持不变。

## 视觉增强

基础 `pdfx` 包不要求 `vision-tools/glance`。只有在 `glance` 及其 Python 依赖有可固定的 Nix 来源并通过对应安装检查后，仓库才会发布增强输出。此输出未交付时，不应把基础包描述成具备完整视觉 OCR 能力。

