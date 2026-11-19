# Acceptance Criteria: ai-rag-infra (Shared AWS Infrastructure)

Every project could be checked in under 15 minutes. Tick an item only when the evidence exists in the repo.

| # | Requirement | Evidence | Done |
|---|-------------|----------|------|
| 1 | One CDK (Python) app with `AiRagFoundation`, `AiRagSearch`, `AiRagAuth`, `AiRagGuardrails` | `app.py`, `stacks/` | [ ] |
| 2 | Shared names published to SSM under `/ai-rag/` | `aws ssm get-parameters-by-path --path /ai-rag` output in README | [ ] |
| 3 | Budget with alerts and a confirmed SNS email | `stacks/foundation.py`, screenshot in `docs/cost.md` | [ ] |
| 4 | GitHub OIDC with separate CI and deploy roles per repo | `stacks/foundation.py`, `tests/test_stacks.py` | [ ] |
| 5 | OpenSearch domain reachable only with SigV4 | Unsigned `curl` returns `403` (README) | [ ] |
| 6 | Audit bucket with Object Lock | `stacks/foundation.py` | [ ] |
| 7 | Cognito pool with role groups and `custom:tenant_id`; test users seeded | `stacks/auth.py`, `scripts/seed_users.py` | [ ] |
| 8 | Bedrock Guardrail with a published version | `stacks/guardrails.py` | [ ] |
| 9 | `cdk-nag` passes with documented suppressions | `ci.yml` run | [ ] |
| 10 | Deploy from GitHub Actions without stored AWS keys | `.github/workflows/deploy.yml`, green run | [ ] |
| 11 | Cost and teardown documented | `docs/cost.md` | [ ] |

## Quality bar

- [ ] No bucket allows public access; every bucket enforces TLS
- [ ] No IAM policy uses `*` actions on `*` resources, except documented CDK bootstrap trust
- [ ] CI roles cannot deploy; deploy roles only work from the `production` environment
- [ ] Bedrock model invocation logging is off, and the reason is written down
- [ ] `cdk destroy AiRagSearch` followed by `cdk deploy AiRagSearch` and one re-ingest restores Project 01 without new embedding calls (embedding cache in S3)
