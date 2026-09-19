"""The canary must stop scraper.main.run() before any API write when a run
scrapes nothing (or nothing survives validation)."""

import pytest

from scraper import api, company as company_validation, job_validator, main
from scraper.validate import CanaryError


@pytest.fixture
def no_api(monkeypatch, tmp_path):
    """Stub the API/ANAF so a test never touches the network, record upserts,
    and run inside a throwaway directory (run() writes docs/jobs.md + company.json)."""
    monkeypatch.chdir(tmp_path)
    upserts = []
    monkeypatch.setattr(api, "query_solr", lambda cif: {"numFound": 0, "docs": []})
    monkeypatch.setattr(api, "upsert_jobs", lambda jobs: upserts.append(jobs))
    # manageCompany and staleJobDeletion now default to True (Category 1 audit),
    # so a bare main.run() would hit these too.
    monkeypatch.setattr(api, "upsert_company", lambda doc: None)
    monkeypatch.setattr(api, "delete_job_by_url", lambda url: None)
    monkeypatch.setattr(
        job_validator, "validate_by_content",
        lambda url, **kw: {"url": url, "status": "active", "httpStatus": 200, "title": None, "error": None},
    )
    monkeypatch.setattr(
        company_validation,
        "validate_and_get_company",
        lambda **kw: {"status": "active", "company": "EXAMPLE CO", "cif": "12345678", "address": ""},
    )
    return upserts


def test_zero_scraped_raises_canary_and_never_upserts(monkeypatch, no_api):
    monkeypatch.setattr(main, "scrape_careers", lambda: [])
    with pytest.raises(CanaryError):
        main.run()
    assert no_api == []


def test_all_jobs_invalid_also_raises_canary(monkeypatch, no_api):
    # scraped something, but every item is unpublishable -> still a canary
    monkeypatch.setattr(main, "scrape_careers", lambda: [
        {"url": "not-a-url", "title": ""},
        {"url": "", "title": "no url"},
    ])
    with pytest.raises(CanaryError):
        main.run()
    assert no_api == []


def test_valid_jobs_reach_upsert(monkeypatch, no_api):
    monkeypatch.setattr(main, "scrape_careers", lambda: [
        {"url": "https://jobs.example.com/careers/widget-engineer/", "title": "Widget Engineer"},
    ])
    count = main.run()
    assert count == 1
    assert len(no_api) == 1 and no_api[0][0]["title"] == "Widget Engineer"


def test_dry_run_scrapes_and_validates_but_does_not_upsert(monkeypatch, no_api):
    monkeypatch.setattr(main, "scrape_careers", lambda: [
        {"url": "https://jobs.example.com/careers/x/", "title": "X"},
    ])
    main.run(dry_run=True)
    assert no_api == []


def test_dry_run_makes_no_real_writes_at_all(monkeypatch, tmp_path):
    """Regression: dry_run=True must reach every real-write call site --
    upsert_company, upsert_jobs, delete_job_by_url, delete_jobs_by_cif -- not
    just the job upsert. Covers both an active/manageCompany=True scenario and
    an ANAF-inactive-company scenario in one test."""
    monkeypatch.chdir(tmp_path)
    calls = {"upsert_company": 0, "upsert_jobs": 0, "delete_job_by_url": 0, "delete_jobs_by_cif": 0}
    monkeypatch.setattr(api, "upsert_company", lambda doc: calls.__setitem__("upsert_company", calls["upsert_company"] + 1))
    monkeypatch.setattr(api, "upsert_jobs", lambda jobs: calls.__setitem__("upsert_jobs", calls["upsert_jobs"] + 1))
    monkeypatch.setattr(api, "delete_job_by_url", lambda url: calls.__setitem__("delete_job_by_url", calls["delete_job_by_url"] + 1))
    monkeypatch.setattr(api, "delete_jobs_by_cif", lambda cif: calls.__setitem__("delete_jobs_by_cif", calls["delete_jobs_by_cif"] + 1))
    monkeypatch.setattr(
        job_validator, "validate_by_content",
        lambda url, **kw: {"url": url, "status": "active", "httpStatus": 200, "title": None, "error": None},
    )
    monkeypatch.setattr(api, "query_solr", lambda cif: {"numFound": 3, "docs": [{"url": "https://ours.example/x/"}]})
    monkeypatch.setattr(main, "scrape_careers", lambda: [
        {"url": "https://jobs.example.com/careers/x/", "title": "X"},
    ])

    # Scenario 1: active company, manageCompany=True -- would upsert_company AND upsert_jobs on a real run.
    monkeypatch.setattr(
        company_validation,
        "validate_and_get_company",
        lambda **kw: {"status": "active", "company": "EXAMPLE CO", "cif": "12345678", "address": ""},
    )
    monkeypatch.setitem(main.scraper, "manageCompany", True)
    main.run(dry_run=True)
    assert calls == {"upsert_company": 0, "upsert_jobs": 0, "delete_job_by_url": 0, "delete_jobs_by_cif": 0}

    # Scenario 2: ANAF-inactive company -- would delete_job_by_url (own jobs) on a real run.
    # (delete_jobs_by_cif lives inside company.validate_and_get_company itself, already
    # covered directly in tests/test_company.py's dry-run regression test.)
    monkeypatch.setattr(
        company_validation,
        "validate_and_get_company",
        lambda **kw: {"status": "inactive", "company": "EXAMPLE CO", "cif": "12345678", "existingJobsCount": 3},
    )
    main.run(dry_run=True)
    assert calls == {"upsert_company": 0, "upsert_jobs": 0, "delete_job_by_url": 0, "delete_jobs_by_cif": 0}
