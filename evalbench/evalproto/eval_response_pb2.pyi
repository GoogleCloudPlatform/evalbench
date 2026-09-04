from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class EvalResponse(_message.Message):
    __slots__ = ("response", "session_id")
    RESPONSE_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    response: str
    session_id: str
    def __init__(self, response: _Optional[str] = ..., session_id: _Optional[str] = ...) -> None: ...
