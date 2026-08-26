from jobcollector.models import Job


def test_dedupe_key_uses_external_id():
    a = Job(title="Engineer", company="Acme", url="https://x/jobs/1", external_id="42", source="remotive")
    b = Job(title="Engineer", company="Acme", url="https://x/jobs/1", external_id="42", source="remotive")
    assert a.dedupe_key == b.dedupe_key == "remotive:42"


def test_dedupe_key_hash_fallback_is_stable_and_sensitive():
    a = Job(title="Engineer", company="Acme", url="https://x/jobs/1", source="careers:Acme")
    b = Job(title="Engineer", company="Acme", url="https://x/jobs/1", source="careers:Acme")
    c = Job(title="Engineer", company="Acme", url="https://x/jobs/2", source="careers:Acme")
    assert a.dedupe_key == b.dedupe_key
    assert a.dedupe_key != c.dedupe_key


def test_dedupe_key_separates_sources():
    a = Job(title="Engineer", company="Acme", url="https://x/jobs/1", external_id="42", source="remotive")
    b = Job(title="Engineer", company="Acme", url="https://x/jobs/1", external_id="42", source="remoteok")
    assert a.dedupe_key != b.dedupe_key
