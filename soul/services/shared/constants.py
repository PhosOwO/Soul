from __future__ import annotations


SOUL_DIR_NAME = ".soul"
STATE_DIR_NAME = "state"
TRACES_DIR_NAME = "traces"
REME_NAME = "reme"
NEEDS_REVIEW = "needs_review"

REME_DIR_NAME = REME_NAME

MEMORY_MODE_SOUL_REME = "soul_reme"

MEMORY_OWNER_REME = REME_NAME
STATE_OWNER_SOUL = "soul"

REME_WRITE_MODE_AUTO_MEMORY = "auto_memory"
REME_WRITE_MODE_FALLBACK_DAILY = "fallback_daily_write"

PATCH_STATUS_PROPOSED = "proposed"
PATCH_STATUS_ACCEPTED = "accepted"
PATCH_STATUS_APPLIED = "applied"
PATCH_STATUS_REJECTED = "rejected"
PATCH_STATUS_ARCHIVED = "archived"
PATCH_STATUS_NEEDS_REVIEW = NEEDS_REVIEW
PATCH_STATUS_TENTATIVE = "tentative"

PATCH_REVIEW_AUTO_ACCEPT = "auto_accept"
PATCH_REVIEW_NEEDS_REVIEW = NEEDS_REVIEW
PATCH_REVIEW_REJECT = "reject"

PATCH_OP_UPSERT_BELIEF = "upsert_belief"
PATCH_OP_ADD_CONSTRAINT = "add_constraint"
PATCH_OP_ADD_OPEN_QUESTION = "add_open_question"
PATCH_OP_UPSERT_STATE_ITEM = "upsert_state_item"

STATE_KIND_ACCEPTED_BELIEF = "accepted_belief"
STATE_KIND_ACTIVE_CONSTRAINT = "active_constraint"
STATE_KIND_DECISION_GATE = "decision_gate"
STATE_KIND_OPEN_QUESTION = "open_question"
STATE_KIND_TENTATIVE_OBSERVATION = "tentative_observation"

HOST_DEEPSEEK_HARNESS = "deepseek-harness"
HOST_HTTP_API = "http-api"
HOST_SOUL_API = "soul-api"
HOST_SOUL_HTTP_API = "soul-http-api"
HOST_SOUL_MCP = "soul-mcp"

OP_GET_STATE = "get_state"
OP_PROPOSE_REME_TRANSITION = "propose_reme_transition"
OP_READ_EVIDENCE = "read_evidence"
OP_TRACE_EVIDENCE = "trace_evidence"
OP_CONSOLIDATE_MEMORY = "consolidate_memory"
OP_GET_PROACTIVE_TOPICS = "get_proactive_topics"

STATUS_SUCCESS = "success"
