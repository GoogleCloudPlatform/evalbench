import time

from threading import Semaphore
from google.cloud import secretmanager_v1
from typing import Any, Tuple
import sqlparse
import logging
import hashlib
import pickle
import redis


class DBResourceExhaustedError(Exception):
    pass


def get_db_secret(secret_path):
    # Create a client
    client = secretmanager_v1.SecretManagerServiceClient()
    # Initialize request argument(s)
    request = secretmanager_v1.AccessSecretVersionRequest(
        name=secret_path,
    )
    # Make the request
    response = client.access_secret_version(request=request)
    # Return the secret
    return response.payload.data.decode("utf-8")


def generate_ddl(data, db_name, comments_data=None):
    ddl_statements = []
    current_table = None
    if comments_data is None:
        comments_data = dict()

    for table_name, column_name, data_type in data:
        if table_name != current_table:
            if current_table is not None:
                ddl_statements.append(";\n")  # End previous table statement
            ddl_statements.append(f"CREATE TABLE {table_name} (\n")
            current_table = table_name

        column_comment = None
        if len(comments_data) > 0:
            try:
                column_comment = comments_data[db_name][table_name][column_name][
                    "column_description"
                ]
            except Exception as e:
                print(e)

        if column_comment is None:
            ddl_statements.append(f"    {column_name} {data_type},\n")
        else:
            ddl_statements.append(
                f"    {column_name} {data_type}, -- {column_comment} \n"
            )

    if current_table is not None:
        ddl_statements.append(");\n")  # End the last table statement

    return "".join(ddl_statements)


def is_bat_dataset(database_name):
    bat_datasets = {"db_hr", "db_blog", "db_chat", "db_ecommerce", "db_finance"}
    return database_name in bat_datasets


def rate_limited_execute(
    query: str,
    execution_method,
    execs_per_minute: int,
    semaphore: Semaphore,
    max_attempts: int,
) -> Tuple[Any, float]:
    semaphore.acquire()
    attempt = 1
    while attempt <= max_attempts:
        try:
            result, error = execution_method(query)
            break
        except DBResourceExhaustedError as e:
            # exponentially backoff starting at 5 seconds
            time.sleep(5 * (2 ** (attempt)))
            attempt += 1
    time.sleep(60 / execs_per_minute)
    semaphore.release()
    return result, error


def with_cache_execute(
    query: str,
    engine_url: str,
    execution_method,
    cache_client: Any,
) -> Tuple[Any, Any]:
    try:
        # Format the query for consistency
        query = sqlparse.format(query, reindent=True, keyword_case="upper")
    except Exception as e:
        logging.warning(f"Failed to format query: {query} with error: {e}")

    # Generate a hash of the query and database URL
    query_hash = hashlib.sha256((query + str(engine_url)).encode()).hexdigest()

    # Attempt to retrieve from cache
    try:
        cached_result = cache_client.get(query_hash)
        if cached_result:
            logging.debug(f"Using cached result for query: {query}")
            return pickle.loads(cached_result), None
    except Exception as e:
        logging.warning(f"Failed to retrieve query from cache: {e}")

    # Execute the query using the internal execute method
    result, error = execution_method(query)

    # If successful, store the result in the cache
    if not error:
        try:
            cache_client.set(query_hash, pickle.dumps(result))
            logging.debug(f"Cached result for query: {query}")
        except Exception as e:
            logging.warning(f"Failed to cache query result: {e}")

    return result, error


def get_cache_client(config):
    cache_client = None
    if config.get("redis_host", None):
        try:
            redis_host = config["redis_host"]
            redis_port = config.get("redis_port", 6379)
            redis_db_id = config.get("redis_db_id", 0)
            logging.info(f"Found Redis config in db_config. redis_host: {redis_host} redis_port: {redis_port} redis_db_id: {redis_db_id}")
            cache_client = redis.StrictRedis(
                host=redis_host,
                port=redis_port,
                db=redis_db_id)
        except Exception as e:
            logging.warning(f"redis_host is found in db_config but failed to connect: {e}")
    return cache_client
