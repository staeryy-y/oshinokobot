# Character bulk import spec

How to hand the admin UI a batch of characters in one shot — the format a
Claude scraping session (wiki/fandom pages, etc.) should produce so
`/admin/characters/import` can ingest it directly. Source of truth is
`app/admin/routes/characters.py`; this file is a stable reference for
whoever/whatever is generating the import file, so it doesn't need to go
read the route code to get the shape right.

## Where it goes

`POST /admin/characters/import`, logged in as an admin. The form on
`/admin/characters` accepts either:

- a `.json` file upload (`import_file` field), or
- pasted JSON text (`import_text` field)

Either one — whichever's easier to produce. If both are somehow present,
the file wins.

## Format

Top-level JSON object with a `characters` array:

```json
{
  "characters": [
    {
      "name": "Ai Hoshino",
      "series": "Oshi no Ko",
      "image_base64": "iVBORw0KGgoAAAANSUhEUgA...",
      "image_mime": "image/png",
      "source_url": "https://example.fandom.com/wiki/Ai_Hoshino"
    }
  ]
}
```

A bare array (no wrapping `{"characters": [...]}`) is **not** accepted —
this shape was picked over a bare top-level array specifically so a
`version` or other metadata key could be added later without breaking it.

### Fields

| Field | Required | Type | Notes |
|---|---|---|---|
| `name` | yes | string | Non-empty after trimming. |
| `image_base64` | yes | string | Raw base64, **no** `data:image/...;base64,` prefix — just the encoded bytes. |
| `series` | no | string | Omit or `null` if unknown. |
| `image_mime` | no | string | One of `image/png`, `image/jpeg`, `image/webp`, `image/gif`. Defaults to `image/png` if omitted. Must be accurate — it's used as-is to pick the file extension, never sniffed from the image bytes. |
| `source_url` | no | string | Stored for provenance/audit only (where the data came from). Not validated as a real URL, not used functionally. |

Max **200 entries** per batch — a request over that limit is rejected
outright (nothing imported) with an error naming the count. Split larger
batches into multiple import files.

## What happens to each entry

The whole batch is processed in one pass; one bad entry never blocks the
rest. Every entry ends up in exactly one of three states, reported back in
a per-row results table after submit:

- **`imported`** — new character created.
- **`skipped`** (not an error) — an existing character already matches on
  `(name, series)`, case-insensitive. Duplicate detection is exact-ish:
  trims whitespace and ignores case, nothing fuzzier (no typo tolerance).
- **`error`** — one of:
  - missing/empty `name`
  - missing/empty `image_base64`
  - `image_base64` isn't valid base64
  - `series`, `source_url`, or `image_mime` present but not a string
  - `image_mime` not one of the four supported types

There is no dry-run/preview mode — a submitted batch commits valid rows
immediately. Re-running the same file is safe (matching entries just get
reported as `skipped` on the second pass, nothing is duplicated).

## Producing image_base64

Whatever you're using to fetch the image, base64-encode the raw bytes and
strip any data-URL wrapper. Example, if you already have the image on disk
as `ai_hoshino.png`:

```bash
base64 -i ai_hoshino.png | tr -d '\n'
```

...and paste the resulting string as `image_base64`, with `image_mime` set
to whatever the actual content type is (`image/png` for a `.png`,
`image/jpeg` for a `.jpg`/`.jpeg`, etc.) — not guessed from the file
extension by the importer, so get it right on the way in.
