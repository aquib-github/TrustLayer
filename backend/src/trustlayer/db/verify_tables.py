"""Quick verification script to list all tables in the trustlayer database."""
import asyncio
import asyncpg


async def main():
    conn = await asyncpg.connect("postgresql://postgres:root1234@localhost:5432/trustlayer")
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    print(f"Tables in DB ({len(rows)} total):")
    for r in rows:
        print(f"  - {r['tablename']}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
