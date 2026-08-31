"""Native Windows interface. Network and scanning jobs never run on Tk's UI thread."""
from datetime import date, datetime
from pathlib import Path
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser
from PIL import Image, ImageTk, ImageOps

from .domain import GAMES, CONDITIONS, STATUSES, VARIANTS, estimate, listing, net_proceeds, money, ready_problems
from .providers import LANGUAGES
from .scanning import Scanner, import_photo, IMAGE_EXTENSIONS
from .phoneintake import PhoneIntake, make_qr_image

BG = "#F3F5F6"
INK = "#142D39"
MUTED = "#657985"
ACCENT = "#147D73"
WHITE = "#FFFFFF"
SIDEBAR = "#152F3B"


def label(parent, text, style="TLabel", **kwargs):
    return ttk.Label(parent, text=text, style=style, **kwargs)


def button(parent, text, command, primary=False):
    return ttk.Button(parent, text=text, command=command, style="Accent.TButton" if primary else "TButton")


def field(parent, text, variable, row, column=0, options=None, width=28):
    box = ttk.Frame(parent)
    box.grid(row=row, column=column, sticky="ew", padx=(0, 18), pady=(0, 10))
    label(box, text, "Small.TLabel").pack(anchor="w", pady=(0, 4))
    widget = ttk.Combobox(box, textvariable=variable, values=options, state="readonly", width=width) if options else ttk.Entry(box, textvariable=variable, width=width)
    widget.pack(fill="x")
    return widget


def text_area(parent, height=8, readonly=False):
    frame = ttk.Frame(parent)
    text = tk.Text(frame, height=height, wrap="word", font=("Segoe UI", 10), relief="flat", padx=12, pady=10, background=WHITE, foreground=INK, undo=True)
    scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    frame.pack(fill="both", expand=True, pady=8)
    if readonly:
        text.configure(state="disabled")
    return text


def set_text(widget, value, readonly=False):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", value)
    if readonly:
        widget.configure(state="disabled")


def open_url(url):
    from urllib.parse import urlparse
    if urlparse(url).scheme in ("https", "http"):
        webbrowser.open(url)


