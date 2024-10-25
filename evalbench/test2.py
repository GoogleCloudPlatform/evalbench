import databases
import util.config as util_config

config_path = "../datasets/nld/google/bat/db_configs/db_blog/csql_postgres.yaml"
config = util_config.load_yaml_config(config_path)

db = databases.get_database(config)

query = "SELECT * FROM allowances limit 1;"
eval_query = "Select * from allowances limit 2;"
# query = "CREATE DATABASE test1;"
# query = "CREATE TABLE example ( column1 VARCHAR(255) );"
# result, error = db.execute(query)
# result, error = db.create_database(query)
query = "drop table allowances cascade;"
query = "DROP SCHEMA public CASCADE;"
query = "select * from tbl_attachments LIMIT 1;"
query = "drop table cars;"
result, error = db.execute(query)

if error:
    print("Error is ", error)
else:
    print("Result is ", result)
    # print("Eval Result is ", eval_result)

# Commit the transaction
# db.rollback_transaction(conn)
print("Done")