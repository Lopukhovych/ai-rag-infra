"""AiRagGuardrails: one Bedrock Guardrail and one published version."""

from aws_cdk import Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from stacks.common import SSM_PREFIX

BLOCKED_INPUT = "Sorry, I can't help with that request."
BLOCKED_OUTPUT = "Sorry, I can't share that response."


class GuardrailsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        content_filters = [
            bedrock.CfnGuardrail.ContentFilterConfigProperty(
                type=filter_type, input_strength="HIGH", output_strength="HIGH"
            )
            for filter_type in ("HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT")
        ]
        # Prompt-attack filtering applies to inputs only (the API requires output NONE).
        content_filters.append(
            bedrock.CfnGuardrail.ContentFilterConfigProperty(
                type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"
            )
        )

        anonymize = ["EMAIL", "PHONE", "NAME"]
        block = [
            "PASSWORD",
            "AWS_ACCESS_KEY",
            "AWS_SECRET_KEY",
            "CREDIT_DEBIT_CARD_NUMBER",
            "US_BANK_ACCOUNT_NUMBER",
        ]
        pii_entities = [
            bedrock.CfnGuardrail.PiiEntityConfigProperty(type=t, action="ANONYMIZE")
            for t in anonymize
        ] + [
            bedrock.CfnGuardrail.PiiEntityConfigProperty(type=t, action="BLOCK")
            for t in block
        ]

        self.guardrail = bedrock.CfnGuardrail(
            self,
            "Guardrail",
            name="ai-rag-guardrail",
            description="Shared safety policy for RAG answers, agents, and chat",
            blocked_input_messaging=BLOCKED_INPUT,
            blocked_outputs_messaging=BLOCKED_OUTPUT,
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=content_filters
            ),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=pii_entities
            ),
            contextual_grounding_policy_config=bedrock.CfnGuardrail.ContextualGroundingPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContextualGroundingFilterConfigProperty(
                        type="GROUNDING", threshold=0.75
                    ),
                    bedrock.CfnGuardrail.ContextualGroundingFilterConfigProperty(
                        type="RELEVANCE", threshold=0.5
                    ),
                ]
            ),
        )

        # A numbered version is immutable. Projects pin this number; to ship a policy change,
        # rename this construct id (for example GuardrailVersionV2) so CloudFormation
        # publishes a new version instead of reusing the old one.
        self.version = bedrock.CfnGuardrailVersion(
            self,
            "GuardrailVersionV1",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
            description="ai-rag guardrail, published version",
        )

        ssm.StringParameter(
            self,
            "GuardrailIdParam",
            parameter_name=f"{SSM_PREFIX}/bedrock/guardrail-id",
            string_value=self.guardrail.attr_guardrail_id,
        )
        ssm.StringParameter(
            self,
            "GuardrailVersionParam",
            parameter_name=f"{SSM_PREFIX}/bedrock/guardrail-version",
            string_value=self.version.attr_version,
        )
