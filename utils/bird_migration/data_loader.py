import sqlite3
from typing import Dict, Any, List
from .schema_extractor import Table
from .ddl_generator import DependencySorter

class DataLoader:
    def __init__(self, source_db_path: str, db_wrapper: Any, dialect: str):
        self.source_db_path = source_db_path
        self.db_wrapper = db_wrapper
        self.dialect = dialect
        self.current_row_idx = 0
        
        if "spanner" not in dialect:
            conn = db_wrapper.engine.raw_connection()
            try:
                cursor = conn.cursor()
                if "postgres" in dialect:
                    cursor.execute("SET session_replication_role = 'replica';")
                elif "mysql" in dialect:
                    cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
                conn.commit()
                cursor.close()
            except Exception as e:
                print(f"Warning: Could not disable FK checks: {e}")
            finally:
                conn.close()

    def clean_val(self, val: Any) -> Any:
        if isinstance(val, str):
            val = val.replace('\x00', '')
        return val

    def load(self, schema: Dict[str, Table]):
        sorter = DependencySorter(schema)
        sorted_tables = sorter.sort()
        
        src_conn = sqlite3.connect(self.source_db_path)
        src_cursor = src_conn.cursor()
        
        for table_name in sorted_tables:
            table = schema[table_name]
            print(f"Loading table: {table_name}")
            self.current_row_idx = 0
            
            # Read all data
            src_cursor.execute(f'SELECT * FROM "{table_name}"')
            rows = src_cursor.fetchall()
            
            if not rows:
                continue
            
            cols = [c.name for c in table.columns]
            
            cleaned_rows = []
            for row in rows:
                cleaned_rows.append([self.clean_val(v) for v in row])
                
            batch_size = 1000
            total_rows = len(cleaned_rows)
            for i in range(0, total_rows, batch_size):
                chunk = cleaned_rows[i:i + batch_size]
                self.insert_batch(table_name, cols, chunk, table)
                if (i // batch_size) % 10 == 0:
                    print(f"  .. {i}/{total_rows} rows loaded")
            print(f"  Done: {total_rows} rows loaded.")
                
        src_conn.close()

    def insert_batch(self, table_name: str, columns: List[str], rows: List[List[Any]], table_obj: Table):
        if "spanner" in self.dialect:
            # Add synthetic PK if needed
            if not table_obj.primary_keys:
                for row in rows:
                    self.current_row_idx += 1
                    row.append(self.current_row_idx)
                # Note: insert_data in SpannerDB uses table.columns internally if we don't pass them?
                # Actually SpannerDB.insert_data (Turn 176) does:
                # b.insert(table, columns=cols, values=row)
                # It gets 'cols' from the database metadata or we must provide?
                # Wait, SpannerDB.insert_data logic (I read before) was:
                # for table, rows in data.items():
                #    columns = [c.name for c in self.inspect.get_columns(table)]
            
            self.db_wrapper.insert_data({table_name: rows})
            return

        conn = self.db_wrapper.engine.raw_connection()
        try:
            if self.dialect == "postgres":
                placeholders = ",".join(["%s"] * len(columns))
                quoted_cols = [f'"{c}"' for c in columns]
                sql = f'INSERT INTO "{table_name}" ({",".join(quoted_cols)}) VALUES ({placeholders})'
                cursor = conn.cursor()
                cursor.executemany(sql, rows)
                conn.commit()
                cursor.close()
                
            elif self.dialect == "mysql":
                placeholders = ",".join(["%s"] * len(columns))
                quoted_cols = [f'`{c.replace("%", "%%")}`' for c in columns]
                sql = f'INSERT INTO `{table_name}` ({",".join(quoted_cols)}) VALUES ({placeholders})'
                cursor = conn.cursor()
                for row in rows:
                    try:
                        cursor.execute(sql, row)
                    except Exception as e:
                        print(f"MySQL Error: {e}")
                        raise e
                conn.commit()
                cursor.close()
        finally:
            conn.close()
