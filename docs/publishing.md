# Publishing the addon

BabelChat ships to two addon sites. Both render Markdown, so **both get the same
text**: [`store-description.md`](../store-description.md) in the repository root.

There is deliberately no per-site copy. Two long bilingual descriptions drift
within one release — the same failure this project has hit repeatedly with
duplicated data — and a store page that describes a version nobody is running is
worse than a short one.

The description is bilingual on purpose: neither site supports per-locale pages,
and a large part of the audience reads Russian.

## What is checked automatically

`tests/test_addon_toc_and_libs.py` fails the build if any document, the store
description included, states a term count that disagrees with `Data/*.lua`,
tells the user to run as administrator, or documents the pre-registry
`config.json` shape. That covers the numbers and the two claims that were
actually wrong in the past — it does not read the prose for you.

## Before a release

1. Bump `## Version` in `addon/BabelChat/BabelChat.toc` and `VERSION` in
   `app/about_dialog.py`. A test asserts they match: the addon is installed by
   hand, so `## Version` is the only way a bug report can say which is running.
2. Check `## Interface` against the live client. `test_the_interface_version_matches_the_client_that_is_installed`
   reads `.build.info` and does this for you when a client is installed; on CI
   it skips, so on a machine with no WoW it is on you.
3. Add the release to `CHANGELOG.md`.
4. Re-read `store-description.md` against what actually changed. The tests catch
   stale numbers, not stale claims.

## CurseForge

- Project category: **Chat & Communication**.
- Paste `store-description.md` into the description field.
- The upload is automated but **commented out** in `.github/workflows/release.yml`
  — see the `curseforge:` job. Enable it after creating the project, and add
  these repository secrets:
  - `CF_API_KEY` — from CurseForge Authors → API Tokens
  - `CF_PROJECT_ID` — shown on the project page
- Once the project exists, add its id to the TOC so the client-side updaters can
  match the installed copy:

  ```
  ## X-Curse-Project-ID: <id>
  ```

## Wago Addons

- Create the addon at <https://addons.wago.io>, then add its id to the TOC:

  ```
  ## X-Wago-ID: <id>
  ```

  Wago's packager uses this to associate an upload with the project. Do not
  commit a placeholder — a wrong id is metadata that ships to players and is
  harder to notice than a missing one.
- Paste the same `store-description.md`.
- Wago reads `## Interface`, `## Title`, `## Notes` and the localised `Notes-*`
  variants straight from the TOC, so those are the strings that appear in
  search results. They are already localised for ruRU, esES and esMX.

## The zip

`release.yml` builds `BabelChat-Addon.zip` containing the `addon/BabelChat`
directory. Both sites expect exactly that shape: one top-level folder whose name
matches the TOC filename.

`addon/BabelChat/README.md` travels inside that zip, so it is what someone sees
after unpacking by hand. Keep it short — the full pitch lives on the store page.
