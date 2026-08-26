import json

import httpx
import pytest

from jobcollector.sources.ats_api import _parse_workday_posted_on, fetch_ats_api

GREENHOUSE = {
    "jobs": [
        {
            "id": 8077887,
            "title": "Senior Backend Engineer",
            "location": {"name": "Remote, US"},
            "absolute_url": "https://stripe.com/jobs/search?gh_jid=8077887",
            "departments": [{"name": "Engineering"}],
            "updated_at": "2026-08-01T12:00:00Z",
        }
    ]
}

ASHBY = {
    "jobs": [
        {
            "id": "d3bc1ced",
            "title": "Staff Fullstack Engineer",
            "location": "Europe",
            "jobUrl": "https://jobs.ashbyhq.com/linear/d3bc1ced",
            "publishedAt": "2026-08-10T09:00:00+00:00",
            "employmentType": "FullTime",
            "department": "Product",
            "team": "Engineering",
            "descriptionPlain": "Build great things.",
            "compensation": {"compensationTier": {"summary": "$180k - $220k"}},
        }
    ]
}

BAMBOOHR = {
    "meta": {"totalCount": 1},
    "result": [
        {
            "id": 42,
            "jobOpeningName": "Account Manager",
            "location": {"city": "Austin", "state": "TX"},
        }
    ]
}

LEVER = [
    {
        "id": "abc123",
        "text": "Product Designer",
        "hostedUrl": "https://jobs.lever.co/acme/abc123",
        "categories": {"location": "Remote", "allLocations": ["Remote", "Berlin"], "commitment": "Full-time"},
        "descriptionPlain": "Design products.",
        "createdAt": 1714550400000,
    }
]

WORKDAY = {
    "total": 1,
    "jobPostings": [
        {
            "title": "Store Manager",
            "externalPath": "/job/Somewhere/Store-Manager_JR001",
            "locationsText": "Melbourne, AU",
            "postedOn": "Posted 2 Days Ago",
            "bulletFields": ["JR001"],
        }
    ],
}


def _client(payload, content_type="application/json", status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request, headers={"content-type": content_type})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_greenhouse_api():
    with _client(GREENHOUSE) as client:
        jobs = fetch_ats_api(client, "greenhouse", "stripe")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.title == "Senior Backend Engineer"
    assert j.location == "Remote, US"
    assert j.tags == ["Engineering"]
    assert j.external_id == "8077887"
    assert j.dedupe_key == "greenhouse:stripe:8077887"


def test_ashby_api():
    with _client(ASHBY) as client:
        jobs = fetch_ats_api(client, "ashby", "linear")
    j = jobs[0]
    assert j.title == "Staff Fullstack Engineer"
    assert j.salary == "$180k - $220k"
    assert j.tags == ["Product", "Engineering"]
    assert j.posted_at is not None
    assert j.dedupe_key == "ashby:linear:d3bc1ced"


def test_bamboohr_api():
    with _client(BAMBOOHR) as client:
        jobs = fetch_ats_api(client, "bamboohr", "cbm")
    j = jobs[0]
    assert j.title == "Account Manager"
    assert j.location == "Austin, TX"
    assert j.url == "https://cbm.bamboohr.com/careers/42"


def test_bamboohr_non_json_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!doctype html><html></html>", request=request,
                              headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_ats_api(client, "bamboohr", "deadtenant") == []


def test_lever_api():
    with _client(LEVER) as client:
        jobs = fetch_ats_api(client, "lever", "15five")
    j = jobs[0]
    assert j.title == "Product Designer"
    assert j.location == "Remote, Berlin"
    assert j.posted_at is not None  # epoch ms handled


