# Nix 安装

本仓库的 Nix flake 提供普通 `pdfx` 包、开发环境和可选的 Codex 资源包。输入版本由 `flake.lock` 固定；消费者应引用已审核的仓库提交，不要使用浮动分支。

当前发行版本为 `0.1.0`。锁文件固定 `agent-env-nix` 到 `760c1bf60e8ec19d1985660ec5e3276a8ef80b5a`，并通过它固定 Nixpkgs 到 `6510408d147d8c5e6d76043e899e9251756f6a75`。包源码仍由消费者选择并固定具体仓库提交。

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

基础包不包含视觉服务、模型或权重。命令存在不代表已配置 OCR 提供者；提供者的配置与凭据由调用环境管理。

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

## 视觉增强

基础 `pdfx` 包不要求 `vision-tools/glance`。只有在 `glance` 及其 Python 依赖有可固定的 Nix 来源并通过对应安装检查后，仓库才会发布增强输出。此输出未交付时，不应把基础包描述成具备完整视觉 OCR 能力。
