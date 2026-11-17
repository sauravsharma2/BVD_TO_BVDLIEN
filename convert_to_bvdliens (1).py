r"""
Reverse lookup: BvdLiens ID  ->  BvD ID.

Input : queries/obo.sql   (returns a column of BvdLiens IDs, named "Id")
Output: output/bvdid.parquet   (single column: BvDID)

Built for ~7M rows: resolved server-side in one pass, streamed back in
chunks, written to parquet row-group by row-group. Memory stays flat.

Usage:
    python liens_to_bvdid.py
    python liens_to_bvdid.py --sql queries/obo.sql --out output/bvdid.parquet
    python liens_to_bvdid.py --liens-col NewID
    python liens_to_bvdid.py --keep-duplicates
"""
import sys
import argparse
from pathlib import Path

import pyodbc
import pyarrow as pa
import pyarrow.parquet as pq

from db_connexion import load_query, prepare_query, connect_db

# ============================================================
# ========================= CONFIG ===========================
# ============================================================

SQL_PATH = Path("queries") / "obo.sql"
OUT_PATH = Path("output") / "bvdid.parquet"

# Reverse mapping source.
# In crossref: Id = the BvD ID, NewId = the BvdLiens ID.
MAP_TABLE = "bvdaffils.dbo.crossref"
MAP_BVDID_COL = "Id"        # column holding the BvD ID      -> what we want
MAP_LIENS_COL = "NewId"     # column holding the BvdLiens ID -> what we have

# Column in obo.sql's result that holds the BvdLiens ID.
LIENS_COL = "Id"

OUT_COL = "BvDID"
CHUNK = 200_000             # rows fetched + written per batch
COMPRESSION = "snappy"

# ============================================================


def build_sql(obo_sql: str, liens_col: str, dedup: bool) -> str:
    """Wrap obo.sql as a subquery and join it to the mapping table."""
    obo_sql = obo_sql.strip().rstrip(";")
    distinct = "distinct " if dedup else ""
    return f"""
with ids as (
    select distinct src.[{liens_col}] as liens
    from ({obo_sql}) src
    where src.[{liens_col}] is not null
)
select {distinct}m.[{MAP_BVDID_COL}] as {OUT_COL}
from ids
inner join {MAP_TABLE} m
    on m.[{MAP_LIENS_COL}] = ids.liens
where m.[{MAP_BVDID_COL}] is not null
"""


@connect_db
def stream_to_parquet(conn: pyodbc.Connection, sql: str, out_path: Path) -> int:
    cur = conn.cursor()
    cur.execute(sql)

    schema = pa.schema([(OUT_COL, pa.string())])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    writer = pq.ParquetWriter(out_path, schema, compression=COMPRESSION)
    try:
        while True:
            rows = cur.fetchmany(CHUNK)
            if not rows:
                break
            values = [r[0] for r in rows]
            writer.write_table(
                pa.table({OUT_COL: pa.array(values, pa.string())}, schema=schema)
            )
            total += len(values)
            print(f"  written: {total:,}", flush=True)
    finally:
        writer.close()
        cur.close()
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sql", default=str(SQL_PATH), help="path to obo.sql")
    ap.add_argument("--out", default=str(OUT_PATH), help="output parquet path")
    ap.add_argument("--liens-col", default=LIENS_COL,
                    help=f"column in obo.sql holding the BvdLiens ID (default: {LIENS_COL})")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="keep repeated BvD IDs (default: distinct)")
    args = ap.parse_args()

    sql_path = Path(args.sql)
    if not sql_path.is_absolute() and not sql_path.exists():
        sql_path = Path(__file__).parent / sql_path
    if not sql_path.exists():
        sys.exit(f"SQL file not found: {sql_path}")

    obo_sql = prepare_query(load_query(str(sql_path)))
    if not obo_sql.strip():
        sys.exit(f"{sql_path} is empty")

    sql = build_sql(obo_sql, args.liens_col, dedup=not args.keep_duplicates)

    print(f"Source : {sql_path}")
    print(f"Column : {args.liens_col}")
    print(f"Mapping: {MAP_TABLE} ({MAP_LIENS_COL} -> {MAP_BVDID_COL})")
    print("Running... (one server-side pass, streamed in chunks)")

    out_path = Path(args.out)
    total = stream_to_parquet(sql, out_path)

    print(f"\nDone. {total:,} BvD IDs -> {out_path.resolve()}")


if __name__ == "__main__":
    main()
