import httpx

from jobcollector.sources.ats import _gh_id, fetch_ats

GREENHOUSE_NEW = """
<html><body>
<h2>3 jobs</h2>
<div class="job-posts"><table><tbody>
<tr class="job-post">
  <td class="cell"><a href="https://careers.acme.com/positions/123?gh_jid=123" target="_top">
    <p class="body body--medium">Senior Backend Engineer<span class="tag-text">New</span></p>
    <p class="body body__secondary body--metadata">Remote - USA</p>
  </a></td>
</tr>
<tr class="job-post">
  <td class="cell"><a href="https://careers.acme.com/positions/456?gh_jid=456" target="_top">
    <p class="body body--medium">Data Scientist</p>
    <p class="body body__secondary body--metadata">Berlin, Germany</p>
  </a></td>
</tr>
</tbody></table></div>
</body></html>
"""

GREENHOUSE_LEGACY = """
<html><body>
<div class="opening" data-company="acme">
  <a href="https://boards.greenhouse.io/acme/jobs/99">
    <div class="title">QA Engineer</div>
    <div class="location">London, UK</div>
    <div class="department">Quality</div>
  </a>
</div>
</body></html>
"""

LEVER_HTML = """
<html><body>
<a class="posting-title" href="https://jobs.lever.co/acme/77">
  <h5>Product Manager</h5>
  <h6>Remote / Berlin</h6>
</a>
</body></html>
"""


def _client(html: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_greenhouse_new_template():
    with _client(GREENHOUSE_NEW) as client:
        jobs = fetch_ats(client, "greenhouse", "acme")
    assert len(jobs) == 2
    first = jobs[0]
    assert first.title == "Senior Backend Engineer"  # trailing "New" tag stripped
    assert first.location == "Remote - USA"
    assert first.external_id == "123"
    assert first.source == "greenhouse:acme"


def test_greenhouse_legacy_template():
    with _client(GREENHOUSE_LEGACY) as client:
        jobs = fetch_ats(client, "greenhouse", "acme")
    assert len(jobs) == 1
    assert jobs[0].title == "QA Engineer"
    assert jobs[0].location == "London, UK"
    assert jobs[0].tags == ["Quality"]
    assert jobs[0].external_id == "99"


def test_lever_template():
    with _client(LEVER_HTML) as client:
        jobs = fetch_ats(client, "lever", "acme")
    assert len(jobs) == 1
    assert jobs[0].title == "Product Manager"
    assert jobs[0].location == "Remote / Berlin"
    assert jobs[0].source == "lever:acme"


def test_gh_id_extraction():
    assert _gh_id("https://instacart.careers/job/?gh_jid=4949335") == "4949335"
    assert _gh_id("https://boards.greenhouse.io/acme/jobs/99") == "99"
