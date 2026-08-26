import sqlite3, time, os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jobs.db')
c = sqlite3.connect(db_path)
t1 = c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
print(f"DB at start: {t1:,}")
time.sleep(60)
t2 = c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
delta = t2 - t1
print(f"DB after 60s: {t2:,} (+{delta:,})")
print(f"Rate: {delta} new/min")
print(f"Gap to 1M: {1000000 - t2:,}")
if delta > 0:
    eta_hours = (1000000 - t2) / delta / 60
    print(f"ETA to 1M: {eta_hours:.1f} hours")
else:
    print("SCRAPER NOT PRODUCING - needs restart")
c.close()
