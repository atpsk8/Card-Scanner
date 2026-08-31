# Card Desk user guide

Your project is installed at:

`C:\Users\Andrew Price\Desktop\Card scanner`

## 1. Open the app

Double-click **Start Card Desk.cmd**. Do not move just the launcher; it needs the rest of the project folder. If the environment is missing, run **Setup Card Desk.cmd** with an internet connection first.

The app runs as a native desktop program. There is no web server, account login or public deployment. Your working inventory starts empty. Test cards live only in separate development folders and temporary databases.

## 2. Import and scan

1. Open **Scan & import**.
2. Keep **Local OCR** selected to start without a paid API key. Choose a game hint if the collection is mostly one game.
3. Leave **Scan each front photo after import** checked.
4. Choose front photos or a folder. Each file is treated as one card front; folder import reads that folder, not nested subfolders.
5. Read the per-file report. Imported records stay saved even if recognition or an online catalog request fails.

Import front images only in a batch. Add backs through the individual review window so they are not mistaken for new inventory. Photograph one card per image with its full border, name and collector number visible. Multi-card photos are not segmented. JPEG, PNG, WebP, BMP and TIFF are accepted up to 25 MB and 40 megapixels. HEIC/HEIF must first be exported to JPEG.

Original files are never edited. The program saves its own normalized JPEG copy, oriented correctly, with EXIF metadata removed and the longest edge limited to 2,600 pixels. Keep your original photos if you need archival-resolution originals. Identical original file bytes are detected as duplicates and skipped; visually similar photos are not deduplicated.

Local OCR reads text, not artwork. A single matching name/number candidate can fill the draft automatically, but always requires review. Multiple printings stay in the candidate chooser. No match is also a valid result: use manual catalog search or enter details yourself.

### Scan directly from your phone

In **Receive from phone** on the Scan & import page, click **Start phone upload**. Card Desk shows a QR code and a link with a 6-digit code, for example `https://192.168.1.23:8420/?key=482913`. Scan the QR code with your phone's camera (no separate scanner app needed on modern iOS/Android) and it connects and authenticates automatically, no typing required. Your phone still needs the **same Wi-Fi network** as this computer. If you'd rather not scan, tap **Copy link** to send the URL to your phone another way (for example through a synced clipboard app) and type it in — the QR code is just a shortcut for the same link.

The link is `https://`, using a certificate this computer generates for itself (needed for live camera access — see below). Your phone will show a privacy/certificate warning the first time: tap **Advanced** (or **Details**), then **Proceed** / **visit this site**. This is expected for a self-made certificate and does not mean the page is exposed to anyone outside your Wi-Fi.

**Hands-free mode:** if your phone's browser supports it, the page opens a live camera view instead of a photo button. Put your phone on a stand pointed at your cards. Place a card in frame and hold it still for under a second — it captures automatically, and the page then waits for you to swap in the next card (it needs to see the card change before it will auto-capture again, so it won't fire twice on the same still card). A **Capture now** button is always available for the first card of a session or if lighting makes auto-capture unreliable, and **Pause auto-capture** turns it off if you'd rather trigger every shot yourself. If the phone's browser can't use the live camera (older browser, camera permission denied, or the security warning wasn't accepted), the page automatically falls back to the original tap-to-photograph button — nothing further to configure either way.

Each photo — automatic or manual — uploads and imports here as a new front photo, using whatever Recognition method and Card game hint are currently set above. Card Desk keeps working normally while this runs; imported cards land in Review queue as usual, and you can keep browsing other pages while photos trickle in.

Only front photos come in this way. Add back and close-up photos afterward from each card's review window, as usual. Click **Stop phone upload** when you're done scanning; this also happens automatically when you close Card Desk. No photo leaves your local network — the page is served only on your Wi-Fi, and nothing is uploaded to the internet. If auto-capture is too twitchy or too slow to trigger for your lighting/camera, that is tuned by two numbers in `cardscanner/phoneintake.py` (`STILL_THRESHOLD`, `MOTION_THRESHOLD`) that can be adjusted.

Received originals are also kept in `data/phone-incoming/` alongside the app's own normalized copies. That folder is not cleaned up automatically; delete old files from it yourself occasionally if it grows large. Doing so does not affect already-imported cards, which have their own separate copy.

## 3. Review the exact card

Double-click a row in Inventory or Review queue.

