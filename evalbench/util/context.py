import contextvars

rpc_id_var = contextvars.ContextVar("rpc_id", default="default")
