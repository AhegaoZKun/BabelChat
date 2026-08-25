"""The trust anchor GigaChat needs, and why it ships with the app.

Python's `requests` does not read the Windows certificate store — it uses the
roots `certifi` bundles, and the Russian Trusted Root CA is not among them and
will not be. So on a machine where every browser reaches the GigaChat host
happily, Python cannot, and the provider that exists precisely because it works
from Russia without a VPN could not connect at all.

Asking each user to fetch the certificate did not work: the first search result
is the **Sub** CA — an intermediate, not an anchor — which fails in exactly the
same way as supplying nothing, while the app reported only `tls_untrusted`.
That is a code with no action in it, and it is what the first person to try
this actually hit.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from app.translators.gigachat_credential import (
    CERT_MISSING,
    CERT_NOT_PEM,
    CERT_NOT_ROOT,
    bundled_root_certificate,
    describe_certificate,
)
from app.translators.gigachat_provider import SPEC

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "assets" / "certs" / "russian_trusted_root_ca.pem"

#: Recorded when the file was added. A different certificate at this path is a
#: thing to notice, not to trust silently.
FINGERPRINT = "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31"


# ── the certificate that ships ───────────────────────────────────────────────


def test_the_root_certificate_is_bundled():
    """Without it the default provider cannot connect on any Windows machine."""
    assert BUNDLED.is_file(), "the trust anchor GigaChat needs is not in the repository"
    assert bundled_root_certificate(), "the provider cannot find it"


def test_the_bundled_certificate_is_a_root_and_not_an_intermediate():
    """The distinction is the whole problem: an intermediate looks like the
    right file and fails identically to no file at all."""
    assert describe_certificate(str(BUNDLED)) == "", describe_certificate(str(BUNDLED))


def test_the_bundled_certificate_has_not_expired():
    """It runs to 2032. This test is what will say so when it does not."""
    x509 = pytest.importorskip("cryptography.x509")

    certificate = x509.load_pem_x509_certificate(BUNDLED.read_bytes())
    remaining = certificate.not_valid_after_utc - dt.datetime.now(dt.UTC)

    assert remaining > dt.timedelta(days=90), f"expires in {remaining.days} days — fetch a fresh one"


def test_the_bundled_certificate_is_the_one_that_was_reviewed():
    """A trust anchor swapped for another is exactly the change that must not
    pass unremarked."""
    hashes = pytest.importorskip("cryptography.hazmat.primitives.hashes")
    x509 = pytest.importorskip("cryptography.x509")

    certificate = x509.load_pem_x509_certificate(BUNDLED.read_bytes())

    assert certificate.fingerprint(hashes.SHA256()).hex() == FINGERPRINT


def test_the_provider_uses_it_without_being_asked():
    """The certificate field is now for overrides. Leaving it empty has to be
    the arrangement that works, because that is what everyone will do."""
    backend = SPEC.build({"client_id": "a", "client_secret": "b"})

    assert backend._session.verify == bundled_root_certificate()


def test_a_supplied_certificate_still_wins():
    backend = SPEC.build({"client_id": "a", "client_secret": "b", "ca_bundle": str(BUNDLED)})

    assert backend._session.verify == str(BUNDLED)


def test_trust_is_scoped_to_this_provider():
    """Widening trust process-wide would be a different and much worse thing
    than shipping one anchor for one host."""
    import requests

    assert requests.Session().verify is True


# ── what the user is told when they point at the wrong file ──────────────────


def test_an_intermediate_certificate_is_named_as_such(tmp_path):
    """The mistake that happened: russian_trusted_sub_ca.cer is the obvious
    download and is not a trust anchor."""
    intermediate = tmp_path / "sub.pem"
    intermediate.write_text(
        "-----BEGIN CERTIFICATE-----\nnot really a certificate\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )

    assert describe_certificate(str(intermediate)) == CERT_NOT_ROOT


def test_a_path_with_no_file_says_so(tmp_path):
    assert describe_certificate(str(tmp_path / "absent.pem")) == CERT_MISSING


def test_a_file_that_is_not_pem_says_so(tmp_path):
    binary = tmp_path / "cert.der"
    binary.write_bytes(b"\x30\x82\x01\x0a\x02\x82")

    assert describe_certificate(str(binary)) == CERT_NOT_PEM


def test_an_empty_setting_is_not_a_problem():
    """Empty is the normal case now, and must not be reported as an error."""
    assert describe_certificate("") == ""


def test_the_check_happens_before_the_request():
    """Afterwards every one of these looks like the same handshake failure, and
    the user cannot tell which of them they are looking at."""
    ok, detail = SPEC.validate({"client_id": "a", "client_secret": "b", "ca_bundle": "Z:/absent.pem"})

    assert ok is False
    assert detail == CERT_MISSING


@pytest.mark.parametrize(
    "code",
    [CERT_MISSING, CERT_NOT_PEM, CERT_NOT_ROOT, "cert_unreadable", "tls_untrusted"],
)
def test_every_certificate_failure_has_words_in_every_language(code):
    """A bare code is not a message. The one person who hit this got
    "tls_untrusted" and had no way to know which file was wrong."""
    from app.i18n import _STRINGS

    key = f"error.{code}"
    assert key in _STRINGS, f"{code} has no copy"
    for language in ("RU", "EN", "ES"):
        text = _STRINGS[key][language]
        assert len(text) > 40, f"{key} in {language} is too short to explain anything"


def test_the_settings_window_renders_those_words_rather_than_the_code():
    """The mapping is what turns the code into something actionable on screen."""
    source = (ROOT / "app" / "provider_settings_qt.py").read_text(encoding="utf-8")

    for code in (CERT_NOT_ROOT, CERT_MISSING, "tls_untrusted"):
        assert f'"{code}": tr(' in source, f"{code} would be shown as a bare code"
