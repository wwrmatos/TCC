
-- -----------------------------------------------------------------------------
-- MJ/SINESP (BancoVDE) 2025 cruzado com o Censo 2022 — mj_censo_2025_geo.csv
-- -----------------------------------------------------------------------------

CREATE TABLE mj_censo_2025 (
    codigo_municipio_dv CHAR(7),
    codigo_municipio    CHAR(6),
    nome_municipio      VARCHAR(60)    NOT NULL,
    municipio_capital   VARCHAR(3),
    cd_uf               CHAR(2)        NOT NULL,
    sigla_uf            CHAR(2)        NOT NULL,
    nome_uf             VARCHAR(30)    NOT NULL,
    cd_regiao           CHAR(1)        NOT NULL,
    nome_regiao         VARCHAR(20)    NOT NULL,
    -- Censo 2022 (nulo quando o município é "NÃO INFORMADO").
    area_km2            NUMERIC(16, 0),
    v0001               INTEGER,
    v0002               INTEGER,
    v0003               INTEGER,
    v0004               INTEGER,
    v0007               INTEGER,
    -- BancoVDE.
    evento              VARCHAR(80)    NOT NULL,
    data_referencia     DATE           NOT NULL,
    agente              VARCHAR(40),
    arma                VARCHAR(40),
    faixa_etaria        VARCHAR(30),
    abrangencia         VARCHAR(40)    NOT NULL,
    feminino            NUMERIC(10, 1) NOT NULL,
    masculino           NUMERIC(10, 1) NOT NULL,
    nao_informado       NUMERIC(10, 1) NOT NULL,
    total_vitima        NUMERIC(10, 1) NOT NULL,
    total               NUMERIC(10, 1) NOT NULL,
    total_peso          NUMERIC(14, 3) NOT NULL,
    -- Coordenadas da sede (nulo quando o município é "NÃO INFORMADO").
    longitude           NUMERIC(15, 7),
    latitude            NUMERIC(15, 7)
);

COMMENT ON COLUMN mj_censo_2025.nome_municipio IS 'Nome oficial do IBGE; "NÃO INFORMADO" quando o BancoVDE não identifica o município.';
COMMENT ON COLUMN mj_censo_2025.area_km2 IS 'Vem sem o separador decimal (Acrelândia = 18116211972). Divida por 1e7 para ter km².';
COMMENT ON COLUMN mj_censo_2025.v0001    IS 'Pessoas residentes (Censo 2022).';
COMMENT ON COLUMN mj_censo_2025.v0002    IS 'Domicílios.';
COMMENT ON COLUMN mj_censo_2025.v0003    IS 'Domicílios particulares.';
COMMENT ON COLUMN mj_censo_2025.v0004    IS 'Domicílios coletivos.';
COMMENT ON COLUMN mj_censo_2025.v0007    IS 'Domicílios particulares permanentes ocupados.';

CREATE INDEX ix_mj_municipio ON mj_censo_2025 (codigo_municipio_dv);

-- -----------------------------------------------------------------------------
-- SSP-DF por Região Administrativa — ssp_dp_geo.csv
-- -----------------------------------------------------------------------------

CREATE TABLE ssp_dp (
    -- Município e dimensões (sempre Brasília).
    codigo_municipio_dv CHAR(7)        NOT NULL,
    codigo_municipio    CHAR(6)        NOT NULL,
    nome_municipio      VARCHAR(60)    NOT NULL,
    municipio_capital   VARCHAR(3)     NOT NULL,
    cd_uf               CHAR(2)        NOT NULL,
    sigla_uf            CHAR(2)        NOT NULL,
    nome_uf             VARCHAR(30)    NOT NULL,
    cd_regiao           CHAR(1)        NOT NULL,
    nome_regiao         VARCHAR(20)    NOT NULL,
    -- Subdistrito (Região Administrativa).
    cd_subdist          CHAR(11)       NOT NULL,
    ra                  VARCHAR(40)    NOT NULL,
    populacao           INTEGER        NOT NULL,
    n_delegacias        SMALLINT,
    delegacias          TEXT,
    -- Ocorrências.
    ano                 SMALLINT       NOT NULL,
    eixo                VARCHAR(100)   NOT NULL,
    natureza            VARCHAR(60)    NOT NULL,
    total               INTEGER        NOT NULL,
    jan                 SMALLINT       NOT NULL,
    fev                 SMALLINT       NOT NULL,
    mar                 SMALLINT       NOT NULL,
    abr                 SMALLINT       NOT NULL,
    mai                 SMALLINT       NOT NULL,
    jun                 SMALLINT       NOT NULL,
    jul                 SMALLINT       NOT NULL,
    ago                 SMALLINT       NOT NULL,
    set                 SMALLINT       NOT NULL,
    out                 SMALLINT       NOT NULL,
    nov                 SMALLINT       NOT NULL,
    dez                 SMALLINT       NOT NULL,
    -- Coordenadas da sede do município.
    longitude           NUMERIC(15, 7) NOT NULL,
    latitude            NUMERIC(15, 7) NOT NULL,

    CONSTRAINT pk_ssp_dp PRIMARY KEY (cd_subdist, ano, natureza)
);

COMMENT ON COLUMN ssp_dp.cd_subdist   IS 'Código IBGE do subdistrito (11 dígitos); corresponde à Região Administrativa.';
COMMENT ON COLUMN ssp_dp.populacao    IS 'População residente do subdistrito (Censo 2022); denominador da taxa por mil habitantes.';
COMMENT ON COLUMN ssp_dp.n_delegacias IS 'Delegacias dentro do polígono do subdistrito; nulo quando não há nenhuma.';
