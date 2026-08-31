# Card Desk

A local Windows desktop program for turning card photos into reviewed inventory and selling drafts.

**Start:** double-click `Start Card Desk.cmd`. The environment has been installed on this computer. On another Windows computer, install Python 3.12 or newer and run `Setup Card Desk.cmd` first.

Read **[USER GUIDE.md](USER%20GUIDE.md)** for the full workflow and limits. **[EBAY SETUP.md](EBAY%20SETUP.md)** is prep work (account + API keys) for a future eBay publishing feature — not built yet.

## Included

- Batch photo/folder import; JPEG, PNG, WebP, BMP, TIFF; front/back/detail photos; duplicate file detection.
- Optional local Wi-Fi phone upload page: photograph cards on your phone and they import automatically as new front photos, no cable or file transfer step.
- Local Windows OCR; optional OpenAI vision extraction, disabled by default.
- Pokémon / TCGdex, Magic / Scryfall, and Yu-Gi-Oh! / YGOPRODeck catalog adapters.
- Exact printing selection, structured card specifics, full provider metadata and recognition evidence.
- Condition and identity review gates, grading-label fields, stock, storage locations, tags and costs.
- Variant-specific price references, timestamps, configurable condition factors, manually verified sold comparables. Asking price is applied automatically once reviewed, grossed up over the underlying item value to cover configured shipping, packaging and marketplace fee; quick-sale price and estimated net proceeds included. Shipping defaults to a standard USPS envelope stamp and can be overridden per card.
- Draft listings, general CSV (sorted by game, set and collector number), a photo catalog PDF (grouped the same way), and a selling ZIP with photos categorized by game/set/SKU.
- Partial/full sales with stock reduction and a local transaction ledger.
- Permanent deletion for a card you no longer want (blocked if it has recorded sales, to protect the ledger — archive those instead).
- SQLite persistence, audit history, backups and restoration instructions.
- Packaged as a single Windows .exe (Build Executable.cmd) for computers without Python installed.

## Honest limits

This is a review-first inventory tool, not an appraisal or authentication service. Photo recognition can be wrong. Finishes, special editions, languages, grading and condition can require physical inspection. It does not guarantee every printing is in a provider catalog, scrape sold-marketplace histories, or automatically publish listings.

Sports and other collectibles support manual records, optional visible-text vision extraction and manual sold comparables; they do not yet have dedicated catalog integrations. TCGdex Pokémon TCG Pocket digital cards are excluded from search.

OpenAI cloud mode requires the user's own separately billed API key and photo-processing consent. Keys are kept in memory (or read from an environment variable), never stored in the database. No paid cloud request was made during development because no key was configured.

## Development

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --smoke-test --data-dir work\smoke-data
.\.venv\Scripts\python.exe main.py --ocr-selftest path\to\photo.jpg
.\.venv\Scripts\python.exe main.py --phone-selftest
.\.venv\Scripts\python.exe main.py --pdf-selftest
.\.venv\Scripts\python.exe work\verify_live.py
.\.venv\Scripts\python.exe work\verify_ui.py
```

Live verification needs internet access. Tests use temporary databases and do not modify the real collection. The UI test exercises native Tk views and dialogs; screen captures are optional when the Windows session supports them.

**Building a standalone .exe:** run `Build Executable.cmd`. It installs PyInstaller into `.venv` if needed, builds `dist\Card Desk.exe` as a single file (PyInstaller `--onefile --windowed`), and copies the user guide and README next to it. The exe creates `data`, `exports` and `backups` next to wherever it is placed (via `sys.executable`, not the temporary PyInstaller extraction folder), so copying just the `.exe` without the rest of the source tree still keeps its own collection. Local Windows OCR (`winrt`), the phone-upload HTTPS server (`cryptography`), QR generation (`qrcode`) and the PDF catalog (`reportlab`) were all verified working inside the frozen build via `--ocr-selftest`, `--phone-selftest` and `--pdf-selftest`.

## Project map

| Path | Purpose |
| --- | --- |
| `main.py` | Desktop entry point |
| `cardscanner/ui.py` | Native views, review dialogs and background jobs |
| `cardscanner/scanning.py` | Photo intake, Windows OCR and optional cloud extraction |
| `cardscanner/phoneintake.py` | Local Wi-Fi HTTP server for phone photo uploads |
| `cardscanner/providers.py` | Catalog and pricing adapters, request pacing and caching |
| `cardscanner/domain.py` | Pricing, readiness, listing and currency logic |
| `cardscanner/storage.py` | SQLite, stock, audit, sales, CSV, packages and backups |
| `tests/test_core.py` | Regression tests for data integrity and pricing safeguards |
| `data/` | Your local database and imported photo copies; excluded from source control |
| `exports/`, `backups/` | Suggested destinations for user exports |
| `work/` | Development checks and isolated test data; not your inventory |

Provider calls use HTTPS and fixed public API hosts. GET responses are cached for 24 hours; explicit price refresh bypasses the cache. Requests are paced, use descriptive headers and have timeouts. Price suggestions exclude stale source data where the provider supplies timestamps.

## Source documentation

- [TCGdex card fields and prices](https://tcgdex.dev/reference/card), [search/filter API](https://tcgdex.dev/rest/filtering-sorting-pagination), [pricing caveats](https://tcgdex.dev/faq).
- [Scryfall API](https://scryfall.com/docs/api), [card search](https://scryfall.com/docs/api/cards/search). Live API responses were also checked during integration.
- [YGOPRODeck API](https://api.ygoprodeck.com/api-guide/): the card-wide prices may be the lowest across multiple versions, so those are references only.
- [Microsoft Windows OCR](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine?view=winrt-26100).
- [OpenAI image input](https://developers.openai.com/api/docs/guides/images-vision), [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
