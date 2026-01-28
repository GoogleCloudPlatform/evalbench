from .generator import PromptGenerator
from util.interactutil import (
    extract_system_response,
    extract_user_response,
    segment_sql,
)
import json
from dataset.evalinteractinput import InteractionType

PG_ENCODE_PROMPT_TEMPLATE_WITH_RULES = """### Task: Ambiguity Resolution

You are a good ambiguity resolver. Your client is asking for clarification about the ambiguities in your Text-to-SQL task and you are required to answer this question based on your ground-truth SQL.

# All Labeled Ambiguity Points:
```json
[[amb_json]]
```

# Ground-truth SQL Segments:
[[SQL_Glot]]

The question from your client maybe about existing labeled Ambiguity Points above or about unlabeled ambiguity or even irrelevant. So, you should choose one action at this turn.
# Action Choices:
1. **labeled(term: str)**: When the question is about existing labeled Ambiguity Points above, use this action and fill in the relevant terms of that ambiguity. Format example: **labeled("Amb")**. Return as many as detected ambiguities in the question.
2. **unlabeled(segment: str)**: When the question is NOT about existing labeled Ambiguity Points BUT is still a valuable and important ambiguity that need to address, use this action and fill in the relevant SQL segment listed above. In unlabeled("…"), you pass a specific SQL fragment that addresses the ambiguity, for example: **unlabeled("ALTER")**.
3. **unanswerable()**: When you think this question is neither related to labeled Ambiguity Points above nor necessary to address, use this action. Format example: **unanswerable()**.

# Ask for Clarification Question:
[[clarification_Q]]
---
**Remember**: You MUST choose only **one action** listed above. You should NOT tell your client any thoughts about solution nor any ground-truth SQL information.

# Action Chosen:
"""


PG_DECODE_PROMPT_TEMPLATE_WITH_RULES = """### Task: Question Answering

You are a good Text-to-SQL engineer and provide Text-to-SQL task to your client. Your client is asking for clarification about the ambiguity of your Text-to-SQL task and you are required to answer this question based on your ground-truth SQL.

Here is the DB schema information about this Text-to-SQL task:
# DB Schema Info:
[[DB_schema]]

# All Labeled Ambiguity Points:
```json
[[amb_json]]
```

# Ground-truth SQL:
```postgresql
[[GT_SQL]]
```

# Ground-truth SQL Segments:
[[SQL_Glot]]

The question from your client maybe about existing labeled Ambiguity Points above or about unlabeled ambiguity or even irrelevant. So, you should choose one action at this turn.
# Action Choices:
1. **labeled(term: str)**: When the question is about existing labeled Ambiguity Points above, this action will be used and you will need to answer based on labeled information.
2. **unlabeled(segment: str)**: When the question is NOT about existing labeled Ambiguity Points BUT is still a valuable and important ambiguity that need to address, use this action will be used with relevant SQL segment listed above that can be used to answer client's question.
3. **unanswerable()**: When this question is neither related to labeled Ambiguity Points above nor necessary to address, this action will be used. And you should refuse to answer this question.

# Ask for Clarification Question:
[[clarification_Q]]

# Action Used:
[[Action]]

# The original clear text-to-SQL question:
```
[[clear_query]]
```

---
**Remember**: If you can do the following points well, you will get 10 thousand USD bonus!
1. You should generate response to answer the client's question based on the action used above. You can NOT directly give the original clear text-to-SQL task but can help you to answer question when you not sure.
2. You should NOT give any unfair information, for example: can **NOT** tell your client any thought steps leading to final solution nor any ground-truth SQL segments. You can **NOT** change or adjust any setting of the text-to-SQL task when answering questions. The response should be concise.
3. You should follow the format "[Fill-in-Your-Response]"; for example, if the action is "unanswerable()", you should respond: "Sorry, this question is out of scope, so I can not answer your question."

# Response
"""

