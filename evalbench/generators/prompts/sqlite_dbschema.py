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


class SQLiteDBSchemaGenerator(PromptGenerator):

    def __init__(self, db: DB, promptgenerator_config):
        super().__init__(db, promptgenerator_config)
        self.db = db

    def setup(self):
        self.schema = self.db.get_ddl_from_db()

    def generate(self, prompt):
        return SQLITE_PROMPT_TEMPLATE_WITH_RULES.format(
            SCHEMA=self.schema, USER_PROMPT=prompt
        )
