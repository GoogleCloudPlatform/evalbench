from .generator import PromptGenerator
from databases import DB

SQLITE_PROMPT_TEMPLATE_WITH_RULES = """You are a SQLite SQL expert.

The database structure is defined by the following table schemas
 (comments after '--' provide additional column descriptions):

**************************
{SCHEMA}
**************************

Please generate a SQLite SQL query for the following question following the rules:
- Output the query only without any explanation.
- Wrap the final generated query in
```sql
QUERY HERE
```
- Always use quotes around the table and column names.

Additional rules to generate correct SQLite SQL dialect:

- Use Table Aliases: Employ aliases to prevent conflicts with duplicate table names.
- Strict Adherence to Schema: You cannot make up any tables or columns that are not explicitly
listed in the provided schema.
- Manage Nulls with ORDER BY: Employ `NULLS LAST` in ORDER BY clauses to control the placement
of null values.
- Arithmetic Operators: Prioritize the use of basic arithmetic operators (+, -, *, /) for
calculations whenever possible.
Only use specialized SQLite functions if absolutely necessary for the desired results.
- Follow SQLite Dialect: Adhere to SQLite syntax, data types, and available functions.
- Choose Join Types Carefully: Select appropriate join types (INNER, LEFT, RIGHT, FULL OUTER)
based on desired relationships.
- Booleans in HAVING: Employ valid boolean expressions within the HAVING clause.
- Type-Aware Functions: Select SQLite functions compatible with the data types in use.
- Cast for Compatibility: Cast data types when necessary for function compatibility or specific manipulations.

Think step by step about generating correct SQLite SQL result!

**************************

Here is the natural language question for generating SQL:
{USER_PROMPT}
"""


PG_PROMPT_TEMPLATE_WITH_RULES = """You are a Postgres SQL expert.

The database structure is defined by the following table schemas:

**************************
{SCHEMA}
**************************

Please generate a Postgres SQL query for the following question following these rules:
- Output the query only without any explanation.
- Do not use markdown code blocks around the outputted query.
- Always use quotes around table and column names.

SQL generation rules:
- Use aliases for tables to avoid ambiguity.
- Only use tables and columns from the provided schema.
- Ensure the generated SQL query is valid and executable.
- Choose appropriate join types for relationships between tables.
- Use functions and operators compatible with the data types of columns.

Think step by step about generating a correct Postgres SQL query!

**************************

Here is the natural language question for generating SQL:
{USER_PROMPT}
"""

MYSQL_PROMPT_TEMPLATE_WITH_RULES = """You are a MySQL SQL expert.

The database structure is defined by the following table schemas:

**************************
{SCHEMA}
**************************

Please generate a MySQL SQL query for the following question following these rules:
- Output the query only without any explanation.
- Do not use markdown code blocks around the outputted query.
- Always use quotes around table and column names.

SQL generation rules:
- Use aliases for tables to avoid ambiguity.
- Only use tables and columns from the provided schema.
- Ensure the generated SQL query is valid and executable.
- Choose appropriate join types for relationships between tables.
- Use functions and operators compatible with the data types of columns.

Think step by step about generating a correct MySQL SQL query!

**************************

Here is the natural language question for generating SQL:
{USER_PROMPT}
"""

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

BIGQUERY_PROMPT_TEMPLATE_WITH_RULES = """You are a BigQuery SQL expert.

The database structure is defined by the following table schemas:

**************************
{SCHEMA}
**************************

Please generate a BigQuery SQL query for the following question following these rules:
- Output the query only without any explanation.
- Do not use markdown code blocks around the outputted query.
- Always use quotes around table and column names.

SQL generation rules:
- Use aliases for tables to avoid ambiguity.
- Only use tables and columns from the provided schema.
- Ensure the generated SQL query is valid and executable.
- Choose appropriate join types for relationships between tables.
- Use functions and operators compatible with the data types of columns.

Think step by step about generating a correct BigQuery SQL query!

**************************

Here is the natural language question for generating SQL:
{USER_PROMPT}
"""

