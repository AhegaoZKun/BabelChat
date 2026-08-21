# BabelChat

**Chat in a language you don't read, translated where you're already looking.**

---

## What it does

Someone types this in Trade:

> `wtb gilded crest, cod`

BabelChat appends the meaning, in grey, on the same line:

> `wtb gilded crest, cod`&nbsp;&nbsp;`wtb = Куплю · gilded = Золочёная эмблема · crest = Эмблема +1`

No second line, no colour spam, no arrows. The original stays readable, copy-chat
still works, and a busy Trade channel stays a Trade channel.

That is the **addon on its own** — 383 gaming terms in 14 languages. No account,
no key, no companion app, nothing to configure.

Add the **free companion app** and whole sentences get translated too, in an
overlay above WoW.

---

## Install and you're done

1. Install the addon.
2. Play.

That's the whole setup. The gloss uses your WoW client's language automatically —
a Russian client gets Russian, a German client gets German.

Want to change something? `/babel config`.

---

## Full-sentence translation (optional)

Grab the companion app from [GitHub](https://github.com/Yumash/BabelChat). It
reads the addon's chat buffer and shows translations in an overlay. Read-only —
it never writes to WoW's memory, never injects, never automates. Same approach as
WeakAuras Companion.

It needs a translation service. **All four have a free tier**, and you only need
one:

| Service | What it asks for | Free allowance |
|---|---|---|
| **GigaChat** *(default)* | a Sber ID — no card, no VPN, works from Russia ([how to get one](https://github.com/Yumash/BabelChat/blob/main/docs/user/gigachat.md)) | 1M tokens / year |
| **MyMemory** | nothing at all | 5,000 words / day (50,000 with an email) |
| **DeepL** | a card to verify identity — never charged on Free | 500K characters / month |
| **Microsoft Translator** | an Azure account | 2M characters / month |

Configure more than one and they cover for each other: if your preferred service
is down or out of allowance, the next one is tried instead of the message being
dropped.

**You can also just not configure any of them.** MyMemory needs no account, so
sentence translation works on first launch, and the in-game glossary works
regardless.

---

## What's in the glossary

**383 terms across 13 categories, in 14 languages**, plus every zone and item-set
name from LibBabble.

| Category | Examples | Terms |
|---|---|---|
| Social | ty, gg, brb, wp, omw | 71 |
| Classes & specs | dk, ret, bm, disc, resto | 59 |
| Raid & dungeon | wipe, prog, soak, kite, brez | 54 |
| Slang | ez, copium, bricked, w2w | 48 |
| Combat | aggro, aoe, cc, dot, cleave | 33 |
| Groups | lfm, lf1m, premade, pug | 29 |
| Endgame & Midnight | delve, m+, keystone, catalyst, warband | 22 |
| Stats | crit, haste, mastery, vers | 19 |
| Professions | jc, bs, enchant, herb, alch | 17 |
| Status | afk, oom | 11 |
| Trade | wtb, wts, bis, mats, cod | 8 |
| Roles | tank, healer, dps | 7 |
| Guild | gm, officer, recruit, gbank | 5 |

**Languages:** English, Russian, Spanish (ES/MX), German, French, Italian,
Portuguese, Polish, Swedish, Norwegian, Korean, Chinese (Simplified &
Traditional).

Each category has its own toggle, so you can switch off the ones you already
know.

---

## Details that matter in practice

- **Item links survive.** Nothing inside a hyperlink or a colour block is ever
  touched, so a linked item name is never half-glossed.
- **Punctuation doesn't hide terms.** `dps/heal`, `gg,wp` and `brb/afk` all
  match — that's how people actually type.
- **Capital letters work.** A sentence starting with `Спс` finds the term `спс`.
- **One entry per term.** `ty ty ty` says `ty = спасибо` once, not three times.
- **In message order**, capped at three terms plus a count, so one chat line
  stays one chat line.
- **The addon goes quiet when the app is running**, so the same message isn't
  answered twice in different words. Override it in `/babel config` if you want
  both.

---

## Commands

| Command | What it does |
|---|---|
| `/babel` | help |
| `/babel config` | settings panel |
| `/babel on` / `off` | glossary on or off |
| `/babel test` | try it on a sample message |
| `/babel companion` | companion buffer status |

---

## Privacy

The addon stores nothing but your settings. The companion app sends message text
to whichever translation service you configured — that's what translation is —
and nothing else leaves your machine. It does not write chat to disk unless you
switch that on yourself, and the checkbox tells you the file will contain other
players' whispers.

Administrator rights are **not** required.

---

## Credits

The glossary began as [WoW Translator](https://www.curseforge.com/wow/addons/wow-translator)
by **Pirson** (MIT), which contributed 314 terms across 14 languages. BabelChat
added slang and a current Endgame/Midnight set, rewrote the matching engine and
the way the gloss reads, and built the companion app.

- **Pirson** — glossary data and the in-game translation idea — [Buy Me a Coffee](https://buymeacoffee.com/franciscorb)
- **Andrey Yumashev** — addon, companion app, translation engine
- **AhegaoZKun** — Linux/Wayland support, Microsoft Translator backend

MIT License.

---
---

# BabelChat · по-русски

**Чат на языке, которого ты не читаешь — переведённый там, куда ты и так смотришь.**

## Что он делает

Кто-то пишет в Торговле:

> `wtb gilded crest, cod`

BabelChat дописывает серым, на той же строке:

> `wtb gilded crest, cod`&nbsp;&nbsp;`wtb = Куплю · gilded = Золочёная эмблема · crest = Эмблема +1`

Без второй строки, без цветного мусора, без стрелок. Оригинал остаётся читаемым,
копирование чата работает, и Торговля остаётся Торговлей.

Это **аддон сам по себе** — 383 игровых термина на 14 языках. Без аккаунта, без
ключей, без приложения, без настройки.

Поставь ещё и **бесплатное приложение-компаньон** — и переводиться будут целые
фразы, в оверлее поверх WoW.

## Установил — и всё

1. Поставь аддон.
2. Играй.

Язык подсказки берётся у клиента WoW сам: русский клиент — русские подсказки.
Что-то поменять — `/babel config`.

## Перевод целыми фразами (по желанию)

Приложение лежит на [GitHub](https://github.com/Yumash/BabelChat). Оно читает
буфер аддона и показывает перевод в оверлее. Только чтение — ничего не пишет в
память WoW, ничего не внедряет, ничего не автоматизирует.

Ему нужен сервис перевода. **У всех четырёх есть бесплатный тариф**, и нужен
только один:

| Сервис | Что просит | Бесплатно |
|---|---|---|
| **GigaChat** *(по умолчанию)* | Sber ID — без карты, без VPN, работает из России ([как получить](https://github.com/Yumash/BabelChat/blob/main/docs/user/gigachat_ru.md)) | 1 млн токенов в год |
| **MyMemory** | ничего | 5 000 слов в день (50 000 с указанным e-mail) |
| **DeepL** | карту для подтверждения личности — на Free не списывает | 500 тыс. символов в месяц |
| **Microsoft Translator** | аккаунт Azure | 2 млн символов в месяц |

Настроишь несколько — они подстрахуют друг друга: если основной лёг или исчерпал
лимит, пробуется следующий, а не теряется сообщение.

**Можно и вовсе ничего не настраивать.** MyMemory не требует аккаунта, так что
перевод фраз работает с первого запуска, а внутриигровой словарь — всегда.

## Что в словаре

**383 термина в 13 категориях на 14 языках**, плюс все названия зон и комплектов
из LibBabble. У каждой категории свой переключатель.

| Категория | Примеры | Терминов |
|---|---|---|
| Общение | ty, gg, brb, wp, omw | 71 |
| Классы и специализации | dk, ret, bm, disc, resto | 59 |
| Рейды и подземелья | wipe, prog, soak, kite, brez | 54 |
| Сленг | ez, copium, bricked, w2w | 48 |
| Бой | aggro, aoe, cc, dot, cleave | 33 |
| Группы | lfm, lf1m, premade, pug | 29 |
| Эндгейм и Midnight | delve, m+, keystone, catalyst | 22 |
| Характеристики | crit, haste, mastery, vers | 19 |
| Профессии | jc, bs, enchant, herb, alch | 17 |
| Состояние | afk, oom | 11 |
| Торговля | wtb, wts, bis, mats, cod | 8 |
| Роли | tank, healer, dps | 7 |
| Гильдия | gm, officer, recruit, gbank | 5 |

## Мелочи, которые заметны на практике

- **Ссылки на предметы не ломаются** — внутрь гиперссылок и цветовых блоков
  подсказка не лезет.
- **Пунктуация не прячет термины.** `dps/heal`, `gg,wp`, `brb/afk` — находятся
  все, потому что так и пишут.
- **Заглавные работают.** Фраза, начинающаяся со `Спс`, находит термин `спс`.
- **Один термин — одна запись.** `ty ty ty` даёт `ty = спасибо` один раз.
- **В порядке сообщения**, максимум три термина и счётчик — строка чата остаётся
  строкой.
- **Аддон молчит, пока работает приложение**, чтобы одно сообщение не переводилось
  дважды разными словами. Нужны оба — включи в `/babel config`.

## Приватность

Аддон не хранит ничего, кроме настроек. Приложение отправляет текст сообщений
тому сервису перевода, который ты настроил, — в этом и состоит перевод — и
больше никуда ничего не уходит. Чат на диск не пишется, пока ты сам это не
включишь, и галочка честно предупреждает, что в файл попадут чужие шёпоты.

Права администратора **не нужны**.

## Благодарности

Словарь вырос из [WoW Translator](https://www.curseforge.com/wow/addons/wow-translator)
от **Pirson** (MIT) — оттуда 314 терминов на 14 языках. BabelChat добавил сленг и
актуальный набор Эндгейм/Midnight, переписал движок сопоставления и то, как
читается подсказка, и получил приложение-компаньон.

- **Pirson** — данные словаря и сама идея перевода в игре — [Buy Me a Coffee](https://buymeacoffee.com/franciscorb)
- **Andrey Yumashev** — аддон, приложение, движок перевода
- **AhegaoZKun** — поддержка Linux/Wayland, бэкенд Microsoft Translator

Лицензия MIT.