class App(tk.Tk):
    def __init__(self, store, project_root):
        super().__init__()
        self.store, self.project_root = store, Path(project_root)
        self.scanner = Scanner(store)
        self.title("Card Desk — Scan • Review • Sell")
        self.geometry("1340x850")
        self.minsize(1060, 720)
        self.configure(background=BG)
        self.events_queue = queue.Queue()
        self.busy = False
        self.cancel_event = threading.Event()
        self.mode = tk.StringVar(value="Local OCR")
        self.game_hint = tk.StringVar(value="Unknown")
        self.auto_scan = tk.BooleanVar(value=True)
        self.status_text = tk.StringVar(value="Ready. Your photos and inventory stay on this computer unless cloud vision is enabled.")
        self.page = "Inventory"
        self.detail_windows = {}
        self.phone_intake = PhoneIntake(store)
        self.phone_received = 0
        self.phone_last_url = ""
        self.phone_qr_photo = None
        self.phone_status = tk.StringVar(value="Not running. Start it, then open the link on your phone's browser over the same Wi-Fi.")
        self.phone_count_text = tk.StringVar(value="Photos received this session: 0")
        self.setup_styles()
        self.build_shell()
        self.after(100, self.drain_events)
        self.after(700, self.poll_phone_inbox)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Control-o>", lambda e: self.import_files())
        self.bind("<Control-f>", lambda e: self.search_entry.focus_set() if hasattr(self, "search_entry") and self.search_entry.winfo_exists() else None)
        self.show_page("Inventory")

    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), foreground=INK)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 26), foreground=INK)
        style.configure("Heading.TLabel", font=("Segoe UI Semibold", 15))
        style.configure("Small.TLabel", font=("Segoe UI", 9), foreground=MUTED)
        style.configure("Metric.TLabel", font=("Segoe UI Semibold", 24), foreground=ACCENT)
        style.configure("TButton", padding=(12, 8), background=WHITE, borderwidth=1)
        style.map("TButton", background=[("active", "#E4ECEA")])
        style.configure("Accent.TButton", background=ACCENT, foreground=WHITE, borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#0C655D"), ("disabled", "#7B9994")], foreground=[("disabled", WHITE)])
        style.configure("Treeview", background=WHITE, fieldbackground=WHITE, rowheight=38, borderwidth=0)
        style.configure("Treeview.Heading", background="#E5EBED", padding=9, font=("Segoe UI Semibold", 10))
        style.map("Treeview", background=[("selected", "#D4EAE5")], foreground=[("selected", INK)])
        style.configure("TEntry", padding=7, fieldbackground=WHITE)
        style.configure("TCombobox", padding=6, fieldbackground=WHITE)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 9))
        style.map("TNotebook.Tab", background=[("selected", WHITE)])
        style.configure("TCheckbutton", background=BG)
        style.configure("TLabelframe", background=BG, bordercolor="#D5DEE1")
        style.configure("TLabelframe.Label", background=BG, font=("Segoe UI Semibold", 11))

    def build_shell(self):
        sidebar = tk.Frame(self, bg=SIDEBAR, width=198)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="CARD DESK", font=("Segoe UI", 19, "bold"), fg=WHITE, bg=SIDEBAR).pack(anchor="w", padx=20, pady=(34, 2))
        tk.Label(sidebar, text="COLLECTION TO COMMERCE", font=("Segoe UI", 8), fg="#94B3BD", bg=SIDEBAR).pack(anchor="w", padx=20, pady=(0, 30))
        self.nav = {}
        for title in ("Inventory", "Scan & import", "Review queue", "Selling", "Settings & help"):
            b = tk.Button(sidebar, text=title, anchor="w", bg=SIDEBAR, fg="#D4E3E7", activebackground="#254650", activeforeground=WHITE,
                          relief="flat", bd=0, padx=20, pady=13, font=("Segoe UI", 11), command=lambda p=title: self.show_page(p))
            b.pack(fill="x", pady=2)
            self.nav[title] = b
        tk.Label(sidebar, text="LOCAL STORAGE\nSQLite + your photos\n\nPrices are guides.\nYou approve every listing.", justify="left", font=("Segoe UI", 9), fg="#94B3BD", bg=SIDEBAR).pack(side="bottom", anchor="w", padx=20, pady=28)
        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True)
        bottom = ttk.Frame(right, padding=(20, 8))
        bottom.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=110)
        self.progress.pack(side="right", padx=8)
        button(bottom, "Cancel job", self.cancel_event.set).pack(side="right")
        label(bottom, "", textvariable=self.status_text, wraplength=730, style="Small.TLabel").pack(side="left", fill="x", expand=True)
        self.content = ttk.Frame(right, padding=(28, 24))
        self.content.pack(side="top", fill="both", expand=True)

    def show_page(self, page):
        self.page = page
        for name, b in self.nav.items():
            b.configure(bg="#254650" if name == page else SIDEBAR, fg=WHITE if name == page else "#D4E3E7")
        for child in self.content.winfo_children():
            child.destroy()
        if page == "Scan & import":
            self.build_intake()
        elif page == "Settings & help":
            self.build_settings()
        else:
            self.build_inventory(page)

    def build_inventory(self, page):
        descriptions = {"Inventory": "Every card, photo, printing and price — in one place.", "Review queue": "Resolve the exact printing and condition before setting a selling price.", "Selling": "Prepare listings, track stock and record your completed sales."}
        label(self.content, page, "Title.TLabel").pack(anchor="w")
        label(self.content, descriptions[page], "Small.TLabel").pack(anchor="w", pady=(3, 18))
        metrics = ttk.Frame(self.content)
        metrics.pack(fill="x", pady=(0, 18))
        cards = self.store.cards()
        available = [c for c in cards if c["status"] not in ("Sold", "Archived")]
        stats = [("IN STOCK", str(sum(c["quantity"] for c in available))), ("NEEDS REVIEW", str(sum(c["status"] == "Needs review" for c in cards))), ("READY / LISTED", str(sum(c["status"] in ("Ready", "Listed") for c in cards)))]
        curr = self.store.settings()["currency"]
        total = sum((c["ask_price"] or 0) * c["quantity"] for c in available if c["currency"] == curr)
        stats.append((f"ASKING TOTAL · {curr}", f"{total:,.2f}"))
        for i, (caption, value) in enumerate(stats):
            f = ttk.Frame(metrics)
            f.pack(side="left", fill="x", expand=True)
            label(f, caption, "Small.TLabel").pack(anchor="w")
            label(f, value, "Metric.TLabel").pack(anchor="w")
        bar = ttk.Frame(self.content)
        bar.pack(fill="x", pady=(0, 14))
        button(bar, "+ Import photos", self.import_files, True).pack(side="left", padx=(0, 8))
        button(bar, "+ Manual card", self.manual_card).pack(side="left", padx=(0, 8))
        if page == "Selling":
            button(bar, "Sales history", self.sales_history).pack(side="left", padx=4)
            button(bar, "Listing package ZIP", self.export_package).pack(side="right", padx=(8, 0))
            button(bar, "Export listing CSV", lambda: self.export(selling=True)).pack(side="right")
            button(bar, "Export PDF catalog", lambda: self.export_pdf(selling=True)).pack(side="right", padx=(0, 8))
        else:
            button(bar, "Export visible CSV", self.export).pack(side="right")
            button(bar, "Export PDF catalog", self.export_pdf).pack(side="right", padx=(0, 8))
        filters = ttk.Frame(self.content)
        filters.pack(fill="x", pady=(0, 10))
        self.search = tk.StringVar()
        self.game_filter = tk.StringVar(value="All")
        self.state_filter = tk.StringVar(value="Needs review" if page == "Review queue" else "All")
        self.search_entry = ttk.Entry(filters, textvariable=self.search, width=30)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", lambda e: self.refresh_table())
        for var, values, width in ((self.game_filter, ["All"] + GAMES, 23), (self.state_filter, ["All"] + STATUSES, 15)):
            combo = ttk.Combobox(filters, textvariable=var, values=values, state="readonly", width=width)
            combo.pack(side="left", padx=(10, 0))
            combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())
        label(self.content, "Search name, SKU, set, number, tags or storage location. Double-click a card to review it.", "Small.TLabel").pack(anchor="w", pady=(0, 8))
        table_frame = ttk.Frame(self.content)
        cols = ["name", "game", "set_name", "number", "condition", "quantity", "ask_price", "status"]
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        for name, title, width in zip(cols, ["Card", "Game", "Set", "No.", "Condition", "Qty", "Ask", "Status"], [210, 130, 160, 70, 130, 45, 90, 115]):
            self.tree.heading(name, text=title, command=lambda k=name: self.sort_table(k))
            self.tree.column(name, width=width, minwidth=45, stretch=name in ("name", "set_name"))
        ys = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)
        self.tree.tag_configure("review", foreground="#9B641C")
        self.tree.tag_configure("ready", foreground=ACCENT)
        self.tree.bind("<Double-1>", lambda e: self.open_selected())
        self.tree.bind("<Return>", lambda e: self.open_selected())
        actions = ttk.Frame(self.content)
        button(actions, "Open / review", self.open_selected, True).pack(side="left", padx=(0, 6))
        button(actions, "Scan selected", self.scan_selected).pack(side="left", padx=6)
        button(actions, "Refresh prices", self.refresh_selected_prices).pack(side="left", padx=6)
        button(actions, "Archive selected", self.archive_selected).pack(side="right")
        button(actions, "Delete selected", self.delete_selected).pack(side="right", padx=(0, 8))
        actions.pack(side="bottom", fill="x", pady=(6, 0))
        self.empty_label = label(self.content, "", "Small.TLabel")
        self.empty_label.pack(side="bottom", anchor="w", pady=7)
        table_frame.pack(side="top", fill="both", expand=True)
        self.refresh_table()

    def refresh_table(self):
        if not hasattr(self, "tree") or not self.tree.winfo_exists():
            return
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.visible_cards = self.store.cards(self.search.get(), self.game_filter.get(), self.state_filter.get())
        if self.page == "Selling" and self.state_filter.get() == "All":
            self.visible_cards = [c for c in self.visible_cards if c["status"] in ("Ready", "Listed", "Sold")]
        for c in self.visible_cards:
            ask = f"{c['currency']} {c['ask_price']:.2f}" if c["ask_price"] is not None else "—"
            self.tree.insert("", "end", iid=c["id"], values=[c["name"] or "Unidentified card", c["game"], c["set_name"], c["number"], c["condition"], c["quantity"], ask, c["status"]], tags=("review" if c["status"] == "Needs review" else "ready" if c["status"] == "Ready" else "normal",))
        for item in selected:
            if self.tree.exists(item):
                self.tree.selection_add(item)
        self.empty_label.configure(text=f"{len(self.visible_cards)} record(s)." if self.visible_cards else "No cards here yet. Import front photos or add a manual card to get started.")

    def sort_table(self, column):
        rows = [(self.tree.set(i, column), i) for i in self.tree.get_children()]
        def key(row):
            if column in ("quantity", "ask_price"):
                try:
                    return float(row[0].split()[-1])
                except ValueError:
                    return -1
            return row[0].casefold()
        reverse = getattr(self, "sort_state", None) == (column, False)
        self.sort_state = (column, reverse)
        for idx, (_, item) in enumerate(sorted(rows, key=key, reverse=reverse)):
            self.tree.move(item, "", idx)

    def selected(self):
        ids = list(self.tree.selection()) if hasattr(self, "tree") and self.tree.winfo_exists() else []
        if not ids:
            messagebox.showinfo("Choose cards", "Select one or more cards first.", parent=self)
        return ids

    def open_selected(self):
        ids = self.selected()
        if ids:
            self.open_card(ids[0])

    def open_card(self, identifier):
        if self.busy:
            messagebox.showinfo("Job in progress", "Wait for the current import or scan to finish before editing cards.", parent=self)
            return
        existing = self.detail_windows.get(identifier)
        if existing and existing.winfo_exists():
            existing.lift()
            return
        self.detail_windows[identifier] = CardWindow(self, identifier)

    def manual_card(self):
        if self.busy:
            return
        self.open_card(self.store.create()["id"])
        self.refresh_table()

    def build_intake(self):
        label(self.content, "Scan & import", "Title.TLabel").pack(anchor="w")
        label(self.content, "One front photo per card. Add the back and close-ups during review.", "Small.TLabel").pack(anchor="w", pady=(4, 24))
        panel = ttk.LabelFrame(self.content, text="Intake settings", padding=20)
        panel.pack(fill="x")
        grid = ttk.Frame(panel)
        grid.pack(fill="x")
        field(grid, "Recognition method", self.mode, 0, 0, ["Local OCR", "Cloud vision"])
        field(grid, "Card game hint", self.game_hint, 0, 1, GAMES)
        ttk.Checkbutton(panel, text="Scan each front photo after import", variable=self.auto_scan).pack(anchor="w", pady=10)
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x", pady=10)
        button(buttons, "Choose front photos", self.import_files, True).pack(side="left", padx=(0, 8))
        button(buttons, "Import a folder", self.import_folder).pack(side="left")
        label(panel, "JPEG, PNG, WebP, BMP and TIFF · 25 MB / 40 MP maximum · exact duplicate files are skipped", "Small.TLabel").pack(anchor="w", pady=(8, 0))
        phone_panel = ttk.LabelFrame(self.content, text="Receive from phone", padding=20)
        phone_panel.pack(fill="x", pady=(20, 0))
        self.phone_qr_label = None
        running = self.phone_intake.running
        label(phone_panel, "Photograph cards on your phone; they import here automatically as front photos." if not running else "Scan the QR code with your phone's camera to connect — no typing needed. Put your phone on a stand: it auto-captures each card once it holds still, then waits for you to swap in the next one.", "Small.TLabel", wraplength=850).pack(anchor="w", pady=(0, 10))
        phone_buttons = ttk.Frame(phone_panel)
        phone_buttons.pack(fill="x")
        button(phone_buttons, "Stop phone upload" if running else "Start phone upload", self.toggle_phone_intake, True).pack(side="left", padx=(0, 8))
        if running:
            button(phone_buttons, "Copy link", self.copy_phone_link).pack(side="left")
            detail_row = ttk.Frame(phone_panel)
            detail_row.pack(fill="x", pady=(12, 0))
            self.phone_qr_label = tk.Label(detail_row, bg=WHITE)
            self.phone_qr_label.pack(side="left", anchor="n", padx=(0, 18))
            detail_info = ttk.Frame(detail_row)
            detail_info.pack(side="left", fill="both", expand=True)
            label(detail_info, "", textvariable=self.phone_status, wraplength=630, justify="left").pack(anchor="w")
            label(detail_info, "", textvariable=self.phone_count_text, style="Small.TLabel").pack(anchor="w", pady=(2, 0))
            label(phone_panel, "Same Wi-Fi network required. Your phone will show a one-time certificate warning — tap Advanced (or Details), then Proceed; this is expected and stays private to your network.", "Small.TLabel", wraplength=850).pack(anchor="w", pady=(10, 0))
        self.refresh_phone_qr()
        label(self.content, "A dependable scan starts with a clear photo", "Heading.TLabel").pack(anchor="w", pady=(24, 8))
        tips = "1. Photograph one card, straight on, with the whole border visible.\n2. Use even light. Avoid sleeves, glare and busy backgrounds.\n3. Make the card name, collector number and set mark readable.\n4. Add a back photo in the review window to document wear.\n5. Inspect the physical card to confirm foil, edition and condition."
        label(self.content, tips, wraplength=850, justify="left").pack(anchor="w")
        label(self.content, "Local OCR keeps photos on this computer and sends search text to public catalogs. Cloud vision is optional, requires your own API key, and sends the selected photos to OpenAI.", "Small.TLabel", wraplength=900).pack(anchor="w", pady=18)
        self.log = text_area(self.content, 7, readonly=True)
        set_text(self.log, "Ready to import. Errors are reported per file, so one bad photo will not stop the batch.", True)

    def import_files(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self, title="Choose front photos — one card per image", filetypes=[("Card photos", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")])
        if paths:
            self.import_batch(paths)

    def import_folder(self):
        folder = filedialog.askdirectory(parent=self, title="Choose folder of front photos")
        if folder:
            self.import_batch(sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS))

    def import_batch(self, paths, switch_page=True, quiet=False):
        mode, hint, scan = self.mode.get(), self.game_hint.get(), self.auto_scan.get()
        if switch_page:
            self.show_page("Scan & import")
        def work():
            lines = []
            for index, path in enumerate(paths, 1):
                if self.cancel_event.is_set():
                    lines.append("Cancelled. Previously imported cards have been saved.")
                    break
                self.events_queue.put(("status", f"Importing {index}/{len(paths)}: {Path(path).name}"))
                try:
                    card, duplicate = import_photo(self.store, path)
                    if duplicate:
                        lines.append(f"SKIPPED duplicate: {Path(path).name} → {card['sku']}")
                        continue
                    lines.append(f"IMPORTED: {Path(path).name} → {card['sku']}")
                    if scan:
                        self.scanner.scan(card["id"], mode, hint)
                        lines.append("  Scan complete. Review required.")
                except Exception as exc:
                    lines.append(f"  CHECK {Path(path).name}: {exc}")
            return "\n".join(lines) or "No supported photos found."
        def finish(text):
            if hasattr(self, "log") and self.log.winfo_exists():
                set_text(self.log, text, True)
            elif not quiet:
                messagebox.showinfo("Import report", text, parent=self)
        self.run_job("Importing phone photo(s)…" if quiet else "Importing card photos…", work, finish)

    def toggle_phone_intake(self):
        if self.phone_intake.running:
            self.phone_intake.stop()
            self.phone_status.set("Stopped. Start it again when you're ready to scan.")
        else:
            try:
                self.phone_last_url = self.phone_intake.start()
            except OSError as exc:
                messagebox.showerror("Could not start phone upload", str(exc), parent=self)
                return
            self.phone_status.set("Running — scan the QR code, or open this on your phone's browser (same Wi-Fi):\n" + self.phone_last_url)
        self.show_page("Scan & import")

    def refresh_phone_qr(self):
        if not self.phone_qr_label or not self.phone_qr_label.winfo_exists():
            return
        self.phone_qr_photo = ImageTk.PhotoImage(make_qr_image(self.phone_last_url))
        self.phone_qr_label.configure(image=self.phone_qr_photo)

    def copy_phone_link(self):
        if not self.phone_last_url:
            messagebox.showinfo("No link yet", "Start phone upload first.", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(self.phone_last_url)
        self.status_text.set("Phone upload link copied.")

    def poll_phone_inbox(self):
        paths = []
        try:
            while True:
                paths.append(self.phone_intake.inbox.get_nowait())
        except queue.Empty:
            pass
        if paths:
            if self.busy:
                for path in paths:
                    self.phone_intake.inbox.put(path)
            else:
                self.phone_received += len(paths)
                self.phone_count_text.set(f"Photos received this session: {self.phone_received}")
                self.import_batch([str(p) for p in paths], switch_page=False, quiet=True)
        self.after(700, self.poll_phone_inbox)

    def scan_selected(self):
        ids = self.selected()
        if not ids:
            return
        if not messagebox.askyesno("Rescan selected cards?", "Scanning replaces draft identification and resets review and asking-price approval. Photos and sales history remain. Continue?", parent=self):
            return
        mode, hint = self.mode.get(), self.game_hint.get()
        self.run_job("Scanning selected cards…", lambda: self.batch(ids, lambda i: self.scanner.scan(i, mode, hint)), self.show_report)

    def batch(self, ids, operation):
        lines = []
        for index, identifier in enumerate(ids, 1):
            if self.cancel_event.is_set():
                lines.append("Cancelled; completed work saved.")
                break
            card = self.store.get(identifier)
            self.events_queue.put(("status", f"Processing {index}/{len(ids)}: {card['name']}"))
            try:
                operation(identifier)
                lines.append(card["sku"] + ": complete")
            except Exception as exc:
                lines.append(card["sku"] + ": " + str(exc))
        return "\n".join(lines)

    def refresh_price(self, identifier):
        card = self.store.get(identifier)
        quotes = self.scanner.catalogs.quotes(card)
        self.store.save_quotes(identifier, quotes)
        return quotes

    def refresh_selected_prices(self):
        ids = self.selected()
        if ids:
            self.run_job("Refreshing source prices…", lambda: self.batch(ids, self.refresh_price), self.show_report)

    def archive_selected(self):
        ids = self.selected()
        if ids and not self.busy and messagebox.askyesno("Archive cards", "Archive these records? You can restore them from the Archived filter. Photos and history will stay saved.", parent=self):
            for identifier in ids:
                self.store.update(identifier, {"status": "Archived"})
            self.refresh_table()

    def delete_selected(self):
        ids = self.selected()
        if not ids or self.busy:
            return
        if not messagebox.askyesno("Delete cards", f"Permanently delete {len(ids)} card record(s)? This removes their photos and identification history and cannot be undone. Archive instead if you might want them back.", parent=self, icon="warning"):
            return
        errors = []
        for identifier in ids:
            try:
                self.store.delete(identifier)
            except ValueError as exc:
                errors.append(str(exc))
        self.refresh_table()
        if errors:
            messagebox.showerror("Some cards were not deleted", "\n".join(errors), parent=self)

    def export(self, selling=False):
        path = filedialog.asksaveasfilename(parent=self, title="Export card records", defaultextension=".csv", initialdir=self.project_root / "exports", initialfile="listing-drafts.csv" if selling else "card-inventory.csv", filetypes=[("CSV spreadsheet", "*.csv")])
        if not path:
            return
        try:
            count = self.store.export_csv(path, self.visible_cards, selling)
        except OSError as exc:
            messagebox.showerror("Could not save the export", f"{exc}\n\nThe file may be open in another program (like Excel) — close it and try again.", parent=self)
            return
        if count == 0 and selling:
            messagebox.showinfo("No cards to export", "0 records exported. Selling exports only include cards that are Ready or Listed with no outstanding review issues — check the Review queue.", parent=self)
            return
        messagebox.showinfo("Export complete", f"Exported {count} records, sorted by game, set and collector number.\n\nThis is a general CSV, not a marketplace-specific upload template. No listing was published.", parent=self)

    def export_pdf(self, selling=False):
        path = filedialog.asksaveasfilename(parent=self, title="Export card catalog PDF", defaultextension=".pdf", initialdir=self.project_root / "exports", initialfile="listing-catalog.pdf" if selling else "card-catalog.pdf", filetypes=[("PDF document", "*.pdf")])
        if not path:
            return
        cards = list(self.visible_cards)
        def work():
            return self.store.export_pdf_catalog(path, cards, selling)
        def finish(count):
            if count == 0 and selling:
                messagebox.showinfo("No cards to include", "0 cards included. Selling catalogs only include cards that are Ready or Listed with no outstanding review issues — check the Review queue.", parent=self)
                return
            messagebox.showinfo("Catalog ready", f"{count} card(s) included, with photos, grouped by game, set and collector number.\n\nThis is a general catalog, not a marketplace-specific upload template.", parent=self)
        self.run_job("Building PDF catalog…", work, finish)

    def export_package(self):
        if self.busy:
            return
        path = filedialog.asksaveasfilename(parent=self, title="Export listing drafts and photos", defaultextension=".zip", initialdir=self.project_root / "exports", initialfile="card-listing-package.zip", filetypes=[("ZIP listing package", "*.zip")])
        if path:
            cards = list(self.visible_cards)
            self.run_job("Organizing listing photos and drafts…", lambda: self.store.export_listing_package(path, cards), lambda count: messagebox.showinfo("Listing package ready", f"{count} reviewed card record(s), with photos organized by game / set / SKU. No listing has been posted.", parent=self))

    def run_job(self, message, work, complete=None):
        if self.busy:
            messagebox.showinfo("Job in progress", "Wait for the current job to finish.", parent=self)
            return False
        self.busy = True
        for win in self.detail_windows.values():
            if win.winfo_exists() and os.name == "nt":
                win.attributes("-disabled", True)
        self.cancel_event.clear()
        self.status_text.set(message)
        self.progress.start(12)
        def execute():
            try:
                self.events_queue.put(("done", work(), complete))
            except Exception as exc:
                self.events_queue.put(("error", str(exc)))
        threading.Thread(target=execute, daemon=True).start()
        return True

    def drain_events(self):
        try:
            while True:
                event = self.events_queue.get_nowait()
                if event[0] == "status":
                    self.status_text.set(event[1])
                    continue
                self.busy = False
                for win in self.detail_windows.values():
                    if win.winfo_exists() and os.name == "nt":
                        win.attributes("-disabled", False)
                self.progress.stop()
                self.status_text.set("Ready. Changes are saved locally.")
                self.refresh_table()
                if event[0] == "error":
                    messagebox.showerror("Could not complete the job", event[1], parent=self)
                elif event[2]:
                    try:
                        event[2](event[1])
                    except tk.TclError:
                        pass  # The user may have closed the originating window.
        except queue.Empty:
            pass
        self.after(100, self.drain_events)

    def show_report(self, text):
        messagebox.showinfo("Job report", text, parent=self)

    def sales_history(self):
        win = tk.Toplevel(self)
        win.title("Sales history")
        win.geometry("950x550")
        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)
        label(frame, "Completed sales", "Heading.TLabel").pack(anchor="w")
        rows = self.store.sales()
        totals = {currency: sum(s["unit_price"] * s["quantity"] for s in rows if s["currency"] == currency) for currency in ("USD", "EUR")}
        # Packed before the expand=True text area below, so the totals can never be squeezed off-window.
        label(frame, "Gross sales: " + "  |  ".join(f"{k} {v:,.2f}" for k, v in totals.items()), "Heading.TLabel").pack(side="bottom", anchor="w")
        text = text_area(frame, 20)
        lines = [f"{s['sold_at']}  ·  {s['sku']}  ·  {s['name']}\n{s['quantity']} × {s['currency']} {s['unit_price']:.2f}  ·  {s['channel']}  ·  {s['reference']}\n" for s in rows]
        set_text(text, "\n".join(lines) if lines else "No sales recorded yet.", True)

    def build_settings(self):
        label(self.content, "Settings & help", "Title.TLabel").pack(anchor="w")
        tabs = ttk.Notebook(self.content)
        tabs.pack(fill="both", expand=True, pady=20)
        pricing = ttk.Frame(tabs, padding=20)
        cloud = ttk.Frame(tabs, padding=20)
        help_tab = ttk.Frame(tabs, padding=20)
        for panel, title in ((pricing, "Pricing assumptions"), (cloud, "Recognition & privacy"), (help_tab, "Backups & guide")):
            tabs.add(panel, text=title)
        settings = self.store.settings()
        values = {}
        specs = [("currency", "Default currency", ["USD", "EUR"]), ("fee_percent", "Marketplace fee (%)", None), ("fixed_fee", "Fixed fee per item", None), ("shipping_cost", "Default shipping per item (starts at a standard USPS envelope stamp)", None), ("packaging_cost", "Packaging per item", None), ("quick_sale_percent", "Quick-sale target (%)", None), ("stale_days", "Price freshness limit (days)", None)]
        for index, (key, title, options) in enumerate(specs):
            values[key] = tk.StringVar(value=str(settings[key]))
            field(pricing, title, values[key], index // 2, index % 2, options)
        factors = {}
        row = 4
        label(pricing, "Condition factors (editable assumptions, not measured market values)", "Small.TLabel").grid(row=row, column=0, columnspan=2, sticky="w", pady=10)
        for index, (key, value) in enumerate(settings["condition_factors"].items()):
            factors[key] = tk.StringVar(value=str(value))
            field(pricing, key, factors[key], row + 1 + index // 2, index % 2)
        def save_pricing():
            try:
                parsed = {k: v.get() if k == "currency" else float(v.get()) for k, v in values.items()}
                for k in ("fee_percent", "quick_sale_percent"):
                    if not 0 <= parsed[k] <= 100:
                        raise ValueError("Percentages must be between 0 and 100.")
                for k in ("fixed_fee", "shipping_cost", "packaging_cost"):
                    parsed[k] = money(parsed[k], False)
                if not 1 <= parsed["stale_days"] <= 90 or not parsed["stale_days"].is_integer():
                    raise ValueError("Freshness must be a whole number from 1 to 90 days.")
                parsed["condition_factors"] = {k: float(v.get()) for k, v in factors.items()}
                if any(not 0 < f <= 1.5 for f in parsed["condition_factors"].values()):
                    raise ValueError("Condition factors must be greater than 0 and no more than 1.5.")
                self.store.save_settings(parsed)
                messagebox.showinfo("Saved", "Pricing assumptions saved. Existing approved asking prices are unchanged.", parent=self)
            except ValueError as exc:
                messagebox.showerror("Check settings", str(exc), parent=self)
        button(pricing, "Save pricing settings", save_pricing, True).grid(row=8, column=0, sticky="w", pady=12)
        label(pricing, "Net calculations exclude tax and use simplified per-item fees. Check your marketplace's actual fee basis. USD and EUR are never converted or combined. Shipping defaults to a First-Class Mail Forever stamp rate as of mid-2026 — postage changes over time, so update this when it does. A card can override shipping individually in its own Pricing tab.", "Small.TLabel", wraplength=750).grid(row=9, column=0, columnspan=2, sticky="w")
        label(cloud, "Local OCR is ready to use", "Heading.TLabel").pack(anchor="w")
        label(cloud, "Windows reads the photo locally. Catalog searches send only card name/number text. For difficult photos, optional cloud vision can extract more details. It still requires your review.", wraplength=780).pack(anchor="w", pady=(8, 20))
        self.cloud_enabled = tk.BooleanVar(value=settings["cloud_enabled"])
        ttk.Checkbutton(cloud, text="Allow selected card photos to be sent to OpenAI for cloud identification (API charges apply)", variable=self.cloud_enabled).pack(anchor="w")
        label(cloud, "OpenAI API key — held in memory for this session only", "Small.TLabel").pack(anchor="w", pady=(20, 4))
        key_var = tk.StringVar()
        ttk.Entry(cloud, textvariable=key_var, show="•", width=65).pack(anchor="w")
        label(cloud, "A key is already available." if self.scanner.api_key else "No API key configured. Local OCR and manual catalog lookup work without one.", "Small.TLabel").pack(anchor="w", pady=5)
        model_var = tk.StringVar(value=settings["vision_model"])
        label(cloud, "Vision-capable model", "Small.TLabel").pack(anchor="w", pady=(16, 4))
        ttk.Entry(cloud, textvariable=model_var, width=36).pack(anchor="w")
        def save_cloud():
            if key_var.get().strip():
                self.scanner.api_key = key_var.get().strip()
                key_var.set("")
            self.store.save_settings({"cloud_enabled": self.cloud_enabled.get(), "vision_model": model_var.get().strip()})
            self.status_text.set("Recognition settings saved. API key is never written to inventory, exports or backups.")
        button(cloud, "Save recognition settings", save_cloud, True).pack(anchor="w", pady=18)
        button(cloud, "Clear session API key", lambda: setattr(self.scanner, "api_key", "")).pack(anchor="w")
        label(cloud, "You can also set OPENAI_API_KEY before launching. Cloud requests set store=false; this does not override the provider's retention or billing policies. Your ChatGPT subscription is separate from API billing.", "Small.TLabel", wraplength=760).pack(anchor="w", pady=20)
        label(help_tab, "Your collection lives here", "Heading.TLabel").pack(anchor="w")
        label(help_tab, str(self.store.root), wraplength=780).pack(anchor="w", pady=10)
        label(help_tab, "Backups contain the database and imported photos, including review and pricing history. They do not contain API keys. Keep another copy on a separate drive.", wraplength=780).pack(anchor="w", pady=10)
        button(help_tab, "Create backup ZIP", self.backup, True).pack(anchor="w", pady=10)
        button(help_tab, "Open user guide", lambda: os.startfile(self.project_root / "USER GUIDE.md")).pack(anchor="w", pady=8)
        label(help_tab, "Coverage", "Heading.TLabel").pack(anchor="w", pady=(24, 8))
        label(help_tab, "Pokémon: TCGdex catalog, TCGplayer / Cardmarket price references.\nMagic: Scryfall printings and pricing.\nYu-Gi-Oh!: YGOPRODeck printings; price references require manual review.\nSports / other: optional vision extraction, manual specifics and sold comparables.\n\nNo auto grading, authenticity guarantees, or automatic marketplace posting.\nHotkeys: Ctrl+O import photos; Ctrl+F search inventory.", wraplength=780, justify="left").pack(anchor="w")

    def backup(self):
        path = filedialog.asksaveasfilename(parent=self, initialdir=self.project_root / "backups", initialfile="card-desk-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip", defaultextension=".zip", filetypes=[("ZIP backup", "*.zip")])
        if path:
            self.run_job("Creating a consistent backup…", lambda: self.store.backup(path), lambda p: messagebox.showinfo("Backup saved", str(p), parent=self))

    def close(self):
        if self.busy:
            messagebox.showinfo("Job running", "Cancel the job and wait for the current card to finish before closing. This protects imported photos and database writes.", parent=self)
            return
        for win in list(self.detail_windows.values()):
            if win.winfo_exists() and not win.request_close():
                return
        self.phone_intake.stop()
        self.destroy()


class CardWindow(tk.Toplevel):
    def __init__(self, app, identifier):
        super().__init__(app)
        self.app, self.store, self.identifier = app, app.store, identifier
        self.card = self.store.get(identifier)
        self.title("Review card · " + self.card["sku"])
        self.geometry("1250x850")
        self.minsize(1050, 720)
        self.configure(bg=BG)
        self.dirty = False
        self.loading = False
        self.protocol("WM_DELETE_WINDOW", self.request_close)
        self.transient(app)
        self.vars = {}
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)
        self.heading = label(outer, "", "Heading.TLabel")
        self.heading.pack(anchor="w")
        self.review_state = label(outer, "", "Small.TLabel")
        self.review_state.pack(anchor="w", pady=(3, 14))
        bottom = ttk.Frame(outer)
        button(bottom, "Save details", self.save, True).pack(side="left", padx=(0, 8))
        button(bottom, "Confirm identity", lambda: self.confirm("identity")).pack(side="left", padx=4)
        button(bottom, "Confirm condition", lambda: self.confirm("condition")).pack(side="left", padx=4)
        button(bottom, "Mark Ready", lambda: self.set_status("Ready")).pack(side="right")
        bottom.pack(side="bottom", fill="x", pady=(16, 0))
        body = ttk.Frame(outer)
        body.pack(side="top", fill="both", expand=True)
        photos = ttk.Frame(body, width=275)
        photos.pack(side="left", fill="y", padx=(0, 20))
        photos.pack_propagate(False)
        self.preview = tk.Label(photos, bg="#E2E9EA", text="No photo attached", font=("Segoe UI", 12), fg=MUTED)
        self.preview.pack(fill="x", ipady=12)
        self.photo_caption = label(photos, "", "Small.TLabel", wraplength=270)
        self.photo_caption.pack(pady=8)
        button(photos, "Next photo", self.next_photo).pack(fill="x", pady=3)
        button(photos, "View full photo", self.full_photo).pack(fill="x", pady=3)
        button(photos, "Add front / back / detail", self.attach_photo).pack(fill="x", pady=3)
        button(photos, "Scan this card", self.scan).pack(fill="x", pady=(16, 3))
        label(photos, "Inspect the physical card for foil, surface wear and authenticity. A photo cannot certify a grade.", "Small.TLabel", wraplength=260).pack(anchor="w", pady=16)
        self.tabs = ttk.Notebook(body)
        self.tabs.pack(side="left", fill="both", expand=True)
        self.identity = ttk.Frame(self.tabs, padding=16)
        self.condition = ttk.Frame(self.tabs, padding=16)
        self.pricing = ttk.Frame(self.tabs, padding=16)
        self.selling = ttk.Frame(self.tabs, padding=16)
        self.evidence = ttk.Frame(self.tabs, padding=16)
        for panel, title in ((self.identity, "Identity"), (self.condition, "Condition & stock"), (self.pricing, "Pricing"), (self.selling, "Listing"), (self.evidence, "Evidence & history")):
            self.tabs.add(panel, text=title)
        self.build_identity()
        self.build_condition()
        self.build_pricing()
        self.build_selling()
        danger = ttk.Frame(self.evidence, padding=(0, 12, 0, 0))
        button(danger, "Delete this card permanently", self.delete_card).pack(anchor="w")
        label(danger, "Removes this record, its photos and identification history. Cards with recorded sales cannot be deleted — archive them instead.", "Small.TLabel", wraplength=650).pack(anchor="w", pady=(4, 0))
        danger.pack(side="bottom", fill="x")
        self.evidence_text = text_area(self.evidence, 24, readonly=True)
        self.photo_index = 0
        self.load()

    def var(self, key):
        if key not in self.vars:
            self.vars[key] = tk.StringVar()
            self.vars[key].trace_add("write", lambda *args: self.changed())
        return self.vars[key]

    def changed(self):
        if not self.loading:
            self.dirty = True

    def build_identity(self):
        bar = ttk.Frame(self.identity)
        bar.pack(fill="x", pady=(0, 14))
        button(bar, "Search catalog / choose printing", self.search_catalog, True).pack(side="left")
        button(bar, "Open source", lambda: open_url(self.card.get("catalog_url", ""))).pack(side="right")
        grid = ttk.Frame(self.identity)
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1)
        fields = [("name", "Card name", None), ("game", "Game / category", GAMES), ("set_name", "Set / product", None), ("set_code", "Catalog set code", None), ("number", "Collector / printed number", None), ("rarity", "Rarity", None), ("variant", "Finish / variant", VARIANTS), ("language", "Language", ["Unknown"] + list(LANGUAGES)), ("edition", "Edition / parallel / stamp", None), ("year", "Year", None), ("card_type", "Card type / position", None), ("artist", "Artist / manufacturer", None)]
        for index, (key, text, options) in enumerate(fields):
            field(grid, text, self.var(key), index // 2, index % 2, options)
        self.match_hint = label(self.identity, "", "Small.TLabel", wraplength=670)
        self.match_hint.pack(anchor="w", pady=12)

    def build_condition(self):
        grid = ttk.Frame(self.condition)
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1)
        fields = [("condition", "Seller-assessed condition", CONDITIONS), ("quantity", "Available quantity", None), ("grade_company", "Grading company (if slabbed)", None), ("grade", "Grade shown on label", None), ("cert_number", "Certification number", None), ("storage_location", "Storage bin / binder / shelf", None), ("cost", "Purchase cost per card", None), ("tags", "Tags / player / team / serial", None)]
        for index, (key, text, options) in enumerate(fields):
            field(grid, text, self.var(key), index // 2, index % 2, options)
        label(self.condition, "Condition notes — corners, edges, surface, centering and visible defects", "Small.TLabel").pack(anchor="w", pady=(10, 0))
        label(self.condition, "Only group copies with the same printing, finish, language and condition. Use separate records when condition differs.", "Small.TLabel", wraplength=650).pack(side="bottom", anchor="w", pady=8)
        self.notes = text_area(self.condition, 8)
        self.notes.bind("<<Modified>>", self.note_changed)

    def note_changed(self, event):
        if self.notes.edit_modified():
            self.changed()
            self.notes.edit_modified(False)

    def build_pricing(self):
        bar = ttk.Frame(self.pricing)
        bar.pack(fill="x")
        button(bar, "Refresh source prices", self.refresh_prices, True).pack(side="left")
        button(bar, "+ Sold comparable", self.add_comp).pack(side="right")
        self.price_summary = label(self.pricing, "", "Heading.TLabel", wraplength=680)
        self.price_summary.pack(anchor="w", pady=(16, 4))
        self.price_explanation = label(self.pricing, "", "Small.TLabel", wraplength=680, justify="left")
        self.price_explanation.pack(anchor="w", pady=(0, 12))
        # Fixed-height content below the quote table is packed (bottom-up) before the
        # table claims its expand=True space, so it can never get squeezed off-window.
        label(self.pricing, "Suggestions are starting points, not guaranteed sale values. Manual asking prices remain unchanged when source prices refresh. Set a shipping override for a card that needs its own packaging (a slab, a bulky mailer) instead of the account-wide default; the suggested ask adds whichever one applies on top of the item's value.", "Small.TLabel", wraplength=670).pack(side="bottom", anchor="w", pady=12)
        buttons = ttk.Frame(self.pricing)
        button(buttons, "Use suggested price", lambda: self.use_price("suggested")).pack(side="left", padx=(0, 8))
        button(buttons, "Use quick-sale price", lambda: self.use_price("quick_sale")).pack(side="left")
        buttons.pack(side="bottom", fill="x")
        form = ttk.Frame(self.pricing)
        field(form, "Asking price per card", self.var("ask_price"), 0, 0)
        field(form, "Currency", self.var("currency"), 0, 1, ["USD", "EUR"])
        default_shipping = self.store.settings()["shipping_cost"]
        field(form, f"Shipping override (blank = {self.card.get('currency', 'USD')} {default_shipping:.2f} default)", self.var("shipping_override"), 1, 0)
        form.pack(side="bottom", fill="x", pady=8)
        self.comp_summary = label(self.pricing, "", "Small.TLabel", wraplength=650)
        self.comp_summary.pack(side="bottom", anchor="w", pady=10)
        cols = ("source", "price", "variant", "updated")
        self.quote_tree = ttk.Treeview(self.pricing, columns=cols, show="headings", height=6)
        for k, title, width in zip(cols, ("Source / scope", "Price", "Variant", "Source date"), (240, 100, 150, 100)):
            self.quote_tree.heading(k, text=title)
            self.quote_tree.column(k, width=width, minwidth=65)
        self.quote_tree.pack(side="top", fill="both", expand=True)

    def build_selling(self):
        self.ready_hint = label(self.selling, "", "Small.TLabel", wraplength=680, justify="left")
        self.ready_hint.pack(anchor="w")
        # Fixed-height controls below the listing draft are packed (bottom-up) before the
        # draft's text area claims its expand=True space, so they can never be squeezed off-window.
        button(self.selling, "Return to Needs review / restore archived card", lambda: self.set_status("Needs review")).pack(side="bottom", anchor="w", pady=6)
        bar = ttk.Frame(self.selling)
        button(bar, "Copy listing draft", self.copy_listing).pack(side="left", padx=(0, 5))
        button(bar, "Mark Listed", lambda: self.set_status("Listed")).pack(side="left", padx=5)
        button(bar, "Record sale", self.record_sale, True).pack(side="left", padx=5)
        bar.pack(side="bottom", fill="x", pady=8)
        self.listing_text = text_area(self.selling, 20, readonly=True)

    def load(self):
        if not self.winfo_exists():
            return
        self.loading = True
        self.card = self.store.get(self.identifier)
        for key, var in self.vars.items():
            value = self.card.get(key, "")
            var.set("" if value is None else str(value))
        set_text(self.notes, self.card["condition_notes"])
        self.notes.edit_modified(False)
        self.heading.configure(text=f"{self.card['name'] or 'Unidentified card'}   ·   {self.card['sku']}")
        self.review_state.configure(text=f"{self.card['status']}   |   Identity: {'confirmed' if self.card['identity_confirmed'] else 'needs review'}   |   Condition: {'confirmed' if self.card['condition_confirmed'] else 'needs review'}")
        self.match_hint.configure(text=f"{len(self.card['candidates'])} candidate printing(s) from the last scan. Choose a catalog match, then verify it against your photo.\nCatalog: {self.card['provider'] or 'not linked'}  {self.card['catalog_id']}")
        self.render_photo()
        self.render_prices()
        title, description = listing(self.card)
        set_text(self.listing_text, title + "\n\n" + description, True)
        problems = ready_problems(self.card, bool(self.store.photos(self.identifier)))
        self.ready_hint.configure(text="Ready checklist: " + ("\n".join(problems) if problems else "All checks complete. You can mark this card Ready.") + "\nListing drafts are not automatically published.")
        evidence = "RECOGNITION NOTES\n" + self.card["scan_notes"] + "\n\nEXTRACTED TEXT\n" + self.card["scan_text"]
        evidence += "\n\nCATALOG METADATA\n" + json.dumps(self.card["metadata"], indent=2, ensure_ascii=False)
        evidence += "\n\nSOLD COMPARABLES\n" + json.dumps(self.store.comps(self.identifier), indent=2, ensure_ascii=False)
        evidence += "\n\nSOURCE QUOTES (including timestamp, scope and source URL)\n" + json.dumps(self.store.quotes(self.identifier), indent=2, ensure_ascii=False)
        evidence += "\n\nAUDIT HISTORY\n" + "\n".join(f"{e['created_at']}  {e['action']}  {e['detail']}" for e in self.store.events(self.identifier))
        set_text(self.evidence_text, evidence, True)
        self.loading = False
        self.dirty = False

    def render_photo(self):
        self.photos = self.store.photos(self.identifier)
        if not self.photos:
            self.preview.configure(image="", text="No photo attached", height=18)
            self.photo_caption.configure(text="Add front and back photos.")
            return
        self.photo_index %= len(self.photos)
        photo = self.photos[self.photo_index]
        with Image.open(self.store.root / photo["path"]) as image:
            image = ImageOps.contain(image, (270, 390), Image.Resampling.LANCZOS)
            self.photo_image = ImageTk.PhotoImage(image)
        self.preview.configure(image=self.photo_image, text="", height=390)
        self.photo_caption.configure(text=f"{photo['side']} · {self.photo_index + 1}/{len(self.photos)}\n{photo['original_name']}")

    def next_photo(self):
        self.photo_index += 1
        self.render_photo()

    def full_photo(self):
        if self.photos:
            os.startfile(self.store.root / self.photos[self.photo_index]["path"])

    def attach_photo(self):
        if self.app.busy or not self.save():
            return
        path = filedialog.askopenfilename(parent=self, filetypes=[("Card photos", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")])
        if not path:
            return
        side = "Front" if not self.photos else "Back" if not any(p["side"] == "Back" for p in self.photos) else "Detail"
        try:
            card, duplicate = import_photo(self.store, path, self.identifier, side)
            if duplicate:
                messagebox.showinfo("Duplicate image", f"That exact file is already attached to {card['sku']}. No duplicate was created.", parent=self)
            self.load()
            self.app.refresh_table()
        except Exception as exc:
            messagebox.showerror("Photo import failed", str(exc), parent=self)

    def save(self):
        if self.app.busy:
            messagebox.showinfo("Job in progress", "Wait for the current job before saving edits.", parent=self)
            return False
        try:
            values = {k: var.get() for k, var in self.vars.items()}
            values["condition_notes"] = self.notes.get("1.0", "end-1c")
            self.store.update(self.identifier, values)
            self.load()
            self.app.refresh_table()
            return True
        except (ValueError, OSError) as exc:
            messagebox.showerror("Check card details", str(exc), parent=self)
            return False

    def confirm(self, kind):
        if not self.save():
            return
        try:
            self.store.confirm(self.identifier, identity=kind == "identity", condition=kind == "condition")
            self.load()
            self.app.refresh_table()
        except ValueError as exc:
            messagebox.showerror("Review incomplete", str(exc), parent=self)

    def set_status(self, status):
        if not self.save():
            return
        try:
            if status == "Needs review" and self.card["quantity"] == 0:
                raise ValueError("This card has no stock left. Create a new record for a newly acquired card.")
            self.store.update(self.identifier, {"status": status})
            self.load()
            self.app.refresh_table()
        except ValueError as exc:
            messagebox.showerror("Cannot change status", str(exc), parent=self)

    def scan(self):
        if not self.save():
            return
        if messagebox.askyesno("Rescan this card?", "This replaces draft identity fields and clears review / asking-price approval. Continue?", parent=self):
            mode, hint = self.app.mode.get(), self.app.game_hint.get()
            self.app.run_job("Reading card photo…", lambda: self.app.scanner.scan(self.identifier, mode, hint), lambda result: self.load())

    def search_catalog(self):
        if not self.save():
            return
        CatalogWindow(self)

    def render_prices(self):
        quotes = self.store.quotes(self.identifier)
        comps = self.store.comps(self.identifier)
        settings = self.store.settings()
        self.recommendation = estimate(self.card, quotes, comps, settings)
        r = self.recommendation
        if r["suggested"] is not None and self.card.get("ask_price") is None:
            self.card = self.store.update(self.identifier, {"ask_price": r["suggested"]}, "Suggested price applied automatically")
            self.var("ask_price").set(str(self.card["ask_price"]))
            self.app.refresh_table()
        suggested = f"{r['currency']} {r['suggested']:.2f}" if r["suggested"] is not None else "Review / evidence needed"
        self.price_summary.configure(text="Suggested ask: " + suggested)
        net = net_proceeds(self.card["ask_price"], self.card["cost"], settings, self.card.get("shipping_override"))
        explanation = r["source"] + "\n" + "\n".join(r["warnings"])
        if net is not None:
            explanation += f"\nEstimated net after cost and configured fees: {self.card['currency']} {net:.2f} per card."
        self.price_explanation.configure(text=explanation)
        self.quote_tree.delete(*self.quote_tree.get_children())
        seen = set()
        for q in quotes:
            key = (q["source"], q["variant"], q["currency"], q["catalog_id"])
            if key in seen:
                continue
            seen.add(key)
            suffix = "" if q["eligible"] else " [reference]"
            self.quote_tree.insert("", "end", values=(q["source"] + suffix, f"{q['currency']} {q['amount']:.2f}", q["variant"], str(q.get("source_updated") or "Not supplied")[:10]))
        self.comp_summary.configure(text=f"{len(comps)} manually entered sold comparable(s). Only exact, confirmed matches sold within 90 days contribute. Details appear in Evidence & history.")

    def refresh_prices(self):
        if self.save():
            self.app.run_job("Fetching price references…", lambda: self.app.refresh_price(self.identifier), lambda result: self.load())

    def use_price(self, key):
        if not self.save():
            return
        amount = self.recommendation[key]
        if amount is None:
            messagebox.showinfo("No defensible suggestion yet", "Confirm the exact card and condition, then refresh matching prices or add sold comparables. You can also enter a manual asking price.", parent=self)
            return
        self.store.update(self.identifier, {"ask_price": amount}, "Suggested price accepted")
        self.load()
        self.app.refresh_table()

    def add_comp(self):
        if self.save():
            CompWindow(self)

    def copy_listing(self):
        if self.save():
            self.clipboard_clear()
            self.clipboard_append(self.listing_text.get("1.0", "end-1c"))
            self.app.status_text.set("Listing draft copied. Review it before posting to a marketplace.")

    def record_sale(self):
        if self.save():
            SaleWindow(self)

    def request_close(self):
        if self.app.busy:
            messagebox.showinfo("Job in progress", "Wait for the current job to finish before closing the review window.", parent=self)
            return False
        if self.dirty:
            result = messagebox.askyesnocancel("Unsaved edits", "Save your changes before closing?", parent=self)
            if result is None or (result and not self.save()):
                return False
        self.destroy()
        return True

    def delete_card(self):
        if self.app.busy:
            messagebox.showinfo("Job in progress", "Wait for the current job to finish before deleting a card.", parent=self)
            return
        if not messagebox.askyesno("Delete this card?", f"Permanently delete {self.card['sku']} — {self.card['name'] or 'Unidentified card'}? This removes its photos and identification history and cannot be undone.", parent=self, icon="warning"):
            return
        try:
            self.store.delete(self.identifier)
        except ValueError as exc:
            messagebox.showerror("Cannot delete", str(exc), parent=self)
            return
        self.app.detail_windows.pop(self.identifier, None)
        self.app.refresh_table()
        self.destroy()


class CatalogWindow(tk.Toplevel):
    def __init__(self, detail):
        super().__init__(detail)
        self.detail, self.app = detail, detail.app
        self.title("Choose the exact printing")
        self.geometry("1010x600")
        self.transient(detail)
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)
        label(frame, "Match the number, set and rarity to your photo", "Heading.TLabel").pack(anchor="w")
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=15)
        self.vars = {k: tk.StringVar(value=detail.card[k] if detail.card[k] != "Unknown" else "English" if k == "language" else "Pokémon" if k == "game" else "") for k in ("game", "name", "number", "set_code", "language")}
        for i, (k, title, opts) in enumerate((("game", "Game", GAMES[1:4]), ("name", "Card name", None), ("number", "Number (optional)", None), ("set_code", "Set code (optional)", None), ("language", "Language", list(LANGUAGES)))):
            field(grid, title, self.vars[k], i // 3, i % 3, opts, 24)
        button(grid, "Search catalog", self.search, True).grid(row=1, column=2, sticky="w")
        self.hint = label(frame, "Results are candidates, not proof of identity. Up to 80 results; narrow by number or set.", "Small.TLabel")
        self.hint.pack(anchor="w", pady=5)
        self.tree = ttk.Treeview(frame, columns=("name", "set", "number", "rarity"), show="headings")
        for k, text, width in (("name", "Card", 230), ("set", "Set", 340), ("number", "Number", 130), ("rarity", "Rarity", 170)):
            self.tree.heading(k, text=text)
            self.tree.column(k, width=width)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.choose())
        button(frame, "Use selected printing", self.choose, True).pack(anchor="e", pady=12)
        self.results = detail.card["candidates"]
        self.populate(self.results)

    def populate(self, results):
        self.results = results
        self.tree.delete(*self.tree.get_children())
        for index, c in enumerate(results):
            self.tree.insert("", "end", iid=str(index), values=(c["name"], c["set_name"], c["number"], c.get("rarity", "")))
        self.hint.configure(text=f"{len(results)} candidates. Select the exact printing, then verify it in the review window." if results else "No results yet. Try the exact name with fewer filters. Set codes are catalog codes, not set names.")

    def search(self):
        values = {k: v.get() for k, v in self.vars.items()}
        self.app.run_job("Searching card catalog…", lambda: self.app.scanner.catalogs.search(**values), lambda results: self.populate(results))

    def choose(self):
        selected = self.tree.selection()
        if not selected:
            return
        candidate = self.results[int(selected[0])]
        def done(result):
            if self.detail.winfo_exists():
                self.detail.load()
            self.destroy()
        self.app.run_job("Loading card specifics…", lambda: self.app.scanner.select_match(self.detail.identifier, candidate), done)


class CompWindow(tk.Toplevel):
    def __init__(self, detail):
        super().__init__(detail)
        self.title("Add a verified sold comparable")
        self.geometry("720x520")
        self.transient(detail)
        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        label(frame, "Use a completed sale, not an asking price", "Heading.TLabel").pack(anchor="w")
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=18)
        defaults = {"amount": "", "currency": detail.card["currency"], "sold_date": date.today().isoformat(), "url": "", "notes": ""}
        values = {k: tk.StringVar(value=v) for k, v in defaults.items()}
        for i, (k, title) in enumerate((("amount", "Sold item price (exclude shipping)"), ("currency", "Currency"), ("sold_date", "Sold date (YYYY-MM-DD)"), ("url", "Source URL"), ("notes", "Source / grade / sale notes"))):
            field(grid, title, values[k], i // 2, i % 2, ["USD", "EUR"] if k == "currency" else None)
        verified = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="I verified the same printing, variant, language, condition and grade.", variable=verified).pack(anchor="w", pady=15)
        label(frame, "Changing the card's identity or condition makes old comparables ineligible. Asking listings and unrelated graded sales must not be entered as sold comparables.", "Small.TLabel", wraplength=650).pack(anchor="w")
        def save():
            try:
                if detail.app.busy:
                    raise ValueError("Wait for the current job to finish.")
                detail.store.add_comp(detail.identifier, **{k: v.get() for k, v in values.items()}, verified=verified.get())
                detail.load()
                self.destroy()
            except ValueError as exc:
                messagebox.showerror("Check comparable", str(exc), parent=self)
        button(frame, "Save comparable", save, True).pack(anchor="e", pady=20)


class SaleWindow(tk.Toplevel):
    def __init__(self, detail):
        super().__init__(detail)
        self.title("Record sale")
        self.geometry("680x440")
        self.transient(detail)
        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        label(frame, f"{detail.card['quantity']} card(s) available", "Heading.TLabel").pack(anchor="w")
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=16)
        initial = {"quantity": "1", "unit_price": str(detail.card["ask_price"] or ""), "sold_at": date.today().isoformat(), "channel": "", "reference": ""}
        values = {k: tk.StringVar(value=v) for k, v in initial.items()}
        for i, (k, title) in enumerate((("quantity", "Quantity sold"), ("unit_price", f"Price per card ({detail.card['currency']})"), ("sold_at", "Sale date (YYYY-MM-DD)"), ("channel", "Marketplace / channel"), ("reference", "Order reference (no private buyer details)"))):
            field(grid, title, values[k], i // 2, i % 2)
        label(frame, "This reduces available stock and records a sale in your local ledger. It does not post or charge anything online.", "Small.TLabel", wraplength=600).pack(anchor="w", pady=10)
        def save():
            try:
                if detail.app.busy:
                    raise ValueError("Wait for the current job to finish.")
                detail.store.record_sale(detail.identifier, **{k: v.get() for k, v in values.items()})
                detail.load()
                detail.app.refresh_table()
                self.destroy()
            except ValueError as exc:
                messagebox.showerror("Check sale", str(exc), parent=self)
        button(frame, "Record sale", save, True).pack(anchor="e", pady=10)
