from .generator import PromptGenerator
from databases import DB

SQLSERVER_PROMPT_TEMPLATE_WITH_RULES = """You are a SQLServer SQL expert.

The database structure is defined by the following table schemas:

**************************
{SCHEMA}
**************************

Please generate a SQLServer SQL query for the following question following these rules:
- Output the query only without any explanation.
- Do not use markdown code blocks around the outputted query.
- Always use quotes around table and column names.

SQL generation rules:
- Use aliases for tables to avoid ambiguity.
- Only use tables and columns from the provided schema.
- Ensure the generated SQL query is valid and executable.
- Choose appropriate join types for relationships between tables.
- Use functions and operators compatible with the data types of columns.

Think step by step about generating a correct SQLServer SQL query!

**************************

Here is the natural language question for generating SQL:
{USER_PROMPT}
"""


class SQLServerDBSchemaGenerator(PromptGenerator):

    def __init__(self, db: DB, promptgenerator_config):
        super().__init__(db, promptgenerator_config)
        self.schema = db.get_ddl_from_db()

    def generate(self, prompt):
        return SQLSERVER_PROMPT_TEMPLATE_WITH_RULES.format(
            SCHEMA=self.schema, USER_PROMPT=prompt
        )
