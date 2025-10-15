-- 001_add_beneficiario_normalizado.sql
-- Normaliza el nombre de beneficiario y mantiene índices auxiliares para búsquedas exactas y difusas.

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE beneficiarios
    ADD COLUMN IF NOT EXISTS beneficiario_normalizado TEXT;

UPDATE beneficiarios
SET beneficiario_normalizado = unaccent(lower("BENEFICIARIO"));

CREATE OR REPLACE FUNCTION set_beneficiario_normalizado() RETURNS trigger AS $$
BEGIN
    NEW.beneficiario_normalizado := unaccent(lower(NEW."BENEFICIARIO"));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_beneficiario_normalizado ON beneficiarios;

CREATE TRIGGER trg_set_beneficiario_normalizado
    BEFORE INSERT OR UPDATE OF "BENEFICIARIO" ON beneficiarios
    FOR EACH ROW
    EXECUTE FUNCTION set_beneficiario_normalizado();

CREATE INDEX IF NOT EXISTS idx_beneficiarios_beneficiario_normalizado
    ON beneficiarios (beneficiario_normalizado);

CREATE INDEX IF NOT EXISTS idx_beneficiarios_beneficiario_trgm
    ON beneficiarios USING gin (beneficiario_normalizado gin_trgm_ops);
