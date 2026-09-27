"""Verification test script for the decisions table's decision column and enum."""
import asyncio
import uuid
import asyncpg


async def test_enum():
    conn = await asyncpg.connect("postgresql://postgres:root1234@localhost:5432/trustlayer")

    # 1. Query pg_enum for decision_type
    enum_vals = await conn.fetch("""
        SELECT e.enumlabel
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        WHERE t.typname = 'decision_type'
        ORDER BY e.enumsortorder;
    """)
    labels = [r["enumlabel"] for r in enum_vals]
    print(f"Enum labels in PostgreSQL decision_type: {labels}")

    # 2. Check column data type in decisions table
    col_info = await conn.fetchrow("""
        SELECT column_name, udt_name, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'decisions' AND column_name = 'decision';
    """)
    print(f"Column info in information_schema: {dict(col_info)}")

    # 3. Test inserting each valid value
    for val in ["allow", "block", "approve"]:
        test_id = uuid.uuid4()
        try:
            await conn.execute("INSERT INTO decisions (id, decision) VALUES ($1, $2)", test_id, val)
            print(f"SUCCESS: Accepted valid value '{val}'")
            await conn.execute("DELETE FROM decisions WHERE id = $1", test_id)
        except Exception as e:
            print(f"FAILED to insert valid value '{val}': {e}")

    # 4. Test inserting invalid values (including old uppercase values and other strings)
    for val in ["ALLOW", "WARN", "BLOCK", "ESCALATE", "pending", "unknown"]:
        test_id = uuid.uuid4()
        try:
            await conn.execute("INSERT INTO decisions (id, decision) VALUES ($1, $2)", test_id, val)
            print(f"UNEXPECTED SUCCESS: Inserted invalid value '{val}'")
            await conn.execute("DELETE FROM decisions WHERE id = $1", test_id)
        except Exception as e:
            print(f"CORRECTLY REJECTED '{val}': {type(e).__name__} - {str(e).strip()}")

    # 5. Test inserting NULL
    test_id = uuid.uuid4()
    try:
        await conn.execute("INSERT INTO decisions (id, decision) VALUES ($1, NULL)", test_id)
        print("UNEXPECTED SUCCESS: Inserted NULL")
        await conn.execute("DELETE FROM decisions WHERE id = $1", test_id)
    except Exception as e:
        print(f"CORRECTLY REJECTED NULL: {type(e).__name__} - {str(e).strip()}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(test_enum())
