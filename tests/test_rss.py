import httpx

from jobcollector.sources.rss import fetch_feed

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Jobs</title>
    <item>
      <title>Staff Engineer</title>
      <link>https://example.com/jobs/1</link>
      <guid>https://example.com/jobs/1</guid>
      <author>Example Corp</author>
      <pubDate>Tue, 01 May 2024 10:00:00 GMT</pubDate>
      <description>&lt;p&gt;A great role.&lt;/p&gt;</description>
      <category>Engineering</category>
    </item>
  </channel>
</rss>
"""


def test_rss_parsing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FEED, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_feed(client, {"name": "Example", "url": "https://example.com/feed"})
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Staff Engineer"
    assert job.company == "Example Corp"  # entry author wins over feed name
    assert job.source == "rss:Example"
    assert job.description == "A great role."
    assert job.tags == ["Engineering"]
    assert job.posted_at is not None
