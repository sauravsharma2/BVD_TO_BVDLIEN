"""
Convert OldID + NewID columns of an Excel file to BvdLiens IDs.
Adds a new sheet "BvDLiens" with columns OldID, NewID (both converted).

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


@connect_db
def lookup_liens(conn: pyodbc.Connection, ids: list[str]) -> dict[str, str]:
    """Send all distinct IDs in one go, convert server-side, return {bvdid: liens_id}."""
    cur = conn.cursor()
    cur.execute("create table #ids (bvdid varchar(50))")
    cur.fast_executemany = True
    cur.executemany("insert into #ids (bvdid) values (?)", [(i,) for i in ids])
    rows = cur.execute(
        "select bvdid, bvdaffils.dbo.GetBvDLiensID(bvdid) as liens from #ids"
    ).fetchall()
    cur.close()
    return {r.bvdid: r.liens for r in rows}


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

    conv = lambda v: mapping.get(v) if v else None
    out = pd.DataFrame({
        "OLDID": df["OldID"],
        "OLD_bvdlien": df["OldID"].map(conv),
        "NEWID": df["NewID"],
        "NEW_bvdlien": df["NewID"].map(conv),
    })

    not_found = sorted(i for i in ids if mapping.get(i) is None)
    print(f"Converted: {len(ids) - len(not_found)} | No BvdLiens ID: {len(not_found)}")

    with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as w:
        out.to_excel(w, index=False, sheet_name=OUT_SHEET)
    print(f"Sheet '{OUT_SHEET}' written to {path}")


if __name__ == "__main__":
    main()