MONGODB_PROMPT_TEMPLATE_WITH_RULES = """You are a MongoDB expert.

The database structure is defined by the following collection schemas:

**************************
{SCHEMA}
**************************

Please generate a MongoDB query (in JSON format) for the following question following these rules:
- Output the query as a valid JSON string only without any explanation.
- The JSON should be in one of the following formats:
  - Find: {{"find": "collection_name", "filter": {{...}}, "projection": {{...}}}}
  - Aggregate: {{"aggregate": "collection_name", "pipeline": [...]}}
  - Count: {{"count": "collection_name", "filter": {{...}}}}
- Do not use markdown code blocks around the outputted query.

MongoDB generation rules:
- Use standard MongoDB operators (e.g., $match, $group, $lookup, $project).
- Ensure the JSON is valid and executable by pymongo.
- Only use collections and fields from the provided schema.

Think step by step about generating a correct MongoDB query!

**************************

Here is the natural language question for generating MongoDB query:
{USER_PROMPT}
"""

SPANNER_GSQL_PROMPT_TEMPLATE_WITH_RULES = """You are a Cloud Spanner GoogleSQL expert.

The database structure is defined by the following table schemas:

**************************
{SCHEMA}
**************************

Please generate a GoogleSQL query for Cloud Spanner for the following question following these rules:
- Output the query only without any explanation.
- Do not use markdown code blocks around the outputted query.
- Use backticks (`) around table and column names if they contain spaces or special characters, but avoid unnecessary quoting.
- Spanner GoogleSQL does NOT support double quotes for identifiers.

SQL generation rules:
- Use aliases for tables to avoid ambiguity.
- Ensure that you are selecting the correct columns based on the provided schema.

Think step by step about generating a correct GoogleSQL query!

**************************

Here is the natural language question for generating SQL:
{USER_PROMPT}"""

SPANNER_PG_PROMPT_TEMPLATE_WITH_RULES = """You are a Cloud Spanner PostgreSQL expert.

The database structure is defined by the following table schemas:

**************************
{SCHEMA}
**************************

Please generate a PostgreSQL query for Cloud Spanner for the following question following these rules:
- Output the query only without any explanation.
- Do not use markdown code blocks around the outputted query.
- Always use quotes around table and column names.

SQL generation rules:
- Use aliases for tables to avoid ambiguity.
- Ensure that you are selecting the correct columns based on the provided schema.

Think step by step about generating a correct PostgreSQL query!

**************************

Here is the natural language question for generating SQL:
{USER_PROMPT}"""

_PROMPTS_BY_DIALECT = {
    "sqlite": SQLITE_PROMPT_TEMPLATE_WITH_RULES,
    "postgres": PG_PROMPT_TEMPLATE_WITH_RULES,
    "mysql": MYSQL_PROMPT_TEMPLATE_WITH_RULES,
    "sqlserver": SQLSERVER_PROMPT_TEMPLATE_WITH_RULES,
    "bigquery": BIGQUERY_PROMPT_TEMPLATE_WITH_RULES,
    "mongodb": MONGODB_PROMPT_TEMPLATE_WITH_RULES,
    "spanner_pg": SPANNER_PG_PROMPT_TEMPLATE_WITH_RULES,
    "spanner_gsql": SPANNER_GSQL_PROMPT_TEMPLATE_WITH_RULES,
}


class SQLGenBasePromptGenerator(PromptGenerator):
    def __init__(self, db: DB, promptgenerator_config):
        super().__init__(db, promptgenerator_config)
        self.db = db

        # Dialect-aware prompt selection for Spanner
        if db.db_type == "spanner":
            dialect = db.config.get("dialect", "").lower()
            if "pg" in dialect or "postgres" in dialect:
                self.base_prompt = SPANNER_PG_PROMPT_TEMPLATE_WITH_RULES
            else:
                self.base_prompt = SPANNER_GSQL_PROMPT_TEMPLATE_WITH_RULES
        else:
            self.base_prompt = _PROMPTS_BY_DIALECT[db.db_type]

    def setup(self):
        self.schema = self.db.get_ddl_from_db()

    def generate(self, item):
        item["prompt"] = self.get_prompt(item)
        return item

    def get_prompt(self, item):
        return self.base_prompt.format(
            SCHEMA=self.schema, USER_PROMPT=item["nl_prompt"]
        )
