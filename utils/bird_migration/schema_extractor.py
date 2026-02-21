import sqlite3
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import re

@dataclass
class Column:
    name: str
    original_name: str
    type: str
    notnull: bool
    pk: int  # 0 means not PK, 1+ is PK index
    default_value: Optional[str]

def sanitize(name: Optional[str]) -> str:
    if name is None:
        return ""
    # Replace invalid chars with _
    # Keep alphanumeric and _
    # Handle specific replacements for readability
    name = name.replace("%", "Percent")
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    
    # Ensure it starts with a letter or underscore. 
    # If it starts with digit, prepend 'col_'
    if name and name[0].isdigit():
        name = f"col_{name}"
        
    return name

@dataclass
class ForeignKey:
    table: str
    from_col: str
    to_col: str
    original_from_col: str
    original_to_col: Optional[str]

@dataclass
class Table:
    name: str
    columns: List[Column] = field(default_factory=list)
    foreign_keys: List[ForeignKey] = field(default_factory=list)
    primary_keys: List[str] = field(default_factory=list)

def extract_schema(db_path: str) -> Dict[str, Table]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    schema = {}
    
    # Get list of tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cursor.fetchall()]
    
    for table_name in tables:
        table = Table(name=table_name)
        
        # Get columns
        # cid, name, type, notnull, dflt_value, pk
        cursor.execute(f'PRAGMA table_info("{table_name}")')
        columns_info = cursor.fetchall()
        
        pk_cols = []
        for info in columns_info:
            sanitized_name = sanitize(info[1])
            col = Column(
                name=sanitized_name,
                original_name=info[1],
                type=info[2],
                notnull=bool(info[3]),
                pk=info[5],
                default_value=info[4]
            )
            table.columns.append(col)
            if col.pk > 0:
                pk_cols.append((col.pk, col.name)) # Use sanitized name for PK list
        
        # Sort PKs by index
        pk_cols.sort(key=lambda x: x[0])
        table.primary_keys = [x[1] for x in pk_cols]
        
        # Get Foreign Keys
        # id, seq, table, from, to, on_update, on_delete, match
        cursor.execute(f'PRAGMA foreign_key_list("{table_name}")')
        fks = cursor.fetchall()
        for fk in fks:
            table.foreign_keys.append(ForeignKey(
                table=fk[2],
                from_col=sanitize(fk[3]),
                to_col=sanitize(fk[4]),
                original_from_col=fk[3],
                original_to_col=fk[4]
            ))
            
        schema[table_name] = table
        
    conn.close()
    return schema
