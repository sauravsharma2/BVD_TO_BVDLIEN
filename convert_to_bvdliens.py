r"""
Convert OldID + NewID columns of an Excel file to BvdLiens IDs.
Adds sheet "BvDLiens": OLDID, OLD_bvdlien, OLD_type, NEWID, NEW_bvdlien, NEW_type.

Usage:
    python convert_to_bvdliens.py "C:\path\to\input.xlsx"
    python convert_to_bvdliens.py "C:\path\to\input.xlsx" --sheet Sheet1
"""
import sys
import argparse
from pathlib import Path

import pandas as pd
import pyodbc

from db_connexion import connect_db

OUT_SHEET = "BvDLiens"
COLS = ["OldID", "NewID"]

# companies table: key column + entity type column
COMPANIES_TABLE = "bvdaffils.dbo.companies"
COMPANIES_KEY = "Id"      # holds the BvdLiens ID
TYPE_COL = "person"       # entity-type column in companies table


@connect_db
def lookup_liens(conn: pyodbc.Connection, ids: list[str]) -> dict[str, tuple]:
    """Send all distinct IDs in one go, convert server-side,
    return {bvdid: (liens_id, entity_type)}."""
    cur = conn.cursor()
    cur.execute("create table #ids (bvdid varchar(50))")
    cur.fast_executemany = True
    cur.executemany("insert into #ids (bvdid) values (?)", [(i,) for i in ids])
    rows = cur.execute(f"""
        select l.bvdid, l.liens, c.etype
        from (select bvdid, bvdaffils.dbo.GetBvDLiensID(bvdid) as liens from #ids) l
        outer apply (select top 1 [{TYPE_COL}] as etype
                     from {COMPANIES_TABLE}
                     where [{COMPANIES_KEY}] = l.liens) c
    """).fetchall()
    cur.close()
    return {r.bvdid: (r.liens, r.etype) for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", nargs="?", default="ids.xlsx",
                    help="input Excel (default: ids.xlsx next to this script)")
    ap.add_argument("--sheet", default=0, help="input sheet (default: first)")
    args = ap.parse_args()

    path = Path(args.xlsx)
    if not path.is_absolute() and not path.exists():
        path = Path(__file__).parent / path
    if not path.exists():
        sys.exit(f"Input file not found: {path}")
    df = pd.read_excel(path, sheet_name=args.sheet, dtype=str)

    # find OldID / NewID case-insensitively
    lower = {c.strip().lower(): c for c in df.columns}
    missing = [c for c in COLS if c.lower() not in lower]
    if missing:
        sys.exit(f"Missing column(s): {missing}. Found: {list(df.columns)}")
    df = df[[lower[c.lower()] for c in COLS]]
    df.columns = COLS

    for c in COLS:
        df[c] = df[c].fillna("").str.strip()

    ids = sorted({v for c in COLS for v in df[c] if v})
    print(f"Distinct IDs to convert: {len(ids)}")

    mapping = lookup_liens(ids)

    liens = lambda v: mapping.get(v, (None, None))[0] if v else None
    etype = lambda v: mapping.get(v, (None, None))[1] if v else None
    out = pd.DataFrame({
        "OLDID": df["OldID"],
        "OLD_bvdlien": df["OldID"].map(liens),
        "OLD_type": df["OldID"].map(etype),
        "NEWID": df["NewID"],
        "NEW_bvdlien": df["NewID"].map(liens),
        "NEW_type": df["NewID"].map(etype),
    })

    not_found = sorted(i for i in ids if mapping.get(i, (None,))[0] is None)
    print(f"Converted: {len(ids) - len(not_found)} | No BvdLiens ID: {len(not_found)}")

    with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        out.to_excel(w, index=False, sheet_name=OUT_SHEET)
    print(f"Sheet '{OUT_SHEET}' written to {path}")


if __name__ == "__main__":
    main()
