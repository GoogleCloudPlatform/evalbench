from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class EvalConnectRequest(_message.Message):
    __slots__ = ("client_id", "streaming_eval", "bidirectional_stream")
    CLIENT_ID_FIELD_NUMBER: _ClassVar[int]
    STREAMING_EVAL_FIELD_NUMBER: _ClassVar[int]
    BIDIRECTIONAL_STREAM_FIELD_NUMBER: _ClassVar[int]
    client_id: str
    streaming_eval: bool
    bidirectional_stream: bool
    def __init__(self, client_id: _Optional[str] = ..., streaming_eval: bool = ..., bidirectional_stream: bool = ...) -> None: ...
