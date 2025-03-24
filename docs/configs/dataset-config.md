# NL2SQL Evaluation Items Configuration

This JSON dataset / prompts file contains a list of evaluation items to run. Each item represents a test case that defines a natural language prompt, the corresponding expected SQL modifications (golden SQL), and the SQL queries for setting up, evaluating, and cleaning up the database. These evaluation items are used to validate that the system correctly generates SQL statements based on natural language inputs.

---

## File Structure

Each evaluation item in the JSON file includes the following keys:

| **Key**        | **Required** | **Description**                                                                                                                                                                                                     |
| -------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`           | Yes          | A unique identifier for the evaluation item.                                                                                                                                                                      |
| `nl_prompt`    | Yes          | The natural language instruction for the SQL Generation. For example, "Write a query to find the fastest plane.", or "Update the labels table to include a json field called comments_description."                                                                   |
| `query_type`   | Yes          | The type of SQL query. This can be `dql` (Data Query Language) for running queries, `dml` (Data Manipulation Language) for modifying data such as insertions or deletions to the database and `ddl` (Data Definition Language) used to test the model's ability to modify and manipulate the schema (such as creating or dropping tables, altering columns, adding constraints, etc.)                                                                                                                                                  |
| `database`     | Yes          | The target database name (e.g., `db_blog`).                                                                                                                                                                         |
| `dialects`     | Yes          | An array of SQL dialects against which the evaluation can run (e.g., `postgres`, `mysql`, `sqlserver`, `sqlite`). See [db-config documentation](/docs/configs/db-config.md) for more info on supported dialects.                                                                                                 |
| `golden_sql`   | Yes          | An object mapping each dialect to a list of expected (golden) SQL queries that represent the correct output for the given prompt.                                                                                  |
| `eval_query`   | Optional     | An object mapping each dialect to a list of SQL queries used to evaluate whether the generated SQL produced the desired changes (e.g., verifying column existence or specific constraints) This is best used for DML queries such as INSERT, UPDATE, DELETE where you want to ensure that a certain record is in the desired end state.                   |
| `setup_sql`    | Optional     | An object mapping each dialect to a list of SQL queries that can be run before the qenerated_query is run to setup a specific test case.                                                                                        |
| `cleanup_sql`  | Optional     | An object mapping each dialect to a list of SQL queries used to revert changes or clean up the database after evaluation.                                                                                           |
| `tags`         | Optional     | An array of tags to categorize the evaluation item (e.g., `DDL`, `difficulty: simple`, `ALTER`, `ADD_COLUMN`, etc.).                                                                                                      |
| `other`        | Optional     | A flexible field where you can include any additional metadata, custom information, or reporting details. This field can be used for further context, logging, or any extra information that may be helpful.     |

---

## Important Notes

- **Multiple Dialects:** Each SQL-related key (`golden_sql`, `eval_query`, `setup_sql`, `cleanup_sql`) maps dialects to their corresponding queries. This ensures that the evaluation items can be used across different database systems.
- **Custom Metadata:** The `other` field is optional and can contain any additional information you deem necessary for reporting or contextual purposes.
- **Structured Testing:** By defining separate SQL statements for setup, evaluation, and cleanup, this configuration supports robust testing of DDL operations.

---

## Example Entry

Below is an excerpt from the JSON file illustrating one evaluation item:

```json
{
  "id": 43,
  "nl_prompt": "Update the first name to Joe for user joesmith@google.com",
  "query_type": "ddl",
  "database": "db_blog",
  "dialects": [
    "postgres",
    "mysql",
    "sqlserver",
    "sqlite"
  ],
  "golden_sql": {
    "postgres": [
      "UPDATE users SET first_name = 'Joe' WHERE email = 'joesmith@google.com';"
    ],
    "mysql": [
      "UPDATE users SET first_name = 'Joe' WHERE email = 'joesmith@google.com';"
    ],
    "sqlserver": [
      "UPDATE users SET first_name = 'Joe' WHERE email = 'joesmith@google.com';"
    ],
    "sqlite": [
      "UPDATE users SET first_name = 'Joe' WHERE email = 'joesmith@google.com';"
    ]
  },
  "eval_query": {
    "postgres": [
      "SELECT first_name FROM users WHERE email = 'joesmith@google.com';"
    ],
    "mysql": [
      "SELECT first_name FROM users WHERE email = 'joesmith@google.com';"
    ],
    "sqlserver": [
      "SELECT first_name FROM users WHERE email = 'joesmith@google.com';"
    ],
    "sqlite": [
      "SELECT first_name FROM users WHERE email = 'joesmith@google.com';"
    ]
  },
  "tags": [
    "DQL",
    "difficulty: simple",
    "UPDATE",
  ],
  "other": {
    "author": "Gemini",
    "include-in-monthly-eval-report": True
  }
}
```

> **Note:** In this example, the `other` field is used to store additional metadata that can be customized as needed for reporting and further analysis.
