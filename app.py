"""Entry point of the shared ai-rag CDK app.

Context (cdk.json or -c key=value):
  githubUser         GitHub username used in OIDC trust subjects (or env GH_USER)
  alertEmail         address for budget and alarm mail (default: placeholder)
  monthlyBudgetUsd   monthly budget limit in USD (default: 50)
  searchInstanceType OpenSearch data node type (default: t3.medium.search)
  existingOidcProviderArn  reuse the account's GitHub OIDC provider if one exists
  nag                set to "false" to skip cdk-nag
"""

import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from stacks.auth import AuthStack
from stacks.common import PROJECT, REGION
from stacks.foundation import FoundationStack
from stacks.guardrails import GuardrailsStack
from stacks.search import SearchStack


def build_app(context: dict | None = None) -> cdk.App:
    app = cdk.App(context=context)

    github_user = app.node.try_get_context("githubUser") or os.environ.get("GH_USER")
    if not github_user:
        # Keeps `cdk synth` usable in CI and tests; deploy requires the real value.
        github_user = "your-github-user"

    env = cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=REGION,
    )

    foundation = FoundationStack(
        app,
        "AiRagFoundation",
        env=env,
        github_user=github_user,
        alert_email=app.node.try_get_context("alertEmail") or "alerts@example.com",
        monthly_budget_usd=float(app.node.try_get_context("monthlyBudgetUsd") or 50),
        existing_oidc_provider_arn=app.node.try_get_context("existingOidcProviderArn"),
        description="ai-rag: buckets, alerts, budget, GitHub OIDC roles",
    )
    search = SearchStack(
        app,
        "AiRagSearch",
        env=env,
        instance_type=app.node.try_get_context("searchInstanceType")
        or "t3.medium.search",
        description="ai-rag: shared OpenSearch domain (SigV4 only)",
    )
    auth = AuthStack(
        app,
        "AiRagAuth",
        env=env,
        description="ai-rag: Cognito user pool, groups, managed-login domain",
    )
    guardrails = GuardrailsStack(
        app,
        "AiRagGuardrails",
        env=env,
        description="ai-rag: shared Bedrock Guardrail with a published version",
    )

    for stack in (foundation, search, auth, guardrails):
        cdk.Tags.of(stack).add("project", PROJECT)
        cdk.Tags.of(stack).add("cost-center", PROJECT)
        cdk.Tags.of(stack).add("owner", github_user)

    if str(app.node.try_get_context("nag")).lower() != "false":
        cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

    return app


if __name__ == "__main__":
    build_app().synth()
