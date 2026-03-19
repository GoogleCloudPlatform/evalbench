import sqlite3
import psycopg2
import mysql.connector
import logging
import os
import warnings
from .query_converters.base import QueryResult

# Silence OpenTelemetry warnings globally
warnings.filterwarnings("ignore", message="Overriding of current MeterProvider is not allowed")

# Suppress noisy logs from Spanner/OpenTelemetry/gRPC globally
os.environ['GOOGLE_CLOUD_SPANNER_LOG_SESSIONS'] = 'off'
for logger_name in [
    'google',
    'google.cloud',
    'google.cloud.spanner_v1',
    'google.cloud.spanner',
    'google.api_core',
    'google.auth',
    'opentelemetry',
    'urllib3',
    'grpc'
]:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.CRITICAL)
    logger.propagate = False


class QueryExecutor:
    """Execute queries on different database types."""
    
    @staticmethod
    def execute_sqlite(db_path: str, query: str) -> QueryResult:
        """Execute query on SQLite database.
        
        Args:
            db_path: Path to SQLite database file
            query: SQL query to execute
            
        Returns:
            QueryResult with execution results
        """
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(query)
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            total_rows = len(rows)
            
            conn.close()
            
            return QueryResult(
                columns=columns,
                rows=rows,
                total_rows=total_rows
            )
        except Exception as e:
            return QueryResult(
                columns=[],
                rows=[],
                total_rows=0,
                error=str(e)
            )
    
    @staticmethod
    def execute_postgresql(
        host: str,
        port: str,
        user: str,
        password: str,
        database: str,
        query: str
    ) -> QueryResult:
        """Execute query on PostgreSQL database.
        
        Args:
            host: PostgreSQL host
            port: PostgreSQL port
            user: PostgreSQL user
            password: PostgreSQL password
            database: PostgreSQL database name
            query: SQL query to execute
            
        Returns:
            QueryResult with execution results
        """
        try:
            # Connect to PostgreSQL using psycopg2
            conn = psycopg2.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database
            )
            cursor = conn.cursor()
            cursor.execute(query)
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            total_rows = len(rows)
            
            cursor.close()
            conn.close()
            
            return QueryResult(
                columns=columns,
                rows=rows,
                total_rows=total_rows
            )
        except Exception as e:
            return QueryResult(
                columns=[],
                rows=[],
                total_rows=0,
                error=str(e)
            )
    
    @staticmethod
    def execute_mysql(
        host: str,
        port: str,
        user: str,
        password: str,
        database: str,
        query: str
    ) -> QueryResult:
        """Execute query on MySQL database.
        
        Args:
            host: MySQL host
            port: MySQL port
            user: MySQL user
            password: MySQL password
            database: MySQL database name
            query: SQL query to execute
            
        Returns:
            QueryResult with execution results
        """
        try:
            # Connect to MySQL using mysql.connector
            conn = mysql.connector.connect(
                host=host,
                port=int(port),
                user=user,
                password=password,
                database=database
            )
            cursor = conn.cursor()
            cursor.execute(query)
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            total_rows = len(rows)
            
            cursor.close()
            conn.close()
            
            return QueryResult(
                columns=columns,
                rows=rows,
                total_rows=total_rows
            )
        except Exception as e:
            return QueryResult(
                columns=[],
                rows=[],
                total_rows=0,
                error=str(e)
            )

    @staticmethod
    def execute_spanner(
        project_id: str,
        instance_id: str,
        database_id: str,
        query: str
    ) -> QueryResult:
        """Execute query on Cloud Spanner.
        
        Args:
            project_id: Google Cloud project ID
            instance_id: Spanner instance ID
            database_id: Spanner database ID
            query: SQL query to execute
            
        Returns:
            QueryResult with execution results
        """
        import contextlib
        import os
        import sys

        # Context manager to redirect C-level stderr to /dev/null
        @contextlib.contextmanager
        def silence_stderr():
            # Only redirect if stderr is a valid terminal/file descriptor
            null_fd = None
            old_stderr_fd = None
            try:
                null_fd = os.open(os.devnull, os.O_WRONLY)
                old_stderr_fd = os.dup(sys.stderr.fileno())
                os.dup2(null_fd, sys.stderr.fileno())
                try:
                    yield
                finally:
                    os.dup2(old_stderr_fd, sys.stderr.fileno())
            except Exception:
                # If we cannot redirect, just proceed normally
                yield
            finally:
                if old_stderr_fd is not None:
                    os.close(old_stderr_fd)
                if null_fd is not None:
                    os.close(null_fd)

        try:
            with silence_stderr():
                from google.cloud import spanner
                import google.auth
                
                # Explicitly load credentials with quota project
                credentials, project = google.auth.default(quota_project_id=project_id)
                client = spanner.Client(project=project_id, credentials=credentials)
                instance = client.instance(instance_id)
                database = instance.database(database_id)
                
                with database.snapshot() as snapshot:
                    results = snapshot.execute_sql(query)
                    
                    rows = []
                    for row in results:
                        rows.append(tuple(row))
                    
                    # Get columns from result metadata (available after iteration)
                    columns = [field.name for field in results.fields] if results.fields else []
                    total_rows = len(rows)
            
            return QueryResult(
                columns=columns,
                rows=rows,
                total_rows=total_rows
            )
        except Exception as e:
            return QueryResult(
                columns=[],
                rows=[],
                total_rows=0,
                error=str(e)
            )

    @staticmethod
    def execute_bigquery(
        project_id: str,
        dataset_id: str,
        query: str
    ) -> QueryResult:
        """Execute query on BigQuery.
        
        Args:
            project_id: Google Cloud project ID
            dataset_id: BigQuery dataset ID
            query: SQL query to execute
            
        Returns:
            QueryResult with execution results
        """
        try:
            from google.cloud import bigquery
            client = bigquery.Client(project=project_id)
            
            # Set default dataset for query execution
            job_config = bigquery.QueryJobConfig(
                default_dataset=f"{project_id}.{dataset_id}"
            )
            
            # BigQuery results can be converted to dataframes or list of tuples
            query_job = client.query(query, job_config=job_config)
            results = query_job.result()
            
            rows = []
            for row in results:
                rows.append(tuple(row.values()))
            
            columns = [field.name for field in results.schema]
            total_rows = len(rows)
            
            return QueryResult(
                columns=columns,
                rows=rows,
                total_rows=total_rows
            )
        except Exception as e:
            return QueryResult(
                columns=[],
                rows=[],
                total_rows=0,
                error=str(e)
            )