_ENCODE_PROMPTS_BY_DIALECT = {
    "postgres": PG_ENCODE_PROMPT_TEMPLATE_WITH_RULES,
    "alloydb": PG_ENCODE_PROMPT_TEMPLATE_WITH_RULES,
}

_DECODE_PROMPTS_BY_DIALECT = {
    "postgres": PG_DECODE_PROMPT_TEMPLATE_WITH_RULES,
    "alloydb": PG_DECODE_PROMPT_TEMPLATE_WITH_RULES,
}


class DataAgentInteractUserGenerator(PromptGenerator):
    def __init__(self, db, promptgenerator_config):
        super().__init__(db, promptgenerator_config)
        self.db = db
        self.encode_prompt = _ENCODE_PROMPTS_BY_DIALECT[db.db_type]
        self.decode_prompt = _DECODE_PROMPTS_BY_DIALECT[db.db_type]

    def setup(self):
        pass

    def generate(self, eval_output):
        step_type = eval_output["step_type"]
        if step_type == InteractionType.VUSER_ENCODE:
            return self.generate_encoder(eval_output)
        elif step_type == InteractionType.VUSER_DECODE:
            return self.generate_decoder(eval_output)
        else:
            raise ValueError(f"Unknown step type: {step_type}")

    def generate_encoder(self, eval_output):
        item = eval_output["payload"]
        turn_i = item["turn"]
        schema = item["schema"]
        prompt = self.encode_prompt.replace(
            "[[clarification_Q]]",
            str(eval_output["disambiguation_question"]),
        )
        prompt = prompt.replace(
            "[[amb_json]]",
            "user_query_ambiguity: \n"
            + json.dumps(item["user_query_ambiguity"], indent=4)
            + "\n\nknowledge_ambiguity: \n"
            + json.dumps(item["knowledge_ambiguity"], indent=4),
        )

        sql_segs = ""
        cnt = 0
        for sol_sql_i in item["sol_sql"]:
            cnt += 1
            if cnt > 1:
                sql_segs = sql_segs + "\n===\n"
            for clause, text in segment_sql(sol_sql_i):
                sql_segs = sql_segs + clause + ":\n" + text + "\n\n"

        prompt = prompt.replace("[[SQL_Glot]]", sql_segs.strip())
        prompt = prompt.replace("[[DB_schema]]", schema)
        item["prompt"] = prompt
        item[f"prompt_encoder_{turn_i}"] = prompt
        return item

    def generate_decoder(self, eval_output):
        item = eval_output["payload"]

        if "prompt" in item:
            del item["prompt"]
        turn_i = item["turn"]
        schema = item["schema"]

        prompt = self.decode_prompt.replace(
            "[[clarification_Q]]",
            str(eval_output["disambiguation_question"]),
        )
        prompt = prompt.replace(
            "[[Action]]",
            extract_user_response(item["user_encoded_answer_" + str(turn_i)]),
        )

        prompt = prompt.replace(
            "[[amb_json]]",
            "user_query_ambiguity: \n"
            + json.dumps(item["user_query_ambiguity"], indent=4)
            + "\n\nknowledge_ambiguity: \n"
            + json.dumps(item["knowledge_ambiguity"], indent=4),
        )

        sql_segs = ""
        sol_sql_all = ""
        cnt = 0
        for sol_sql_i in item["sol_sql"]:
            sol_sql_all = sol_sql_all + sol_sql_i + "\n\n"
            cnt += 1
            if cnt > 1:
                sql_segs = sql_segs + "\n===\n"
            for clause, text in segment_sql(sol_sql_i):
                sql_segs = sql_segs + clause + ":\n" + text + "\n\n"
        prompt = prompt.replace("[[SQL_Glot]]", sql_segs.strip())
        prompt = prompt.replace("[[GT_SQL]]", sol_sql_all.strip())
        prompt = prompt.replace("[[DB_schema]]", schema)
        prompt = prompt.replace("[[clear_query]]", item["query"])

        item["prompt"] = prompt
        item[f"prompt_decoder_{turn_i}"] = prompt
        eval_output["prompt"] = item["prompt"]
        return item
