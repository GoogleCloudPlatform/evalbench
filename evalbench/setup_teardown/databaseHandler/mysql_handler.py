import os
import csv
import random
import string
from typing import Any, Tuple, List
from .db_handler import DBHandler
from databases import get_database

CREATE_USER_QUERY = [
    'CREATE USER IF NOT EXISTS "tmp_dql"@"%" IDENTIFIED BY "nl2sql";',
    'GRANT USAGE ON *.* TO "tmp_dql"@"%";',
    'GRANT SELECT ON `{database}`.* TO "tmp_dql"@"%";',
    'FLUSH PRIVILEGES;',
    'CREATE USER IF NOT EXISTS "tmp_dml"@"%" IDENTIFIED BY "nl2sql";',
    'GRANT USAGE ON *.* TO "tmp_dml"@"%";',
    'GRANT SELECT, INSERT, UPDATE, DELETE ON `{database}`.* TO "tmp_dml"@"%";',
    'FLUSH PRIVILEGES;',
]


class MYSQLHandler(DBHandler):

    def __init__(self, db_config: dict):
        self.db_engine = "mysql"
        self.db_config = db_config

    def drop_all_tables(self):
        drop_all_tables_query = [
            f"DROP DATABASE IF EXISTS {self.db_config['database_name']};",
            f"CREATE DATABASE {self.db_config['database_name']};"
        ]
        return self.execute(drop_all_tables_query)

    def create_user(self, db_config: dict):
        for query in CREATE_USER_QUERY:
            query = query.format(database=db_config['database_name'])
            result, error = self.execute([query])
            if error:
                return error

    def create_schema_statements(self, schema, excluded_columns):
        excluded_columns = excluded_columns or set()
        create_statements = []

        for table in schema.tables:
            table_name = table.table
            primary_key = None
            columns = []

            for column in table.columns:
                column_def = f"`{column.column}` {column.data_type}"
                if "AUTO_INCREMENT" in column.data_type.upper():
                    primary_key = column.column
                if column.column not in excluded_columns:
                    columns.append(column_def)

            columns_str = ",\n    ".join(columns)

            if primary_key:
                columns_str += f",\n    PRIMARY KEY (`{primary_key}`)"

            create_statement = f"CREATE TABLE {table_name} (\n    {columns_str}\n);"
            create_statements.append(create_statement)

        return create_statements

    def create_insert_statements(self, data_directory):
        table_inserts = {}

        for filename in os.listdir(data_directory):
            if filename.endswith(".csv"):
                table_name = filename[:-4]

                if table_name not in table_inserts:
                    table_inserts[table_name] = []

                with open(os.path.join(data_directory, filename), 'r') as csvfile:
                    reader = csv.reader(csvfile)
                    for row in reader:
                        values = ", ".join([f"{value}" for value in row])
                        table_inserts[table_name].append(f"({values})")

        insertion_strings = []
        for table_name, values_list in table_inserts.items():
            values_str = ",\n".join(values_list)
            insert_statement = f"INSERT INTO `{table_name}` VALUES {values_str};"
            insertion_strings.append(insert_statement)

        return insertion_strings

    def create_temp_databases(self, num_database: int):
        commands = []
        db_names = []

        def generate_random_string(length=12):
            return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

        for _ in range(num_database):
            temp_db_name = f"temp_db_{generate_random_string()}"
            create_db_query = f"CREATE DATABASE {temp_db_name};"
            commands.append(create_db_query)
            db_names.append(temp_db_name)
        self.execute(commands)
        return db_names

    def drop_temp_databases(self, temp_databases: List[str]):
        drop_commands = [f"DROP DATABASE `{db}`;" for db in temp_databases]
        return self.execute(drop_commands)

    def execute(self, queries: List[str]):
        result = None
        error = None
        db_instance = get_database(self.db_config)
        for query in queries:
            result, error = db_instance.execute(query)
            if error:
                print(f"Error while executing query. error: {error}")
        return result, error
