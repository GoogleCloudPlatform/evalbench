# NL2SQL Database Configuration

The Database Configuration is a YAML configuration file that sets up your database connection for the NL2SQL project. It is designed to be flexible, supporting multiple database types and authentication methods, including integration with Google Cloud Platform's Secret Manager.

**Note:** For `database_path`, this project mainly uses SQLAlchemy; please refer to [SQLAlchemy's Connections Documentation](https://docs.sqlalchemy.org/en/20/core/connections.html#basic-usage) for details. If you'd like to create a new Database class or `db` type that does not use SQLAlchemy, see [db.py](/evalbench/databases/db.py) for extending the DB class.

## File Structure

The YAML configuration file is structured as follows:

| **Key**                     | **Required**                 | **Description**                                                                                                                                       |
| --------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `db_type`                    | Yes                         | Specifies the type of database. Supported types include `postgres`, `mysql`, `alloydb`, `sqlserver`, and `sqlite`.                                               |
| `dialect`                    | Yes                         | SQL dialect used by the database. Often matches `db_type`, but may differ (e.g., `alloydb` uses `postgres` dialect).                                               |
| `database_name`             | Yes                          | The name of your database that is used for the default connection. This can be the default admin database (i.e. `postgres`) on the instance. This DB is only used to create databases needed for running evaluations.                                                                                                                            |
| `database_path`             | Yes                          | The path or instance reference to your database (e.g., cloud instance path, local path). Please see note above on database_path for more information on SQLAlchemy with more instructions on how to connect to local or GCP databases. *NOTE: For Sqlite, database_path is the directory that the .db files are found or stored in.*                                                              |
| `max_executions_per_minute` | No                           | Optional throttle limit for the number of executions per minute.                                                                                      |
| `user_name`                 | Conditionally (if needed)    | Required only for databases that need authentication (e.g., MySQL, PostgreSQL). Not needed for databases like SQLite or when using IAM authentication (username derived from ADC).                                 |
| `password`                  | Conditionally (if needed)    | The password for the database. Can be interchanged with `secret_manager_path` if you prefer using GCP Secret Manager for secure storage. Omit if using IAM authentication.           |
| `secret_manager_path`       | No (alternative to password) | An alternative to `password` that specifies the path to your secret in GCP Secret Manager. Use this if you prefer not to store the password directly. |
| `extension`                 | Conditionally (if needed)    | Required only for SQLite when datasets do not use the default `.db` extension. |
| `location`                  | Conditionally (if needed)    | Specifies the location of your dataset. Required for BigQuery. Default is "US".|


## Important Notes

- **Mandatory Fields:**
  - `db_type`, `database_name`, and `database_path` are required.
- **Authentication:**
  - For databases that require authentication (e.g., MySQL), provide both `user_name` and either `password` or `secret_manager_path`.
  - For databases like SQLite, omit `user_name` and `password`.
  - For IAM authentication (e.g., AlloyDB, CloudSQL), omit `user_name` and `password`. The user name will be derived from Application Default Credentials (ADC).
- **Optional Parameters:**
  - `max_executions_per_minute` is optional and can be adjusted according to your application's needs.
- **Secret Management:**
  - Use `secret_manager_path` if you want to keep your password secure by storing it in GCP Secret Manager instead of directly in the file.

> **Note:** Either `password` or `secret_manager_path` must be provided for databases that require authentication. For databases like SQLite, these keys can be omitted.

## Example Configuration

Below are a couple of examples of the YAML configuration file:

```yaml
db: mysql
database_name: my_database_name
database_path: my_gcp_project:my_gcp_region:my_gcp_mysql_instance
max_executions_per_minute: 180
user_name: my_cool_username
password: my_super_secure_password
```

```yaml
db_type: sqlite
database_name: <not-required-for-sqlite>
database_path: db_connections/my-dataset/
```
