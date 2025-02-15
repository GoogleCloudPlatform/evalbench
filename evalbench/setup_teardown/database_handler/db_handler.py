from abc import ABC, abstractmethod
from typing import Any, Tuple, List, Optional, Set
from databases import MySQLDB, PGDB
from evalproto.schema_details_pb2 import SchemaDetails


class DBHandler(ABC):

    def __init__(self, db: MySQLDB | PGDB, database_name: str):
        self.db = db
        self.database_name = database_name

    @abstractmethod
    def drop_all_tables(self) -> Tuple[Any, Optional[Exception]]:
        """
        Generates SQL statement to drop all tables in the database.
        Returns:
            Tuple[Any, Optional[Exception]]: The result of the execution and any error that occurred.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def create_users(self) -> Optional[Exception]:
        """
        Creates users that have access to only run DQL, DML, or DDL.
        Returns:
            Optional[Exception]: Any error that occurred during user creation.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def create_schema_statements(
        self, schema: SchemaDetails, excluded_columns: Optional[Set[str]]
    ) -> List[str]:
        """
        Generates SQL statement to create table schema.
        Args:
            schema: The schema to create.
            excluded_columns: Columns to exclude from the schema creation.
        Returns:
            List[str]: A list of SQL statements for creating the schema.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def create_insert_statements(self, data_directory: str) -> List[str]:
        """
        Generates SQL insert statements based on CSV file.
        Args:
            data_directory: The directory containing the CSV files.
        Returns:
            List[str]: A list of SQL insert statements.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def execute(self, queries: List[str]) -> Tuple[Any, Optional[Exception]]:
        """
        Execute a list of query strings and return the execution results and total time spent.
        Args:
            queries (List[str]): The SQL queries to execute.
        Returns:
            Tuple[Any, Optional[Exception]]: The result of the execution and any error that occurred.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def create_temp_databases(self, num_database: int):
        """
        Creates temporary databases.
        Args:
            num_database: The number of databases to create.
        Returns:
            List[str]: A list of created database names.
        """
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def drop_temp_databases(self, temp_databases: List[str]) -> None:
        """
        Drops temporary databases.
        Args:
            temp_databases: A list of database names to drop.
        """
        raise NotImplementedError("Subclasses must implement this method")
