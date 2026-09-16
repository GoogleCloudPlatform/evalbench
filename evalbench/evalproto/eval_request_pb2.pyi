import datetime

from google.protobuf import duration_pb2 as _duration_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PingRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: str
    def __init__(self, id: _Optional[str] = ...) -> None: ...

class DialectBasedSQLStatements(_message.Message):
    __slots__ = ("sql_statements",)
    SQL_STATEMENTS_FIELD_NUMBER: _ClassVar[int]
    sql_statements: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, sql_statements: _Optional[_Iterable[str]] = ...) -> None: ...

class EvalInputRequest(_message.Message):
    __slots__ = ("id", "nl_prompt", "query_type", "database", "dialects", "golden_sql", "eval_query", "setup_sql", "cleanup_sql", "tags", "other", "sql_generator_error", "sql_generator_time", "generated_sql", "job_id", "trace_id", "payload", "conversation_id", "generated_nl_response")
    class GoldenSqlEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: DialectBasedSQLStatements
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[DialectBasedSQLStatements, _Mapping]] = ...) -> None: ...
    class EvalQueryEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: DialectBasedSQLStatements
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[DialectBasedSQLStatements, _Mapping]] = ...) -> None: ...
    class SetupSqlEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: DialectBasedSQLStatements
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[DialectBasedSQLStatements, _Mapping]] = ...) -> None: ...
    class CleanupSqlEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: DialectBasedSQLStatements
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[DialectBasedSQLStatements, _Mapping]] = ...) -> None: ...
    class OtherEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    NL_PROMPT_FIELD_NUMBER: _ClassVar[int]
    QUERY_TYPE_FIELD_NUMBER: _ClassVar[int]
    DATABASE_FIELD_NUMBER: _ClassVar[int]
    DIALECTS_FIELD_NUMBER: _ClassVar[int]
    GOLDEN_SQL_FIELD_NUMBER: _ClassVar[int]
    EVAL_QUERY_FIELD_NUMBER: _ClassVar[int]
    SETUP_SQL_FIELD_NUMBER: _ClassVar[int]
    CLEANUP_SQL_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    OTHER_FIELD_NUMBER: _ClassVar[int]
    SQL_GENERATOR_ERROR_FIELD_NUMBER: _ClassVar[int]
    SQL_GENERATOR_TIME_FIELD_NUMBER: _ClassVar[int]
    GENERATED_SQL_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    CONVERSATION_ID_FIELD_NUMBER: _ClassVar[int]
    GENERATED_NL_RESPONSE_FIELD_NUMBER: _ClassVar[int]
    id: int
    nl_prompt: str
    query_type: str
    database: str
    dialects: _containers.RepeatedScalarFieldContainer[str]
    golden_sql: _containers.MessageMap[str, DialectBasedSQLStatements]
    eval_query: _containers.MessageMap[str, DialectBasedSQLStatements]
    setup_sql: _containers.MessageMap[str, DialectBasedSQLStatements]
    cleanup_sql: _containers.MessageMap[str, DialectBasedSQLStatements]
    tags: _containers.RepeatedScalarFieldContainer[str]
    other: _containers.ScalarMap[str, str]
    sql_generator_error: str
    sql_generator_time: float
    generated_sql: str
    job_id: str
    trace_id: str
    payload: str
    conversation_id: str
    generated_nl_response: str
    def __init__(self, id: _Optional[int] = ..., nl_prompt: _Optional[str] = ..., query_type: _Optional[str] = ..., database: _Optional[str] = ..., dialects: _Optional[_Iterable[str]] = ..., golden_sql: _Optional[_Mapping[str, DialectBasedSQLStatements]] = ..., eval_query: _Optional[_Mapping[str, DialectBasedSQLStatements]] = ..., setup_sql: _Optional[_Mapping[str, DialectBasedSQLStatements]] = ..., cleanup_sql: _Optional[_Mapping[str, DialectBasedSQLStatements]] = ..., tags: _Optional[_Iterable[str]] = ..., other: _Optional[_Mapping[str, str]] = ..., sql_generator_error: _Optional[str] = ..., sql_generator_time: _Optional[float] = ..., generated_sql: _Optional[str] = ..., job_id: _Optional[str] = ..., trace_id: _Optional[str] = ..., payload: _Optional[str] = ..., conversation_id: _Optional[str] = ..., generated_nl_response: _Optional[str] = ...) -> None: ...

