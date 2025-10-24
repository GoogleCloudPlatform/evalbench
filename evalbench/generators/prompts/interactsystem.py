from .generator import PromptGenerator
from dataset.evalinteractinput import InteractionType
from util.interactutil import (
    extract_system_response,
    extract_user_response,
)


PG_PROMPT_TEMPLATE_WITH_RULES = """You are a good data scientist with great SQL writing ability. You have a DB called "[[DB_name]]". You are given the DB schema information below:

# DB Schema Info:
[[DB_schema]]

And you are given some useful external knowledge about this DB below:
# External Knowledge:
```json
[[external_kg]]
```

# Instructions:
You are a good data scientist who is tasked with generating PostgreSQL to solve the user task below. However, the user’s query may not be clear enough. Then you need to ask for clarification about these ambiguity in user task below. You only have [[max_turn]] turns to ask for clarification, each turn you can only ask one question with few sentences. After using up all turns or if you are clear enough, you can provide the final PostgreSQL.

You have the following choice at each turn:
1. **Ask for Clarification**: You can only ask **ONE** question each time! Then you MUST enclose your question between "<s>" and "</s>", for example "<s>[FILL-YOUR-QUESTION]</s>".
2. **Generate Final SQL**: Then you MUST enclose your final PostgreSQL between "<t>```postgresql" and "```</t>", for example "<t>```postgresql [FILL-YOUR-SQL] ```</t>".

NOTE: If you think you have asked enough questions or used up all turns, you MUST provide the final PostgreSQL about the Text-to-SQL task!

# User Task:
[[user_query]]

### Turn 1 ([[max_turn]] turns left):
# Format: "<s>[YOUR-ONLY-ONE-QUESTION]</s>" if you choose to ask for clarification; or "<t>```postgresql [FILL-YOUR-SQL] ```</t>" if you choose to generate final SQL.
- You: """

_PROMPTS_BY_DIALECT = {
    "postgres": PG_PROMPT_TEMPLATE_WITH_RULES,
}


class InteractSystemGenerator(PromptGenerator):
    def __init__(self, db, promptgenerator_config):
        super().__init__(db, promptgenerator_config)
        self.db = db
        self.base_prompt = _PROMPTS_BY_DIALECT[db.db_type]

    def setup(self):
        pass

    def generate(self, eval_output):
        item = eval_output["payload"]
        turn_i = item["turn"]
        if turn_i == 1:
            knowledge = item["knowledge"]
            schema = item["schema"]
            question = item.get("amb_user_query", "")
            db_name = item.get("selected_database", "")
            prompt = self.base_prompt.replace("[[user_query]]", question)
            prompt = prompt.replace("[[DB_name]]", db_name)
            prompt = prompt.replace("[[max_turn]]", str(item["max_turn"]))
            prompt = prompt.replace("[[DB_schema]]", schema)
            prompt = prompt.replace("[[external_kg]]", knowledge)
            item["prompt"] = prompt
            item[f"prompt_turn_{turn_i}"] = prompt
        else:
            max_turn = item["max_turn"]
            prompt = item[f"prompt_turn_{turn_i - 1}"]
            response_prev = item[f"prediction_turn_{turn_i - 1}"]
            response_user_prev = item[f"user_answer_{turn_i - 1}"]
            if max_turn >= turn_i:
                sys_response, terminate_flag = extract_system_response(response_prev)
                if terminate_flag:
                    item["Terminate_flg"] = True
                user_response = extract_user_response(response_user_prev)
                prompt = (
                    prompt
                    + sys_response
                    + "\n- User: "
                    + user_response
                    + '\n\n### Turn [[turn_i]] ([[turn_left]] turns left): \n# Format: "<s>[YOUR-ONLY-ONE-QUESTION]]</s>" if you choose to ask for clarification; or "<t>```postgresql [YOUR-SQL] ```</t>" if you choose to generate final SQL.\n- You: '.replace(
                        "[[turn_i]]", str(turn_i)
                    ).replace(
                        "[[turn_left]]", str(max_turn - turn_i + 1)
                    )
                )
                item["prompt"] = prompt
                item[f"prompt_turn_{turn_i}"] = prompt
        eval_output["prompt"] = item["prompt"]
        return eval_output
