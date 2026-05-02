from sqlalchemy import inspect, text

from app import models  # noqa: F401
from app.db import Base


def ensure_runtime_schema(engine) -> None:
    """Create missing tables and add missing columns without dropping data."""
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in table_names:
                table.create(bind=connection, checkfirst=True)
                continue

            existing_columns = {
                column["name"]
                for column in inspector.get_columns(table.name)
            }
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                column_type = column.type.compile(dialect=engine.dialect)
                quoted_table = engine.dialect.identifier_preparer.quote(table.name)
                quoted_column = engine.dialect.identifier_preparer.quote(column.name)
                connection.execute(
                    text(f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} {column_type}")
                )
                print(f"[schema] added missing column {table.name}.{column.name}")
