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
- **Chamadas de rede em R (httr2, `url()`) travam com "segmentation fault" no
  sandbox onde o repositório foi montado inicialmente** (confirmado até para
  `base::url()`, sem geobr envolvido) — não é o bug documentado do geobr 2.x.
  Fora desse sandbox (a máquina real do Gil) a rede funciona normalmente; os
  `.qmd` de 00–01 já rodaram de ponta a ponta lá com dados reais de 2022 (ver
  abaixo).
- **Duas instalações de R nesta máquina** (Gil): R 4.6.0 no `PATH` (usado por
  `Rscript`/`R.exe` direto), e R 4.5.2 em `C:\Program Files\R\R-4.5.2`, que é a
  que o RStudio/VSCode-R usa de fato e onde `renv::restore()` populou o
  `renv/library/windows/R-4.5/...`. Rodar `Rscript`/`quarto render` direto do
  terminal sem apontar para a 4.5.2 falha com "não há pacote chamado X", mesmo
  com o `renv.lock` restaurado. Para renderizar por fora do RStudio:
  ```bash
  R_HOME="/c/Program Files/R/R-4.5.2" PATH="/c/Program Files/R/R-4.5.2/bin:$PATH" \
    quarto render 00_auxiliares.qmd
  ```
- **Esquema real de `eleitorado_local_votacao_{ano}.csv`** (confirmado contra o
  arquivo de 2022 em 28/09/2026 — não adivinhar de novo, olhar o cabeçalho real
  antes de mudar `01_locais_votacao.qmd`):
  - **uma linha por seção por turno** (`NR_TURNO`), não uma linha por seção —
    `(CD_MUNICIPIO, NR_ZONA, NR_SECAO)` sozinho não é chave única, precisa de
    `NR_TURNO` junto. Em 2022, o local de votação difere entre turno 1 e 2 em
    1.387 das 496.856 seções (0,28%) — `secao_local_{ano}.parquet` guarda
    `nr_turno` por isso, e `resultados.secao_para_id_local()` (Python) junta
    por `(id_secao, turno)`, não só por `id_secao`.
  - **`DS_TIPO_SECAO_AGREGADA`** tem `Principal`, `Agregada` e `Distribuída de
    ofício`. Só `Agregada` tem `NR_SECAO_PRINCIPAL` de verdade (as outras vêm
    com `-1`, sentinela). Em ~2% das linhas `Agregada` (952 de 48.326 em 2022),
    o `NR_LOCAL_VOTACAO` da própria linha **diverge** do local da seção
    principal — resolver sempre pela seção principal (mesmo `nr_turno`), nunca
    pelo campo da própria linha. Ver `01_locais_votacao.qmd` > "Seções
    agregadas".
  - **`DS_TIPO_LOCAL`** (não `DS_SITU_LOCAL_VOTACAO`, que é status
    ativo/bloqueado) tem `Convencional`, `Temporário`, `Preso provisório` e
    `Voto em trânsito`. `flag_especial` (fora da distribuição espacial) é só
    `Preso provisório` e `Voto em trânsito`; `Temporário` fica dentro.
  - `CD_MUNICIPIO` vem com **5 dígitos e zero à esquerda** (`"01120"`). O CSV
    de origem do crosswalk (`yuripassuelo/codigos_mun_tse_ibge`) guarda
    `cd_mun_tse` **sem** esse padding (`"1120"`) — sem `str_pad(..., 5, "0")`
    no crosswalk, o join da etapa 01 falha silenciosamente para ~15% dos
    locais (não só o exterior). Corrigido em `00_auxiliares.qmd`.
  - Resultado real (2022, ambos os turnos): 993.712 seções×turno, 92.393 locais,
    262 flags especiais, 0 coordenadas nulas, 181 municípios sem crosswalk —
    **todos** com `SG_UF = "ZZ"` (exterior; ex.: Katmandu), como esperado.
