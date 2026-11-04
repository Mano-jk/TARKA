"""TARKA deterministic logic gateway."""

from tarka.gateway import (
	Gateway,
	LogicViolationError,
	PlanExtractionError,
	TimeoutPolicyError,
	UpstreamProtocolError,
)
from tarka.integrations import ToolValidationError, guard_tool
from tarka.rules import RuleSyntaxError
from tarka.templates import TemplateError, available_templates, get_template_rules
from tarka.temporal import WorkflowResult, WorkflowRuleError, verify_workflow
from tarka.verifier import RuleEvaluationError, VerificationResult, VerificationStatus, verify

__version__ = "0.1.0"

__all__ = [
	"Gateway",
	"LogicViolationError",
	"PlanExtractionError",
	"RuleEvaluationError",
	"RuleSyntaxError",
	"TimeoutPolicyError",
	"TemplateError",
	"ToolValidationError",
	"UpstreamProtocolError",
	"VerificationResult",
	"VerificationStatus",
	"WorkflowResult",
	"WorkflowRuleError",
	"available_templates",
	"get_template_rules",
	"guard_tool",
	"verify",
	"verify_workflow",
]