class UserAction(_message.Message):
    __slots__ = ("action_type", "prompt", "file_path", "cursor_start", "cursor_end")
    ACTION_TYPE_FIELD_NUMBER: _ClassVar[int]
    PROMPT_FIELD_NUMBER: _ClassVar[int]
    FILE_PATH_FIELD_NUMBER: _ClassVar[int]
    CURSOR_START_FIELD_NUMBER: _ClassVar[int]
    CURSOR_END_FIELD_NUMBER: _ClassVar[int]
    action_type: str
    prompt: str
    file_path: str
    cursor_start: int
    cursor_end: int
    def __init__(self, action_type: _Optional[str] = ..., prompt: _Optional[str] = ..., file_path: _Optional[str] = ..., cursor_start: _Optional[int] = ..., cursor_end: _Optional[int] = ...) -> None: ...

class EvalCodeInputRequest(_message.Message):
    __slots__ = ("id", "patch", "user_action", "verification_command", "description", "application_context", "current_file_content", "generated_code", "dbcodegen_time", "dbcodegen_error", "job_id", "golden_code", "build_command")
    ID_FIELD_NUMBER: _ClassVar[int]
    PATCH_FIELD_NUMBER: _ClassVar[int]
    USER_ACTION_FIELD_NUMBER: _ClassVar[int]
    VERIFICATION_COMMAND_FIELD_NUMBER: _ClassVar[int]
    DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    APPLICATION_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    CURRENT_FILE_CONTENT_FIELD_NUMBER: _ClassVar[int]
    GENERATED_CODE_FIELD_NUMBER: _ClassVar[int]
    DBCODEGEN_TIME_FIELD_NUMBER: _ClassVar[int]
    DBCODEGEN_ERROR_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    GOLDEN_CODE_FIELD_NUMBER: _ClassVar[int]
    BUILD_COMMAND_FIELD_NUMBER: _ClassVar[int]
    id: str
    patch: str
    user_action: UserAction
    verification_command: str
    description: str
    application_context: str
    current_file_content: str
    generated_code: str
    dbcodegen_time: _duration_pb2.Duration
    dbcodegen_error: str
    job_id: str
    golden_code: str
    build_command: str
    def __init__(self, id: _Optional[str] = ..., patch: _Optional[str] = ..., user_action: _Optional[_Union[UserAction, _Mapping]] = ..., verification_command: _Optional[str] = ..., description: _Optional[str] = ..., application_context: _Optional[str] = ..., current_file_content: _Optional[str] = ..., generated_code: _Optional[str] = ..., dbcodegen_time: _Optional[_Union[datetime.timedelta, _duration_pb2.Duration, _Mapping]] = ..., dbcodegen_error: _Optional[str] = ..., job_id: _Optional[str] = ..., golden_code: _Optional[str] = ..., build_command: _Optional[str] = ...) -> None: ...

class EvalInteractInputRequest(_message.Message):
    __slots__ = ("id", "amb_user_query", "query_type", "database", "dialects", "tags", "payload", "job_id", "trace_id")
    ID_FIELD_NUMBER: _ClassVar[int]
    AMB_USER_QUERY_FIELD_NUMBER: _ClassVar[int]
    QUERY_TYPE_FIELD_NUMBER: _ClassVar[int]
    DATABASE_FIELD_NUMBER: _ClassVar[int]
    DIALECTS_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    id: int
    amb_user_query: str
    query_type: str
    database: str
    dialects: _containers.RepeatedScalarFieldContainer[str]
    tags: _containers.RepeatedScalarFieldContainer[str]
    payload: str
    job_id: str
    trace_id: str
    def __init__(self, id: _Optional[int] = ..., amb_user_query: _Optional[str] = ..., query_type: _Optional[str] = ..., database: _Optional[str] = ..., dialects: _Optional[_Iterable[str]] = ..., tags: _Optional[_Iterable[str]] = ..., payload: _Optional[str] = ..., job_id: _Optional[str] = ..., trace_id: _Optional[str] = ...) -> None: ...
