# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What this repo is

An analysis/ETL repo (not a package): it turns TSE electoral data (polling places
and Presidential results, 2018/2022/2026) into GeoReDUS-ready layers — points
(polling places), zona × município (observed), and setores censitários 2022
(estimated via dasymetric allocation using CNEFE 2022 as ancillary data). No tests,
no linting — prioritize pipeline reproducibility and readable notebooks.

Bilingual by nature: prose and domain nouns in Portuguese, code structure in
English. See [PLAN.md](PLAN.md) for the full context, the research on how votes are
distributed across territory, and the schedule.

## Commands

R / Quarto (locais de votação + geocodificação):

```bash
Rscript -e "renv::restore()"
quarto render 00_auxiliares.qmd
quarto render 01_locais_votacao.qmd
quarto render 02_geocodificacao_locais.qmd
```

`.Rprofile` sources `renv/activate.R`.

Python (resultados, alocação, exportação):

```bash
venv\Scripts\activate.bat        # PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/03_pesos_alocacao.py --ano 2022
python scripts/processa_turno.py --ano 2022 --turno 1 --fonte votacao_secao
```

## Architecture

Four stages, two languages, parquet/gpkg as the handoff format. See `PLAN.md` for
the full data model (geometry files vs. result files joined by a shared id).

- **00–02 (R)**: crosswalk TSE↔IBGE, malhas, `pop18` por setor; download e chaves
  dos locais/seções; validação e geocodificação das coordenadas.
- **03 (Python, `scripts/03_pesos_alocacao.py`, roda 1× por ano)**: aloca cada
  domicílio do CNEFE ao local de votação mais próximo do mesmo município,
  constrói a matriz de pesos `local → setor` (ponderada pela população adulta) e
  gera as geometrias derivadas (`geom_areas_locais`, `geom_zona_mun`).
- **04 (Python, `scripts/processa_turno.py`)**: lê os resultados (boletim de urna
  ou `votacao_secao`), agrega por local, por zona × município e, multiplicando
  pela matriz de pesos, por setor.
- **05 (Python)**: exporta os arquivos no modelo GeoReDUS, os artefatos de carga
  (`table.sh`/`schema.sql`/`down.sql`), a linha de cadastro e os mapas de prévia.

## Conventions and gotchas

- **CNEFE 2022** vem de `../cnefe-etl/output/points/cnefe_points_all_mun.parquet`
  (repo irmão). A geometria ali é **WKB cru sem metadados GeoParquet** — decodificar
  manualmente (`shapely.from_wkb`), nunca com `gpd.read_parquet()`. CRS é EPSG:4674
  (SIRGAS 2000), não 4326.
- **CRS:** tudo em EPSG:4674 nas saídas para a GeoReDUS. Cálculos de distância usam
  EPSG:5880 (SIRGAS 2000 / Brazil Polyconic), métrico nacional.
- **geobr fixado em 1.9.1** — não atualizar (ver `cnefe-etl/CLAUDE.md` para o motivo:
  2.x quebra o catálogo de malhas).
- **`geobr::read_municipality(..., showProgress = FALSE)` devolve `NULL` em silêncio**
  ao renderizar via `quarto render` (fora de um console interativo) — confirmado em
  25/09/2026. Deixar `showProgress` no padrão (`TRUE`, omitir o argumento), mesmo que
  isso jogue a barra de progresso no output do Quarto.
- **Ids são texto**, no formato IBGE/TSE (nunca inteiro, para preservar zeros à
  esquerda): `id_local`, `id_secao`, `id_zona_mun`, `cd_mun`, `cd_setor`.
- **Camadas observadas vs. estimadas:** `geom_locais_votacao` e
  `res_..._zona_mun` são valores publicados pelo TSE, exatos. `geom_areas_locais`,
  `geom_zona_mun` (quando `geom_estimada = TRUE`) e `res_..._setor` são estimativas
  do método de alocação — sempre expor a camada observada ao lado.
- **A matriz de pesos é cara de calcular, barata de aplicar.** `scripts/03_pesos_alocacao.py`
  roda uma vez por ano, antes da eleição. `processa_turno.py`, na noite da eleição,
  só multiplica — não recalcula a matriz.
- **duckdb (Python) em `fontes.py`:** o método `con.read_csv(..., encoding="latin-1")`
  ignora o encoding e sempre exige UTF-8 nesta versão (1.5.x). Use a função SQL
  `read_csv(...)` (via `con.sql(...)`), que respeita o parâmetro — ver `_sql_read_csv()`.
- **Testes manuais:** não há suíte formal (`scripts/_teste_manual_*.{py,R}`), mas
  esses scripts validam a lógica central com dados sintéticos (conservação de
  totais, alocação por vizinho mais próximo, validação de coordenada) e valem
  a pena rodar de novo depois de qualquer mudança em `alocacao.py`/`resultados.py`.
- **Chamadas de rede em R (httr2, `url()`) travam com "segmentation fault"
  neste ambiente de desenvolvimento** (confirmado até para `base::url()`, sem
  geobr envolvido) — parece ser uma incompatibilidade do sandbox com o
  libcurl do R, não o bug documentado do geobr 2.x. Os `.qmd` desta etapa não
  foram executados de ponta a ponta aqui; só a lógica de dados (crosswalk,
  chaves, validação de coordenada) foi testada com fixtures locais. Rodar o
  download de verdade num ambiente sem essa restrição (ex.: a rede do CEM).
