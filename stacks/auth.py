"""AiRagAuth: Cognito user pool, role groups, tenant attribute, managed-login domain."""

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ssm as ssm
from cdk_nag import NagSuppressions
from constructs import Construct

from stacks.common import GROUPS, SSM_PREFIX


class AuthStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="ai-rag-users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True)
            ),
            custom_attributes={
                "tenant_id": cognito.StringAttribute(
                    min_len=1, max_len=64, mutable=False
                )
            },
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=Duration.days(7),
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY,
        )

        for group in GROUPS:
            cognito.CfnUserPoolGroup(
                self,
                f"Group{group.capitalize()}",
                user_pool_id=self.user_pool.user_pool_id,
                group_name=group,
                description=f"ai-rag role group: {group}",
            )

        prefix = f"ai-rag-{self.account}"
        self.domain = self.user_pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=prefix),
            managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
        )

        ssm.StringParameter(
            self,
            "UserPoolIdParam",
            parameter_name=f"{SSM_PREFIX}/cognito/user-pool-id",
            string_value=self.user_pool.user_pool_id,
        )
        ssm.StringParameter(
            self,
            "DomainParam",
            parameter_name=f"{SSM_PREFIX}/cognito/domain",
            string_value=f"{prefix}.auth.{self.region}.amazoncognito.com",
        )

        NagSuppressions.add_resource_suppressions(
            self.user_pool,
            [
                {
                    "id": "AwsSolutions-COG2",
                    "reason": "Test users only (30 seeded accounts, no self sign-up); "
                    "MFA would block the CLI demos.",
                },
                {
                    "id": "AwsSolutions-COG8",
                    "reason": "Plus tier adds per-MAU cost; the pool holds synthetic test users.",
                },
                {
                    "id": "AwsSolutions-COG3",
                    "reason": "Advanced security mode adds per-MAU cost; the pool holds "
                    "synthetic test users.",
                },
            ],
        )
