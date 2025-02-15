import os
import csv
import random
import string
from typing import Any, Tuple, List
from .db_handler import DBHandler
from databases import PGDB

DROP_ALL_TABLE_QUERY = [
    "DROP SCHEMA public CASCADE;",
    "CREATE SCHEMA public;",
]

CREATE_USER_QUERY = [
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tmp_dql') \
        THEN EXECUTE 'CREATE USER tmp_dql WITH PASSWORD ''pantheon'''; END IF; END $$;",
    "GRANT USAGE ON SCHEMA public TO tmp_dql;",
    "GRANT SELECT ON ALL TABLES IN SCHEMA public TO tmp_dql;",
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tmp_dml') \
        THEN EXECUTE 'CREATE USER tmp_dml WITH PASSWORD ''pantheon'''; END IF; END $$;",
    "GRANT USAGE ON SCHEMA public TO tmp_dml;",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tmp_dml;",
]


class PostgresHandler(DBHandler):

    def __init__(self, db: PGDB, database_name: str):
        self.db_engine = "postgres"
        self.db = db
        self.database_name = database_name

    def drop_all_tables(self):
        return self.execute(DROP_ALL_TABLE_QUERY)

    def create_users(self):
        _, error = self.execute(CREATE_USER_QUERY)
        if error:
            return error

    def create_schema_statements(self, schema, excluded_columns):
        excluded_columns = excluded_columns or set()
        create_statements = []

        for table in schema.tables:
            table_name = table.table
            columns = [
                f"{column.column} {column.data_type}"
                for column in table.columns
                if column.column not in excluded_columns
            ]

            columns_str = ",\n    ".join(columns)
            create_statement = f"CREATE TABLE {table_name} (\n    {columns_str}\n);"
            create_statements.append(create_statement)

        return create_statements

    def create_insert_statements(self, data_directory):
        insertion_strings = []
        for filename in os.listdir(data_directory):
            if filename.endswith(".csv"):
                table_name = filename[:-4]
                with open(os.path.join(data_directory, filename), "r") as csvfile:
                    reader = csv.reader(csvfile)
                    for row in reader:
                        values = ", ".join([f"{value}" for value in row])
                        insertion_strings.append(
                            f"INSERT INTO public.{table_name} VALUES ({values});"
                        )

        return insertion_strings

    def create_temp_databases(self, num_database: int):
        db_names = []

        def generate_random_string(length=12):
            return "".join(
                random.choices(string.ascii_lowercase + string.digits, k=length)
            )

        for _ in range(num_database):
            temp_db_name = f"temp_{self.database_name}_{generate_random_string()}"
            db_names.append(temp_db_name)

        for database in db_names:
            self.db.create_database(database)
        return db_names

    def drop_temp_databases(self, temp_databases: List[str]):
        for database in temp_databases:
            self.db.drop_database(database)

    def execute(self, queries: List[str]):
        result = None
        error = None
        combined_query = "\n".join(queries)
        result, error = self.db.execute(combined_query)
        return result, error
