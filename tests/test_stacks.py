"""CDK assertions for the shared ai-rag stacks (acceptance criteria 1-9)."""

import json

import pytest
from aws_cdk.assertions import Annotations, Match, Template

from app import build_app
from stacks.common import GROUPS, REPOS


@pytest.fixture(scope="module")
def foundation(stacks):
    return Template.from_stack(stacks["AiRagFoundation"])


@pytest.fixture(scope="module")
def search(stacks):
    return Template.from_stack(stacks["AiRagSearch"])


@pytest.fixture(scope="module")
def auth(stacks):
    return Template.from_stack(stacks["AiRagAuth"])


@pytest.fixture(scope="module")
def guardrails(stacks):
    return Template.from_stack(stacks["AiRagGuardrails"])


def test_four_stacks_exist(stacks):
    assert {"AiRagFoundation", "AiRagSearch", "AiRagAuth", "AiRagGuardrails"} <= set(
        stacks
    )


def test_resources_are_tagged(stacks):
    expected = [
        {"Key": "cost-center", "Value": "ai-rag"},
        {"Key": "owner", "Value": "octo"},
        {"Key": "project", "Value": "ai-rag"},
    ]
    foundation = Template.from_stack(stacks["AiRagFoundation"])
    for bucket in foundation.find_resources("AWS::S3::Bucket").values():
        assert all(tag in bucket["Properties"]["Tags"] for tag in expected)
    search = Template.from_stack(stacks["AiRagSearch"])
    for domain in search.find_resources("AWS::OpenSearchService::Domain").values():
        assert all(tag in domain["Properties"]["Tags"] for tag in expected)


# ---------------------------------------------------------------- buckets
def test_no_public_buckets_and_tls_enforced(foundation):
    buckets = foundation.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 4
    for bucket in buckets.values():
        assert bucket["Properties"]["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
        enc = bucket["Properties"]["BucketEncryption"][
            "ServerSideEncryptionConfiguration"
        ]
        assert enc[0]["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
    # one TLS-only deny policy per bucket
    foundation.resource_count_is("AWS::S3::BucketPolicy", 4)
    for policy in foundation.find_resources("AWS::S3::BucketPolicy").values():
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        assert any(
            s["Effect"] == "Deny"
            and s["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}
            for s in statements
        )


def test_audit_bucket_has_governance_object_lock(foundation):
    foundation.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "ObjectLockEnabled": True,
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled",
                "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 30}},
            },
        },
    )


def test_traces_expire_and_data_buckets_are_retained(foundation):
    foundation.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "LifecycleConfiguration": {
                "Rules": [Match.object_like({"ExpirationInDays": 90})]
            }
        },
    )
    retained = [
        r
        for r in foundation.find_resources("AWS::S3::Bucket").values()
        if r.get("DeletionPolicy") == "Retain"
    ]
    assert len(retained) == 3  # data, audit, evals


# ----------------------------------------------------------- budget and SNS
def test_budget_alerts_at_50_80_100(foundation):
    budget = next(iter(foundation.find_resources("AWS::Budgets::Budget").values()))
    props = budget["Properties"]
    assert props["Budget"]["TimeUnit"] == "MONTHLY"
    thresholds = sorted(
        n["Notification"]["Threshold"] for n in props["NotificationsWithSubscribers"]
    )
    assert thresholds == [50, 80, 100]


def test_alert_topic_has_email_subscription(foundation):
    foundation.has_resource_properties(
        "AWS::SNS::Topic", {"TopicName": "ai-rag-alerts"}
    )
    foundation.has_resource_properties(
        "AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": "me@example.com"}
    )


# ------------------------------------------------------------- GitHub OIDC
def _roles(template):
    return {
        r["Properties"]["RoleName"]: r
        for r in template.find_resources("AWS::IAM::Role").values()
        if "RoleName" in r["Properties"]
    }


def test_ci_and_deploy_role_per_repo(foundation):
    roles = _roles(foundation)
    for repo in REPOS:
        assert f"gh-{repo}-ci" in roles
        assert f"gh-{repo}-deploy" in roles


def test_ci_roles_trust_pr_and_branches_only(foundation):
    roles = _roles(foundation)
    for repo in REPOS:
        cond = roles[f"gh-{repo}-ci"]["Properties"]["AssumeRolePolicyDocument"][
            "Statement"
        ][0]["Condition"]
        subs = cond["StringLike"]["token.actions.githubusercontent.com:sub"]
        assert subs == [
            f"repo:octo/{repo}:pull_request",
            f"repo:octo/{repo}:ref:refs/heads/*",
        ]
        assert (
            cond["StringEquals"]["token.actions.githubusercontent.com:aud"]
            == "sts.amazonaws.com"
        )


def test_deploy_roles_trust_production_environment_only(foundation):
    roles = _roles(foundation)
    for repo in REPOS:
        cond = roles[f"gh-{repo}-deploy"]["Properties"]["AssumeRolePolicyDocument"][
            "Statement"
        ][0]["Condition"]
        assert (
            cond["StringEquals"]["token.actions.githubusercontent.com:sub"]
            == f"repo:octo/{repo}:environment:production"
        )
        assert "StringLike" not in cond


