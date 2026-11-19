"""AiRagSearch: one OpenSearch domain, public endpoint, SigV4 only."""

from aws_cdk import RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_opensearchservice as opensearch
from aws_cdk import aws_ssm as ssm
from cdk_nag import NagSuppressions
from constructs import Construct

from stacks.common import SEARCH_DOMAIN_NAME, SSM_PREFIX


class SearchStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        instance_type: str = "t3.medium.search",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.domain = opensearch.Domain(
            self,
            "Domain",
            domain_name=SEARCH_DOMAIN_NAME,
            version=opensearch.EngineVersion.OPENSEARCH_2_19,
            capacity=opensearch.CapacityConfig(
                data_nodes=1,
                data_node_instance_type=instance_type,
                multi_az_with_standby_enabled=False,
            ),
            ebs=opensearch.EbsOptions(
                enabled=True,
                volume_size=20,
                volume_type=ec2.EbsDeviceVolumeType.GP3,
            ),
            zone_awareness=opensearch.ZoneAwarenessConfig(enabled=False),
            enforce_https=True,
            tls_security_policy=opensearch.TLSSecurityPolicy.TLS_1_2,
            node_to_node_encryption=True,
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            # No master user and no basic auth: IAM-signed requests only.
            # The domain trusts the account; each role's own IAM policy decides what it may do.
            access_policies=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    principals=[iam.AccountRootPrincipal()],
                    actions=["es:ESHttp*"],
                    resources=[
                        f"arn:aws:es:{self.region}:{self.account}:domain/{SEARCH_DOMAIN_NAME}/*"
                    ],
                )
            ],
            removal_policy=RemovalPolicy.DESTROY,
        )

        ssm.StringParameter(
            self,
            "EndpointParam",
            parameter_name=f"{SSM_PREFIX}/opensearch/endpoint",
            string_value=self.domain.domain_endpoint,
        )
        ssm.StringParameter(
            self,
            "DomainArnParam",
            parameter_name=f"{SSM_PREFIX}/opensearch/domain-arn",
            string_value=self.domain.domain_arn,
        )

        NagSuppressions.add_resource_suppressions(
            self.domain,
            [
                {
                    "id": "AwsSolutions-OS1",
                    "reason": "Deliberate: no VPC, NAT, or load balancer (cost). Access is "
                    "SigV4-only on a public endpoint; unsigned requests get 403.",
                },
                {
                    "id": "AwsSolutions-OS3",
                    "reason": "Access is controlled by IAM-signed requests, not source IPs.",
                },
                {
                    "id": "AwsSolutions-OS4",
                    "reason": "Single data node for cost; dedicated masters are not needed "
                    "for a demo-sized domain.",
                },
                {
                    "id": "AwsSolutions-OS5",
                    "reason": "Access policy trusts the account root only; per-role IAM "
                    "policies scope what each role may do.",
                },
                {
                    "id": "AwsSolutions-OS7",
                    "reason": "Single node by design (cost); no zone awareness.",
                },
                {
                    "id": "AwsSolutions-OS9",
                    "reason": "Slow-log publishing is off to avoid CloudWatch cost and "
                    "query text in logs; projects log latency themselves.",
                },
            ],
        )

        NagSuppressions.add_stack_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-L1",
                    "reason": "CDK-managed custom resource Lambda that applies the domain "
                    "access policy; its runtime is chosen by the aws-cdk-lib release.",
                },
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "Basic execution role of the CDK-managed custom resource Lambda.",
                    "applies_to": [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
                    ],
                },
            ],
        )
