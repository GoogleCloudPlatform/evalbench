import json
import copy


class EvalDeaRequest:
    def __init__(self, raw_dict: dict, job_id: str = "", trace_id: str = ""):
        """
        Initializes an EvalDeaRequest from a parsed JSON dictionary.
        """
        # Store the raw dictionary so process_scenario can read max_turns and the plan
        self.scenario = raw_dict

        # Extract top-level identification
        self.id = str(raw_dict.get("id", "-1"))
        self.job_id = job_id
        self.trace_id = trace_id

        self.nl_prompt = raw_dict.get("starting_prompt", "")

        # Ensure the stringified payload is ready for the A2A connection
        self.payload_str = json.dumps(raw_dict)
        self.payload = self.payload_str

        self.agent_results = []
        self.scoring_results = []

    @classmethod
    def init_from_proto(cls, proto):
        """Unpacks the Protobuf from Google3 back into the object."""
        payload_str = getattr(proto, "payload", "{}")
        try:
            raw_dict = json.loads(payload_str)
        except json.JSONDecodeError:
            raw_dict = {}

        raw_dict["id"] = str(getattr(proto, "id", "-1"))

        return cls(
            raw_dict=raw_dict,
            job_id=getattr(proto, "job_id", ""),
            trace_id=getattr(proto, "trace_id", ""),
        )

    def to_proto(self):
        """Packs the object into the Protobuf to send to Google3."""
        # Note: You must import eval_request_pb2 here to prevent circular dependencies
        from evalproto import eval_request_pb2

        return eval_request_pb2.EvalInputRequest(
            id=int(self.id) if self.id.isdigit() else 0,
            payload=self.payload_str,
            # We map starting_prompt to nl_prompt for backwards compatibility
            nl_prompt=self.nl_prompt
        )

    def copy(self):
        return copy.deepcopy(self)
