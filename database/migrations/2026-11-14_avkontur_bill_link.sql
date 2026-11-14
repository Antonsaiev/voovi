ALTER TABLE avkontur
    ADD COLUMN bill_id INT UNSIGNED NULL AFTER name_product,
    ADD CONSTRAINT fk_avkontur_bill
        FOREIGN KEY (bill_id) REFERENCES bills (id) ON DELETE RESTRICT;

-- Only carry over existing, explicit links to a known Kontur bill.
UPDATE avkontur AS a
JOIN bills_voovi_links AS l ON l.schet_rand = a.rand_schet
JOIN bills AS b ON b.BillId = l.BillId
SET a.bill_id = b.id
WHERE a.rand_schet IS NOT NULL AND a.rand_schet NOT IN ('', '0');

ALTER TABLE avkontur DROP COLUMN rand_schet;
