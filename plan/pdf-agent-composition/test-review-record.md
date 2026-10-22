# Independent Gate2 review R3

Date: 2026-10-22. Reviewer: independent review role, not the plan author.
Product: 7ac7bca10206c5481f47ccd89617566cf8a5a065.
Recipe: PDF-COMPOSE-T3, test-plan.md SHA256 e4d53a24358ac43389a55aa19040bb4e546e18f1f9be1def66bb5050097b3040.

## Conclusion

Gate2 PASS for the stated minimal cloud x86_64-linux recipe. Gate1 remains PASS. This is permission to proceed under the existing user authorization, not a test-result PASS, Gate3 verdict, or merge approval. No formal tests were run by this reviewer.

## Review and resolved correction

T2 had one concrete environment wiring defect: its Nix wrapper exposes /nix only inside the existing bin/run mount namespace, while later store execution and reads were on the host, where /nix does not exist. T3 places the recipe inside that existing runner, invokes its nix, sets isolated HOME/CODEX_HOME for apply inside the runner, and uses the available /usr/bin/python3. The defect is resolved without adding software, changing system mounts, or adding test goals.

Reviewed the complete recipe, actual bootstrap runner, fixed shared-layer package/project definitions at 760c1bf60e8ec19d1985660ec5e3276a8ef80b5a, and applicable old issue9 T4 evidence. The agentEnv metadata, manifest source mapping, role TOML fields and required resource paths agree with those interfaces. The fixed producer and locked inputs are explicit. Failure handling stops dependent work; external recovery does not authorize changing credentials, permissions or product files.

## Scope and evidence limits

One clean consumer, one project build/apply and generated-resource inspection cover the changed public factory output. Default/disabled harness metadata is evaluation only, not an extra installation matrix. The identical producer-factory roots do not establish final Contact+Study integration; Study #16 retains that unique later responsibility, and Contact #131 only cites it. Exam #34 retains the real caller batch handoff.

Unchanged wrapper startup evidence is reused only with its recorded Darwin platform limitation. No new Linux wrapper startup success, Darwin execution, OCR/model result, or APM regression execution is claimed. Static compatibility is supplied by code review R2. Actual results and manual generated-role inspection must still be recorded and independently reviewed for Gate3.

## R4: T3 result classification and T4 affected Gate2 review

2026-10-22 14:32 UTC. T3 first-attempt log was read directly. The pinned producer source was fetched, but the original locked shared-layer archive returned HTTP 404 during the first metadata evaluation. No build/apply or resource assertions ran. Target1 is BLOCKED/INDETERMINATE, not product FAIL or execution PASS; Gate3 remains incomplete. Targets2/3 retain the documented reuse/static-review boundaries.

T4 affected Gate2: PASS. Reviewed test-plan.md SHA256 13c87a6bc69d60b8f9b2779f82aae2bff790d9c054b6e27cd44f9ae02eb3c8e3. Only acquisition preparation and same-RUN recovery change. Independently computed the reconstructed source directory NAR hash using the existing Nix diagnostic: sha256-x1of5F3pJ5pc6V3ybv1mVfkLZ6h5rXrla3Ynlq7x2Mw=, exactly the original locked content hash. This was a read-only content diagnostic, not a formal product test. Source contains 24 files; the normal connector supplied the fixed tree without an access denial.

The recipe verifies that exact hash, adds the same content under the existing source store name, then resumes the identical fixed SRC/inputs/assertions once in /tmp/pdf-compose-iWkpcy6c. No dependency override, lock change, credential setting, tool installation or system-mount change is authorized by this review. If Nix still cannot consume this content, stop with the actual error; do not replace the declared input or continue retrying. Preserve both attempt logs. The archive endpoint itself is not claimed to have recovered, and later success would not establish an uncached anonymous fetch. All earlier scope/platform and final consumer integration limits remain.

## R5: Independent Gate3 review of T4 results

2026-10-22 14:35 UTC. Product 7ac7bca10206c5481f47ccd89617566cf8a5a065; T4 recipe published at c389e07b. Gate3: PASS for this producer change's bounded scope, with the following explicit reuse and downstream limits. This is not a final merge review.

Read the complete /tmp/pdf-compose-T4-recipe.log, the executed recipe, saved harness/output/cache-path records, actual project skill documents, and the generated environment manifest. Execution owner reports exit 0. The complete log reaches the final role print under set -euo pipefail, after build/apply, resource diff and field assertions; no failed step is hidden in the observed sequence. Reviewer did not rerun formal tests.

Target1 PASS on x86_64-linux: defaults are ["codex"] and disabled options []; the same factory instantiated twice builds one public package identity in the actual manifest. Output /nix/store/4k7d310nfd80zp7dw08k3vv27li8sxzx-agent-environment applied successfully in the sole /tmp/pdf-compose-iWkpcy6c/project consumer. Two skills and three role TOMLs are present, resource differences were empty, and runner/benchmark adjacency remains. Full printed role texts and installed SKILL.md files preserve the fixed public wrappers, caller-owned absolute batch command, check-first/cache behavior, existing role fields and conditional JSON contract. pdf-to-text's normal summary remains permitted.

Target2: unchanged wrapper startup evidence is reused from old T4 on its recorded Darwin platform, not a new Linux startup result. The changed real caller batch handoff belongs to Exam #34 and is not completed or claimed by this producer result. Target3 PASS by R2 static compatibility review and the actual generated documents; no APM/algorithm/CLI business change or new runtime regression execution is claimed.

The first T3 attempt remains recorded as archive HTTP404 and target1 indeterminate. The single T4 recovery succeeded using the exact originally locked content supplied to the source cache; its stored source is /nix/store/8wcag1p0iwsc0f5kk9smz05wd11a5d2y-source. This does not demonstrate recovery of the GitHub archive endpoint, anonymous cold fetching, or Darwin execution of this revision.

The producer's equal-input duplicate roots are not Contact+Study's final combined dependency graph. Final consumer validation stays with Study #16; Contact #131 reuses that record. These facts allow Exam's fixed producer integration to proceed, but do not mark Exam or either consumer workflow ready. No additional test requirement is imposed by this review.
