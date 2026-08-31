"""Public catalog adapters. Raw provider metadata is kept for inspection."""
import json
import threading
import time
from urllib.parse import urlencode, quote, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .domain import now, money

LANGUAGES = {"English": "en", "Japanese": "ja", "French": "fr", "German": "de", "Spanish": "es", "Italian": "it", "Portuguese": "pt", "Korean": "ko", "Chinese": "zh"}
VARIANT_KEYS = {"normal": "Normal", "nonfoil": "Normal", "holo": "Holofoil", "holofoil": "Holofoil", "reverse": "Reverse Holofoil", "reverse-holofoil": "Reverse Holofoil", "foil": "Foil", "etched": "Etched", "1st-edition": "1st Edition", "1st-edition-holofoil": "1st Edition Holofoil", "unlimited": "Unlimited", "unlimited-holofoil": "Unlimited Holofoil"}


class ProviderError(Exception):
    pass


class Http:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.last_request = 0.0

    def get(self, url, refresh=False):
        if urlparse(url).hostname not in ("api.tcgdex.net", "api.scryfall.com", "db.ygoprodeck.com"):
            raise ProviderError("Unsupported catalog host.")
        if not refresh:
            cached = self.store.cache_get(url)
            if cached is not None:
                return cached
        # Serialize and cap to four requests/second across all adapters.
        with self.lock:
            time.sleep(max(0, 0.25 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
        try:
            request = Request(url, headers={"User-Agent": "CardDesk/1.0 (local card inventory)", "Accept": "application/json"})
            with urlopen(request, timeout=25) as response:
                data = json.loads(response.read(12_000_000))
            self.store.cache_put(url, data)
            return data
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code == 400 and "ygoprodeck" in url:
                return None
            if exc.code == 429:
                raise ProviderError("Catalog rate limit reached. Wait a minute before retrying.") from exc
            raise ProviderError(f"Catalog request failed (HTTP {exc.code}). Your inventory is safe; retry later.") from exc
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise ProviderError("Could not reach the catalog. Check your internet connection and try again.") from exc


class Catalogs:
    def __init__(self, store):
        self.http = Http(store)

    def search(self, game, name, number="", set_code="", language="English"):
        name, number, set_code = name.strip(), number.strip(), set_code.strip()
        if len(name) < 2 and not (number and set_code):
            raise ValueError("Enter at least two letters of the card name, or a set code and collector number.")
        if game == "Pokémon":
            return self._pokemon_search(name, number, set_code, language)
        if game == "Magic: The Gathering":
            return self._magic_search(name, number, set_code, language)
        if game == "Yu-Gi-Oh!":
            return self._yugioh_search(name, number)
        raise ValueError("Choose Pokémon, Magic or Yu-Gi-Oh! for catalog lookup. Other categories can use vision extraction and manual details/comparables.")

    def _pokemon_search(self, name, number, set_code, language):
        lang = LANGUAGES.get(language, "en")
        params = {"name": name, "pagination:page": 1, "pagination:itemsPerPage": 80}
        if number:
            # The live API returns no results for eq: on some numeric local IDs.
            # Use its substring filter and enforce exact equality locally below.
            params["localId"] = number.split("/")[0].lstrip("0") if number.split("/")[0].isdigit() else number.split("/")[0]
        if set_code:
            # Fetching the whole named set avoids guessing API filter support for nested set IDs.
            data = self.http.get(f"https://api.tcgdex.net/v2/{lang}/sets/{quote(set_code, safe='')}")
            cards = (data or {}).get("cards", [])
            cards = [c for c in cards if (not name or name.casefold() in c["name"].casefold()) and (not number or c["localId"].lstrip("0") == number.split("/")[0].lstrip("0"))]
        else:
            cards = self.http.get(f"https://api.tcgdex.net/v2/{lang}/cards?" + urlencode(params)) or []
        if number:
            cards = [c for c in cards if c.get("localId", "").lstrip("0").casefold() == number.split("/")[0].lstrip("0").casefold()]
        return [{"provider": "tcgdex", "catalog_id": c["id"], "name": c["name"], "set_name": c["id"].rsplit("-", 1)[0], "number": c.get("localId", ""), "rarity": "", "language": language if language in LANGUAGES else "English"} for c in cards[:80] if not c["id"].startswith("A")]

    def _magic_search(self, name, number, set_code, language):
        escaped = name.replace('"', '').replace('\\', '')
        terms = [f'"{escaped}"'] if escaped else []
        if number:
            terms.append('cn:"' + number.replace('"', '') + '"')
        if set_code:
            terms.append('set:"' + set_code.replace('"', '') + '"')
        terms += ["game:paper", "lang:" + LANGUAGES.get(language, "en")]
        data = self.http.get("https://api.scryfall.com/cards/search?" + urlencode({"q": " ".join(terms), "unique": "prints", "order": "released", "dir": "desc"}))
        return [{"provider": "scryfall", "catalog_id": c["id"], "name": c["name"], "set_name": c["set_name"], "number": c["collector_number"], "rarity": c["rarity"], "language": next((k for k, v in LANGUAGES.items() if v == c["lang"]), c["lang"])} for c in (data or {}).get("data", [])[:80]]

    def _yugioh_search(self, name, number):
        if number:
            card_set = self.http.get("https://db.ygoprodeck.com/api/v7/cardsetsinfo.php?" + urlencode({"setcode": number}))
            if card_set:
                data = self.http.get("https://db.ygoprodeck.com/api/v7/cardinfo.php?" + urlencode({"id": card_set["id"]}))
            else:
                return []
        else:
            data = self.http.get("https://db.ygoprodeck.com/api/v7/cardinfo.php?" + urlencode({"fname": name, "num": 15, "offset": 0}))
        found = []
        for c in (data or {}).get("data", []):
            for s in c.get("card_sets", []):
                if number and s["set_code"].casefold() != number.casefold():
                    continue
                found.append({"provider": "ygoprodeck", "catalog_id": str(c["id"]) + ":" + s["set_code"] + ":" + s["set_rarity"], "name": c["name"], "set_name": s["set_name"], "number": s["set_code"], "rarity": s["set_rarity"], "language": "Unknown"})
        return found[:80]

    def detail(self, provider, identifier, language="English", refresh=False):
        if provider == "tcgdex":
            data = self.http.get(f"https://api.tcgdex.net/v2/{LANGUAGES.get(language, 'en')}/cards/{quote(identifier, safe='')}", refresh)
            if not data:
                raise ProviderError("This card is no longer available in the selected catalog language.")
            cset = data.get("set", {})
            return {"provider": provider, "catalog_id": data["id"], "name": data["name"], "game": "Pokémon", "set_name": cset.get("name", ""), "set_code": cset.get("id", ""), "number": data.get("localId", ""), "rarity": data.get("rarity", ""), "card_type": data.get("category", ""), "artist": data.get("illustrator", ""), "description": data.get("description", ""), "language": language, "metadata": data, "catalog_url": f"https://www.tcgdex.net/database/{LANGUAGES.get(language, 'en')}/{cset.get('id', '')}/{data['id']}"}
        if provider == "scryfall":
            data = self.http.get("https://api.scryfall.com/cards/" + quote(identifier, safe=""), refresh)
            if not data:
                raise ProviderError("Card not found in Scryfall.")
            return {"provider": provider, "catalog_id": data["id"], "name": data["name"], "game": "Magic: The Gathering", "set_name": data["set_name"], "set_code": data["set"], "number": data["collector_number"], "rarity": data["rarity"], "year": data.get("released_at", "")[:4], "card_type": data.get("type_line", ""), "artist": data.get("artist", ""), "description": data.get("oracle_text", ""), "language": next((k for k, v in LANGUAGES.items() if v == data["lang"]), data["lang"]), "metadata": data, "catalog_url": data.get("scryfall_uri", "")}
        if provider == "ygoprodeck":
            card_id, code, rarity = identifier.split(":", 2)
            response = self.http.get("https://db.ygoprodeck.com/api/v7/cardinfo.php?" + urlencode({"id": card_id}), refresh)
            if not response:
                raise ProviderError("Card not found in YGOPRODeck.")
            data = response["data"][0]
            selected = next((s for s in data.get("card_sets", []) if s["set_code"] == code and s["set_rarity"] == rarity), None)
            if not selected:
                raise ProviderError("This set/rarity match is no longer in the catalog.")
            return {"provider": provider, "catalog_id": identifier, "name": data["name"], "game": "Yu-Gi-Oh!", "set_name": selected["set_name"], "set_code": code.split("-")[0], "number": code, "rarity": rarity, "card_type": data.get("type", ""), "description": data.get("desc", ""), "language": language, "metadata": {**data, "selected_printing": selected}, "catalog_url": data.get("ygoprodeck_url", "")}
        raise ValueError("No catalog is linked to this card.")

    def quotes(self, card, refresh=True):
        detail = self.detail(card["provider"], card["catalog_id"], card["language"], refresh)
        data = detail["metadata"]
        quotes = []
        def add(source, amount, currency, variant, eligible, source_updated="", url="", note=""):
            try:
                amount = money(amount)
            except ValueError:
                return
            if amount is not None and amount > 0:
                quotes.append({"source": source, "amount": amount, "currency": currency, "variant": variant,
                               "eligible": bool(eligible), "source_updated": source_updated, "fetched_at": now(),
                               "language": card["language"], "provider": card["provider"], "catalog_id": card["catalog_id"], "url": url,
                               "note": note})
        if card["provider"] == "tcgdex":
            pricing = data.get("pricing") or {}
            tcg = pricing.get("tcgplayer") or {}
            for key, variant in VARIANT_KEYS.items():
                value = tcg.get(key)
                if isinstance(value, dict):
                    product = value.get("productId")
                    add("TCGplayer market via TCGdex", value.get("marketPrice"), tcg.get("unit", "USD"), variant,
                        card["language"] == "English", tcg.get("updated", ""),
                        f"https://www.tcgplayer.com/product/{product}" if product else "https://www.tcgplayer.com/", "Provider mapping may combine some printings; verify valuable cards.")
            cm = pricing.get("cardmarket") or {}
            add("Cardmarket trend via TCGdex", cm.get("trend"), "EUR", "Normal", False, cm.get("updated", ""),
                "https://www.cardmarket.com/en/Pokemon", "Reference only: language and exact variant may be combined.")
            add("Cardmarket foil trend via TCGdex", cm.get("trend-holo"), "EUR", "Foil (unspecified)", False, cm.get("updated", ""),
                "https://www.cardmarket.com/en/Pokemon", "Reference only: holo and reverse holo are not safely separable.")
        elif card["provider"] == "scryfall":
            prices = data.get("prices") or {}
            for key, variant, curr in (("usd", "Normal", "USD"), ("usd_foil", "Foil", "USD"), ("usd_etched", "Etched", "USD"), ("eur", "Normal", "EUR"), ("eur_foil", "Foil", "EUR")):
                add("Scryfall catalog guide", prices.get(key), curr, variant, card["language"] == "English", "", data.get("scryfall_uri", ""), "Indicative catalog pricing; not individual sold sales. Source price timestamp unavailable.")
        else:
            selected = data.get("selected_printing") or {}
            add("YGOPRODeck set guide", selected.get("set_price"), "USD", "Edition unspecified", False, "", detail.get("catalog_url", ""), "Reference only: edition, condition and sale recency are unspecified.")
            for price in data.get("card_prices", []):
                for key, currency in (("tcgplayer_price", "USD"), ("cardmarket_price", "EUR")):
                    add("YGOPRODeck all-printing floor", price.get(key), currency, "Across all versions", False, "", detail.get("catalog_url", ""), "Lowest across versions; never used as a suggested selling price.")
        return quotes
