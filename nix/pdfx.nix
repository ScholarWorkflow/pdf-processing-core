{ pkgs, mkAgentPackage, enableHarnesses ? [ "codex" ] }:

let
  lib = pkgs.lib;
  repository = ../.;
  releaseProject = builtins.fromTOML (builtins.readFile ../packaging/scholar-workflow-pdfx/pyproject.toml);
  version = releaseProject.project.version;

  python = pkgs.python3;
  pythonPackages = python.pkgs;
  pymupdf = pythonPackages.pymupdf;
  pillow = pythonPackages.pillow;

  stagedSource = pkgs.runCommand "scholar-workflow-pdfx-${version}-staged-source" {
    nativeBuildInputs = [ python ];
  } ''
    ${python}/bin/python ${repository}/scripts/stage_release_package.py \
      "$TMPDIR/staged-release" --repository-root ${repository}
    mkdir -p "$out"
    cp -R "$TMPDIR/staged-release/." "$out/"
  '';

  pdfxDistribution = assert lib.versionAtLeast pymupdf.version "1.24";
    pythonPackages.buildPythonPackage {
      pname = "scholar-workflow-pdfx";
      inherit version;
      pyproject = true;
      src = stagedSource;
      build-system = [ pythonPackages.hatchling ];
      dependencies = [ pymupdf ];
      doCheck = false;
    };

  pythonEnvironment = python.withPackages (_: [ pymupdf pillow pdfxDistribution ]);

  pdfx = pkgs.runCommand "pdf-processing-core-${version}" {
    nativeBuildInputs = [ pkgs.makeWrapper ];
  } ''
    mkdir -p "$out/bin" "$out/share/pdf-processing-core/apm"
    ln -s ${pythonEnvironment}/bin/pdfx "$out/bin/pdfx"
    cp -R ${repository}/.apm "$out/share/pdf-processing-core/apm/"
    makeWrapper ${pythonEnvironment}/bin/python \
      "$out/bin/pdfx-formula-repair-runner" \
      --add-flags "$out/share/pdf-processing-core/apm/.apm/skills/formula-repair/formula_repair_runner.py"
    makeWrapper ${pythonEnvironment}/bin/python \
      "$out/bin/pdfx-ocr-refresh-jobs" \
      --add-flags "$out/share/pdf-processing-core/apm/.apm/skills/llm-ocr-refresh/ocr_refresh_jobs.py"
  '';

  replaceEntrypoints = text:
    builtins.replaceStrings
      [
        "uvx --from 'scholar-workflow-pdfx>=0.1.0,<0.2' pdfx"
        "uv run --script \"<resolved formula-repair skill dir>/formula_repair_runner.py\""
        "uv run --script \"<resolved llm-ocr-refresh skill dir>/ocr_refresh_jobs.py\""
        "Resolve `formula_repair_runner.py` from the loaded/deployed `formula-repair` skill directory and invoke that skill-local PEP 723 script:"
        "Use the repository-owned runner as a skill-local resource. Resolve the loaded/deployed `formula-repair` skill directory, then invoke the resolved script with its PEP 723 metadata:"
      ]
      [
        "${pdfx}/bin/pdfx"
        "${pdfx}/bin/pdfx-formula-repair-runner"
        "${pdfx}/bin/pdfx-ocr-refresh-jobs"
        "Use the packaged formula-repair runner wrapper. It resolves the read-only script resource from this Nix package:"
        "Use the packaged formula-repair runner wrapper, which runs the packaged script with this package's Python environment:"
      ]
      text;

  formulaRepairSkillText = pkgs.writeText "pdfx-nix-formula-repair-SKILL.md"
    (replaceEntrypoints (builtins.readFile ../.apm/skills/formula-repair/SKILL.md));
  ocrRefreshSkillText = pkgs.writeText "pdfx-nix-llm-ocr-refresh-SKILL.md"
    (replaceEntrypoints (builtins.readFile ../.apm/skills/llm-ocr-refresh/SKILL.md));

  adaptedSkills = pkgs.runCommand "pdfx-agent-skills-${version}" { } ''
    mkdir -p "$out/formula-repair" "$out/llm-ocr-refresh"
    cp -R ${repository}/.apm/skills/formula-repair/. "$out/formula-repair/"
    cp -R ${repository}/.apm/skills/llm-ocr-refresh/. "$out/llm-ocr-refresh/"
    chmod u+w "$out/formula-repair/SKILL.md" "$out/llm-ocr-refresh/SKILL.md"
    cp ${formulaRepairSkillText} "$out/formula-repair/SKILL.md"
    cp ${ocrRefreshSkillText} "$out/llm-ocr-refresh/SKILL.md"
  '';

  replaceAgentCommands = path: fileName:
    let
      parts = lib.splitString "\n---\n" (builtins.readFile path);
    in
    if builtins.length parts == 2 then
      replaceEntrypoints (builtins.elemAt parts 1)
    else
      throw "${fileName} must have one APM front-matter separator";

  # Preserve each role's existing response schema; not every successful role
  # response is JSON (pdf-to-text also allows a concise conversion summary).
  codexResponseProtocol = ''

    ## Codex 返回约定

    - 调用工具时不发送进度消息；任务结束时只发送一条最终消息。
    - 本角色约定要求 JSON 时，最终消息正文只包含一个符合本角色既有字段约定的 JSON 对象，不添加 Markdown 围栏或说明文字。
    - 澄清请求、错误和受阻状态使用本角色既有字段表示；业务文件仍按本角色原有约定写入。
  '';

  pdfToTextInstructions = (builtins.replaceStrings
    [
      "uv run <consumer-pdf-to-text-command>"
      "The conversion command is consumer-owned and must use the installable `pdfx` package."
      "Missing dependencies from `uv run` are reported as-is; do not perform manual installation."
      "Use shell only for `pwd`, `ls`, `uv`, `file`, `wc`, and `mkdir`."
    ]
    [
      "\"<consumer-pdf-to-text-command>\""
      "The conversion command is consumer-owned and must use the installable `pdfx` package. In this Nix projection the caller supplies an absolute path to its fixed batch wrapper; execute that wrapper directly, without uv or dependency downloads. The wrapper uses this package's Python library or `${pdfx}/bin/pdfx`; runtimePackages does not add executables to PATH."
      "Missing runtime dependencies are reported as-is; do not perform manual installation."
      "Use shell only for the caller-specified fixed `<consumer-pdf-to-text-command>` wrapper, `pwd`, `ls`, `file`, `wc`, and `mkdir`; execute that wrapper directly without uv."
    ]
    (replaceAgentCommands ../.apm/agents/pdf-to-text.agent.md "pdf-to-text.agent.md")) + codexResponseProtocol;
  formulaRepairInstructions = (replaceAgentCommands ../.apm/agents/formula-repair.agent.md "formula-repair.agent.md") + codexResponseProtocol;
  refreshWorkerInstructions = (replaceAgentCommands ../.apm/agents/llm-ocr-refresh-unit-worker.agent.md "llm-ocr-refresh-unit-worker.agent.md") + codexResponseProtocol;

  pdfxAgent = mkAgentPackage {
    inherit pkgs;
    id = "scholar-workflow-pdfx-agent";
    inherit version;
    files = {
      "skills/formula-repair" = "${adaptedSkills}/formula-repair";
      "skills/llm-ocr-refresh" = "${adaptedSkills}/llm-ocr-refresh";
    };
    exports = {
      skills = {
        formula-repair = "skills/formula-repair";
        llm-ocr-refresh = "skills/llm-ocr-refresh";
      };
      agents = {
        pdf-to-text = {
          description = "Batch-converts every PDF in a directory into one Markdown source document each, stored under an extraction subfolder. Uses the shared pdfx kernel with automatic per-page quality routing or an optional fast compatibility strategy.";
          developer_instructions = pdfToTextInstructions;
        };
        formula-repair = {
          description = "Formula audit and repair orchestrator. Runs a recoverable audit -> OCR repair -> verification loop; workers write only transient unit results and the runtime owns state and atomic source merging.";
          developer_instructions = formulaRepairInstructions;
        };
        llm-ocr-refresh-unit-worker = {
          description = "Unit repair worker. Reads one unit-job JSON, reuses the refresh skill and configured visual-OCR capability, and writes only transient unit patches plus RESULT.json. Never writes source documents, audit sidecars, manifests, or downstream indexes.";
          developer_instructions = refreshWorkerInstructions;
        };
      };
    };
    runtimePackages = [ pdfx ];
    inherit enableHarnesses;
  };
in
{
  inherit pdfx pdfxAgent;
  python = pythonEnvironment;
}

