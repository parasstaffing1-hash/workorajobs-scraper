"""Probe major job-portal endpoints to verify which are reachable and keyless."""
import httpx

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
client = httpx.Client(headers=UA, timeout=25, follow_redirects=True)


def probe(label, url, **kw):
    try:
        r = client.request("GET", url, **kw)
        ct = r.headers.get("content-type", "")
        body = r.text[:60000]
        sig = []
        for m in ["application/json", "rss", "xml", "job", "Job", "position", "title"]:
            if m in body:
                sig.append(m)
        print(f"{label:34} {r.status_code} {ct[:38]:38} len={len(r.text):8} {' '.join(dict.fromkeys(sig))[:60]}")
        return r
    except Exception as e:
        print(f"{label:34} ERR {type(e).__name__}: {str(e)[:60]}")
        return None


probe("themuse.com/api/public/jobs", "https://www.themuse.com/api/public/jobs?page=1")
probe("themuse jobs p1", "https://www.themuse.com/api/public/jobs?page=1")
probe("monster rss", "https://rss.jobsearch.monster.com/rssquery.ashx?q=software+engineer&cy=us")
probe("jooble (no key -> 401?)", "https://jooble.org/api/")
probe("reed (no key -> 401?)", "https://www.reed.co.uk/api/1.0/search?q=software")
probe("working nomads", "https://www.workingnomads.com/api/exposed_jobs/")
probe("working nomads tech", "https://www.workingnomads.com/api/exposed_jobs/?category=programming")
probe("remote.co rss", "https://remote.co/remote-jobs/rss-feed/")
probe("nodesk rss", "https://nodesk.co/remote-jobs/feed/")
probe("weworkremotely rss", "https://weworkremotely.com/categories/remote-programming-jobs.rss")
probe("uk civil service", "https://www.civilservicejobs.service.gov.uk/csr/index.cgi?SID=3&search=1")
probe("eures api", "https://api.eures.europa.eu/search/v1/jobs?query=software")
probe("powertofly", "https://powertofly.com/jobs")
probe("jobspresso rss", "https://jobspresso.co/feed/")
probe("dice", "https://www.dice.com/jobs?q=python")
probe("ziprecruiter", "https://www.ziprecruiter.com/jobs?q=python")
probe("careerbuilder", "https://www.careerbuilder.com/jobs?q=python")
