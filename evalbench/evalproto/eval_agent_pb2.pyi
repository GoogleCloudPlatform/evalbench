from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AgentStreamMessage(_message.Message):
    __slots__ = ("session_id", "correlation_id", "turn_request", "turn_response", "scoring_request", "scoring_response", "reporting_request", "reporting_response", "session_summary")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    CORRELATION_ID_FIELD_NUMBER: _ClassVar[int]
    TURN_REQUEST_FIELD_NUMBER: _ClassVar[int]
    TURN_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    SCORING_REQUEST_FIELD_NUMBER: _ClassVar[int]
    SCORING_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    REPORTING_REQUEST_FIELD_NUMBER: _ClassVar[int]
    REPORTING_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    SESSION_SUMMARY_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    correlation_id: str
    turn_request: TurnRequest
    turn_response: TurnResponse
    scoring_request: ScoringRequest
    scoring_response: ScoringResponse
    reporting_request: ReportingRequest
    reporting_response: ReportingResponse
    session_summary: SessionSummaryMessage
    def __init__(self, session_id: _Optional[str] = ..., correlation_id: _Optional[str] = ..., turn_request: _Optional[_Union[TurnRequest, _Mapping]] = ..., turn_response: _Optional[_Union[TurnResponse, _Mapping]] = ..., scoring_request: _Optional[_Union[ScoringRequest, _Mapping]] = ..., scoring_response: _Optional[_Union[ScoringResponse, _Mapping]] = ..., reporting_request: _Optional[_Union[ReportingRequest, _Mapping]] = ..., reporting_response: _Optional[_Union[ReportingResponse, _Mapping]] = ..., session_summary: _Optional[_Union[SessionSummaryMessage, _Mapping]] = ...) -> None: ...

class TurnRequest(_message.Message):
    __slots__ = ("turn_index", "prompt", "env", "working_dir", "timeout_seconds", "resume")
    class EnvEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    PROMPT_FIELD_NUMBER: _ClassVar[int]
    ENV_FIELD_NUMBER: _ClassVar[int]
    WORKING_DIR_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    RESUME_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    prompt: str
    env: _containers.ScalarMap[str, str]
    working_dir: str
    timeout_seconds: float
    resume: bool
    def __init__(self, turn_index: _Optional[int] = ..., prompt: _Optional[str] = ..., env: _Optional[_Mapping[str, str]] = ..., working_dir: _Optional[str] = ..., timeout_seconds: _Optional[float] = ..., resume: _Optional[bool] = ...) -> None: ...

class ToolCallRecord(_message.Message):
    __slots__ = ("tool_id", "tool_name", "parameters_json", "output", "status", "duration_ms")
    TOOL_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    PARAMETERS_JSON_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    tool_id: str
    tool_name: str
    parameters_json: str
    output: str
    status: str
    duration_ms: int
    def __init__(self, tool_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., parameters_json: _Optional[str] = ..., output: _Optional[str] = ..., status: _Optional[str] = ..., duration_ms: _Optional[int] = ...) -> None: ...

class TurnResponse(_message.Message):
    __slots__ = ("turn_index", "response_text", "tool_calls", "token_stats", "success", "execution_completed", "error_message")
    class TokenStatsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    TURN_INDEX_FIELD_NUMBER: _ClassVar[int]
    RESPONSE_TEXT_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALLS_FIELD_NUMBER: _ClassVar[int]
    TOKEN_STATS_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    turn_index: int
    response_text: str
    tool_calls: _containers.RepeatedCompositeFieldContainer[ToolCallRecord]
    token_stats: _containers.ScalarMap[str, int]
    success: bool
    execution_completed: bool
    error_message: str
    def __init__(self, turn_index: _Optional[int] = ..., response_text: _Optional[str] = ..., tool_calls: _Optional[_Iterable[_Union[ToolCallRecord, _Mapping]]] = ..., token_stats: _Optional[_Mapping[str, int]] = ..., success: _Optional[bool] = ..., execution_completed: _Optional[bool] = ..., error_message: _Optional[str] = ...) -> None: ...

class ScorerSpec(_message.Message):
    __slots__ = ("scorer_name", "config_json", "timeout_seconds")
    SCORER_NAME_FIELD_NUMBER: _ClassVar[int]
    CONFIG_JSON_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    scorer_name: str
    config_json: str
    timeout_seconds: float
    def __init__(self, scorer_name: _Optional[str] = ..., config_json: _Optional[str] = ..., timeout_seconds: _Optional[float] = ...) -> None: ...

