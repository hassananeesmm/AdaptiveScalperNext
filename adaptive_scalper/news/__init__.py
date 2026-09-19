"""Keyless economic news system (directive sections 38-44).

News protection is mandatory and must fail closed: a provider outage
blocks NEW entries rather than being interpreted as "no events today."
See `calendar_service.py` for the provider fallback chain and
`blocking.py` for the deterministic block-decision logic.
"""
