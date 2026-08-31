from contextlib import contextmanager, closing
from pathlib import Path
import csv
import json
import re
import sqlite3
import uuid
import zipfile
import tempfile

from .domain import now, DEFAULT_SETTINGS, GAMES, CONDITIONS, STATUSES, IDENTITY_FIELDS, CONDITION_FIELDS, money, ready_problems, listing, fingerprint

TEXT_FIELDS = ["name", "game", "set_name", "set_code", "number", "rarity", "variant", "edition", "language", "year", "card_type", "artist", "description", "tags", "condition", "condition_notes", "grade_company", "grade", "cert_number", "storage_location", "status", "provider", "catalog_id", "catalog_url", "scan_text", "scan_notes", "currency", "sold_at", "buyer_reference"]
JSON_FIELDS = ["metadata", "candidates"]
NUMBER_FIELDS = ["quantity", "cost", "ask_price", "sold_price", "identity_confirmed", "condition_confirmed", "shipping_override"]


def export_sort_key(c):
    """Group export rows by game, then set, then collector number treated
    numerically (2 before 10), matching how a collector browses a binder."""
    number = c.get("number") or ""
    leading_digits = re.match(r"\d+", number)
    numeric = int(leading_digits.group()) if leading_digits else float("inf")
    return ((c.get("game") or "").casefold(), (c.get("set_name") or "").casefold(), numeric, number.casefold(), (c.get("name") or "").casefold())


