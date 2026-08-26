"""Probe enterprise ATS endpoints to nail their contracts before writing adapters."""
import sys, json, re
import httpx

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
client = httpx.Client(headers=UA, timeout=30, follow_redirects=True)

def probe(label, url, method="GET", **kw):
    try:
        r = client.request(method, url, **kw)
        ct = r.headers.get("content-type", "")
        body = r.text
        sig = []
        if r.status_code >= 400:
            sig.append(f"HTTP {r.status_code}")
        for marker in ["icims", "taleo", "jobvite", "successfactors", "jobSearch", "JobSearch", "__INITIAL_STATE__", "__NEXT_DATA__", "application/json", "window."]:
            if marker.lower() in body.lower()[:200000]:
                sig.append(marker)
        print(f"{label:35} {r.status_code} {ct[:45]:45} {', '.join(sig)[:120]}")
        return r
    except Exception as e:
        print(f"{label:35} ERROR {type(e).__name__}: {str(e)[:100]}")
        return None

print("=== iCIMS (Accept: application/json) ===")
for t in ["synchrony", "cvs", "chipotle", "truist", "slb", "citizensbank"]:
    probe(f"icims:{t}", f"https://careers.{t}.icims.com/jobs/search?ss=1&searchRelation=keywordAll&searchText=",
          headers={**UA, "Accept": "application/json"})

print("=== iCIMS HTML probe (cvs) ===")
r = probe("icims:cvs html", "https://careers.cvs.com/jobs/search?ss=1&searchRelation=keywordAll&searchText=")

print("=== Taleo ===")
for t, sec in [("oracle", "2"), ("chq", "external"), ("lumen", "external"), ("disney", "external")]:
    probe(f"taleo:{t}", f"https://{t}.taleo.net/careersection/{sec}/jobsearch.ftl?lang=en")

print("=== Jobvite ===")
for c in ["jobvite", "everbridge", "zendesk", "twilio", "pinterest", "netflix"]:
    probe(f"jobvite:{c}", f"https://jobs.jobvite.com/{c}/jobs")

print("=== SuccessFactors ===")
probe("sf:jobs.sap.com", "https://jobs.sap.com/career?company=SAP&career_ns=job_listing&navBarLevel=JOB_SEARCH")

print("=== Adzuna (no key -> expect 401/403, confirms contract) ===")
probe("adzuna:us p1", "https://api.adzuna.com/v1/api/jobs/us/search/1?app_id=x&app_key=x&results_per_page=10&content_type=application/json")

print("=== USAJobs (no key -> expect 401, confirms contract) ===")
probe("usajobs", "https://data.usajobs.gov/api/search?ResultsPerPage=5",
      headers={**UA, "Host": "data.usajobs.gov", "Authorization-Key": "x"})
