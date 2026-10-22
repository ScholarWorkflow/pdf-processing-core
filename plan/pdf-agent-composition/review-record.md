# PDF composition independent review R1

Date: 2026-10-22. Independent reviewer, not the plan author. Read-only review; no tests, installation, remote changes or merge performed.

## Reviewed objects and authority

- PDF-COMPOSE-P1 / PDF-COMPOSE-T1 against pdf-processing-core main@3444dca0e0b84f46d1839ab5c707afa78e23d3a7.
- EXAM-PDF-COMPOSE-P1 / EXAM-PDF-COMPOSE-T1 against exam-question-pipeline master@d57c2c47ac5d3a4dc6579bbcfb2508352e696acd.
- CONTACT-PDF-H1 and proposed PR body against professor-contact 582f731caf438851bcec23662047daf1ed2098b9.
- STUDY-PDF-H1 and proposed PR body against study-tools master cb325d6576003251a17cfc3d2dacc839f73dcf97; separate Study #14 head df70f05a22158d577c134432a35220bdc52afb30 is not changed by this work.
- Requirement: user's 2026-10-22 generic package declarations and recursive composition requirement; explicit request to create four PRs and begin the first two; execution and test plans in separate files.
- Rules read: Project Consensus as of 2026-10-19, Plan Reviewer Rule and Test Engineer Rule as of 2026-10-09, plus personal execution/test/fast-merge review skills.

## Source-grounded decisions

Reviewed the fixed-revision source excerpts cited in the plans: public nix/pdfx.nix, public pdf-to-text role, Exam pdf-adapted.nix, agent-from-apm.nix, and kakomon-folder-pipeline.nix.

- Public producer already owns pdfx, two fixed wrappers, complete skill resources and three native roles. Exam currently overrides files and exports under the same public package ID. Moving generic fixed-entry projection into the producer and removing this override addresses the actual conflict without weakening collision protection.
- Public pdf-to-text accepts a consumer-owned batch command. Its cached result path returns the check result; normal conversion permits a concise summary and obtains detailed business JSON from the command. Conditional JSON wording must not impose a new unconditional schema.
- Exam retains its batch/gate/post/split ownership, Python runtime and factory return interface. The former skill-local refresh command must be replaced at its actual caller points when the duplicate skill wrappers are removed.
- Runtime closure does not imply PATH injection. Store-fixed wrappers and caller-supplied absolute batch path are therefore appropriate.
- The new producer revision is an implementation sequencing prerequisite, not a speculative dependency claimed as delivered. Consumers must not invent pins or compensate with another override.

## Execution-plan review

For both PDF-COMPOSE-P1 and EXAM-PDF-COMPOSE-P1:

P1 Requirement & Reality Grounding: PASS.
P2 Solution Validity & Ownership: PASS.
P3 Impact, Safety & Counterexample: PASS.
P4 Executability: PASS.
Gate review completeness: COMPLETE.
Plan conclusion: APPROVED.

Critical assumptions: none unresolved at design level. Actual build/runtime availability and future product revisions remain execution prerequisites, not PASS claims.
Unresolved critical impacts: none found in this bounded design.
Approved behavior changes: public Nix fixed-entry projection and consistent dependency composition only, as authorized by the user. No PDF algorithm, business schema, APM path or installer-protection change.
Reused prior Plan Gate PASS: none; this is an independent initial review.
Required modifications: none.

The alternative of changing the package ID or weakening collision handling would not satisfy shared export ownership and is correctly rejected. Same producer/input/options is the intended consistency boundary; arbitrary incompatible historical pins are not promised. The proposed public factory's python field exposes an already returned runtime required by consumers, not a reinstated requirement for a separate public python.withPackages package attribute.

## Test-plan review

For PDF-COMPOSE-T1 and EXAM-PDF-COMPOSE-T1:

Gate1 mandatory scope and necessity: PASS.
Review completeness: COMPLETE for the full submitted scope, preparation constraints, references and final dual-root handoff.
Gate2 executable recipe: PENDING / NOT YET SUBMITTED AS EXECUTABLE.
Gate3 execution results: NOT EXECUTED.

The producer checks its public resources and changed fixed entries; Exam checks the actual public-role-to-consumer-batch handoff and preserves its own unaffected business behavior. Static compatibility review suffices for unchanged APM/business algorithms. These are distinct necessary targets, not repeated full-workflow regression.

Final Contact+Study combination is owned once by the Study consumer PR executor, with the result in its consumer-handoff.md; Contact only cites it. Scope is combination success, one public PDF instance and actual resource copy. This does not reopen Study #14 business testing or block standalone producer output on unfinished consumers.

Before formal execution, the existing plans themselves require actual revisions, inputs, platform, commands, configuration, expected fields and output locations. No complete Gate2 recipe or test PASS is asserted here. Do not turn this remaining recipe work into extra test targets or a new judge/framework. Existing valid evidence is reused by semantic impact, not invalidated solely by document SHA.

## Consumer draft review

CONTACT-PDF-H1 / STUDY-PDF-H1 and their proposed PR bodies: suitable to publish as waiting Drafts. No blocking scope or status defect found. They are handoff records for later bounded consumption, not substitute implementation plans or merge approval. Keeping them lightweight is appropriate while upstream output is absent.

Non-blocking accuracy cleanup: the reference to following Exam's combination steps should say the steps are to be completed there before execution, since currently only their scope/owner is frozen. Once real PRs and branch URLs exist, replace placeholder cross-links with those actual URLs. These pointer/wording changes do not alter the approved design or Gate1 scope.

## Implementation caution, not a new gate

When replacing Exam refreshRunner with a store path, retain the old source text in substitution.from and the new path in substitution.to. Changing a shared interpolation on both sides would make the existing exact-match adaptation fail. This is covered by the existing caller-entry change point, not an additional test obligation.

## Candidate fingerprints

Fingerprints identify this review snapshot only and are not a product provenance requirement.
- PDF execution: d00cc7c62250cb7476993e4e2aa4d6b69771d670e9b3ba7d422ccc499a59ce24
- PDF test: 6dac4db0bc33cf7423e68e71fa827f1415f211620e1fd97a83b508356e31e264
- Exam execution: d596289d2a6d1e96b0c60b57990cc404e4d267373956b67aa3e353ca5bdcbed3
- Exam test: df3ba58b1cec992c40c279db171ed2b446ef0bf06314f3a9638cd9d2cfa507f9
- Contact handoff: e1ec36a56407107cae98003d2dde44689a274eccb1ba9e6421e82a011280d31f
- Study handoff: a41cbb51d97cfdad47807e5c1550217f0da8ba9f7ad6a21e1f16dfb820c727bf
- Contact PR body: 31e0b27573364962279ece834adbeb3bab20ef34cd08b78a388317c43e64d3af
- Study PR body: 9aebd533ab25cf91879de66698ed458cc5a8e6a40fa220bbd51458fd68aef69a

APPROVED applies to implementation-plan design only. It is not implementation correctness, test PASS, Merge READY or permission to merge. No core-product implementation has been reviewed by this record.