class Store:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "images").mkdir(exist_ok=True)
        self.db = self.root / "inventory.sqlite3"
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            fields = ",".join(f"{field} TEXT NOT NULL DEFAULT ''" for field in TEXT_FIELDS)
            con.executescript(f"""
                CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, sku TEXT UNIQUE NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, {fields},
                  quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity >= 0), cost REAL, ask_price REAL, sold_price REAL,
                  identity_confirmed INTEGER NOT NULL DEFAULT 0, condition_confirmed INTEGER NOT NULL DEFAULT 0,
                  metadata TEXT NOT NULL DEFAULT '{{}}', candidates TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(id),
                  path TEXT NOT NULL, original_name TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, side TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS quotes (id INTEGER PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(id), payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS comps (id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(id), fingerprint TEXT NOT NULL,
                  amount REAL NOT NULL, currency TEXT NOT NULL, sold_date TEXT NOT NULL, url TEXT NOT NULL, notes TEXT NOT NULL, verified INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, card_id TEXT REFERENCES cards(id), created_at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sales (id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(id), sold_at TEXT NOT NULL,
                  quantity INTEGER NOT NULL, unit_price REAL NOT NULL, currency TEXT NOT NULL, channel TEXT NOT NULL, reference TEXT NOT NULL, unit_cost REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS cards_status ON cards(status);
                CREATE INDEX IF NOT EXISTS photos_card ON photos(card_id);
                CREATE INDEX IF NOT EXISTS quotes_card ON quotes(card_id);
                PRAGMA user_version=1;
            """)
            existing_columns = {row["name"] for row in con.execute("PRAGMA table_info(cards)")}
            if "shipping_override" not in existing_columns:
                con.execute("ALTER TABLE cards ADD COLUMN shipping_override REAL")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        item = dict(row)
        for key in JSON_FIELDS:
            if key in item:
                item[key] = json.loads(item[key])
        return item

    def settings(self):
        result = json.loads(json.dumps(DEFAULT_SETTINGS))
        with self.connect() as con:
            for row in con.execute("SELECT * FROM settings"):
                result[row["key"]] = json.loads(row["value"])
        return result

    def save_settings(self, values):
        with self.connect() as con:
            for key, value in values.items():
                if key in DEFAULT_SETTINGS:
                    con.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def create(self, values=None):
        identifier = uuid.uuid4().hex
        stamp = now()
        with self.connect() as con:
            con.execute("INSERT INTO cards(id,sku,created_at,updated_at,name,game,condition,status,variant,language,currency) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (identifier, "CD-" + identifier[:8].upper(), stamp, stamp, "Unidentified card", "Unknown", "Unreviewed", "Needs review", "Unknown", "Unknown", self.settings()["currency"]))
        if values:
            self.update(identifier, values)
        self.event(identifier, "Created", "New inventory record")
        return self.get(identifier)

    def get(self, identifier):
        with self.connect() as con:
            return self.decode(con.execute("SELECT * FROM cards WHERE id=?", (identifier,)).fetchone())

    def cards(self, search="", game="All", status="All"):
        clauses, args = [], []
        if search:
            clauses.append("(name LIKE ? OR sku LIKE ? OR set_name LIKE ? OR number LIKE ? OR tags LIKE ? OR storage_location LIKE ?)")
            args += ["%" + search + "%"] * 6
        if game != "All":
            clauses.append("game=?")
            args.append(game)
        if status != "All":
            clauses.append("status=?")
            args.append(status)
        with self.connect() as con:
            query = "SELECT * FROM cards" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY created_at DESC"
            return [self.decode(r) for r in con.execute(query, args)]

    def update(self, identifier, values, action="Updated"):
        old = self.get(identifier)
        if old is None:
            raise ValueError("Card no longer exists.")
        changes = {k: v for k, v in values.items() if k in TEXT_FIELDS + JSON_FIELDS + NUMBER_FIELDS}
        for field in TEXT_FIELDS:
            if field in changes:
                changes[field] = str(changes[field] or "").strip()[:20000]
        for field in ("cost", "ask_price", "sold_price", "shipping_override"):
            if field in changes:
                changes[field] = money(changes[field])
        if "quantity" in changes:
            try:
                quantity = int(str(changes["quantity"]).strip())
            except ValueError as exc:
                raise ValueError("Quantity must be a whole number.") from exc
            if not 0 <= quantity <= 100000 or (quantity == 0 and old["status"] != "Sold"):
                raise ValueError("Quantity must be between 1 and 100,000. Use Record sale to reduce stock to zero.")
            changes["quantity"] = quantity
        for key, options in (("game", GAMES), ("condition", CONDITIONS), ("status", STATUSES), ("currency", ["USD", "EUR"])):
            if key in changes and changes[key] not in options:
                raise ValueError(f"Invalid {key}.")
        identity_changed = any(k in changes and changes[k] != old[k] for k in IDENTITY_FIELDS)
        condition_changed = any(k in changes and changes[k] != old[k] for k in CONDITION_FIELDS)
        if identity_changed:
            changes["identity_confirmed"] = 0
        if condition_changed:
            changes["condition_confirmed"] = 0
        if identity_changed or condition_changed:
            # A price approved for another printing or condition must never carry over silently.
            changes["ask_price"] = None
            if old["status"] not in ("Sold", "Archived"):
                changes["status"] = "Needs review"
        updated = {**old, **changes}
        if updated["status"] in ("Ready", "Listed"):
            problems = ready_problems(updated, bool(self.photos(identifier)))
            if problems:
                raise ValueError("Cannot mark ready/listed:\n" + "\n".join(problems))
        if updated["status"] == "Sold" and (not updated.get("sold_price") or not updated.get("sold_at")):
            raise ValueError("Enter the sold price per card and sale date before marking sold.")
        encoded = {k: json.dumps(v, ensure_ascii=False) if k in JSON_FIELDS else v for k, v in changes.items()}
        encoded["updated_at"] = now()
        with self.connect() as con:
            con.execute("UPDATE cards SET " + ",".join(k + "=?" for k in encoded) + " WHERE id=?", [*encoded.values(), identifier])
        self.event(identifier, action, ", ".join(changes))
        return self.get(identifier)

    def record_sale(self, identifier, quantity, unit_price, sold_at, channel="", reference=""):
        from datetime import date
        quantity = int(quantity)
        unit_price = money(unit_price, False)
        if quantity <= 0 or unit_price <= 0:
            raise ValueError("Quantity and sale price must be positive.")
        if date.fromisoformat(sold_at) > date.today():
            raise ValueError("Sale date cannot be in the future.")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM cards WHERE id=?", (identifier,)).fetchone()
            if not row or row["status"] not in ("Ready", "Listed"):
                raise ValueError("Review this card and mark it Ready or Listed before recording a sale.")
            if quantity > row["quantity"]:
                raise ValueError("Sale quantity exceeds available stock.")
            con.execute("INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, identifier, sold_at, quantity, unit_price, row["currency"], channel, reference, row["cost"] or 0))
            remaining = row["quantity"] - quantity
            con.execute("UPDATE cards SET quantity=?,status=?,sold_price=?,sold_at=?,updated_at=? WHERE id=?", (remaining, "Sold" if remaining == 0 else row["status"], unit_price, sold_at, now(), identifier))
        self.event(identifier, "Sale recorded", f"{quantity} × {unit_price:.2f}; {remaining} remaining")

    def sales(self):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT s.*,c.sku,c.name FROM sales s JOIN cards c ON c.id=s.card_id ORDER BY sold_at DESC")]

    def confirm(self, identifier, identity=False, condition=False):
        card = self.get(identifier)
        if identity:
            if any(not card.get(k) or card[k] == "Unknown" for k in ("name", "game", "set_name", "number", "variant", "language")):
                raise ValueError("Enter name, game, set, number, finish and language before confirming identity.")
        if condition and (card["condition"] == "Unreviewed" or (card["condition"] == "Graded" and (not card["grade_company"] or not card["grade"]))):
            raise ValueError("Select a condition, and enter grading details for a graded card.")
        return self.update(identifier, {"identity_confirmed": int(identity or card["identity_confirmed"]),
                                        "condition_confirmed": int(condition or card["condition_confirmed"])}, "Review confirmed")

    def delete(self, identifier):
        """Permanently remove a card, its photos and its history. Refuses to
        delete a card with recorded sales, so the sales ledger can never be
        silently destroyed — archive that card instead."""
        card = self.get(identifier)
        if card is None:
            raise ValueError("Card no longer exists.")
        with self.connect() as con:
            if con.execute("SELECT 1 FROM sales WHERE card_id=?", (identifier,)).fetchone():
                raise ValueError(f"{card['sku']} has recorded sales; archive it instead to keep the sales ledger intact.")
            photo_paths = [row["path"] for row in con.execute("SELECT path FROM photos WHERE card_id=?", (identifier,))]
            for table in ("photos", "quotes", "comps", "events"):
                con.execute(f"DELETE FROM {table} WHERE card_id=?", (identifier,))
            con.execute("DELETE FROM cards WHERE id=?", (identifier,))
        for relative in photo_paths:
            path = (self.root / relative).resolve()
            if path.is_relative_to(self.root):
                path.unlink(missing_ok=True)
        return card["sku"]

    def photos(self, identifier):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM photos WHERE card_id=? ORDER BY created_at,id", (identifier,))]

    def photo_by_digest(self, digest):
        with self.connect() as con:
            row = con.execute("SELECT * FROM photos WHERE digest=?", (digest,)).fetchone()
            return dict(row) if row else None

    def attach_photo(self, identifier, path, name, digest, side):
        with self.connect() as con:
            con.execute("INSERT INTO photos VALUES (?,?,?,?,?,?,?)", (uuid.uuid4().hex, identifier, path, name, digest, side, now()))
        self.update(identifier, {"identity_confirmed": 0, "condition_confirmed": 0,
                                "status": "Needs review" if self.get(identifier)["status"] not in ("Sold", "Archived") else self.get(identifier)["status"]}, "Photo attached")

    def save_quotes(self, identifier, quotes):
        with self.connect() as con:
            for q in quotes:
                con.execute("INSERT INTO quotes(card_id,payload,created_at) VALUES (?,?,?)", (identifier, json.dumps(q), now()))
        self.event(identifier, "Prices refreshed", f"{len(quotes)} source quotes saved")

    def quotes(self, identifier):
        with self.connect() as con:
            rows = con.execute("SELECT payload FROM quotes WHERE card_id=? ORDER BY id DESC", (identifier,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def add_comp(self, identifier, amount, currency, sold_date, url, notes, verified):
        from datetime import date
        from urllib.parse import urlparse
        amount = money(amount, False)
        if amount <= 0 or currency not in ("USD", "EUR"):
            raise ValueError("Enter a positive sold item price and USD or EUR.")
        if date.fromisoformat(sold_date) > date.today():
            raise ValueError("A sold date cannot be in the future.")
        if urlparse(url).scheme not in ("https", "http") or not urlparse(url).netloc:
            raise ValueError("Enter the full source URL for this sale.")
        if not verified:
            raise ValueError("Confirm the sale matches the card's exact printing, finish, language and condition.")
        with self.connect() as con:
            con.execute("INSERT INTO comps VALUES (?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, identifier, fingerprint(self.get(identifier)), amount, currency, sold_date, url, notes, 1))
        self.event(identifier, "Comparable added", f"{currency} {amount:.2f} on {sold_date}")

    def comps(self, identifier):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM comps WHERE card_id=? ORDER BY sold_date DESC", (identifier,))]

    def event(self, identifier, action, detail):
        with self.connect() as con:
            con.execute("INSERT INTO events(card_id,created_at,action,detail) VALUES (?,?,?,?)", (identifier, now(), action, detail))

    def events(self, identifier):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM events WHERE card_id=? ORDER BY id DESC LIMIT 100", (identifier,))]

    def cache_get(self, key, seconds=86400):
        from datetime import datetime
        with self.connect() as con:
            row = con.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone()
        if row and (datetime.fromisoformat(now()) - datetime.fromisoformat(row["created_at"])).total_seconds() < seconds:
            return json.loads(row["payload"])
        return None

    def cache_put(self, key, payload):
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, json.dumps(payload), now()))

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination.is_relative_to(self.root):
            raise ValueError("Save backups outside the data folder.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / "inventory.sqlite3"
            with self.connect() as src, closing(sqlite3.connect(snapshot)) as dst:
                src.backup(dst)
            # Read image paths from the snapshot, not a concurrently changing live database.
            with closing(sqlite3.connect(snapshot)) as snap:
                photo_paths = [row[0] for row in snap.execute("SELECT path FROM photos")]
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot, "data/inventory.sqlite3")
                for relative in photo_paths:
                    path = (self.root / relative).resolve()
                    if not path.is_relative_to(self.root):
                        raise ValueError("Unsafe photo path in database.")
                    archive.write(path, "data/" + relative.replace("\\", "/"))
                archive.writestr("RESTORE.txt", "Close Card Desk. Rename your existing data folder as a precaution. Extract this archive's data folder next to main.py, then restart. API keys are not included.\n")
        return destination

    def export_csv(self, destination, cards=None, selling=False):
        cards = cards if cards is not None else self.cards()
        if selling:
            cards = [c for c in cards if c["status"] in ("Ready", "Listed") and not ready_problems(c, bool(self.photos(c["id"])))]
        cards = sorted(cards, key=export_sort_key)
        headers = ["sku", "name", "game", "set_name", "set_code", "number", "rarity", "variant", "edition", "language", "year", "condition", "condition_notes", "grade_company", "grade", "cert_number", "quantity", "currency", "cost", "ask_price", "status", "storage_location", "tags", "sold_price", "sold_at", "catalog_url", "title", "description", "photo_files"]
        def safe(value):
            if value is None:
                return ""
            s = str(value)
            if s.lstrip().startswith(("=", "+", "-", "@")) or s.startswith(("\t", "\r", "\n")):
                return "'" + s
            return s
        with open(destination, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            for c in cards:
                row = {key: c.get(key, "") for key in headers}
                row["title"], row["description"] = listing(c)
                row["photo_files"] = " | ".join(str(self.root / p["path"]) for p in self.photos(c["id"]))
                writer.writerow({k: safe(v) for k, v in row.items()})
        return len(cards)

    def export_pdf_catalog(self, destination, cards=None, selling=False):
        """A human-readable catalog PDF: one row per card with its front photo,
        grouped by game and set, in the same order as the CSV export."""
        from xml.sax.saxutils import escape
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        cards = cards if cards is not None else self.cards()
        if selling:
            cards = [c for c in cards if c["status"] in ("Ready", "Listed") and not ready_problems(c, bool(self.photos(c["id"])))]
        cards = sorted(cards, key=export_sort_key)

        styles = getSampleStyleSheet()
        group_style = ParagraphStyle("CardDeskGroup", parent=styles["Heading2"], spaceBefore=16, spaceAfter=6)
        detail_style = ParagraphStyle("CardDeskDetail", parent=styles["Normal"], fontSize=9.5, leading=13, textColor=colors.HexColor("#333333"))
        photo_box = (1.05 * inch, 1.45 * inch)

        def thumbnail(card):
            photos = self.photos(card["id"])
            front = next((p for p in photos if p["side"] == "Front"), photos[0] if photos else None)
            if not front:
                return Paragraph("No photo", detail_style)
            try:
                return RLImage(str(self.root / front["path"]), width=photo_box[0], height=photo_box[1], kind="proportional")
            except Exception:
                return Paragraph("Photo unavailable", detail_style)

        def detail_text(card):
            lines = [f"<b>{escape(card.get('name') or 'Unidentified card')}</b>"]
            lines.append(escape(" · ".join(filter(None, [card.get("set_name"), f"No. {card['number']}" if card.get("number") else "", card.get("variant") if card.get("variant") not in (None, "Unknown") else "", card.get("condition")]))))
            tail = f"Qty {card.get('quantity', 0)} · SKU {card.get('sku', '')}"
            if card.get("ask_price") is not None:
                tail += f" · Ask {card.get('currency', 'USD')} {card['ask_price']:.2f}"
            lines.append(escape(tail))
            return Paragraph("<br/>".join(lines), detail_style)

        doc = SimpleDocTemplate(str(destination), pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch, leftMargin=0.6 * inch, rightMargin=0.6 * inch, title="Card Desk catalog")
        story = [Paragraph("Card Desk — Card Catalog", styles["Title"]),
                 Paragraph(f"{len(cards)} card(s) · exported {now()[:10]}", detail_style),
                 Spacer(1, 12)]
        last_group = None
        for card in cards:
            group = (card.get("game") or "Unknown", card.get("set_name") or "Unsorted set")
            if group != last_group:
                story.append(Paragraph(escape(f"{group[0]} — {group[1]}"), group_style))
                last_group = group
            row = Table([[thumbnail(card), detail_text(card)]], colWidths=[photo_box[0] + 12, 5.2 * inch])
            row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                                      ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD"))]))
            story.append(row)
        doc.build(story)
        return len(cards)

    def export_listing_package(self, destination, cards=None):
        cards = cards if cards is not None else self.cards()
        ready = [c for c in cards if c["status"] in ("Ready", "Listed") and not ready_problems(c, bool(self.photos(c["id"])))]
        if not ready:
            raise ValueError("No reviewed Ready or Listed cards to export.")
        def segment(text):
            return re.sub(r'[^\w .-]', '_', text).strip(" .")[:70] or "Uncategorized"
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp) / "listings.csv"
            self.export_csv(csv_path, ready, selling=True)
            with csv_path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as z:
                for c, row in zip(ready, rows):
                    directory = f"photos/{segment(c['game'])}/{segment(c['set_name'])}/{c['sku']}"
                    relative_paths = []
                    for idx, photo in enumerate(self.photos(c["id"]), 1):
                        relative = f"{directory}/{idx:02d}-{segment(photo['side'])}.jpg"
                        source = (self.root / photo["path"]).resolve()
                        if not source.is_relative_to(self.root):
                            raise ValueError("Unsafe photo path.")
                        z.write(source, relative)
                        relative_paths.append(relative)
                    row["photo_files"] = " | ".join(relative_paths)
                with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                z.write(csv_path, "listings.csv")
                z.writestr("READ ME.txt", "Listing drafts and normalized photos, organized by game / set / SKU.\nReview all descriptions and asking prices. This CSV is general purpose; map columns to your marketplace's current template before upload. Local photos must be uploaded to that marketplace separately. No listings have been posted by Card Desk.\n")
        return len(ready)
