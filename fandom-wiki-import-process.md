# Process: importing a character roster from a Fandom/Wikia wiki

Notes from doing this for `List_of_characters_in_Fire_Emblem:_Three_Houses`
(see `fe3h-characters-import.json`, generated 2026-08-11). Follow this next
time instead of re-deriving it. Ingest format itself is documented in
`character-import.md` — this file is just the *scraping* playbook.

## 1. The wiki page itself is usually Cloudflare-blocked

Both `WebFetch` and a direct `curl` from this sandbox get served Cloudflare's
"Just a moment..." bot-check page (title `Just a moment...`, a
`challenges.cloudflare.com` CSP) instead of the article. Don't bother
retrying with different user agents — ask the user for the raw HTML instead
of guessing.

## 2. What the user hands back: a "diagnostics" JSON dump

The user's browser-side tool saves a file named like
`source-test-diagnostics-<host>-<timestamp>.json` into the project root.
Ignore the "diagnostic" framing — the payload is what matters. Shape:

```json
{
  "url": "...", "title": "...", "isEmpty": false, "downloadedAt": "...",
  "diagnostics": { "winner": "readability", "strategies": [...], "page": {...} },
  "originalHtml": "...",   // full raw page HTML, huge, lots of cruft
  "filteredHtml": "..."    // readability-extracted article body, much smaller
}
```

Use `filteredHtml` — it's the article content only and orders of magnitude
easier to regex against than `originalHtml` (which is the full rendered SPA
shell, nav chrome, cookie-consent widgets, etc).

No `bs4`/`lxml` in this sandbox — parse with plain `re` against the raw
markup. Attribute order inside `<img>` tags is **not consistent** (some have
`src` before `data-image-name`, others after) — parse each tag's attributes
into a dict with a generic `(\w[\w-]*)="([^"]*)"` regex rather than assuming
order, or a position-dependent capture will silently miss matches.

## 3. Two useful structures inside the article body

- **Section headers**: `<h2><span id="Section_Id">Label</span>...` /
  `<h3>` likewise — walk these to get each category's byte range, then
  extract images per-section rather than globally (needed to know which
  in-article group — Playable, NPC, Antagonist, etc. — a portrait belongs
  to, and to bound each section's content).
- **The navbox table near the end of the article** (search for
  `<table><tbody><tr><th>` near a recognizable first category like
  `Protagonist(s)`): a full canonical roster grouped by faction/category,
  as clean `<a title="Name">Name</a>` links. This is the authoritative name
  list — use it to catch typos/mislabels in the gallery `alt` text (e.g. the
  Fire Emblem page's Blue Lions gallery had a portrait file literally named
  `Doudou_Portrait.png` with `alt="Doudou Portrait"` — the navbox link
  confirmed the actual character is **Dedue**). This navbox is usually much
  bigger than the "characters" you actually want (locations, weapons,
  chapter names, merch) — only pull the character-shaped `<th>` rows out of
  it.

## 4. Portrait images: `<a href="FULL_URL"><img alt="..." ...>`

The `<a href>` wrapping each gallery `<img>` points at the full-resolution
revision (`.../revision/latest?cb=...`), not the scaled-down thumbnail in
`src` — use the `<a href>` for downloading, not `img@src`.

## 5. Confirm scope with the user before building 100+ entries

A "list of characters" wiki page is often much wider than the visible
gallery: navboxes/backstory sections include lore-only names with **no**
individual portrait, or a placeholder image (`NA.png`), or one **generic
image shared across several different named characters** (e.g. a shared
class-portrait used for multiple "Children of the Goddess", or two
different characters both captioned "Gremory portrait"). Don't guess which
name maps to a shared/ambiguous image — skip those entries rather than
invent a mapping. Ask the user up front how wide to go (playable cast only /
+ named NPCs & antagonists with their own art / literally everything
including the imageless lore entries) — saves redoing the whole batch.

## 6. Downloading images: the CDN is not behind the same block

`static.wikia.nocookie.net` (Fandom's image CDN) is reachable directly via
plain `curl`, even though the wiki page itself is Cloudflare-gated. No need
to ask the user for images separately once you have the HTML.

**Gotcha**: the CDN serves **WebP bytes even for URLs ending in `.png`**.
Don't trust the extension — check the real content
(`file --mime-type downloaded_file`) and set `image_mime` in the import JSON
to what the bytes actually are (`image/webp` was correct for all 95 in the
FE3H batch, despite every source URL saying `.png`). The import route uses
`image_mime` as-is, unsniffed — getting this wrong makes every row silently
mislabeled.

## 7. Validate locally before handing off

Before publishing the JSON, replicate the `character-import.md` rules in a
throwaway script: non-empty trimmed `name`, `image_base64` is valid base64,
`image_mime` is one of the four accepted types, `series`/`source_url` are
strings, ≤200 entries, and no duplicate `(name, series)` pairs within the
batch. Catches mistakes before the admin-UI round trip.

## Naming convention used

Output written to the project root (not committed) as
`<topic>-characters-import.json`, e.g. `fe3h-characters-import.json`.
