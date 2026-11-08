-- Required before deploying atomic PHP/API tariff version writes.
-- Preserve all rows, IDs, dates and historical invoice references.
-- Verify a backup and row hashes before/after. ALTER needs a brief table lock.
ALTER TABLE tarif ENGINE=InnoDB;
-- tarif_parent must also be InnoDB (already so on the existing installation).
