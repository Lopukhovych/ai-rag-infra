"""Names and settings shared by every stack. Nothing here creates resources."""

REGION = "us-east-1"
PROJECT = "ai-rag"
SSM_PREFIX = "/ai-rag"

# Fixed so that IAM policies in AiRagFoundation can name the domain ARN without
# a cross-stack reference (AiRagSearch can be destroyed and re-created freely).
SEARCH_DOMAIN_NAME = "ai-rag-search"

REPOS = [
    "ai-rag-data",
    "evalkit",
    "enterprise-rag-qa",
    "ops-desk-agents",
    "workplace-copilot",
    "ai-rag-infra",
]

GROUPS = ["employee", "engineering", "sales", "hr", "exec"]

# Default CDK bootstrap qualifier. Change only if you bootstrapped with --qualifier.
CDK_QUALIFIER = "hnb659fds"
