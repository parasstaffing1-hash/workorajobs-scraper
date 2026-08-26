import httpx
from bs4 import BeautifulSoup

from jobcollector.models import CompanyConfig
from jobcollector.sources.careers import CareersCrawler, extract_job

LISTING_HTML = """
<html><body>
<h1>Careers at Acme</h1>
<a href="/careers/">Careers home</a>
<a href="/about/">About us</a>
<a href="/careers/backend-engineer">Backend Engineer</a>
<a href="/careers/data-scientist">Data Scientist</a>
<a href="/blog/why-we-love-python">Blog post</a>
<a href="/careers/product-designer">Product Designer</a>
</body></html>
"""

DETAIL_HTML = """
<html><head><title>Backend Engineer | Acme</title></head>
<body>
<h1>Backend Engineer</h1>
<meta name="job-location" content="Berlin, Germany">
<p>We are looking for a Backend Engineer to build distributed systems.</p>
</body></html>
"""

LD_HTML = """
<html><head><title>Staff Engineer</title></head>
<body>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "JobPosting",
  "title": "Staff Engineer",
  "datePosted": "2024-05-01T09:00:00Z",
  "hiringOrganization": {"@type": "Organization", "name": "Acme GmbH"},
  "jobLocation": {"@type": "Place", "address": {
     "@type": "PostalAddress", "addressLocality": "Remote", "addressCountry": "DE"}},
  "baseSalary": {"@type": "MonetaryAmount", "currency": "EUR", "value": {"value": 120000}},
  "description": "A senior role."
}
</script>
</body></html>
"""


def _crawler(pages: dict[str, str]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path, "")
        if not body:
            return httpx.Response(404, request=request)
        return httpx.Response(200, text=body, request=request)

    return CareersCrawler(httpx.Client(transport=httpx.MockTransport(handler)))


def test_candidate_link_filtering():
    crawler = _crawler({"/careers/": LISTING_HTML})
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    company = CompanyConfig(name="Acme", careers_url="https://acme.com/careers/")
    links = crawler._candidate_links(soup, company.careers_url, company)
    assert "https://acme.com/careers/backend-engineer" in links
    assert "https://acme.com/careers/data-scientist" in links
    assert "https://acme.com/careers/product-designer" in links
    assert not any("/about/" in u for u in links)
    assert not any("/blog/" in u for u in links)
    assert not any(u.rstrip("/").endswith("/careers") for u in links)  # hub page itself


def test_extract_job_heuristics():
    job = extract_job(DETAIL_HTML, "https://acme.com/careers/backend-engineer", "Acme")
    assert job.title == "Backend Engineer"
    assert job.location == "Berlin, Germany"
    assert "distributed systems" in job.description
    assert job.source == "careers:Acme"


def test_extract_job_json_ld_takes_precedence():
    job = extract_job(LD_HTML, "https://acme.com/jobs/5", "Acme")
    assert job.title == "Staff Engineer"
    assert job.company == "Acme GmbH"
    assert job.location == "Remote, DE"
    assert job.salary == "EUR 120000"
    assert job.posted_at is not None


def test_boilerplate_hub_pages_rejected_in_fallback():
    """No-JSON-LD hub/marketing pages must not become fake jobs."""
    for title in ("Careers", "Open roles", "Company culture", "Current Mozilla job openings",
                  "Red Hat Jobs | Opportunities are open", "Navigation | Canonical",
                  "Canonical's hiring process", "Launch your career at the heart of open source",
                  "We hire for talent, passion, and work ethic", "Feel good about your work"):
        html = f"<html><head><title>{title}</title></head><body><h1>{title}</h1></body></html>"
        assert extract_job(html, "https://acme.com/careers/engineering", "Acme") is None, title


def test_real_job_title_not_flagged():
    html = "<html><head><title>Senior Backend Engineer | Acme</title></head><body><h1>Senior Backend Engineer</h1></body></html>"
    job = extract_job(html, "https://acme.com/careers/senior-backend-engineer", "Acme")
    assert job is not None and job.title == "Senior Backend Engineer"


def test_crawl_company_end_to_end():
    pages = {
        "/careers/": LISTING_HTML,
        "/careers/backend-engineer": DETAIL_HTML,
        "/careers/data-scientist": DETAIL_HTML,
        "/careers/product-designer": DETAIL_HTML,
    }
    crawler = _crawler(pages)
    company = CompanyConfig(name="Acme", careers_url="https://acme.com/careers/", max_pages=10)
    jobs = crawler.crawl([company])
    assert len(jobs) == 3
    assert all(j.url.endswith(("/backend-engineer", "/data-scientist", "/product-designer")) for j in jobs)
    assert not crawler.errors
