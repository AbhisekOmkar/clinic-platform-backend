import phonenumbers


def normalize_phone(raw: str | None, default_region: str = "IN") -> str | None:
    """Normalize to E.164 (+91...). Returns the trimmed input if unparseable
    so lookups still work on whatever the telephony layer sent."""
    if not raw:
        return None
    candidate = raw.strip()
    try:
        parsed = phonenumbers.parse(candidate, default_region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    return candidate
