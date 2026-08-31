from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import statistics

GAMES = ["Unknown", "Pokémon", "Magic: The Gathering", "Yu-Gi-Oh!", "Sports", "Other"]
CONDITIONS = ["Unreviewed", "Near Mint", "Lightly Played", "Moderately Played", "Heavily Played", "Damaged", "Graded"]
STATUSES = ["Needs review", "Ready", "Listed", "Sold", "Archived"]
VARIANTS = ["Unknown", "Normal", "Holofoil", "Reverse Holofoil", "Foil", "Etched", "1st Edition", "1st Edition Holofoil", "Unlimited", "Unlimited Holofoil", "Other"]
IDENTITY_FIELDS = ["name", "game", "set_name", "set_code", "number", "rarity", "variant", "edition", "language", "provider", "catalog_id", "year"]
CONDITION_FIELDS = ["condition", "grade_company", "grade", "cert_number"]
DEFAULT_SETTINGS = {"currency": "USD", "fee_percent": 13.25, "fixed_fee": 0.30, "shipping_cost": 0.82,
                    "packaging_cost": 0.25, "quick_sale_percent": 90, "stale_days": 7,
                    "condition_factors": {"Near Mint": 1.0, "Lightly Played": 0.8, "Moderately Played": 0.6, "Heavily Played": 0.4, "Damaged": 0.2},
                    "vision_model": "gpt-4.1-mini", "cloud_enabled": False}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def money(value, allow_blank=True):
    if value is None or str(value).strip() == "":
        if allow_blank:
            return None
        raise ValueError("Enter an amount.")
    try:
        d = Decimal(str(value).replace(",", "").strip())
        if not d.is_finite() or d < 0 or d > 100000000:
            raise ValueError("Amounts must be finite and between 0 and 100,000,000.")
        return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError("Enter a valid amount, such as 12.50.") from exc


def fingerprint(card):
    fields = IDENTITY_FIELDS + CONDITION_FIELDS
    return hashlib.sha256(json.dumps([str(card.get(f, "")).strip().casefold() for f in fields], ensure_ascii=False).encode()).hexdigest()


def age_days(stamp):
    if not stamp:
        return None
    try:
        if isinstance(stamp, (int, float)):
            dt = datetime.fromtimestamp(stamp / 1000 if stamp > 10**11 else stamp, timezone.utc)
        else:
            dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except (ValueError, TypeError, OverflowError):
        return None


