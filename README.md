# ai-rag-infra: Shared AWS Infrastructure

> **At a glance**
> - **Problem:** The RAG, agent, copilot, and eval repos share one search domain, one user pool, one guardrail, and the same buckets; created per repo, they would drift and cost several times as much.
> - **Proves:** AWS infrastructure as code (CDK, Python), keyless CI/CD with GitHub OIDC, least-privilege IAM per repo, cost guardrails, one-command teardown.
> - **Stack:** AWS CDK (Python), CloudFormation, GitHub Actions, IAM, S3, OpenSearch Service, Cognito, Bedrock Guardrails, SSM Parameter Store, SNS, AWS Budgets
> - **Connects to:** read by `evalkit`, `enterprise-rag-qa`, `ops-desk-agents`, and `workplace-copilot` through SSM parameters under `/ai-rag/`
> - **ai-rag order:** release right after `ai-rag-data`, before every project repo

One CDK app that owns every AWS resource used by more than one project. Project repos never hard-code names or ARNs: they read them from SSM Parameter Store. Each project repo deploys only its own resources (`ops-desk-agents`: AgentCore runtime; `workplace-copilot`: Lambda, CloudFront, DynamoDB) with a small CDK app of its own.

Region: **us-east-1**. Model provider: **Amazon Bedrock only**. No VPC, NAT Gateway, load balancer, database server, or cache is created.

The pass/fail checklist lives in [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md).

---

## Stacks

| Stack | Resources | Why it is shared |
|-------|-----------|------------------|
| `AiRagFoundation` | S3 buckets `data`, `audit` (Object Lock), `traces`, `evals`; SNS topic `ai-rag-alerts` with an email subscription; AWS Budgets monthly budget with alerts at 50/80/100%; GitHub OIDC provider and one CI role + one deploy role per repo | Every repo reads data, writes logs, and deploys from GitHub |
| `AiRagSearch` | OpenSearch Service domain (OpenSearch 2.19 or later), 1 node `t3.medium.search`, 20 GB gp3, public endpoint, HTTPS only, encryption at rest and node-to-node | One index serves `enterprise-rag-qa`, `ops-desk-agents`, and `workplace-copilot`; one domain is the largest fixed cost |
| `AiRagAuth` | Cognito user pool, groups `employee`, `engineering`, `sales`, `hr`, `exec`, custom attribute `custom:tenant_id`, managed-login domain | One identity for the CLI demos, the agents, and the web app |
| `AiRagGuardrails` | Bedrock Guardrail with content filters, prompt-attack filter, PII filters (anonymize email/phone/name, block credentials and account numbers), contextual grounding check; one published version | Same safety policy for RAG answers, agents, and chat |

### OpenSearch access model

- Public endpoint, **IAM-signed requests only** (SigV4, service `es`). No master user, no basic auth.
- The domain access policy trusts the account (`arn:aws:iam::<account>:root`) for `es:ESHttp*`; each role's own IAM policy decides what it may do. This avoids a circular dependency between this stack and the roles created in project stacks.
- Per-role scope: CI roles may read `rag*` and create/delete only `rag-pr-*` indexes; deploy roles may write; the `workplace-copilot` Lambda and `ops-desk-agents` runtime roles may search and write only what their features need.
- Sizing: `t3.medium.search` fits the `mini` profile at 1024-dim Titan embeddings. For the `full` profile use `r7g.large.search`, or keep `t3.medium` and turn on k-NN disk mode / byte quantization (`-c searchInstanceType=...`).

### Buckets

| Bucket (SSM name) | Holds | Retention |
|-------------------|-------|-----------|
| `/ai-rag/s3/data-bucket` | `ai-rag-data/<version>/<profile>/` (built corpus), `embeddings-cache/`, `quarantine/<repo>/` | Retained on destroy |
| `/ai-rag/s3/audit-bucket` | Append-only audit events, one object per batch | Object Lock, governance mode, 30-day default retention |
| `/ai-rag/s3/traces-bucket` | Anonymized request traces `traces/<repo>/<yyyy>/<mm>/<dd>/` | Lifecycle: expire after 90 days |
| `/ai-rag/s3/evals-bucket` | evalkit results, reports, DVC remote | Retained on destroy |

