import csv
from datetime import date, timedelta
import io
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

from PIL import Image
from cardscanner.domain import estimate, fingerprint, gross_up_for_selling_costs, money, net_proceeds, now, DEFAULT_SETTINGS
from cardscanner.storage import Store
from cardscanner.scanning import import_photo, parse_ocr, Scanner, cloud_extract
from cardscanner.providers import Catalogs
from cardscanner.phoneintake import PhoneIntake


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data")
        self.photo = self.root / "front.png"
        Image.new("RGB", (320, 450), "white").save(self.photo)
        self.card, _ = import_photo(self.store, self.photo)
        self.identifier = self.card["id"]

    def tearDown(self):
        self.temp.cleanup()

    def reviewed(self):
        self.store.update(self.identifier, {"name": "Furret", "game": "Pokémon", "set_name": "Darkness Ablaze", "set_code": "swsh3", "number": "136", "variant": "Normal", "language": "English", "condition": "Near Mint", "provider": "tcgdex", "catalog_id": "swsh3-136", "quantity": 3})
        self.store.confirm(self.identifier, identity=True, condition=True)
        self.store.update(self.identifier, {"ask_price": 2.5})
        return self.store.get(self.identifier)

    def quote(self, **overrides):
        return {"source": "Test market guide", "amount": 10, "currency": "USD", "variant": "Normal", "language": "English", "provider": "tcgdex", "catalog_id": "swsh3-136", "eligible": True, "fetched_at": now(), "source_updated": now(), **overrides}

    def test_import_survives_original_removal(self):
        self.photo.unlink()
        imported = self.store.root / self.store.photos(self.identifier)[0]["path"]
        self.assertTrue(imported.is_file())
        with Image.open(imported) as photo:
            self.assertEqual(photo.size, (320, 450))

    def test_duplicate_does_not_create_card_or_increase_quantity(self):
        card, duplicate = import_photo(self.store, self.photo)
        self.assertTrue(duplicate)
        self.assertEqual(card["id"], self.identifier)
        self.assertEqual(len(self.store.cards()), 1)
        self.assertEqual(card["quantity"], 1)

    def test_invalid_image_does_not_create_record(self):
        bad = self.root / "bad.png"
        bad.write_bytes(b"not an image")
        with self.assertRaises(Exception):
            import_photo(self.store, bad)
        self.assertEqual(len(self.store.cards()), 1)

    def test_ready_requires_review(self):
        with self.assertRaises(ValueError):
            self.store.update(self.identifier, {"status": "Ready"})

    def test_asking_price_cannot_survive_identity_change(self):
        self.reviewed()
        self.store.update(self.identifier, {"status": "Ready"})
        card = self.store.update(self.identifier, {"number": "137"})
        self.assertFalse(card["identity_confirmed"])
        self.assertIsNone(card["ask_price"])
        self.assertEqual(card["status"], "Needs review")

    def test_condition_change_resets_review_and_price(self):
        self.reviewed()
        card = self.store.update(self.identifier, {"condition": "Damaged"})
        self.assertFalse(card["condition_confirmed"])
        self.assertIsNone(card["ask_price"])

    def test_partial_and_complete_sales(self):
        self.reviewed()
        self.store.update(self.identifier, {"status": "Ready"})
        self.store.record_sale(self.identifier, 1, 2.25, date.today().isoformat())
        self.assertEqual(self.store.get(self.identifier)["quantity"], 2)
        self.assertEqual(self.store.get(self.identifier)["status"], "Ready")
        self.store.record_sale(self.identifier, 2, 2, date.today().isoformat())
        self.assertEqual(self.store.get(self.identifier)["quantity"], 0)
        self.assertEqual(self.store.get(self.identifier)["status"], "Sold")
        self.assertEqual(len(self.store.sales()), 2)

    def test_overselling_does_not_create_transaction(self):
        self.reviewed()
        self.store.update(self.identifier, {"status": "Ready"})
        with self.assertRaises(ValueError):
            self.store.record_sale(self.identifier, 4, 2.25, date.today().isoformat())
        self.assertEqual(len(self.store.sales()), 0)
        self.assertEqual(self.store.get(self.identifier)["quantity"], 3)

    def test_currency_is_not_mixed(self):
        card = self.reviewed()
        self.assertIsNone(estimate(card, [self.quote(currency="EUR")], [], DEFAULT_SETTINGS)["suggested"])

    def test_wrong_variant_is_not_used(self):
        card = self.reviewed()
        self.assertIsNone(estimate(card, [self.quote(variant="Holofoil")], [], DEFAULT_SETTINGS)["suggested"])

    def test_wrong_language_is_not_used(self):
        card = self.reviewed()
        self.assertIsNone(estimate(card, [self.quote(language="Japanese")], [], DEFAULT_SETTINGS)["suggested"])

    def test_wrong_printing_is_not_used(self):
        card = self.reviewed()
        self.assertIsNone(estimate(card, [self.quote(catalog_id="other")], [], DEFAULT_SETTINGS)["suggested"])

    def test_cross_printing_floor_is_not_used(self):
        card = self.reviewed()
        self.assertIsNone(estimate(card, [self.quote(eligible=False)], [], DEFAULT_SETTINGS)["suggested"])

    def test_stale_price_is_not_used(self):
        card = self.reviewed()
        old = (date.today() - timedelta(days=30)).isoformat()
        self.assertIsNone(estimate(card, [self.quote(source_updated=old)], [], DEFAULT_SETTINGS)["suggested"])

    def test_provider_guide_uses_configured_condition_factor(self):
        self.reviewed()
        self.store.update(self.identifier, {"condition": "Lightly Played"})
        card = self.store.confirm(self.identifier, condition=True)
        r = estimate(card, [self.quote()], [], DEFAULT_SETTINGS)
        self.assertEqual(r["market_value"], 8)
        self.assertEqual(r["suggested"], 10.80)  # 8 market value grossed up for 0.30 fixed fee, 0.82 shipping, 0.25 packaging and 13.25% marketplace fee
        self.assertEqual(r["quick_sale"], 9.72)
        self.assertIn("assumptions", " ".join(r["warnings"]))

    def test_gross_up_nets_exactly_the_market_value(self):
        ask = gross_up_for_selling_costs(8, DEFAULT_SETTINGS)
        self.assertEqual(ask, 10.80)
        self.assertEqual(net_proceeds(ask, 0, DEFAULT_SETTINGS), 8.0)

    def test_unreviewed_card_has_no_price(self):
        self.assertIsNone(estimate(self.card, [self.quote()], [], DEFAULT_SETTINGS)["suggested"])

    def test_raw_quote_cannot_price_graded_card(self):
        self.reviewed()
        self.store.update(self.identifier, {"condition": "Graded", "grade_company": "PSA", "grade": "10"})
        card = self.store.confirm(self.identifier, condition=True)
        self.assertIsNone(estimate(card, [self.quote()], [], DEFAULT_SETTINGS)["suggested"])

    def test_special_stamp_needs_matching_comps(self):
        self.reviewed()
        self.store.update(self.identifier, {"edition": "Prerelease stamp"})
        card = self.store.confirm(self.identifier, identity=True)
        self.assertIsNone(estimate(card, [self.quote()], [], DEFAULT_SETTINGS)["suggested"])

    def test_comparables_stop_matching_after_identity_change(self):
        card = self.reviewed()
        self.store.add_comp(self.identifier, 12, "USD", date.today().isoformat(), "https://example.com/sale/1", "Test fixture", True)
        self.assertEqual(estimate(card, [], self.store.comps(self.identifier), DEFAULT_SETTINGS)["suggested"], 15.41)  # 12 market value grossed up the same way
        self.store.update(self.identifier, {"variant": "Reverse Holofoil"})
        changed = self.store.confirm(self.identifier, identity=True)
        self.assertIsNone(estimate(changed, [], self.store.comps(self.identifier), DEFAULT_SETTINGS)["suggested"])

    def test_unverified_comparable_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add_comp(self.identifier, 12, "USD", date.today().isoformat(), "https://example.com/sale/1", "", False)

    def test_backup_restores_photos_and_database(self):
        self.reviewed()
        archive = self.store.backup(self.root / "backup.zip")
        restored = self.root / "restored"
        with zipfile.ZipFile(archive) as z:
            self.assertNotIn(".env", z.namelist())
            z.extractall(restored)
        store = Store(restored / "data")
        self.assertEqual(store.get(self.identifier)["name"], "Furret")
        self.assertTrue((store.root / store.photos(self.identifier)[0]["path"]).exists())

    def test_csv_formula_injection_is_escaped(self):
        self.store.update(self.identifier, {"name": "=HYPERLINK(1)", "tags": "@SUM(1)"})
        path = self.root / "export.csv"
        self.store.export_csv(path)
        with path.open(encoding="utf-8-sig", newline="") as f:
            row = next(csv.DictReader(f))
        self.assertTrue(row["name"].startswith("'="))
        self.assertTrue(row["tags"].startswith("'@"))

    def test_export_selling_excludes_unreviewed_records(self):
        path = self.root / "listing.csv"
        self.assertEqual(self.store.export_csv(path, selling=True), 0)
        self.reviewed()
        self.store.update(self.identifier, {"status": "Ready"})
        self.assertEqual(self.store.export_csv(path, selling=True), 1)

    def test_export_csv_is_sorted_by_game_set_and_number(self):
        def new_card(color, **fields):
            photo = self.root / f"{color}.png"
            Image.new("RGB", (10, 10), color).save(photo)
            card, _ = import_photo(self.store, photo)
            self.store.update(card["id"], fields)
        self.store.update(self.identifier, {"game": "Pokémon", "set_name": "Base Set", "number": "10", "name": "Ten"})
        new_card("red", game="Pokémon", set_name="Base Set", number="2", name="Two")
        new_card("blue", game="Yu-Gi-Oh!", set_name="Legend", number="1", name="Last")
        path = self.root / "sorted.csv"
        self.store.export_csv(path)
        with path.open(encoding="utf-8-sig", newline="") as f:
            names = [row["name"] for row in csv.DictReader(f)]
        # Grouped by game, then set, then collector number treated numerically (2 before 10, not string order)
        self.assertEqual(names, ["Two", "Ten", "Last"])

    def test_delete_removes_card_and_photo_file(self):
        photo_path = self.store.root / self.store.photos(self.identifier)[0]["path"]
        self.assertTrue(photo_path.is_file())
        sku = self.store.delete(self.identifier)
        self.assertIsNone(self.store.get(self.identifier))
        self.assertFalse(photo_path.is_file())
        self.assertEqual(sku, self.card["sku"])

    def test_delete_refuses_when_sales_exist(self):
        self.reviewed()
        self.store.update(self.identifier, {"status": "Ready"})
        self.store.record_sale(self.identifier, 1, 5, date.today().isoformat())
        with self.assertRaises(ValueError):
            self.store.delete(self.identifier)
        self.assertIsNotNone(self.store.get(self.identifier))

    def test_shipping_override_changes_gross_up(self):
        default_ask = gross_up_for_selling_costs(10, DEFAULT_SETTINGS)
        override_ask = gross_up_for_selling_costs(10, DEFAULT_SETTINGS, shipping_override=5)
        self.assertGreater(override_ask, default_ask)
        self.assertEqual(override_ask, gross_up_for_selling_costs(10, {**DEFAULT_SETTINGS, "shipping_cost": 5}))

    def test_estimate_uses_cards_shipping_override(self):
        self.reviewed()
        card = self.store.update(self.identifier, {"shipping_override": 5})
        result = estimate(card, [self.quote()], [], DEFAULT_SETTINGS)
        self.assertEqual(result["suggested"], gross_up_for_selling_costs(10, DEFAULT_SETTINGS, 5))

    def test_export_pdf_catalog_creates_file_with_photo(self):
        self.reviewed()
        self.store.update(self.identifier, {"status": "Ready"})
        path = self.root / "catalog.pdf"
        count = self.store.export_pdf_catalog(path)
        self.assertEqual(count, 1)
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 1000)
        self.assertEqual(path.read_bytes()[:4], b"%PDF")

    def test_money_rejects_nan_infinity_and_negative(self):
        for value in ("nan", "inf", "-1", "bogus"):
            with self.assertRaises(ValueError):
                money(value)
        self.assertEqual(money("0.105"), 0.11)

    def test_net_uses_fees_shipping_cost(self):
        self.assertEqual(net_proceeds(10, 2, DEFAULT_SETTINGS), 5.31)

    def test_ocr_extracts_game_name_and_number(self):
        extracted = parse_ocr("BASIC Pikachu HP 60\nThunder shock\nweakness\n025/165")
        self.assertEqual(extracted["game"], "Pokémon")
        self.assertEqual(extracted["name"], "Pikachu")
        self.assertEqual(extracted["number"], "025")

    def test_catalog_adapter_preserves_variants_and_price_dates(self):
        catalog = Catalogs(self.store)
        card = self.reviewed()
        source = {"id": "swsh3-136", "name": "Furret", "set": {"id": "swsh3", "name": "Darkness Ablaze"}, "pricing": {"tcgplayer": {"unit": "USD", "updated": now(), "normal": {"marketPrice": 0.25}, "reverse-holofoil": {"marketPrice": 0.43}}}}
        with patch.object(catalog.http, "get", return_value=source):
            quotes = catalog.quotes(card)
        self.assertEqual({q["variant"] for q in quotes}, {"Normal", "Reverse Holofoil"})
        self.assertTrue(all(q["source_updated"] for q in quotes))

    def test_cloud_request_uses_schema_and_never_requests_price(self):
        payload = {"name": "Furret", "game": "Pokémon", "variant": "Unknown", "is_card": True}
        response = {"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps(payload)}]}]}
        with patch("cardscanner.scanning.urlopen") as mocked:
            mocked.return_value.__enter__.return_value = io.BytesIO(json.dumps(response).encode())
            result = cloud_extract([self.photo], "unit-test-key", "gpt-4.1-mini")
            request = mocked.call_args.args[0]
            body = json.loads(request.data)
            self.assertFalse(body["store"])
            self.assertTrue(body["text"]["format"]["strict"])
            self.assertNotIn("price", body["text"]["format"]["schema"]["properties"])
            self.assertEqual(result["name"], "Furret")

    def test_catalog_failure_retains_scanned_photo_and_text(self):
        scanner = Scanner(self.store)
        with patch("cardscanner.scanning.local_ocr", return_value="Furret\nweakness\n136/189"), patch.object(scanner.catalogs, "search", side_effect=RuntimeError("Offline")):
            card = scanner.scan(self.identifier)
        self.assertEqual(card["name"], "Furret")
        self.assertIn("Offline", card["scan_notes"])
        self.assertEqual(len(self.store.photos(self.identifier)), 1)


class PhoneIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "data")
        self.intake = PhoneIntake(self.store)
        self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def tearDown(self):
        self.intake.stop()
        self.temp.cleanup()

    def test_wrong_key_is_rejected(self):
        self.intake.start(port=0)
        url = f"https://127.0.0.1:{self.intake.httpd.server_port}/?key=000000"
        with self.assertRaises(HTTPError) as ctx:
            urlopen(url, timeout=5, context=self.ssl_context)
        self.assertEqual(ctx.exception.code, 403)
        ctx.exception.close()

    def test_upload_lands_in_incoming_folder_and_inbox_queue(self):
        self.intake.start(port=0)
        buffer = io.BytesIO()
        Image.new("RGB", (40, 60), "white").save(buffer, "JPEG")
        url = f"https://127.0.0.1:{self.intake.httpd.server_port}/upload?key={self.intake.token}"
        request = Request(url, data=buffer.getvalue(), headers={"Content-Type": "image/jpeg"}, method="POST")
        with urlopen(request, timeout=5, context=self.ssl_context) as response:
            self.assertEqual(response.status, 200)
        saved = self.intake.inbox.get(timeout=5)
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.parent, self.store.root / "phone-incoming")

    def test_oversized_content_length_is_rejected(self):
        self.intake.start(port=0)
        url = f"https://127.0.0.1:{self.intake.httpd.server_port}/upload?key={self.intake.token}"
        request = Request(url, data=b"tiny", headers={"Content-Type": "image/jpeg", "Content-Length": str(26 * 1024 * 1024)}, method="POST")
        with self.assertRaises(HTTPError) as ctx:
            urlopen(request, timeout=5, context=self.ssl_context)
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()

    def test_certificate_is_cached_between_starts(self):
        self.intake.start(port=0)
        cert_path = self.store.root / "phone-cert.pem"
        self.assertTrue(cert_path.exists())
        first = cert_path.read_bytes()
        self.intake.stop()
        self.intake.start(port=0)
        self.assertEqual(cert_path.read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