def test_workday_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = request.read()
        assert b'"offset": 0' in body or b'"offset":0' in body
        return httpx.Response(200, json=WORKDAY, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(client, "workday", "7eleven|wd3|7eleven")
    j = jobs[0]
    assert j.title == "Store Manager"
    assert j.url == "https://7eleven.wd3.myworkdayjobs.com/7eleven/job/Somewhere/Store-Manager_JR001"
    assert j.location == "Melbourne, AU"
    assert j.external_id == "JR001"
    assert j.posted_at is not None


def test_workday_bad_slug():
    with _client({}) as client:
        with pytest.raises(ValueError):
            fetch_ats_api(client, "workday", "badslug")


def test_workday_relative_date_parser():
    assert _parse_workday_posted_on("Posted Today") is not None
    assert _parse_workday_posted_on("Posted 3 Weeks Ago") is not None
    assert _parse_workday_posted_on("Posted 2 Months Ago") is not None
    assert _parse_workday_posted_on("") is None


def test_unknown_kind():
    with _client({}) as client:
        with pytest.raises(KeyError):
            fetch_ats_api(client, "nope", "x")


SMARTRECRUITERS = {
    "content": [
        {
            "id": "abc",
            "name": "Senior Backend Engineer",
            "refNumber": "R1001",
            "releasedDate": "2026-08-01T12:00:00Z",
            "location": {"fullLocation": "London, United Kingdom"},
            "department": {"label": "Engineering"},
            "function": {"label": "Engineering"},
            "typeOfEmployment": {"label": "Full-time"},
        }
    ]
}

WORKABLE = {
    "name": "Acme",
    "jobs": [
        {
            "title": "Backend Developer",
            "shortcode": "SH1",
            "url": "https://apply.workable.com/j/SH1",
            "shortlink": "https://apply.workable.com/j/SH1",
            "department": "Eng",
            "city": "Berlin",
            "country": "Germany",
            "published_on": "2026-08-02",
        }
    ],
}

BREEZY = [
    {
        "id": "b1",
        "friendly_id": "backend-dev",
        "name": "Backend Developer",
        "url": "https://duolingo.breezy.hr/p/backend-dev",
        "published_date": "2026-08-03T12:00:00Z",
        "location": {"name": "Jakarta, ID"},
        "department": "Engineering",
        "type": {"name": "Full-time"},
        "company": {"name": "Duolingo"},
        "description": "<p>Build things</p>",
    }
]

TEAMTAILOR = {
    "items": [
        {
            "id": 1,
            "title": "Data Engineer",
            "url": "https://storytel.teamtailor.com/jobs/1",
            "date_published": "2026-08-04T12:00:00Z",
            "content_html": "<p>Data</p>",
            "_jobposting": {
                "hiringOrganization": {"name": "Storytel"},
                "jobLocation": [{"address": {"addressLocality": "Stockholm"}}],
            },
        }
    ]
}

HIREHIVE = {
    "items": [
        {
            "id": "job_abc",
            "title": "Support Engineer",
            "location": "Cork",
            "country": {"name": "Ireland"},
            "description": {"text": "Support users"},
            "hosted_url": "https://hirehive.hirehive.com/job/job_abc",
            "published_date": "2026-08-05T12:00:00Z",
        }
    ]
}

RECRUITEE = {
    "offers": [
        {
            "id": 5,
            "title": "Full-Stack Engineer",
            "city": "Warsaw",
            "country": "Poland",
            "remote": True,
            "department": "R&D",
            "careers_url": "https://auditdata.recruitee.com/o/fullstack",
            "description": "Build stuff",
            "published_at": "2026-08-06 10:00:00 UTC",
            "company_name": "Auditdata",
        }
    ]
}

RISE = {
    "result": {
        "count": 1,
        "jobs": [
            {
                "_id": "r1",
                "title": "Customer Success Manager",
                "url": "https://joinrise.io/job/r1",
                "locationAddress": "US",
                "createdAt": "2026-08-07T12:00:00Z",
                "owner": {"companyName": "Jobgether"},
            }
        ],
    }
}


def test_smartrecruiters_api():
    with _client(SMARTRECRUITERS) as client:
        jobs = fetch_ats_api(client, "smartrecruiters", "canva")
    j = jobs[0]
    assert j.title == "Senior Backend Engineer"
    assert j.location == "London, United Kingdom"
    assert j.tags == ["Engineering", "Engineering", "Full-time"]
    assert j.external_id == "R1001"
    assert j.dedupe_key == "smartrecruiters:canva:R1001"


def test_workable_api():
    with _client(WORKABLE) as client:
        jobs = fetch_ats_api(client, "workable", "huzzle")
    j = jobs[0]
    assert j.title == "Backend Developer"
    assert j.location == "Berlin, Germany"
    assert j.tags == ["Eng"]
    assert j.external_id == "SH1"
    assert j.dedupe_key == "workable:huzzle:SH1"


def test_breezy_api():
    with _client(BREEZY) as client:
        jobs = fetch_ats_api(client, "breezy", "duolingo")
    j = jobs[0]
    assert j.title == "Backend Developer"
    assert j.company == "Duolingo"
    assert j.location == "Jakarta, ID"
    assert j.tags == ["Engineering", "Full-time"]
    assert j.dedupe_key == "breezy:duolingo:b1"


def test_teamtailor_api():
    with _client(TEAMTAILOR) as client:
        jobs = fetch_ats_api(client, "teamtailor", "storytel")
    j = jobs[0]
    assert j.title == "Data Engineer"
    assert j.company == "Storytel"
    assert j.location == "Stockholm"
    assert j.dedupe_key == "teamtailor:storytel:1"


def test_hirehive_api():
    with _client(HIREHIVE) as client:
        jobs = fetch_ats_api(client, "hirehive", "hirehive")
    j = jobs[0]
    assert j.title == "Support Engineer"
    assert j.location == "Cork, Ireland"
    assert j.dedupe_key == "hirehive:hirehive:job_abc"


def test_recruitee_api():
    with _client(RECRUITEE) as client:
        jobs = fetch_ats_api(client, "recruitee", "auditdata")
    j = jobs[0]
    assert j.title == "Full-Stack Engineer"
    assert "Remote" in j.location
    assert j.tags == ["R&D"]
    assert j.dedupe_key == "recruitee:auditdata:5"


def test_rise_api():
    with _client(RISE) as client:
        jobs = fetch_ats_api(client, "rise", "public")
    j = jobs[0]
    assert j.title == "Customer Success Manager"
    assert j.company == "Jobgether"
    assert j.source == "rise:public"
    assert j.dedupe_key == "rise:public:r1"


ADZUNA = {
    "count": 1,
    "results": [
        {
            "id": "a1b2",
            "title": "Platform Engineer",
            "company": {"display_name": "Acme Corp"},
            "location": {"area": ["Berlin", "Germany"]},
            "redirect_url": "https://adzuna.com/job/a1b2",
            "description": "<p>Build the platform.</p>",
            "created": "2026-08-10",
            "salary_min": 70000.0,
            "salary_max": 90000.0,
            "salary_currency": "EUR",
            "category": {"label": "IT Jobs"},
        }
    ],
}

USAJOBS = {
    "SearchResult": {
        "SearchResultCount": 1,
        "SearchResultItems": [
            {
                "MatchedObjectDescriptor": {
                    "PositionID": "1234567",
                    "PositionTitle": "IT Specialist (INFOSEC)",
                    "OrganizationName": "Cybersecurity & Infrastructure Security Agency",
                    "DepartmentName": "Department of Homeland Security",
                    "PositionLocationDisplay": "Arlington, VA",
                    "PositionURI": "https://www.usajobs.gov/GetJob/ViewDetails/1234567",
                    "PublicationStartDate": "2026-08-12",
                    "JobSummary": "Protect federal networks.",
                    "QualificationSummary": "Experience with NIST.",
                    "PositionRemuneration": [{"MinimumRange": 100000.0, "MaximumRange": 150000.0, "RateIntervalCode": "PA"}],
                }
            }
        ],
    }
}

JOBVITE_HTML = """
<!doctype html><html><body><ul class="jv-job-list">
<li><div class="jv-featured-job">
  <div class="jv-featured-job-title"><a href="/carfax/job/ojLDAfw1">Manager - Dealer Accounts</a></div>
  <div class="jv-featured-job-location">London, Ontario</div>
</div></li>
<li><div class="jv-featured-job">
  <div class="jv-featured-job-title"><a href="/carfax/job/ovMDAfwe">Backend Engineer</a></div>
  <div class="jv-featured-job-location">Centreville, Virginia</div>
</div></li>
</ul></body></html>
"""

ICIMS_HTML = """
<!doctype html><html><body><ul id="searchresultsContainer">
<li class="iCIMS_JobHeader">
  <div class="row"><div class="col-xs-10"><h3><a href="/jobs/23020/job">Staff Engineer</a></h3></div></div>
  <div class="col-xs-2"><span class="iCIMS_JobLocation">Denver, CO</span></div>
  <div class="col-xs-2"><span class="iCIMS_JobCategory">Engineering</span></div>
</li>
</ul></body></html>
"""


def test_adzuna_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "app_id=APP" in request.url.query.decode()
        return httpx.Response(200, json=ADZUNA, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(client, "adzuna", "de", api_keys={"adzuna_app_id": "APP", "adzuna_api_key": "KEY"})
    j = jobs[0]
    assert j.title == "Platform Engineer"
    assert j.company == "Acme Corp"
    assert j.location == "Berlin, Germany"
    assert j.tags == ["IT Jobs"]
    assert j.salary == "70,000 - 90,000 EUR"
    assert j.source == "adzuna:de"
    assert j.dedupe_key == "adzuna:de:a1b2"
    assert j.posted_at is not None


def test_adzuna_missing_keys():
    with _client({}) as client:
        with pytest.raises(RuntimeError):
            fetch_ats_api(client, "adzuna", "us")


def test_usajobs_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization-Key"] == "K"
        return httpx.Response(200, json=USAJOBS, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(
            client, "usajobs", "public",
            api_keys={"usajobs_api_key": "K", "usajobs_user_agent": "me@example.com"},
        )
    j = jobs[0]
    assert j.title == "IT Specialist (INFOSEC)"
    assert "Homeland Security" in j.company
    assert j.location == "Arlington, VA"
    assert j.source == "usajobs"
    assert j.external_id == "1234567"
    assert "Protect federal networks" in j.description
    assert j.salary
    assert j.dedupe_key == "usajobs:1234567"


def test_usajobs_keyword_param():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["Keyword"] == "software engineer"
        assert "Keyword" in request.url.query.decode()
        return httpx.Response(200, json=USAJOBS, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(
            client, "usajobs", "software engineer",
            api_keys={"usajobs_api_key": "K", "usajobs_user_agent": "me@example.com"},
        )
    assert len(jobs) == 1
    assert jobs[0].title == "IT Specialist (INFOSEC)"


def test_usajobs_public_omits_keyword():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Keyword" not in request.url.query.decode()
        return httpx.Response(200, json=USAJOBS, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(
            client, "usajobs", "public",
            api_keys={"usajobs_api_key": "K", "usajobs_user_agent": "me@example.com"},
        )
    assert len(jobs) == 1


def test_usajobs_missing_keys():
    with _client({}) as client:
        with pytest.raises(RuntimeError):
            fetch_ats_api(client, "usajobs", "public")


def test_jobvite_html():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=JOBVITE_HTML, request=request,
                              headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(client, "jobvite", "carfax")
    assert len(jobs) == 2
    j = jobs[0]
    assert j.title == "Manager - Dealer Accounts"
    assert j.location == "London, Ontario"
    assert j.external_id == "ojLDAfw1"
    assert j.url == "https://jobs.jobvite.com/carfax/job/ojLDAfw1"
    assert j.dedupe_key == "jobvite:carfax:ojLDAfw1"


def test_icims_html():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=ICIMS_HTML, request=request,
                              headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(client, "icims", "here")
    j = jobs[0]
    assert j.title == "Staff Engineer"
    assert j.location == "Denver, CO"
    assert j.tags == ["Engineering"]
    assert j.external_id == "23020"
    assert j.dedupe_key == "icims:here:23020"


def test_icims_bot_gate_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(405, text="<title>Human Verification</title>", request=request,
                              headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_ats_api(client, "icims", "here") == []


def _yc_page(jobs: list[dict]) -> str:
    import html as _html

    payload = {
        "props": {
            "company": {"name": "Stripe", "batch": "S09"},
            "jobPostings": jobs,
        }
    }
    return f'<!doctype html><html><body><div id="app" data-page="{_html.escape(json.dumps(payload))}"></div></body></html>'


YC_JOBS = [
    {
        "id": 47049,
        "title": "Frontend Engineer, Identity",
        "url": "/companies/stripe/jobs/jdBhPmD-frontend-engineer-identity",
        "location": "United States / Remote",
        "salaryRange": "$180k - $220k",
        "equityRange": "0.1% - 0.2%",
        "prettyRole": "Engineering",
        "createdAt": "2026-08-10T12:00:00Z",
    }
]


def test_yc_api():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_yc_page(YC_JOBS), request=request,
                              headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(client, "yc", "stripe")
    j = jobs[0]
    assert j.title == "Frontend Engineer, Identity"
    assert j.company == "Stripe"
    assert j.location == "United States / Remote"
    assert "equity" in j.salary.lower()
    assert j.tags == ["Engineering"]
    assert j.external_id == "47049"
    assert j.url == "https://www.ycombinator.com/companies/stripe/jobs/jdBhPmD-frontend-engineer-identity"
    assert j.dedupe_key == "yc:stripe:47049"


def test_yc_404_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch_ats_api(client, "yc", "openai") == []


LINKEDIN_HTML = """<!DOCTYPE html>
<li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:3952273769">
  <div class="base-search-card__info">
    <h3 class="base-search-card__title">Python Developer</h3>
    <h4 class="base-search-card__subtitle">Open Systems Technologies</h4>
    <div class="base-search-card__metadata">New York, NYActively Hiring1 week ago</div>
    <time datetime="2026-08-09">1 week ago</time>
    <a href="https://www.linkedin.com/jobs/view/python-developer-at-ost-3952273769"></a>
  </div>
</div></li>
"""


def test_linkedin_guest_search():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["keywords"] == "python"
        return httpx.Response(200, text=LINKEDIN_HTML, request=request,
                              headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(client, "linkedin", "python|United States|7")
    j = jobs[0]
    assert j.title == "Python Developer"
    assert j.company == "Open Systems Technologies"
    assert "New York" in j.location
    assert "Actively Hiring" not in j.location
    assert j.external_id == "3952273769"
    assert j.posted_at is not None
    assert j.dedupe_key == "linkedin:python:3952273769"


THEMUSE = {
    "page": 1,
    "page_count": 1,
    "count": 1,
    "results": [
        {
            "id": 18054997,
            "name": "Platform Engineer",
            "company": {"name": "Acme Corp"},
            "locations": [{"name": "Remote, US"}],
            "levels": [{"name": "Mid Level"}],
            "categories": [{"name": "Engineering"}],
            "publication_date": "2026-08-11T12:00:00Z",
            "refs": {"landing_page": "https://www.themuse.com/jobs/acme/platform-engineer"},
        }
    ],
}

WORKINGNOMADS = [
    {
        "id": "wn1",
        "title": "Senior Data Engineer",
        "company_name": "Lemon.io",
        "location": "Europe, North America",
        "url": "https://www.workingnomads.com/job/go/1792423/",
        "pub_date": "2026-08-10",
        "tags": "data science,azure,python",
        "category_name": "Development",
        "description": "<p>Build data pipelines</p>",
    }
]

REED = {
    "results": [
        {
            "jobId": 123456,
            "jobTitle": "Software Engineer",
            "employerName": "Acme UK",
            "locationName": "London",
            "jobUrl": "https://www.reed.co.uk/jobs/software-engineer/123456",
            "postedDate": "2026-08-09T00:00:00",
            "description": "Build software",
            "salary": "£60,000",
            "applicationType": "Online Application",
        }
    ]
}


def test_themuse_api():
    with _client(THEMUSE) as client:
        jobs = fetch_ats_api(client, "themuse", "public")
    j = jobs[0]
    assert j.title == "Platform Engineer"
    assert j.company == "Acme Corp"
    assert j.location == "Remote, US"
    assert j.tags == ["Mid Level", "Engineering"]
    assert j.source == "themuse"
    assert j.dedupe_key == "themuse:18054997"


def test_workingnomads_api():
    with _client(WORKINGNOMADS) as client:
        jobs = fetch_ats_api(client, "workingnomads", "public")
    j = jobs[0]
    assert j.title == "Senior Data Engineer"
    assert j.company == "Lemon.io"
    assert "data science" in j.tags
    assert "Build data pipelines" in j.description
    assert j.source == "workingnomads"
    assert j.dedupe_key == "workingnomads:wn1"


def test_reed_missing_keys():
    with _client({}) as client:
        with pytest.raises(RuntimeError):
            fetch_ats_api(client, "reed", "software")


def test_reed_api():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Basic ")
        return httpx.Response(200, json=REED, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_ats_api(client, "reed", "software", api_keys={"reed_api_key": "K"})
    j = jobs[0]
    assert j.title == "Software Engineer"
    assert j.company == "Acme UK"
    assert j.location == "London"
    assert j.salary == "£60,000"
    assert j.source == "reed:software"
    assert j.dedupe_key == "reed:software:123456"
