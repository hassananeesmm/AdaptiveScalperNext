-- 0003_symbol_validation: attach broker-side validation evidence (trade
-- mode, contract spec sanity, live quote check) to the symbol_mapping
-- row written by name resolution. Name resolution and validation are
-- deliberately separate steps (see gateway/symbol_validation.py) so a
-- symbol can be re-validated on its own cadence without re-resolving.

ALTER TABLE symbol_mapping ADD COLUMN valid INTEGER;
ALTER TABLE symbol_mapping ADD COLUMN validation_reason TEXT;
ALTER TABLE symbol_mapping ADD COLUMN validation_detail TEXT;
ALTER TABLE symbol_mapping ADD COLUMN validated_at TEXT;
