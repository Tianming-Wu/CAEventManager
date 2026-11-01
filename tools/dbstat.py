import sqlite3
import sys

db = sys.argv[1]
c = sqlite3.connect(db)
c.row_factory = sqlite3.Row
print("  students:", c.execute("select count(*) from students").fetchone()[0])
print("  marks   :", c.execute("select count(*) from marks").fetchone()[0])
print("  tickets :", c.execute("select count(*) from tickets where released_at is null").fetchone()[0], "live")
print("  draws   :", c.execute("select count(*) from draws").fetchone()[0])
row = c.execute("select value from config where key='tie_seed'").fetchone()
print("  tie seed:", row[0] if row else "-")
row = c.execute("select value from config where key='pool_mode'").fetchone()
print("  pool md :", row[0] if row else "-")
print("  pool    :", c.execute("select value from config where key='pool_size'").fetchone()[0])
