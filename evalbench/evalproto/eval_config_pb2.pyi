from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Google3Resources(_message.Message):
    __slots__ = ("address", "content")
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    address: str
    content: bytes
    def __init__(self, address: _Optional[str] = ..., content: _Optional[bytes] = ...) -> None: ...

class EvalConfigRequest(_message.Message):
    __slots__ = ("yaml_config", "resources")
    YAML_CONFIG_FIELD_NUMBER: _ClassVar[int]
    RESOURCES_FIELD_NUMBER: _ClassVar[int]
    yaml_config: bytes
    resources: _containers.RepeatedCompositeFieldContainer[Google3Resources]
    def __init__(self, yaml_config: _Optional[bytes] = ..., resources: _Optional[_Iterable[_Union[Google3Resources, _Mapping]]] = ...) -> None: ...
