from __future__ import annotations


DEFAULT_WORKING_EXPIRY = "end_of_day"
DEFAULT_WORKING_REVIEW_AFTER = "end_of_day"
DEFAULT_WORKING_SCOPE = "current task"

WORKING_STATEMENT_MAX_LENGTH = 320
WORKING_REASON_MAX_LENGTH = 240
WORKING_SCOPE_MAX_LENGTH = 120

WORKING_TOKEN_MIN_LENGTH = 4

DEFAULT_WORKING_REASON = (
    "Not retaining this working assumption may cause the next turn to repeat or follow an outdated direction."
)

EPISODIC_PREFIXES = (
    "created ",
    "configured ",
    "updated ",
    "added ",
    "ran ",
    "tested ",
    "verified ",
    "fixed ",
    "read ",
    "reviewed ",
    "今天阅读",
    "阅读了",
    "查看了",
    "运行了",
)

WORKING_CHANGE_SIGNALS = (
    "当前",
    "主线",
    "转向",
    "不再",
    "不能等同",
    "已诊断",
    "诊断出",
    "瓶颈",
    "优先",
    "暂定",
    "先验证",
    "先评估",
    "先考虑",
    "后续",
    "下一步",
    "should",
    "must",
    "default",
    "do not",
    "don't",
    "avoid",
    "qnet",
)

CONFLICT_NEGATION_SIGNALS = (
    "不再",
    "do not",
    "don't",
)