- **`02_geocodificacao_locais.qmd` rodou de ponta a ponta com 2022** em
  28/09/2026, depois de quatro correções (ver histórico do commit para o
  detalhe de cada uma — não repetir os erros):
  - `valida_coordenada_tse` fazia um `st_intersects` por local num loop (~90
    mil iterações, refiltrando os 5.570 municípios a cada uma) — trocado por
    uma chamada vetorizada em modo esparso. O loop chegou a rodar por mais
    de 5 minutos sem terminar; a versão vetorizada leva segundos.
  - `dplyr::if_else()` avalia os dois ramos para o vetor inteiro antes de
    escolher — uma função chamada dentro dele (aqui, `calcula_distancia_m`)
    **não pode presumir** que só recebe as linhas do ramo escolhido; tem que
    tratar `NA` sozinha.
  - `select(fonte_coord, precisao = fonte_coord)` mantém só a última
    referência quando a mesma coluna de origem é citada duas vezes com nomes
    diferentes — a primeira (`fonte_coord`, sem renomear) desaparece em
    silêncio. Usar `mutate(nova = coluna)` antes do `select()` para ter as
    duas.
  - **Esquema real do painel Hidalgo** (`geocode_br_polling_stations` v0.16,
    confirmado em 28/09/2026 — não é o que eu tinha assumido sem checar):
    colunas `cd_localidade_tse` (sem zero à esquerda), `nr_zona`, `nr_locvot`,
    `pred_lat`/`pred_long` (a coordenada geocodificada, independente da do
    TSE). **Não usar** `lat`/`long`: essas colunas já vêm misturadas com a
    coordenada do TSE de outro snapshot/ano, o que dobraria a fonte errada
    na hora de aplicar a regra dos 800 m.
  - Faltava o último degrau do fallback (centróide do município) — sem ele,
    locais não resolvidos publicavam a coordenada **inválida** original do
    TSE (o sentinela `-1,-1`) como se fosse um ponto de verdade. Só apareceu
    inspecionando os `bounds` da saída (`longitude` batendo exatamente em
    `-1.0`) — vale sempre conferir os limites geográficos do resultado, não
    só a contagem por `fonte_coord`.
  - Resultado real (2022): 92.185 pontos na camada espacial (92.393 locais
    menos os 208 do exterior, fora do mapa por design). 91,5% mantém a
    coordenada do TSE, 7,8% usa o Hidalgo, 0,56% cai no centróide do
    município, 0,12% é TSE reprovado mas confirmado a <800 m da referência.
- **Etapas 03 (Python) e 04 (`processa_turno.py`) rodaram para o Brasil
  inteiro com 2022** em 29/09/2026 — o resultado por candidato bate
  **exatamente** com os números oficiais reais (Lula 57.259.504, Bolsonaro
  51.072.345, comparecimento 123.682.372, aptos 156.451.515), depois de
  cinco correções:
  - **Malha de setores 2022:** não gerar cópia própria — usar direto
    `SETORES_2022_GPKG` (`../process_gtfs/Dados/BR_setores_CD2022.gpkg`,
    ~1,5 GB, já processado num repo irmão). `CD_SETOR` ali tem 15 dígitos.
  - **`COD_SETOR` do CNEFE tem um sufixo `"P"` fixo** (`"...5000002P"`, 16
    caracteres) que a malha oficial não tem — remover com `.str[:-1]`. Mesmo
    removendo, **~8–10% dos domicílios do CNEFE têm `cd_setor` que não
    existe na malha 2022** em nenhum lugar (varia de 0% a 96% por
    município, mediana ~5,5% — ver `alocacao.mede_cobertura_setor` e
    `cobertura_setor_cnefe_{ano}.parquet`), cadastro de endereços numa
    safra diferente da malha de resultados. Não tratado além de medir: esses
    domicílios não puxam peso (`pop18` implícito 0), a menos que **todos**
    os domicílios da célula sejam assim, caso em que o fallback de
    distribuição igual bota peso num setor sem geometria correspondente.
  - **`shapely.from_wkb` não aceita `bytearray`**, só `bytes`/`str` — o
    duckdb devolve colunas binárias como `bytearray` (a leitura direta via
    `pd.read_parquet` dá `bytes`, então isso só aparece ao filtrar por
    município com duckdb antes de decodificar). `decodifica_wkb` agora faz
    `.map(bytes)` antes de `shapely.from_wkb`.
  - **`constroi_zona_mun` colidia em `cd_mun`** no merge `setores.merge(setor_zona, ...)`
    (as duas tabelas têm a coluna, pandas sufixa `cd_mun_x`/`cd_mun_y` e o
    `dissolve(by=[col_mun, ...])` quebra com `KeyError: 'cd_mun'`) — não
    apareceu antes porque o `setores` sintético do teste manual não tinha
    `cd_mun` (só `cd_setor`), diferente da malha real. Corrigido selecionando
    só `[col_setor, "geometry"]` de `setores` antes do merge.
  - **`cod_setor` do `cn22-pop` precisa ser lido como texto** (mesma classe
    do bug do crosswalk): sem `col_types`, o `readr` adivinha "número" para
    um código de 15 dígitos, ele vira `double` (perde precisão E quebra o
    join com `cd_setor` em silêncio — 0% de correspondência, sem erro).
  - **`qt_eleitores` nunca tinha sido extraído em `01_locais_votacao.qmd`** —
    soma de `QT_ELEITOR_SECAO` por `id_local` (turno 1), usado pelo
    diagnóstico de alocação e, depois, como `qt_aptos` em `processa_turno.py`
    (ver abaixo). Precisa passar pela etapa 02 também (`select()` da saída).
