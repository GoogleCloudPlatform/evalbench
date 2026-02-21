from typing import List, Dict, Set
from .schema_extractor import Table, Column

class DependencySorter:
    def __init__(self, schema: Dict[str, Table]):
        self.schema = schema
        self.visited = set()
        self.sorted_tables = []

    def visit(self, table_name: str):
        if table_name in self.visited:
            return
        
        # Check dependencies
        if table_name in self.schema:
            table = self.schema[table_name]
            for fk in table.foreign_keys:
                # Avoid self-references and ensure target exists
                if fk.table != table_name and fk.table in self.schema:
                    self.visit(fk.table)
        
        self.visited.add(table_name)
        self.sorted_tables.append(table_name)

    def sort(self) -> List[str]:
        for table_name in self.schema:
            self.visit(table_name)
        return self.sorted_tables

class DDLGenerator:
    def __init__(self, dialect: str):
        self.dialect = dialect
        # Manual overrides for columns that use SQLite affinity to store 
        # types that don't match their declaration.
        self.type_overrides = {
            "Player.height": "REAL",
            "Player.weight": "REAL"
        }

    def map_type(self, sqlite_type: str, is_pk: bool = False) -> str:
        sqlite_type = sqlite_type.upper()
        
        if self.dialect == "spanner_gsql":
            if "INT" in sqlite_type: return "INT64"
            if "CHAR" in sqlite_type or "TEXT" in sqlite_type or "CLOB" in sqlite_type: return "STRING(MAX)"
            if "REAL" in sqlite_type or "FLOA" in sqlite_type or "DOUB" in sqlite_type: return "FLOAT64"
            if "BLOB" in sqlite_type: return "BYTES(MAX)"
            if "BOOL" in sqlite_type: return "BOOL"
            if "DATE" in sqlite_type or "TIME" in sqlite_type: return "STRING(MAX)" # Safer
            return "STRING(MAX)" # Fallback
            
        elif self.dialect == "spanner_pg":
            if "INT" in sqlite_type: return "bigint"
            if "CHAR" in sqlite_type or "TEXT" in sqlite_type: return "text"
            if "REAL" in sqlite_type or "FLOA" in sqlite_type or "DOUB" in sqlite_type: return "double precision"
            if "BLOB" in sqlite_type: return "bytea"
            if "BOOL" in sqlite_type: return "boolean"
            # Spanner PG allows 'timestamp with time zone' but simpler to map to text if unsure
            return "text"

        elif self.dialect == "postgres":
            if "INT" in sqlite_type: return "INTEGER" # Or BIGINT
            if "TEXT" in sqlite_type: return "TEXT"
            if "REAL" in sqlite_type: return "DOUBLE PRECISION"
            if "DATETIME" in sqlite_type: return "TIMESTAMP"
            if "DATE" in sqlite_type: return "DATE"
            return sqlite_type # Fallback to original

        elif self.dialect == "mysql":
            if "INT" in sqlite_type: return "INTEGER"
            # MySQL requires length for Keys on TEXT columns.
            if "TEXT" in sqlite_type: 
                return "VARCHAR(255)" if is_pk else "LONGTEXT"
            if "DATETIME" in sqlite_type: return "DATETIME"
            return sqlite_type

        return sqlite_type

    def quote_ident(self, name: str) -> str:
        if self.dialect in ["postgres", "spanner_pg"]:
            return f'"{name}"'
        if self.dialect in ["mysql", "spanner_gsql"]: # GSQL uses backticks
            return f'`{name}`'
        return name

    def generate(self, schema: Dict[str, Table]) -> List[str]:
        sorter = DependencySorter(schema)
        sorted_table_names = sorter.sort()
        
        ddl_statements = []
        
        for table_name in sorted_table_names:
            table = schema[table_name]
            columns_ddl = []
            
            for col in table.columns:
                is_pk = (col.pk > 0) or (col.name in table.primary_keys)
                
                # Check for manual overrides (e.g. "Player.height")
                override_key = f"{table.name}.{col.original_name}"
                type_to_map = self.type_overrides.get(override_key, col.type)
                
                col_type = self.map_type(type_to_map, is_pk)
                col_def = f"{self.quote_ident(col.name)} {col_type}"
                
                # BIRD data often violates NOT NULL. Only enforce for PK.
                # Spanner PK columns MUST be NOT NULL.
                if col.notnull:
                    if col.pk > 0 or "spanner" not in self.dialect:
                        col_def += " NOT NULL"
                
                columns_ddl.append(col_def)
            
            # Primary Keys
            pk_clause = ""
            actual_pks = list(table.primary_keys)
            
            # Spanner REQUIRES a PK. If missing, add a synthetic one.
            if not actual_pks and "spanner" in self.dialect:
                pk_name = "eb_row_id"
                actual_pks = [pk_name]
                col_type = "INT64" if self.dialect == "spanner_gsql" else "bigint"
                # PK column must be NOT NULL in Spanner
                columns_ddl.append(f"{self.quote_ident(pk_name)} {col_type} NOT NULL")

            if actual_pks:
                pk_cols = ", ".join([self.quote_ident(pk) for pk in actual_pks])
                if self.dialect == "spanner_gsql":
                    pk_clause = f" PRIMARY KEY ({pk_cols})"
                else:
                    columns_ddl.append(f"PRIMARY KEY ({pk_cols})")
            
            # Foreign Keys - Skipped for BIRD
            
            create_stmt = f"CREATE TABLE {self.quote_ident(table.name)} ({', '.join(columns_ddl)})" + pk_clause
            
            if self.dialect == "mysql":
                create_stmt += ";"
            
            ddl_statements.append(create_stmt)
            
        return ddl_statements
