"""TARKA deterministic logic gateway."""

from tarka.gateway import (
	Gateway,
	LogicViolationError,
	PlanExtractionError,
	TimeoutPolicyError,
	UpstreamProtocolError,
)
from tarka.rules import RuleSyntaxError
from tarka.verifier import RuleEvaluationError, VerificationResult, VerificationStatus, verify

__version__ = "0.1.0"

__all__ = [
	"Gateway",
	"LogicViolationError",
	"PlanExtractionError",
	"RuleEvaluationError",
	"RuleSyntaxError",
	"TimeoutPolicyError",
	"UpstreamProtocolError",
	"VerificationResult",
	"VerificationStatus",
	"verify",
]