- **Identity:** choose the catalog printing. Check set, collector number, rarity, language, finish, edition/stamp, year and other details. Pokémon catalog set codes can differ from the printed abbreviation; leave that filter blank if unsure. Search results are limited to 80 candidates, so narrow a long list by number or set.
- **Condition & stock:** select the condition, write visible defects, enter quantity, purchase cost, location and tags. For a slabbed card, enter the grading company, the grade printed on the label and the certificate. These are recorded claims, not authenticated grades.
- Add back and close-up photos as needed. **View full photo** opens your imported copy in the system photo viewer.
- Click **Save details**, **Confirm identity**, and **Confirm condition** after checking the physical card.
- **Evidence & history** keeps OCR text, full catalog metadata, source-price information, manually entered comparables and an audit log. **Delete this card permanently** is here too, for a scan you don't want to keep — it removes the record, its photos and identification history and cannot be undone. It refuses to delete a card with recorded sales, so the sales ledger can't be lost this way; archive that card instead. The same action is available as **Delete selected** from the Inventory/Review queue/Selling table for removing several at once. **Archive** (in the table's action row) is the reversible alternative — it hides a card without deleting anything, and it can be restored from the Archived filter.

Save changes before confirming. Editing identity or condition clears the corresponding confirmation and asking price. A formerly Ready/Listed record moves back to Needs review. This prevents using a price approved for another printing or condition. Attaching a new photo also resets review.

Only group copies with the same printing, finish, language and condition in one quantity. For different wear or special variants, create separate records. Exact duplicate image files are deliberately not reusable for multiple records, so photograph the actual copies separately.

## 4. Understand prices

Click **Refresh source prices** in the Pricing tab. A refresh records source quotes; it never overwrites your approved asking price.

| Evidence | How the app uses it |
| --- | --- |
| Matching recent sold comparables that you entered and verified | Uses median item price; must match printing, finish, language, condition and grade. Includes sales within 90 days. |
| Eligible catalog guide for the exact linked printing/finish/language/currency | Applies your configured condition factor to create an indicative asking price. |
| Mixed-version floors or ambiguous edition/language data | Shows as a reference only; does not generate a selling suggestion. |
| Missing or stale prices | Shows that more evidence is needed, not a zero-dollar valuation. |
| Graded cards | Raw-card quotes are never used; enter matching graded sales or a manual ask. |

**The asking price fills in automatically** as soon as identity and condition are confirmed and a defensible price exists (a matching sold comparable or an eligible fresh catalog quote) — no button click required. It is not just the raw comp/catalog number: it is grossed up so that, after your configured marketplace fee, fixed fee, shipping and packaging costs, you still net the underlying item value — so offering free shipping or absorbing the marketplace's cut doesn't quietly cost you part of what the card is worth. **Suggested ask** is a starting point, not a guaranteed fair value, and remains editable — type over it and save if you want a different number, and the app will not overwrite a price you've already set. **Quick-sale price** is the suggested ask multiplied by your configurable target percentage. Defaults are illustrative assumptions, not statistically calibrated pricing models.

The initial settings use USD, a 13.25% variable fee, a 0.30 fixed fee, 0.82 shipping (a standard USPS First-Class Mail Forever stamp, as of mid-2026), and 0.25 packaging per item. Change these to your actual marketplace and shipping method — and update the shipping figure whenever USPS changes postage rates, since this app has no way to check that automatically. The net estimate subtracts those costs and your acquisition cost. It excludes taxes and does not model marketplace-specific shipping/tax fee bases. These fee amounts are not a claim about any particular marketplace's current rates.

A single card can override the account-wide shipping figure in its own Pricing tab — useful for something that needs its own box or a bulkier/tracked service (a slab, a bulky mailer) instead of the default envelope. Leave it blank to keep using the account-wide default. The override affects both the suggested ask and the estimated net proceeds for that card only.

Condition factors start at Near Mint 1.00, Lightly Played 0.80, Moderately Played 0.60, Heavily Played 0.40 and Damaged 0.20. All can be changed. Provider guides may combine conditions or nearby variants. Review valuable cards with better comparables.

Price freshness defaults to seven days where a provider supplies a price timestamp. If a provider does not supply one, the app records retrieval time and warns that true source age is unknown. Fetching a price today does not prove it reflects a sale today.

USD and EUR are not converted or combined. Cardmarket references can aggregate languages or finishes and are deliberately not used for automatic suggestions. TCGdex pricing coverage is incomplete and some external mappings may combine printings. Yu-Gi-Oh! card-wide floors combine versions; even set-guide prices can omit edition/condition, so these require manual pricing or verified comparables.

Use **+ Sold comparable** for real completed sales, not active asking listings. Enter the item price excluding shipping, currency, date and evidence URL. Confirm the same printing/variant/language/condition/grade. The app does not scrape that URL or verify the sale itself; your checkbox records your review. It does not have a paid sold-sales feed.

The ask price is already filled in once evidence exists (see above). Click **Use suggested price** or **Use quick-sale price** to switch back to one of those after typing a manual override, or enter an ask manually and save.

## 5. Prepare a sale

The app requires a name, game/category, set/product, collector number, known finish and language, reviewed identity and condition, at least one photo, positive stock, and a positive ask before a card can be marked Ready or Listed.

In **Listing**, inspect the generated title and description. Titles are capped at 80 characters. Copy the draft, or use **Selling → Export listing CSV** or **Export PDF catalog**. Both are sorted by game, then set/product, then collector number (numerically, so 2 sorts before 10), so cards from the same set land together instead of appearing in random scan order. The CSV is general-purpose: it is not a guaranteed eBay/TCGplayer import template. Map its columns to the marketplace's current template and upload photos there yourself.

**Export PDF catalog** builds a human-readable document instead: each card's front photo next to its name, set, number, finish, condition, quantity and asking price, grouped under a heading per game/set. It's meant for browsing, printing, insurance records or sharing your collection — not for uploading to a marketplace. If a card has no ask price yet, that line is simply omitted; if it has no photo, the entry says so instead of failing.

**Listing package ZIP** includes reviewed Ready/Listed cards, a CSV and photos organized as:

`photos / game / set / SKU / photo.jpg`

Exports protect text fields against spreadsheet formula execution. No listing is automatically posted, and no money is charged. If a CSV or PDF export fails outright (for example the destination file is open in another program), the app now shows the actual error instead of doing nothing.

## 6. Track stock and completed sales

Mark a card Listed once you have posted it yourself. **Record sale** asks for quantity, item price, date, channel and an optional order reference. It reduces available stock atomically; overselling is rejected. Partial sales leave the remaining quantity available. A complete sale changes status to Sold with zero stock.

**Selling → Sales history** shows a local transaction ledger and gross totals separated by currency. Avoid entering private buyer details in the order-reference field.

Archive records you do not want to see in active workflows. This preserves photos and history. Use the Archived filter and return a record with stock to Needs review to restore it. Do not delete the database to clear one card.

## 7. Optional cloud vision

Open **Settings & help → Recognition & privacy**. Supply your own OpenAI API key, opt into sending selected card photos to OpenAI, then save. Select **Cloud vision** in Scan & import.

The key stays in memory only until the app exits, or it can be supplied through the `OPENAI_API_KEY` environment variable. It is never stored in inventory, exports or backups. Clear the session key when done on a shared computer. Cloud calls use the configurable vision-capable model `gpt-4.1-mini` by default and `store=false`; this does not override the provider's other data-retention policies. API billing is separate from ChatGPT billing.

Cloud vision extracts visible identity details from up to two photos. It is instructed not to estimate monetary value, authenticity or a condition grade. A confident-looking answer can still be wrong. Physical review remains mandatory.

## 8. Back up and restore

Use **Settings & help → Backups & guide → Create backup ZIP**. The backup uses a consistent database snapshot and includes imported photo copies. Save it outside the `data` directory and keep a copy on another drive. It excludes API keys but contains your inventory and order-reference data, so treat it as private.

To restore:

1. Close Card Desk.
2. Rename the existing `data` folder to keep a safety copy.
3. Extract the backup's `data` folder next to `main.py`.
4. Start Card Desk and inspect your records and photos.

Avoid editing the SQLite database directly. Use the app's review and sales controls.

## Troubleshooting

- **Nothing opens:** run `.venv\Scripts\python.exe main.py` from this folder in a terminal to see an error, or run Setup again. Startup errors are also written to `data/startup-error.log` when possible.
- **OCR cannot find a language:** add an OCR-capable language pack under Windows Settings → Time & language. Manual catalog lookup works without OCR.
- **Wrong OCR name:** improve lighting/crop before import, select the correct game hint, or correct the name in catalog search. Keep the full card borders in the photo.
- **No catalog result:** remove the set-code filter and try the exact printed name/number. Some languages/printings are not covered. Enter details manually where needed.
- **No price suggestion:** confirm review, choose the exact variant/language, check your currency, refresh prices, or add verified sold comparables. A missing source value is not a software instruction to price at zero.
- **Cloud authentication/quota error:** verify the API key, model access and available API billing. Local OCR remains available.
- **Cancel takes a moment:** cancellation is checked between cards. The current OCR/network call finishes or times out before the job stops. Wait before closing to protect writes.
- **Photo capture/visual differences:** this app was tested through native widget construction and workflow calls. The managed development session did not expose a capturable desktop for screenshot-based visual QA.

Source documentation and development test commands are in README.md.
