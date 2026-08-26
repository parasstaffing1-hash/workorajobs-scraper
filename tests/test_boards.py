import httpx
import pytest

from jobcollector.sources.boards import fetch_board

REMOTIVE_PAYLOAD = {
    "jobs": [
        {
            "id": 123,
            "title": "Senior Python Dev",
            "company_name": "Acme",
            "url": "https://remotive.com/remote-jobs/123",
            "candidate_required_location": "Worldwide",
            "description": "<p>Build things</p>",
            "tags": ["python"],
            "category": "Software Development",
            "publication_date": "2024-05-01T10:00:00",
        }
    ]
}


def _client(payload, content_type="application/json"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_remotive_parsing():
    with _client(REMOTIVE_PAYLOAD) as client:
        jobs = fetch_board(client, "remotive")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Senior Python Dev"
    assert job.company == "Acme"
    assert job.location == "Worldwide"
    assert job.description == "Build things"
    assert job.source == "remotive"
    assert job.dedupe_key == "remotive:123"
    assert job.posted_at is not None


def test_unknown_board_raises():
    with _client({}) as client:
        with pytest.raises(KeyError):
            fetch_board(client, "not-a-board")


def test_client_side_keyword_filter():
    # arbeitnow has no server-side search param; keyword filters the feed locally.
    payload = {
        "data": [
            {
                "slug": "a1",
                "company_name": "Acme",
                "title": "Python Backend Engineer",
                "description": "<p>Build APIs</p>",
                "url": "https://arbeitnow.com/jobs/a1",
                "location": "Berlin",
                "tags": ["python"],
                "created_at": "2024-05-01T10:00:00",
            },
            {
                "slug": "a2",
                "company_name": "Beta",
                "title": "Rust Systems Engineer",
                "description": "<p>Low level</p>",
                "url": "https://arbeitnow.com/jobs/a2",
                "location": "Remote",
                "tags": ["rust"],
                "created_at": "2024-05-01T10:00:00",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_board(client, "arbeitnow|python", limit=200)
    assert len(jobs) == 1
    assert jobs[0].title == "Python Backend Engineer"


def test_comma_keywords_match_any():
    # "arbeitnow|python,react" keeps jobs mentioning python OR react — one fetch.
    payload = {
        "data": [
            {
                "slug": "c1", "company_name": "Acme", "title": "Python Dev",
                "description": "<p>x</p>", "url": "https://arbeitnow.com/jobs/c1",
                "location": "Remote", "tags": [], "created_at": "2024-05-01T10:00:00",
            },
            {
                "slug": "c2", "company_name": "Beta", "title": "React Dev",
                "description": "<p>y</p>", "url": "https://arbeitnow.com/jobs/c2",
                "location": "Berlin", "tags": [], "created_at": "2024-05-01T10:00:00",
            },
            {
                "slug": "c3", "company_name": "Gamma", "title": "Rust Dev",
                "description": "<p>z</p>", "url": "https://arbeitnow.com/jobs/c3",
                "location": "Remote", "tags": [], "created_at": "2024-05-01T10:00:00",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_board(client, "arbeitnow|python,react", limit=200)
    assert {j.title for j in jobs} == {"Python Dev", "React Dev"}


def test_server_side_keyword_param():
    # remotive supports ?search= — the keyword must hit the URL, not a local filter.
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=REMOTIVE_PAYLOAD, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_board(client, "remotive|rust", limit=200)
    assert "search=rust" in seen["url"]
    assert len(jobs) == 1  # payload is unfiltered; server-side search is trusted


def test_remoteok_epoch_date():
    payload = [{"id": "x1", "position": "Dev", "company": "Corp", "url": "https://r.ok/x1", "date": 1714550400}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_board(client, "remoteok")
    assert jobs[0].posted_at is not None
