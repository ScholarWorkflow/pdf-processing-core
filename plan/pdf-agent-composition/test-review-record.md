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