class ScoringContext(_message.Message):
    __slots__ = ("nl_prompt", "golden_query", "query_type", "golden_result_json", "golden_eval_results", "golden_error", "generated_query", "generated_result_json", "eval_results_json", "generated_error", "database", "scenario_id")
    NL_PROMPT_FIELD_NUMBER: _ClassVar[int]
    GOLDEN_QUERY_FIELD_NUMBER: _ClassVar[int]
    QUERY_TYPE_FIELD_NUMBER: _ClassVar[int]
    GOLDEN_RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    GOLDEN_EVAL_RESULTS_FIELD_NUMBER: _ClassVar[int]
    GOLDEN_ERROR_FIELD_NUMBER: _ClassVar[int]
    GENERATED_QUERY_FIELD_NUMBER: _ClassVar[int]
    GENERATED_RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    EVAL_RESULTS_JSON_FIELD_NUMBER: _ClassVar[int]
    GENERATED_ERROR_FIELD_NUMBER: _ClassVar[int]
    DATABASE_FIELD_NUMBER: _ClassVar[int]
    SCENARIO_ID_FIELD_NUMBER: _ClassVar[int]
    nl_prompt: str
    golden_query: str
    query_type: str
    golden_result_json: str
    golden_eval_results: str
    golden_error: str
    generated_query: str
    generated_result_json: str
    eval_results_json: str
    generated_error: str
    database: str
    scenario_id: str
    def __init__(self, nl_prompt: _Optional[str] = ..., golden_query: _Optional[str] = ..., query_type: _Optional[str] = ..., golden_result_json: _Optional[str] = ..., golden_eval_results: _Optional[str] = ..., golden_error: _Optional[str] = ..., generated_query: _Optional[str] = ..., generated_result_json: _Optional[str] = ..., eval_results_json: _Optional[str] = ..., generated_error: _Optional[str] = ..., database: _Optional[str] = ..., scenario_id: _Optional[str] = ...) -> None: ...

class ScoringRequest(_message.Message):
    __slots__ = ("scorer", "context")
    SCORER_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    scorer: ScorerSpec
    context: ScoringContext
    def __init__(self, scorer: _Optional[_Union[ScorerSpec, _Mapping]] = ..., context: _Optional[_Union[ScoringContext, _Mapping]] = ...) -> None: ...

class MetricScore(_message.Message):
    __slots__ = ("metric_name", "score", "comparison_logs", "success", "error_message", "execution_duration_ms", "result_json")
    METRIC_NAME_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    COMPARISON_LOGS_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    metric_name: str
    score: float
    comparison_logs: str
    success: bool
    error_message: str
    execution_duration_ms: int
    result_json: str
    def __init__(self, metric_name: _Optional[str] = ..., score: _Optional[float] = ..., comparison_logs: _Optional[str] = ..., success: _Optional[bool] = ..., error_message: _Optional[str] = ..., execution_duration_ms: _Optional[int] = ..., result_json: _Optional[str] = ...) -> None: ...

class ScoringResponse(_message.Message):
    __slots__ = ("scores",)
    SCORES_FIELD_NUMBER: _ClassVar[int]
    scores: _containers.RepeatedCompositeFieldContainer[MetricScore]
    def __init__(self, scores: _Optional[_Iterable[_Union[MetricScore, _Mapping]]] = ...) -> None: ...

class ReporterSpec(_message.Message):
    __slots__ = ("reporter_name", "config_json", "timeout_seconds")
    REPORTER_NAME_FIELD_NUMBER: _ClassVar[int]
    CONFIG_JSON_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    reporter_name: str
    config_json: str
    timeout_seconds: float
    def __init__(self, reporter_name: _Optional[str] = ..., config_json: _Optional[str] = ..., timeout_seconds: _Optional[float] = ...) -> None: ...

class ReportingRequest(_message.Message):
    __slots__ = ("reporter",)
    REPORTER_FIELD_NUMBER: _ClassVar[int]
    reporter: ReporterSpec
    def __init__(self, reporter: _Optional[_Union[ReporterSpec, _Mapping]] = ...) -> None: ...

class ReporterResult(_message.Message):
    __slots__ = ("reporter_name", "success", "result_json", "error_message")
    REPORTER_NAME_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    reporter_name: str
    success: bool
    result_json: str
    error_message: str
    def __init__(self, reporter_name: _Optional[str] = ..., success: _Optional[bool] = ..., result_json: _Optional[str] = ..., error_message: _Optional[str] = ...) -> None: ...

class ReportingResponse(_message.Message):
    __slots__ = ("result",)
    RESULT_FIELD_NUMBER: _ClassVar[int]
    result: ReporterResult
    def __init__(self, result: _Optional[_Union[ReporterResult, _Mapping]] = ...) -> None: ...

class SessionSummaryMessage(_message.Message):
    __slots__ = ("job_id", "summary_json")
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    SUMMARY_JSON_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    summary_json: str
    def __init__(self, job_id: _Optional[str] = ..., summary_json: _Optional[str] = ...) -> None: ...
