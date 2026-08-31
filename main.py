from pathlib import Path
import argparse
import os
import sys

# When packaged by PyInstaller, __file__ resolves inside the temporary extraction
# folder, which is wiped between runs; sys.executable is the real, stable exe location.
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Card Desk — local trading-card scanner and inventory")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data", help="Override local data directory")
    parser.add_argument("--smoke-test", action="store_true", help="Initialize GUI, visit each view, then exit")
    parser.add_argument("--ocr-selftest", type=Path, help="Run local Windows OCR on an image file and print the result, without opening the GUI")
    parser.add_argument("--phone-selftest", action="store_true", help="Start and immediately stop the phone-upload HTTPS server to confirm certificate generation works, without opening the GUI")
    parser.add_argument("--pdf-selftest", action="store_true", help="Build a one-card PDF catalog in a temporary folder to confirm reportlab works, without opening the GUI")
    args = parser.parse_args()
    if args.ocr_selftest:
        from cardscanner.scanning import local_ocr
        print(local_ocr(args.ocr_selftest))
        return
    if args.phone_selftest:
        from cardscanner.storage import Store
        from cardscanner.phoneintake import PhoneIntake, make_qr_image
        intake = PhoneIntake(Store(args.data_dir))
        url = intake.start(port=0)
        intake.stop()
        qr = make_qr_image(url)
        print("OK:", url, "| QR image size:", qr.size)
        return
    if args.pdf_selftest:
        import tempfile
        from PIL import Image
        from cardscanner.storage import Store
        from cardscanner.scanning import import_photo
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "data")
            photo = Path(temp) / "sample.png"
            Image.new("RGB", (300, 420), "gray").save(photo)
            card, _ = import_photo(store, photo)
            pdf_path = Path(temp) / "selftest.pdf"
            count = store.export_pdf_catalog(pdf_path)
            print("OK:", count, "card(s),", pdf_path.stat().st_size, "bytes")
        return
    from cardscanner.storage import Store
    from cardscanner.ui import App
    for folder in (ROOT / "exports", ROOT / "backups"):
        folder.mkdir(exist_ok=True)
    store = Store(args.data_dir)
    app = App(store, ROOT)
    if args.smoke_test:
        for page in ("Inventory", "Scan & import", "Review queue", "Selling", "Settings & help"):
            app.show_page(page)
            app.update_idletasks()
        app.after(200, app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()