All buckets: block public access, SSE-S3, enforce TLS, `cost-center=ai-rag` tag.

### GitHub OIDC roles

| Role | Trusted subject | Permissions |
|------|-----------------|-------------|
| `gh-<repo>-ci` | `repo:Lopukhovych/<repo>:pull_request` and `:ref:refs/heads/*` | `bedrock:InvokeModel*`, `bedrock:ApplyGuardrail`, read its S3 prefixes, OpenSearch read (+ `rag-pr-*` for `enterprise-rag-qa`) |
| `gh-<repo>-deploy` | `repo:Lopukhovych/<repo>:environment:production` | Assume the CDK bootstrap roles (`cdk-*`), plus the data-plane rights its workflows need (`enterprise-rag-qa`: write the index; `ai-rag-data`: write `ai-rag-data/*`) |

Repos: `ai-rag-data`, `evalkit`, `enterprise-rag-qa`, `ops-desk-agents`, `workplace-copilot`, `ai-rag-infra`. No long-lived AWS keys exist anywhere.

### SSM parameters written by this repo

```
/ai-rag/s3/data-bucket            /ai-rag/opensearch/endpoint
/ai-rag/s3/audit-bucket           /ai-rag/opensearch/domain-arn
/ai-rag/s3/traces-bucket          /ai-rag/cognito/user-pool-id
/ai-rag/s3/evals-bucket           /ai-rag/cognito/domain
/ai-rag/sns/alerts-topic-arn      /ai-rag/bedrock/guardrail-id
/ai-rag/iam/<repo>/ci-role-arn    /ai-rag/bedrock/guardrail-version
/ai-rag/iam/<repo>/deploy-role-arn
```

Project stacks add `/ai-rag/agents/*` (`ops-desk-agents`) and `/ai-rag/copilot/*` (`workplace-copilot`).

---

## Requirements

- [ ] One CDK app (Python) with the four stacks above; `cdk synth` passes with no warnings from `cdk-nag` (AwsSolutions pack, documented suppressions only)
- [ ] Every resource tagged `project=ai-rag`, `cost-center=ai-rag`, `owner=Lopukhovych`
- [ ] Budget alarm and SNS email confirmed before any other stack is deployed
- [ ] GitHub OIDC roles per repo, CI and deploy split; deploy roles only usable from the `production` environment
- [ ] OpenSearch reachable only with SigV4; an unsigned `curl` returns `403`
- [ ] Bedrock Guardrail published as a numbered version; projects pin that version
- [ ] Bedrock model invocation logging stays **off** (it would store full prompts and break the PII-free log rule); projects log tokens and latency themselves
- [ ] `scripts/seed_users.py` creates the 30 test users from `ai-rag-data` `users.jsonl` in Cognito, with groups and `custom:tenant_id`
- [ ] `cdk destroy` of `AiRagSearch`, `AiRagAuth`, `AiRagGuardrails` leaves only the retained buckets
- [ ] `docs/cost.md`: fixed monthly cost per stack, idle cost, and the teardown order

---

## Suggested Structure

```
ai-rag-infra/
├── README.md
├── ACCEPTANCE_CRITERIA.md
├── LICENSE
├── pyproject.toml           # uv app; aws-cdk-lib, constructs, cdk-nag
├── cdk.json                 # "app": "uv run python app.py"
├── app.py                   # Instantiates the four stacks, tags, cdk-nag
├── stacks/
│   ├── foundation.py        # Buckets, SNS, budget, GitHub OIDC roles, SSM params
│   ├── search.py            # OpenSearch domain and access policy
│   ├── auth.py              # Cognito user pool, groups, domain
│   └── guardrails.py        # Bedrock Guardrail + version
├── scripts/
│   ├── seed_users.py        # users.jsonl -> Cognito users + groups
│   └── print_env.sh         # Prints /ai-rag/* as .env lines
├── tests/
│   └── test_stacks.py       # CDK assertions: no public buckets, SigV4-only domain, OIDC subjects
├── docs/
│   └── cost.md
└── .github/workflows/
    ├── ci.yml               # ruff, pytest, cdk synth
    └── deploy.yml           # cdk deploy on main (environment: production)
```

---
