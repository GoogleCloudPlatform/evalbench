from abc import ABC, abstractmethod
from typing import Any, Optional, Tuple, List
from threading import Semaphore
from util.config import generate_key
from .util import get_cache_client, get_db_secret, DatabaseSchema, Table, Column


class DB(ABC):

    def __init__(self, db_config):
        self.db_path = db_config["database_path"]
        self.db_name = db_config["database_name"]
        self.db_type = db_config["db"]
        self.username = db_config.get("user_name") or ""
        if "password" in db_config and db_config["password"]:
            self.password = db_config["password"]
        elif "secret_manager_path" in db_config and db_config["secret_manager_path"]:
            self.password = get_db_secret(db_config["secret_manager_path"])
        else:
            # no password
            self.password = None

        # Setup the concurrency requirements
        self.execs_per_minute = db_config["max_executions_per_minute"]
        self.max_attempts = 3
        self.semaphore = Semaphore(self.execs_per_minute)

        # Maintain setup / teardown information
        self.tmp_dbs = []
        self.tmp_users = []
        self.was_re_setup_this_session = False

        # Initialize the Redis cache client
        self.cache_client = get_cache_client(db_config)

    def clean_tmp_creations(self) -> None:
        self.drop_tmp_databases(self.tmp_dbs.copy())
        self.delete_tmp_users(self.tmp_users.copy())

    def set_setup_instructions(self, setup_scripts, data) -> None:
        self.setup_scripts = setup_scripts
        self.data = data

    def resetup_database(self, force=False, setup_users=False) -> None:
        if not self.setup_scripts:
            raise ValueError("Setup sql is required for setup.")
        if self.was_re_setup_this_session and not force:
            # If database was already re-setup (for DQL, DML, etc.)
            # and it can be re-used, don't re-run setup unless forced.
            return
        pre_setup, setup, post_setup = self.setup_scripts

        self.drop_all_tables()
        self.batch_execute(pre_setup)
        self.batch_execute(setup)
        self.insert_data(self.data)
        self.batch_execute(post_setup)
        if setup_users:
            self.setup_tmp_users()
        self.was_re_setup_this_session = True

    def get_ddl_from_db(self):
        db_schema = DatabaseSchema(name=self.db_name)
        metadata = self.get_metadata()
        for table_name, columns in metadata.items():
            tmp_table = Table(name=table_name)
            for column in columns:
                tmp_table.columns.append(
                    Column(name=column["name"], type=column["type"])
                )
            db_schema.tables.append(tmp_table)
        return self.generate_ddl(db_schema)

    def create_tmp_databases(self, db_config, num_dbs: int) -> list[str]:
        tmp_dbs = []
        for _ in range(num_dbs):
            base_db_name = db_config["database_name"]
            tmp_db_name = f"tmp_{base_db_name}_{generate_key()}"
            self.create_tmp_database(tmp_db_name)
            tmp_dbs.append(tmp_db_name)
        return tmp_dbs

    def drop_tmp_databases(self, databases) -> None:
        for database_name in databases:
            self.drop_tmp_database(database_name)

    def setup_tmp_users(self):
        self.dql_user = "tmp_dql_user_" + generate_key()
        self.dml_user = "tmp_dml_user_" + generate_key()
        self.tmp_user_password = generate_key()
        self.create_tmp_users(self.dql_user, self.dml_user, self.tmp_user_password)
        self.tmp_users.extend([self.dql_user, self.dml_user])

    def delete_tmp_users(self, users) -> None:
        for username in users:
            self.delete_tmp_user(username)

    def get_dql_user(self) -> str:
        if not self.dql_user:
            raise RuntimeError("No DQL user was created by this connection.")
        return self.dql_user

    def get_dml_user(self) -> str:
        if not self.dql_user:
            raise RuntimeError("No DML user was created by this connection.")
        return self.dml_user

    def get_tmp_user_password(self) -> str:
        if not self.dql_user:
            raise RuntimeError("No users were created by this connection.")
        return self.tmp_user_password

    @abstractmethod
    def execute(
        self,
        query: str,
        eval_query: Optional[str] = None,
        use_cache=False,
        rollback=False,
    ) -> Tuple[Any, Any, Any]:
        """
        Executes a database query.

        Args:
            query (str): The SQL query to execute.
            eval_query (Optional[str], optional): An optional query to evaluate the result of the main query. Defaults to None.
            use_cache (bool, optional): Whether to use caching for the query result. Defaults to False.
            rollback (bool, optional): Whether to rollback the transaction after execution. Defaults to False.

        Returns:
            Tuple[Any, Any, Any]: A tuple containing the result of the main query, the result of the eval query (if provided), and any error that occurred.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def batch_execute(self, commands: list[str]) -> None:
        """
        Executes a batch of SQL commands.
         * Raises RuntimeError if it cannot execute any command in batch.

        Args:
            commands (list[str]): A list of SQL commands to execute.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def get_metadata(self) -> dict:
        """
        Retrieves metadata about the database schema.

        Returns:
            dict: A dictionary containing metadata about the database schema.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def create_tmp_database(self, database_name: str) -> None:
        """
        Creates a temporary database.
        * Raises RuntimeError if it cannot create databases.

        Args:
            database_name (str): The name of the temporary database to create.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def drop_tmp_database(self, database_name: str) -> None:
        """
        Drops a temporary database.

        Args:
            database_name (str): The name of the temporary database to drop.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def drop_all_tables(self) -> None:
        """
        Drops all tables in the database.
        * Raises RuntimeError if it cannot drop tables.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def insert_data(self, data: dict[str, List[str]]) -> None:
        """
        Inserts data into the database tables.
        * Raises RuntimeError if it cannot insert data.

        Args:
            data (dict[str, List[str]]): A dictionary mapping table names to lists of rows to insert.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def create_tmp_users(self, dql_user: str, dml_user: str, tmp_password: str) -> None:
        """
        Creates temporary database users for DQL and DML.
        * Raises RuntimeError if cannot create user.

        Args:
            dql_user (str): The name of the DQL (Data Query Language) user.
            dml_user (str): The name of the DML (Data Manipulation Language) user.
            tmp_password (str): The password for the temporary users.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def delete_tmp_user(self, username: str) -> None:
        """
        Deletes a temporary database user.

        Args:
            username (str): The name of the user to delete.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def close_connections(self) -> None:
        """
        Closes all database connections.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def generate_ddl(
        self,
        schema: DatabaseSchema,
    ) -> list[str]:
        """
        Generates Data Definition Language (DDL) statements for the given schema.

        Args:
            schema (DatabaseSchema): Details of the Database Schema.

        Returns:
            list[str]: The generated DDL statements.
        """
        raise NotImplementedError("Subclasses must implement this method")
