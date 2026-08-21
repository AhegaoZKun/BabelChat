"""What the user typed, and what the connection trusts.

Two things sit here rather than in the provider: reading the credential
whichever way it was pasted, and finding the trust anchor the connection needs.
Both are about the values that arrive from outside the app — a settings form, a
downloaded file — and both had to be forgiving of what people actually do with
them.
"""

from __future__ import annotations

import base64
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)

# Certificate problems, named apart from each other: a handshake failure looks
# identical whether the file is an intermediate, the wrong format or missing,
# and reporting all three the same way leaves the reader guessing at the one
# thing they can fix.
CERT_UNREADABLE = "cert_unreadable"
CERT_MISSING = "cert_missing"
CERT_NOT_PEM = "cert_not_pem"
CERT_NOT_ROOT = "cert_not_root"


def authorization_key(settings: dict[str, str]) -> str:
    """The Basic-auth value GigaChat's OAuth endpoint expects.

    Sber's portal shows a Client ID and a Client Secret, and separately an
    "authorization key" that is just base64 of `id:secret`. Asking a player for
    the base64 form meant asking them to know what base64 is, and the field that
    did was the one people got stuck on.

    So the two obvious values are what the app asks for, and it does the
    encoding. A config saved before this still carries the encoded form, and
    that keeps working.
    """
    client_id = (settings.get("client_id") or "").strip()
    client_secret = (settings.get("client_secret") or "").strip()

    # The portal shows all three values on one page, and the authorization key
    # is the longest and most key-looking of them, so it is the one people
    # paste into a field marked "secret". Encoding it a second time produced a
    # header the server could not decode, and it said so as a bare 400.
    #
    # A real Client Secret cannot be mistaken for an authorization key: it is a
    # UUID, and the dashes are outside the base64 alphabet, so it does not
    # decode at all.
    for pasted in (client_secret, client_id):
        if pasted and split_authorization_key(pasted) != ("", ""):
            return pasted

    if client_id and client_secret:
        return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    return (settings.get("authorization_key") or "").strip()


def split_authorization_key(encoded: str) -> tuple[str, str]:
    """The Client ID and Secret inside an authorization key, if it holds them.

    Returns ("", "") for anything that does not decode to `id:secret` — a
    truncated paste, a key from somewhere else, or plain nonsense. The caller
    keeps the original in that case rather than throwing away a credential it
    merely failed to recognise.
    """
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return "", ""
    client_id, separator, client_secret = decoded.partition(":")
    if not separator or not client_id.strip() or not client_secret.strip():
        return "", ""
    return client_id.strip(), client_secret.strip()


#: The trust anchor GigaChat is served behind, shipped with the app.
#:
#: requests does not read the Windows certificate store — it uses the roots
#: certifi ships, and the Russian Trusted Root CA is not among them and will not
#: be. So on a machine where every browser reaches this host without complaint,
#: Python cannot, and the provider that exists because it works from Russia
#: without a VPN could not connect at all.
#:
#: Asking each user to fetch it did not work either: the first search result is
#: the Sub CA, an intermediate rather than an anchor, which fails identically to
#: supplying nothing. See assets/certs/README.md.
_BUNDLED_ROOT = "russian_trusted_root_ca.pem"


def bundled_root_certificate() -> str:
    """Absolute path to the shipped root, or "" if it is not on disk.

    Returning "" rather than raising is deliberate: a missing certificate should
    degrade to the stock trust store and a clear connection error, not stop the
    app from starting.
    """
    here = pathlib.Path(__file__).resolve().parent
    roots = [
        # PyInstaller unpacks bundled data next to the frozen executable.
        pathlib.Path(getattr(sys, "_MEIPASS", "")) / "assets" / "certs" / _BUNDLED_ROOT,
        here.parent.parent / "assets" / "certs" / _BUNDLED_ROOT,
    ]
    for path in roots:
        if path.is_file():
            return str(path)
    return ""


def describe_certificate(path: str) -> str:
    """Why this file cannot serve as a trust anchor, or "" if it can.

    Checked before the request rather than after, because the answer afterwards
    is the same opaque handshake failure whether the file is an intermediate,
    the wrong format, or absent — and "tls_untrusted" told the one user who hit
    this nothing at all. They had downloaded russian_trusted_sub_ca.cer, which
    is exactly the reasonable mistake.
    """
    if not path:
        return ""
    certificate = pathlib.Path(path)
    if not certificate.is_file():
        return CERT_MISSING
    try:
        text = certificate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return CERT_UNREADABLE
    if "BEGIN CERTIFICATE" not in text:
        return CERT_NOT_PEM
    if not _contains_a_root(text):
        return CERT_NOT_ROOT
    return ""


def _contains_a_root(pem: str) -> bool:
    """True if any certificate in the file is self-signed.

    A trust anchor is self-signed by definition. An intermediate is not, and is
    the file people actually download.
    """
    try:
        from cryptography import x509
    except ImportError:  # pragma: no cover - cryptography ships with requests' deps
        # Cannot tell, so do not claim to: let the handshake answer.
        return True

    blocks = pem.split("-----END CERTIFICATE-----")
    for block in blocks:
        marker = block.find("-----BEGIN CERTIFICATE-----")
        if marker == -1:
            continue
        single = block[marker:] + "-----END CERTIFICATE-----" + chr(10)
        try:
            certificate = x509.load_pem_x509_certificate(single.encode())
        except Exception:
            continue
        if certificate.subject == certificate.issuer:
            return True
    return False
