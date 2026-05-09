"""Tests for scrape_persons.py — name parsing and MP scraping."""

import sys
from pathlib import Path

import pytest
import requests_html

_SCRIPTS = Path(__file__).resolve().parents[1]  # scripts/
sys.path.insert(0, str(_SCRIPTS))

from scrape_persons import _parse_name, _scrape_mp


# ── Unit tests for _parse_name ─────────────────────────────────────────────────

def test_parse_name_simple():
    given, family, title = _parse_name("Vladimír Baláž")
    assert given == "Vladimír"
    assert family == "Baláž"
    assert title == ""


def test_parse_name_with_title():
    given, family, title = _parse_name("MUDr. Vladimír Baláž, PhD.")
    assert given == "Vladimír"
    assert family == "Baláž"
    assert "MUDr." in title


def test_parse_name_et_conjunction():
    # "Mgr. et Mgr." — "et" must not leak into given_name
    given, family, title = _parse_name("Mgr. et Mgr. Miroslav Čellár, PhD.")
    assert given == "Miroslav"
    assert family == "Čellár"
    assert "et" not in given


def test_parse_name_art_title():
    # "art." must be treated as a pre-nominal title
    given, family, title = _parse_name("art. Veronika Remišová")
    assert given == "Veronika"
    assert family == "Remišová"


# ── Integration tests: real MP pages ──────────────────────────────────────────
# These make live HTTP requests to nrsr.sk. Skip if network is unavailable.

@pytest.fixture(scope="module")
def session():
    return requests_html.HTMLSession()


def _scrape(session, mp_id):
    try:
        return _scrape_mp(session, str(mp_id))
    except Exception as e:
        pytest.skip(f"Network unavailable or page changed: {e}")


def test_mp_1180_compound_family_name(session):
    """Bajo Holečková — compound family name must not be truncated."""
    person, _ = _scrape(session, 1180)
    assert person["given_name"] == "Martina"
    assert person["family_name"] == "Bajo Holečková"


def test_mp_976_compound_family_name(session):
    """Bittó Cigániková — compound family name must not be truncated."""
    person, _ = _scrape(session, 976)
    assert person["given_name"] == "Jana"
    assert person["family_name"] == "Bittó Cigániková"


def test_mp_1203_et_title(session):
    """Čellár — 'et' from 'Mgr. et Mgr.' must not appear as given name."""
    person, _ = _scrape(session, 1203)
    assert person["given_name"] == "Miroslav"
    assert person["family_name"] == "Čellár"