- **`detalhe_votacao_secao` não existe** (ou não está mais publicado) — nenhum
  nome plausível (`detalhe_votacao_secao`, `comparecimento_abstencao`,
  `perfil_comparecimento_abstencao`, `detalhe_votacao_munzona`) respondeu 200
  ao tentar de verdade em 29/09/2026. Como o voto para Presidente é único e
  obrigatório para quem comparece, **`qt_comparecimento` de uma seção é a
  soma de `qt_votos` de todas as linhas daquela seção** (válidos + brancos +
  nulos) — não precisa de arquivo à parte. `qt_aptos` não vem do stream de
  resultados: vem de `locais.qt_eleitores` (soma de `QT_ELEITOR_SECAO` da
  etapa 01). `fontes.ler_votacao_secao()` não recebe mais
  `caminho_detalhe_votacao_secao`, e `resultados.agrega_por_local()`/
  `agrega_por_zona_mun()` recebem `locais` para buscar `qt_aptos`.
- **`CD_MUNICIPIO` não tem o mesmo padding em todos os arquivos do TSE.**
  Confirmado real: `eleitorado_local_votacao` sempre tem 5 dígitos
  (`"01120"`); `votacao_secao` **não** — varia de 2 a 5 dígitos (`"1120"`).
  Sem `lpad(cd_municipio, 5, '0')` em `fontes.ler_votacao_secao()`, ~10% das
  linhas não encontravam `id_local` (todo município com zero à esquerda no
  código). Mesma cautela em `ler_bweb` (especulativo, nunca testado contra
  um arquivo real — `.zfill(5)` defensivo).
- **`pandas.Series.astype(str)` numa coluna de texto nula (`pd.NA`) devolve
  `NaN` de verdade, não a string `"nan"`**, e a concatenação (`+`) propaga
  isso. `agrega_por_zona_mun()` construía `id_zona_mun` a partir de `cd_mun`
  (nulo para o exterior, ~208 locais) sem tratar isso — o `id_zona_mun`
  virava `NaN` para essas linhas, e `pivot_table()` **descarta grupos com
  índice `NaN` por padrão** (`dropna=True`). Resultado: ~294 mil votos do
  exterior sumiam do `res_..._zona_mun`, silenciosamente, sem erro — só
  visível comparando o total com `res_..._local` (que não tem esse problema,
  porque agrega por `id_local`, nunca nulo). Corrigido com
  `resultados.SENTINELA_EXTERIOR = "EXTERIOR"` (`.fillna()` antes da
  concatenação). **Sempre que uma chave de agregação for montada por
  concatenação de string, checar se alguma coluna de entrada pode ser nula
  — o pivot não avisa, só encolhe o total.**
- **`res_..._setor` tem menos votos que `res_..._local`/`res_..._zona_mun` por
  desenho, não por bug**: locais especiais (voto em trânsito, preso
  provisório) e locais sem nenhum domicílio do CNEFE alocado não entram na
  matriz de pesos, então seus votos não aparecem no setor (ver
  `resultados.agrega_por_setor`). Em 2022: 0,81% dos votos válidos+brancos+
  nulos ficam de fora do setor (122.682.541 vs. 123.682.372 no zona_mun) —
  ordem de grandeza esperada, dado que ~262 locais são especiais e ~2.062
  ficaram sem nenhum domicílio alocado (de 91.923).
