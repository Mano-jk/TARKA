"""TARKA deterministic logic gateway."""

from tarka.gateway import (
	Gateway,
	LogicViolationError,
	PlanExtractionError,
	TimeoutPolicyError,
	UpstreamProtocolError,
)
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
	"UpstreamProtocolError",
	"VerificationResult",
	"VerificationStatus",
	"WorkflowResult",
	"WorkflowRuleError",
	"available_templates",
	"get_template_rules",
	"verify",
	"verify_workflow",
]