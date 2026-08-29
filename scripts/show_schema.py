"""Print the SQL that our models generate.

Run this to see exactly what PostgreSQL would be asked to create.
Requires no database connection - it only reads Base.metadata.

    python -m scripts.show_schema
"""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.models import Base


def main() -> None:
    dialect = postgresql.dialect()

    tables = Base.metadata.sorted_tables

    print(f"\n{len(tables)} tables registered in Base.metadata\n")
    print("=" * 70)

    for table in tables:
        print()
        print(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")

        for index in table.indexes:
            print(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")

    print()
    print("=" * 70)
    print("\nCreation order (parents before children):\n")

    for position, table in enumerate(tables, start=1):
        print(f"  {position:>2}. {table.name}")

    print()


if __name__ == "__main__":
    main()