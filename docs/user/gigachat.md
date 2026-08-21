# Getting a GigaChat key

*[По-русски](gigachat_ru.md)*

GigaChat is what BabelChat uses by default, because it is the only one of the
four services that works from Russia without a VPN and does not ask for a card.
Free for individuals: **1M tokens a year**, which is roughly 50,000–70,000
translated chat messages.

All it needs is a Sber ID — the same login as SberBank Online.

It takes about five minutes. If you get stuck,
[open an issue](https://github.com/Yumash/BabelChat/issues) — that usually means
these instructions are bad, not you.

---

## Step 1. Sign in to Studio

Open <https://developers.sber.ru/studio/workspaces> and sign in with your
**Sber ID**.

You will be asked to confirm a phone number by SMS. No card, no passport, no
company details.

## Step 2. Create a GigaChat API project

In the workspace, click **Create project** and pick **GigaChat API**.

When asked which access type, choose the **individual** tier
(`GIGACHAT_API_PERS`). That is the free one; the corporate options are paid and
you do not need them.

## Step 3. Copy the Client ID and Client Secret

The project page shows two values:

| What | What it looks like |
| --- | --- |
| **Client ID** | a long string with dashes, like `01234567-89ab-cdef-0123-456789abcdef` |
| **Client Secret** | the same shape, but **shown only once** |

> **The Client Secret appears only when you create the project.** Copy it
> straight away. If you closed the page without saving it, nothing is lost —
> click "generate a new one": the old secret stops working and you get a fresh
> one immediately.

The same page may also show an "authorization key" — a long string, often ending
in `==`. **You do not need it.** That is just those two values joined and
encoded, and BabelChat does the encoding itself.

## Step 4. Paste them into BabelChat

Open **Settings → General → Translation services** and find the **GigaChat**
block:

1. Paste the first value into **Client ID**.
2. Paste the second into **Client Secret**.
3. Press **Validate**.

You should get a green "valid". That's it.

To have GigaChat tried first, pick it in **Preferred translator** at the bottom
of the block. Any other services you configured stay as fallbacks: if the
preferred one fails or runs out, the message goes to the next rather than being
dropped.

---

## If it doesn't work

### "Invalid key" right after pasting

Nearly always the two fields swapped, or a stray space. Copy both values again,
whole, with no leading or trailing spaces.

The other possibility is that the project was created with corporate access
instead of `GIGACHAT_API_PERS`. Check the scope on the project page.

### A certificate or TLS error

**Most likely there is nothing to do** — the root certificate this needs ships
inside the app, and the "Your own root certificate" field can stay empty.

If you do see a certificate error, here is what is going on.

GigaChat is served behind the **Russian Trusted Root CA**, and Python does not
use the Windows certificate store — it uses the roots `certifi` bundles, which
do not include that one and will not. So a browser on the same machine reaches
GigaChat happily while the app cannot. We ship the certificate so you never have
to go looking for it.

**The common mistake when looking manually:** the first search result is
`russian_trusted_sub_ca`, which is an *intermediate* certificate, not a root. It
does not work — a trust anchor has to be self-signed. If you point at it, the
app now says so in as many words.

If you genuinely need your own (a corporate proxy, say), take the **root** PEM
from <https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt>

### Out of allowance

1M tokens a year is a lot, but if you do run out nothing breaks: the other
services you configured take over. **MyMemory** needs no registration at all and
is always available, so you will not be left with no translation.

### No translation appears at all

That is not about the key. Check, in order:

1. The addon is enabled in WoW (character select → **AddOns**).
2. The overlay's title bar says "WoW: connected".
3. The channel they are typing in is enabled in **Settings → Channels** —
   Trade and General are off by default.

---

## What is sent to GigaChat

The text of the messages being translated, and nothing else. Your character
name, your realm and everything else stay on your machine.

Full-sentence translation is optional in the first place: the built-in glossary
of gaming terms works with no network and no key at all.
