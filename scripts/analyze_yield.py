import sqlite3
conn = sqlite3.connect('jobs.db')

# Board size distribution
rows = conn.execute("SELECT COUNT(*) c FROM jobs WHERE source_kind='ats' GROUP BY source").fetchall()
sizes = [r[0] for r in rows]
sizes.sort(reverse=True)

brackets = [(1,10,'1-10'), (11,50,'11-50'), (51,100,'51-100'), (101,500,'101-500'), (501,5000,'500+')]
for lo,hi,label in brackets:
    count = sum(1 for s in sizes if lo <= s <= hi)
    total = sum(s for s in sizes if lo <= s <= hi)
    print(f"  {label:10s} jobs/board: {count:4d} boards, {total:7d} total jobs")

print(f"\n  Total ATS boards: {len(sizes)}")
print(f"  Total ATS jobs: {sum(sizes):,}")
print(f"  Average jobs/board: {sum(sizes)/len(sizes):.1f}")
print(f"  Median jobs/board: {sizes[len(sizes)//2]}")

# Top 10 boards
rows = conn.execute("SELECT source, COUNT(*) c FROM jobs WHERE source_kind='ats' GROUP BY source ORDER BY c DESC LIMIT 10").fetchall()
print("\n  Top 10 boards:")
for src, c in rows:
    print(f"    {src}: {c:,}")

# Gap analysis
total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
fresh = conn.execute("SELECT COUNT(*) FROM jobs WHERE first_seen_at >= datetime('now','-7 days')").fetchone()[0]
print(f"\n  Total DB: {total:,}")
print(f"  Fresh 7d: {fresh:,}")
print(f"  Gap to 1M: {max(0, 1_000_000 - total):,}")

# If we could find N more boards, how many jobs?
avg = sum(sizes) / len(sizes)
big_avg = sum(s for s in sizes if s >= 100) / max(1, sum(1 for s in sizes if s >= 100))
small_avg = sum(s for s in sizes if s < 100) / max(1, sum(1 for s in sizes if s < 100))
gap = max(0, 1_000_000 - total)
print(f"\n  To close {gap:,} gap:")
print(f"    At {avg:.0f} avg jobs/board: need {int(gap/avg):,} more boards")
print(f"    At {big_avg:.0f} avg (100+ boards): need {int(gap/big_avg):,} more boards")
print(f"    At {small_avg:.0f} avg (<100 boards): need {int(gap/small_avg):,} more boards")