def gross_up_for_selling_costs(market_value, settings, shipping_override=None):
    """Solve for the ask price whose net proceeds — after the configured marketplace
    fee %, fixed fee, shipping and packaging costs — equal market_value, so covering
    shipping or absorbing fees doesn't quietly cost the seller part of the item's value.
    shipping_override replaces settings["shipping_cost"] for a card that needs its own
    shipping figure (a slab, a bulky mailer, etc.) instead of the account-wide default."""
    fee_fraction = Decimal(str(settings["fee_percent"])) / 100
    if fee_fraction >= 1:
        return money(market_value)
    shipping = shipping_override if shipping_override is not None else settings["shipping_cost"]
    extra = Decimal(str(settings["fixed_fee"])) + Decimal(str(shipping)) + Decimal(str(settings["packaging_cost"]))
    ask = (Decimal(str(market_value)) + extra) / (1 - fee_fraction)
    return float(ask.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def estimate(card, quotes, comps, settings):
    """No invented prices. Quotes are guides; confirmed comps are matched observations.
    The suggested ask is grossed up over the raw market value to cover this computer's
    configured shipping, packaging and marketplace fee, so the seller still nets the
    underlying item value (see gross_up_for_selling_costs)."""
    currency = card.get("currency", settings["currency"])
    result = {"suggested": None, "quick_sale": None, "market_value": None, "source": "No matching price evidence", "warnings": [], "currency": currency}
    if not card.get("identity_confirmed") or not card.get("condition_confirmed"):
        result["warnings"].append("Confirm identity, variant, language and condition before pricing.")
        return result
    current = fingerprint(card)
    market_value = None
    relevant = [c for c in comps if c["fingerprint"] == current and c["currency"] == currency
                and c.get("verified") and age_days(c["sold_date"]) is not None and age_days(c["sold_date"]) <= 90]
    if relevant:
        market_value = money(statistics.median(c["amount"] for c in relevant))
        result["source"] = f"Median of {len(relevant)} manually verified sold comparable(s), item price excluding shipping"
        if len(relevant) < 3:
            result["warnings"].append("Fewer than three sold comparables; price confidence is limited.")
    elif card.get("condition") == "Graded":
        result["warnings"].append("Raw-card prices cannot value a graded card. Add matching graded sold comparables or set a manual price.")
    elif card.get("edition", "").strip().casefold() not in ("", "standard", "unlimited", "1st edition", "first edition"):
        result["warnings"].append("Special edition, stamp or parallel requires matching sold comparables or a manual price.")
    elif card.get("edition", "").strip().casefold() in ("1st edition", "first edition") and not card.get("variant", "").startswith("1st Edition"):
        result["warnings"].append("First edition needs a matching first-edition price variant.")
    else:
        eligible = [q for q in quotes if q.get("eligible") and q.get("currency") == currency
                    and q.get("variant") == card.get("variant") and q.get("language") == card.get("language")
                    and q.get("provider") == card.get("provider") and q.get("catalog_id") == card.get("catalog_id")]
        fresh = [q for q in eligible if age_days(q.get("source_updated") or q.get("fetched_at")) is not None
                 and age_days(q.get("source_updated") or q.get("fetched_at")) <= int(settings["stale_days"])]
        if fresh:
            q = fresh[0]
            factor = float(settings["condition_factors"].get(card.get("condition"), 0))
            if factor:
                market_value = money(q["amount"] * factor)
                result["source"] = q["source"] + f" × {factor:g} condition factor"
                result["warnings"].append("Catalog guide, not a verified sold comp. Condition factors are editable assumptions, not market measurements.")
                if not q.get("source_updated"):
                    result["warnings"].append("Provider does not report price age; the shown date is retrieval time only.")
        elif eligible:
            result["warnings"].append("Matching pricing is stale. Refresh prices before using a suggestion.")
        else:
            result["warnings"].append("No eligible price for this exact printing, finish, language and currency. Set a manual price or add sold comparables.")
    if market_value is not None:
        result["market_value"] = market_value
        shipping_override = card.get("shipping_override")
        shipping_used = shipping_override if shipping_override is not None else settings["shipping_cost"]
        shipping_label = "the shipping cost set on this card" if shipping_override is not None else "your configured default shipping cost"
        result["suggested"] = gross_up_for_selling_costs(market_value, settings, shipping_override)
        result["source"] += (f"; asking price adds {shipping_label} ({currency} {shipping_used:.2f}), "
                              f"{currency} {settings['packaging_cost']:.2f} packaging and "
                              f"{settings['fee_percent']:g}% marketplace fee on top of the {currency} {market_value:.2f} item value, "
                              f"so selling costs don't eat into it")
        result["quick_sale"] = money(result["suggested"] * float(settings["quick_sale_percent"]) / 100)
    return result


def net_proceeds(price, cost, settings, shipping_override=None):
    """Per item estimate, taxes and marketplace-specific fee bases excluded."""
    if price is None:
        return None
    gross = Decimal(str(price))
    net = gross * (1 - Decimal(str(settings["fee_percent"])) / 100)
    shipping = shipping_override if shipping_override is not None else settings["shipping_cost"]
    for value in (settings["fixed_fee"], shipping, settings["packaging_cost"]):
        net -= Decimal(str(value))
    net -= Decimal(str(cost or 0))
    return float(net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def ready_problems(card, has_photo=True):
    problems = []
    for key, label in (("name", "Card name"), ("set_name", "Set / product"), ("number", "Collector number")):
        if not str(card.get(key, "")).strip():
            problems.append(label + " is missing.")
    if card.get("game") == "Unknown":
        problems.append("Choose the card game / category.")
    if card.get("variant") in (None, "", "Unknown") or card.get("language") in (None, "", "Unknown"):
        problems.append("Confirm finish / variant and language.")
    if not card.get("identity_confirmed"):
        problems.append("Identity has not been confirmed against the photos.")
    if not card.get("condition_confirmed") or card.get("condition") == "Unreviewed":
        problems.append("Condition has not been reviewed.")
    if card.get("condition") == "Graded" and (not card.get("grade_company") or not card.get("grade")):
        problems.append("Enter grading company and label grade.")
    if not card.get("ask_price") or card["ask_price"] <= 0:
        problems.append("Set a positive asking price.")
    if not has_photo:
        problems.append("Attach at least one card photo.")
    if card.get("quantity", 0) < 1:
        problems.append("There is no available stock.")
    return problems


def listing(card):
    title = " ".join(str(card.get(k, "")).strip() for k in ("name", "set_name", "number", "variant", "condition") if card.get(k))
    title = title[:80].rstrip()
    lines = [f"{card.get('name', 'Card')} — {card.get('game', '')}", ""]
    for field, label in (("set_name", "Set"), ("set_code", "Set code"), ("number", "Collector number"), ("year", "Year"),
                         ("rarity", "Rarity"), ("variant", "Finish / variant"), ("edition", "Edition"), ("language", "Language"),
                         ("condition", "Seller-assessed condition"), ("grade_company", "Grading company"), ("grade", "Label grade"), ("cert_number", "Certificate")):
        if card.get(field):
            lines.append(f"{label}: {card[field]}")
    lines += ["", "Condition notes: " + (card.get("condition_notes") or "See attached front and back photos."),
              "", "Please review the photos for the exact item and visible wear.", "SKU: " + card.get("sku", "")]
    if not card.get("identity_confirmed") or not card.get("condition_confirmed"):
        lines.insert(0, "DRAFT — identification and condition must be reviewed before publishing.\n")
    return title, "\n".join(lines)
