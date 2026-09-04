import os
import sys

# Append evalproto directory to sys.path so generated protobuf stubs can import peer protos.
_proto_dir = os.path.dirname(__file__)
if _proto_dir not in sys.path:
    sys.path.append(_proto_dir)
