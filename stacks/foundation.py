"""AiRagFoundation: buckets, alerts, budget, GitHub OIDC roles, SSM parameters."""

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_ssm as ssm
from cdk_nag import NagSuppressions
from constructs import Construct

from stacks.common import CDK_QUALIFIER, REPOS, SEARCH_DOMAIN_NAME, SSM_PREFIX

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"


class FoundationStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        github_user: str,
        alert_email: str,
        monthly_budget_usd: float,
        existing_oidc_provider_arn: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.github_user = github_user

        self._create_buckets()
        self._create_alerts(alert_email, monthly_budget_usd)
        self._create_github_roles(existing_oidc_provider_arn)

    # ------------------------------------------------------------------ buckets
    def _bucket(self, name: str, **kwargs) -> s3.Bucket:
        bucket = s3.Bucket(
            self,
            f"{name.capitalize()}Bucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            **kwargs,
        )
        ssm.StringParameter(
            self,
            f"{name.capitalize()}BucketParam",
            parameter_name=f"{SSM_PREFIX}/s3/{name}-bucket",
            string_value=bucket.bucket_name,
        )
        NagSuppressions.add_resource_suppressions(
            bucket,
            [
                {
                    "id": "AwsSolutions-S1",
                    "reason": "Server access logs would add a bucket per bucket; audit "
                    "events and traces are written by the projects themselves.",
                }
            ],
        )
        return bucket

    def _create_buckets(self) -> None:
        self.data_bucket = self._bucket(
            "data", removal_policy=RemovalPolicy.RETAIN, versioned=True
        )
        self.audit_bucket = self._bucket(
            "audit",
            removal_policy=RemovalPolicy.RETAIN,
            object_lock_enabled=True,
            object_lock_default_retention=s3.ObjectLockRetention.governance(
                Duration.days(30)
            ),
        )
        self.traces_bucket = self._bucket(
            "traces",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(90))],
        )
        self.evals_bucket = self._bucket(
            "evals", removal_policy=RemovalPolicy.RETAIN, versioned=True
        )

    # ------------------------------------------------------------ alerts, budget
    def _create_alerts(self, alert_email: str, monthly_budget_usd: float) -> None:
        self.alerts_topic = sns.Topic(self, "AlertsTopic", topic_name="ai-rag-alerts")
        self.alerts_topic.add_subscription(subs.EmailSubscription(alert_email))
        self.alerts_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowBudgetsPublish",
                principals=[iam.ServicePrincipal("budgets.amazonaws.com")],
                actions=["sns:Publish"],
                resources=[self.alerts_topic.topic_arn],
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            )
        )
        self.alerts_topic.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyInsecureTransport",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["sns:Publish"],
                resources=[self.alerts_topic.topic_arn],
                conditions={"Bool": {"aws:SecureTransport": "false"}},
            )
        )
        NagSuppressions.add_resource_suppressions(
            self.alerts_topic,
            [
                {
                    "id": "AwsSolutions-SNS2",
                    "reason": "AWS Budgets cannot publish to a topic encrypted with the "
                    "AWS-managed key; the topic carries cost alerts only, no PII.",
                }
            ],
        )

        ssm.StringParameter(
            self,
            "AlertsTopicParam",
            parameter_name=f"{SSM_PREFIX}/sns/alerts-topic-arn",
            string_value=self.alerts_topic.topic_arn,
        )

        notifications = [
            budgets.CfnBudget.NotificationWithSubscribersProperty(
                notification=budgets.CfnBudget.NotificationProperty(
                    notification_type="ACTUAL",
                    comparison_operator="GREATER_THAN",
                    threshold=threshold,
                    threshold_type="PERCENTAGE",
                ),
                subscribers=[
                    budgets.CfnBudget.SubscriberProperty(
                        subscription_type="SNS", address=self.alerts_topic.topic_arn
                    ),
                    budgets.CfnBudget.SubscriberProperty(
                        subscription_type="EMAIL", address=alert_email
                    ),
                ],
            )
            for threshold in (50, 80, 100)
        ]
        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="ai-rag-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=monthly_budget_usd, unit="USD"
                ),
            ),
            notifications_with_subscribers=notifications,
        )

    # --------------------------------------------------------------- GitHub OIDC
    def _create_github_roles(self, existing_provider_arn: str | None) -> None:
        if existing_provider_arn:
            provider_arn = existing_provider_arn
        else:
            provider = iam.OpenIdConnectProvider(
                self,
                "GitHubOidc",
                url=GITHUB_OIDC_URL,
                client_ids=["sts.amazonaws.com"],
            )
            provider_arn = provider.open_id_connect_provider_arn

        account = self.account
        region = self.region
        domain_arn = f"arn:aws:es:{region}:{account}:domain/{SEARCH_DOMAIN_NAME}"
        bedrock_models = [
            "arn:aws:bedrock:*::foundation-model/*",
            f"arn:aws:bedrock:{region}:{account}:inference-profile/*",
        ]
        guardrail_arn = f"arn:aws:bedrock:{region}:{account}:guardrail/*"

        for repo in REPOS:
            subject_base = f"repo:{self.github_user}/{repo}"
            ci_role = iam.Role(
                self,
                f"{self._pascal(repo)}CiRole",
                role_name=f"gh-{repo}-ci",
                max_session_duration=Duration.hours(1),
                assumed_by=iam.WebIdentityPrincipal(
                    provider_arn,
                    {
                        "StringEquals": {
                            f"{GITHUB_OIDC_HOST}:aud": "sts.amazonaws.com"
                        },
                        "StringLike": {
                            f"{GITHUB_OIDC_HOST}:sub": [
                                f"{subject_base}:pull_request",
                                f"{subject_base}:ref:refs/heads/*",
                            ]
                        },
                    },
                ),
            )
            deploy_role = iam.Role(
                self,
                f"{self._pascal(repo)}DeployRole",
                role_name=f"gh-{repo}-deploy",
                max_session_duration=Duration.hours(1),
                assumed_by=iam.WebIdentityPrincipal(
                    provider_arn,
                    {
                        "StringEquals": {
                            f"{GITHUB_OIDC_HOST}:aud": "sts.amazonaws.com",
                            f"{GITHUB_OIDC_HOST}:sub": f"{subject_base}:environment:production",
                        }
                    },
                ),
            )

            # --- CI: invoke models, read data, read the index (never deploy)
            ci_role.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    resources=bedrock_models,
                )
            )
            ci_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["bedrock:ApplyGuardrail"], resources=[guardrail_arn]
                )
            )
            ci_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=[
                        self.data_bucket.arn_for_objects("ai-rag-data/*"),
                        self.data_bucket.arn_for_objects("embeddings-cache/*"),
                    ],
                )
            )
            ci_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:ListBucket"], resources=[self.data_bucket.bucket_arn]
                )
            )
            ci_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:GetObject", "s3:PutObject"],
                    resources=[self.evals_bucket.arn_for_objects(f"{repo}/*")],
                )
            )
            ci_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["s3:PutObject"],
                    resources=[self.traces_bucket.arn_for_objects(f"traces/{repo}/*")],
                )
            )
            ci_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["es:ESHttpGet", "es:ESHttpHead"],
                    resources=[f"{domain_arn}/rag*"],
                )
            )
            ci_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["es:ESHttpPost"],
                    resources=[
                        f"{domain_arn}/rag*/_search*",
                        f"{domain_arn}/_msearch*",
                    ],
                )
            )
            if repo == "enterprise-rag-qa":
                ci_role.add_to_policy(
                    iam.PolicyStatement(
                        actions=[
                            "es:ESHttpPut",
                            "es:ESHttpPost",
                            "es:ESHttpDelete",
                            "es:ESHttpGet",
                        ],
                        resources=[
                            f"{domain_arn}/rag-pr-*",
                            f"{domain_arn}/rag-pr-*/*",
                        ],
                    )
                )

            # --- Deploy: assume the CDK bootstrap roles, plus per-repo data-plane rights
            deploy_role.add_to_policy(
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[
                        f"arn:aws:iam::{account}:role/cdk-{CDK_QUALIFIER}-*-role-{account}-{region}"
                    ],
                )
            )
            deploy_role.add_to_policy(
                iam.PolicyStatement(
                    actions=[
                        "ssm:GetParameter",
                        "ssm:GetParameters",
                        "ssm:GetParametersByPath",
                    ],
                    resources=[
                        f"arn:aws:ssm:{region}:{account}:parameter{SSM_PREFIX}/*"
                    ],
                )
            )
            if repo == "enterprise-rag-qa":
                deploy_role.add_to_policy(
                    iam.PolicyStatement(
                        actions=[
                            "es:ESHttpGet",
                            "es:ESHttpHead",
                            "es:ESHttpPost",
                            "es:ESHttpPut",
                            "es:ESHttpDelete",
                        ],
                        resources=[f"{domain_arn}/rag*", f"{domain_arn}/rag*/*"],
                    )
                )
            if repo == "ai-rag-data":
                deploy_role.add_to_policy(
                    iam.PolicyStatement(
                        actions=["s3:PutObject", "s3:AbortMultipartUpload"],
                        resources=[self.data_bucket.arn_for_objects("ai-rag-data/*")],
                    )
                )

            NagSuppressions.add_resource_suppressions(
                [ci_role, deploy_role],
                [
                    {
                        "id": "AwsSolutions-IAM5",
                        "reason": "Wildcards are limited to model ARNs, per-repo S3 prefixes, "
                        "index name patterns, and the CDK bootstrap role name pattern.",
                    }
                ],
                apply_to_children=True,
            )

            ssm.StringParameter(
                self,
                f"{self._pascal(repo)}CiRoleParam",
                parameter_name=f"{SSM_PREFIX}/iam/{repo}/ci-role-arn",
                string_value=ci_role.role_arn,
            )
            ssm.StringParameter(
                self,
                f"{self._pascal(repo)}DeployRoleParam",
                parameter_name=f"{SSM_PREFIX}/iam/{repo}/deploy-role-arn",
                string_value=deploy_role.role_arn,
            )

    @staticmethod
    def _pascal(repo: str) -> str:
        return "".join(part.capitalize() for part in repo.split("-"))