def test_ci_roles_cannot_deploy(foundation):
    for policy in foundation.find_resources("AWS::IAM::Policy").values():
        roles = json.dumps(policy["Properties"]["Roles"])
        if "-ci" not in roles:
            continue
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = (
                stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
            )
            assert "sts:AssumeRole" not in actions
            assert not any(a.startswith(("cloudformation:", "iam:")) for a in actions)


def test_no_star_action_on_star_resource(foundation):
    for policy in foundation.find_resources("AWS::IAM::Policy").values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            actions = (
                stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
            )
            resources = (
                stmt["Resource"]
                if isinstance(stmt["Resource"], list)
                else [stmt["Resource"]]
            )
            assert not ("*" in actions and "*" in resources)


# ---------------------------------------------------------------- OpenSearch
def test_domain_is_https_encrypted_and_has_no_basic_auth(search):
    search.has_resource_properties(
        "AWS::OpenSearchService::Domain",
        {
            "EngineVersion": "OpenSearch_2.19",
            "ClusterConfig": Match.object_like(
                {"InstanceCount": 1, "InstanceType": "t3.medium.search"}
            ),
            "EBSOptions": Match.object_like({"VolumeSize": 20, "VolumeType": "gp3"}),
            "DomainEndpointOptions": Match.object_like({"EnforceHTTPS": True}),
            "NodeToNodeEncryptionOptions": {"Enabled": True},
            "EncryptionAtRestOptions": {"Enabled": True},
        },
    )
    domain = next(
        iter(search.find_resources("AWS::OpenSearchService::Domain").values())
    )
    assert "AdvancedSecurityOptions" not in domain["Properties"]


def test_domain_access_policy_trusts_account_root_only(search):
    # CDK applies the access policy through a custom resource (updateDomainConfig).
    resource = next(
        iter(search.find_resources("Custom::OpenSearchAccessPolicy").values())
    )
    text = json.dumps(resource["Properties"]["Create"])
    assert "es:ESHttp*" in text
    assert ":iam::123456789012:root" in text
    assert '\\"AWS\\":\\"*\\"' not in text  # never an anonymous principal


# ------------------------------------------------------------------- Cognito
def test_user_pool_has_tenant_attribute_and_no_self_signup(auth):
    auth.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
            "Schema": Match.array_with(
                [
                    Match.object_like(
                        {"Name": "tenant_id", "AttributeDataType": "String"}
                    )
                ]
            ),
        },
    )


def test_user_pool_groups(auth):
    names = {
        g["Properties"]["GroupName"]
        for g in auth.find_resources("AWS::Cognito::UserPoolGroup").values()
    }
    assert names == set(GROUPS)


# ---------------------------------------------------------------- Guardrails
def test_guardrail_policies(guardrails):
    guardrails.has_resource_properties(
        "AWS::Bedrock::Guardrail",
        {
            "ContentPolicyConfig": {
                "FiltersConfig": Match.array_with(
                    [
                        Match.object_like(
                            {"Type": "PROMPT_ATTACK", "InputStrength": "HIGH"}
                        )
                    ]
                )
            },
            "SensitiveInformationPolicyConfig": {
                "PiiEntitiesConfig": Match.array_with(
                    [
                        {"Type": "EMAIL", "Action": "ANONYMIZE"},
                        {"Type": "PASSWORD", "Action": "BLOCK"},
                        {"Type": "US_BANK_ACCOUNT_NUMBER", "Action": "BLOCK"},
                    ]
                )
            },
            "ContextualGroundingPolicyConfig": {
                "FiltersConfig": Match.array_with(
                    [Match.object_like({"Type": "GROUNDING"})]
                )
            },
        },
    )


def test_guardrail_has_published_version(guardrails):
    guardrails.resource_count_is("AWS::Bedrock::GuardrailVersion", 1)


# ---------------------------------------------------------------- SSM names
def test_ssm_parameters_published(stacks):
    names = set()
    for stack in stacks.values():
        template = Template.from_stack(stack)
        for p in template.find_resources("AWS::SSM::Parameter").values():
            names.add(p["Properties"]["Name"])
    expected = {
        "/ai-rag/s3/data-bucket",
        "/ai-rag/s3/audit-bucket",
        "/ai-rag/s3/traces-bucket",
        "/ai-rag/s3/evals-bucket",
        "/ai-rag/sns/alerts-topic-arn",
        "/ai-rag/opensearch/endpoint",
        "/ai-rag/opensearch/domain-arn",
        "/ai-rag/cognito/user-pool-id",
        "/ai-rag/cognito/domain",
        "/ai-rag/bedrock/guardrail-id",
        "/ai-rag/bedrock/guardrail-version",
    }
    for repo in REPOS:
        expected |= {
            f"/ai-rag/iam/{repo}/ci-role-arn",
            f"/ai-rag/iam/{repo}/deploy-role-arn",
        }
    assert expected <= names


# ------------------------------------------------------------------- cdk-nag
def test_cdk_nag_has_no_findings():
    app = build_app({"githubUser": "octo", "alertEmail": "me@example.com"})
    app.synth()
    for stack in (c for c in app.node.children if hasattr(c, "stack_name")):
        annotations = Annotations.from_stack(stack)
        pattern = Match.string_like_regexp("AwsSolutions-.*")
        assert annotations.find_error("*", pattern) == [], stack.stack_name
        assert annotations.find_warning("*", pattern) == [], stack.stack_name
