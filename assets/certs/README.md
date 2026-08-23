# Bundled trust anchor

## `russian_trusted_root_ca.pem`

The Russian Trusted Root CA, issued by the Ministry of Digital Development.
GigaChat is served behind it, and Python's `requests` does not use the Windows
certificate store — it uses the roots `certifi` ships, which do not and will not
include this one. Without this file the default translation provider cannot
connect at all, on a machine where every browser reaches the same host happily.

Asking each user to find and download it was the previous arrangement, and it
did not work. The obvious search result is the **Sub** CA, which is an
intermediate rather than a trust anchor, so pointing at it fails in exactly the
same way as pointing at nothing — and the app said only `tls_untrusted`, which
gives the reader no way to tell those two cases apart.

| | |
| --- | --- |
| Subject | `C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Root CA` |
| Self-signed | yes — it is the anchor |
| Valid | 2022-03-01 to 2032-02-27 |
| SHA-256 | `d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31` |
| Source | <https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt> |

**It is used for the GigaChat session and nothing else.** `verify=` is set on
that provider's own `requests.Session`, so DeepL, Microsoft and MyMemory keep
the stock trust store and the process-wide `REQUESTS_CA_BUNDLE` is never
touched. Bundling a root that widened trust across the whole process would be a
different and much worse thing than this.

A test asserts the file is present, is self-signed, has not expired, and still
has that fingerprint.

## Replacing it

Download the PEM from the source above and overwrite this file. The test will
say so if you have fetched an intermediate by mistake.
