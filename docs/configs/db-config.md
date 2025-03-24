# NL2SQL Database Configuration

The Database Configuration is a YAML configuration file that sets up your database connection for the NL2SQL project. It is designed to be flexible, supporting multiple database types and authentication methods, including integration with Google Cloud Platform's Secret Manager.

**Note:** For `database_path`, this project mainly uses SQLAlchemy; please refer to [SQLAlchemy's Connections Documentation](https://docs.sqlalchemy.org/en/20/core/connections.html#basic-usage) for details. If you'd like to create a new Database class or `db` type that does not use SQLAlchemy, see [db.py](/evalbench/databases/db.py) for extending the DB class.

## File Structure

The YAML configuration file is structured as follows:

| **Key**                     | **Required**                 | **Description**                                                                                                                                       |
| --------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `db_type`                    | Yes                         | Specifies the type of database. Supported types include `postgres`, `mysql`, `sqlserver`, and `sqlite`.                                               |
| `database_name`             | Yes                          | The name of your database.                                                                                                                            |
| `database_path`             | Yes                          | The path or instance reference to your database (e.g., cloud instance path, local path).                                                              |
| `max_executions_per_minute` | No                           | Optional throttle limit for the number of executions per minute.                                                                                      |
| `user_name`                 | Conditionally (if needed)    | Required only for databases that need authentication (e.g., MySQL, PostgreSQL). Not needed for databases like SQLite.                                 |
| `password`                  | Conditionally (if needed)    | The password for the database. Can be interchanged with `secret_manager_path` if you prefer using GCP Secret Manager for secure storage.              |
| `secret_manager_path`       | No (alternative to password) | An alternative to `password` that specifies the path to your secret in GCP Secret Manager. Use this if you prefer not to store the password directly. |

## Important Notes

- **Mandatory Fields:**
  - `db`, `database_name`, and `database_path` are required.
- **Authentication:**
  - For databases that require authentication (e.g., MySQL), provide both `user_name` and either `password` or `secret_manager_path`.
  - For databases like SQLite, omit `user_name` and `password`.
- **Optional Parameters:**
  - `max_executions_per_minute` is optional and can be adjusted according to your application's needs.
- **Secret Management:**
  - Use `secret_manager_path` if you want to keep your password secure by storing it in GCP Secret Manager instead of directly in the file.

> **Note:** Either `password` or `secret_manager_path` must be provided for databases that require authentication. For databases like SQLite, these keys can be omitted.

## Example Configuration

Below is an example of the YAML configuration file:

```yaml
db: mysql
database_name: my_database_name
database_path: my_gcp_project:my_gcp_region:my_gcp_mysql_instance
max_executions_per_minute: 180
user_name: my_cool_username
password: my_super_secure_password
```
