# PLAN — Dados eleitorais (TSE) para a GeoReDUS

> Revisão 2 (25/09/2026): método de distribuição, modelo geometria × resultados e camada zona × município.

## Contexto

A GeoReDUS (CEM + FNP, ORI:ORO, GIZ) ainda não tem dados eleitorais. O objetivo é construir, para o Brasil todo:

1. **Locais de votação georreferenciados**;
2. **Resultados eleitorais na menor agregação** (seção → local).

O ano de 2022 serve de ensaio do fluxo, para processar e publicar 2026 logo após cada turno. As bases também alimentam o trabalho final da disciplina.

**Decisões:**
- **Anos:** 2018, 2022 e 2026.
- **Cargo:** Presidente.
- **Stack híbrida:**
  - R/Quarto/renv para locais de votação e geocodificação;
  - Python para resultados, distribuição espacial e exportação.
- **Modelo de dados:**
  - **arquivos só de geometria** (pontos ou polígonos);
  - **arquivos só de resultados** (sem geometria), ligados à geometria por um **id compartilhado**;
  - a malha de **setores censitários 2022** é a unidade de área para o join dos votos.
- **Publicação:** feita diretamente por Gil (upload no S3 + carga no banco da GeoReDUS). Não depende da equipe da ORI:ORO.

**Calendário:** hoje 25/09/2026 · 1º turno **04/10/2026** · 2º turno **25/10/2026**.

## Pesquisa: como distribuir os resultados no território

### Como o eleitor é vinculado ao local de votação (regras do TSE)

- **Domicílio eleitoral = município.** O vínculo pode ser residencial, afetivo, familiar, profissional ou comunitário (Res. TSE 23.659/2021). O endereço no cadastro "não necessariamente corresponderá ao domicílio eleitoral".
- **Não existe alocação automática no local mais próximo.** O eleitor **escolhe** o local, **entre os disponíveis na sua zona eleitoral**, no alistamento, na transferência ou na revisão (Res. TSE 21.407/2003; Res. 23.659/2021). Na prática, o atendimento sugere um local perto do endereço informado.
- **Inércia:** quem se muda dentro da cidade não é obrigado a atualizar o local de votação. A troca é opcional (online, pelo "Eleitor em Dia"). Em 2026 o prazo foi 06/05.
- **Exceções que quebram a proximidade:**
  - seções de **voto em trânsito** (capitais e municípios com mais de 100 mil eleitores);
  - seções em presídios e unidades de internação;
  - indígenas, quilombolas, assentados e pessoas em situação de rua, que podem votar em "local diverso".
- **Evidência empírica no Brasil:** Silva et al. (2022, *Opinião Pública*, Rio de Janeiro) mostram territórios **sem local de votação dentro** (Complexo do Alemão, Jacarezinho, Maré): todos os moradores votam em seções fora da própria região. Nicolau & Terron (2012) alertam que usar só o endereço da seção, sem um polígono de cobertura, desconsidera quem vota em bairros vizinhos.

**Zonas eleitorais: não como malha nacional, mas sim como a unidade "zona × município"** (ver abaixo). Por que a zona pura não serve:

- **Tamanho:** em 2026 são **2.641 zonas** para **5.571 municípios** e 517.206 seções. Na maior parte do país, uma zona reúne **vários municípios** (o município-sede e os demais municípios da comarca). Só nas cidades grandes o município se divide em várias zonas: a capital paulista tem dezenas, cada uma com mais de 100 mil eleitores, escala parecida com a dos distritos. Zona e município se cruzam em relação muitos-para-muitos, sem hierarquia.
- **Sem geometria oficial:** o TSE não publica polígonos de zona. Os limites são definidos por listas de bairros e logradouros em resoluções dos TREs. Existem apenas malhas locais e antigas (DF, Goiânia, SP pré-rezoneamento) e um CSV de endereços de cartórios (`mapaslivres/zonas-eleitorais`), que não são limites.
- **Como restrição da alocação, o ganho é pequeno:** só vale nas cidades com várias zonas. Exigiria reconstruir os polígonos a partir das listas de bairros dos TREs, e reconstruí-los a partir dos próprios locais seria circular.
- **Outras unidades do TSE:**
  - **circunscrições:** país (Presidente), UF (governador, senador, deputados) e município (prefeito, vereador), que usam as geometrias do IBGE;
  - **seção:** uma lista de eleitores (uma urna) dentro de um local, não um território;
  - **bairro:** texto livre no cadastro de locais, sem polígono.

  A única unidade georreferenciada publicada pelo TSE é o **local de votação** (ponto).
- **Zona em município inteiro:** fora das cidades divididas, a zona é um conjunto de municípios inteiros. Dá para construir o polígono exato por dissolve dos municípios do IBGE, usando a relação município × zona do arquivo de locais. Só os limites internos das cidades com várias zonas é que dependem de tabelas bairro × zona dos TREs.
- **Decisão: criar a unidade "zona × município"** (`id_zona_mun = {cd_mun}{nr_zona:04}`), cruzando o município e a zona de cada local:
  - **Zona com vários municípios** → quebrada em municípios. A geometria é o **polígono do município do IBGE**, exata.
  - **Município com várias zonas** (capitais e cidades grandes: Fortaleza tem 17, Salvador 19, Manaus 13, SP dezenas) → cada zona é mantida. A geometria é **estimada**: dissolve dos setores 2022 atribuídos à zona.
  - Cada setor vai à zona que recebe a maior parte dos seus adultos na matriz `W` (etapa 03). Setores sem domicílio vão à zona do local mais próximo do centróide.
  - Flag `geom_estimada = TRUE` nesses casos. Onde houver tabela bairro × zona do TRE (Salvador, SP), usá-la só para validação.
  - **Os valores são observados, não estimados:** o TSE já publica os resultados nessa mesma agregação (`votacao_candidato_munzona`, `detalhe_votacao_munzona`). Só a fronteira interna das cidades divididas é aproximada.
  - É a camada mais "honesta" das quatro: mais fina que o município nas cidades grandes, idêntica ao município no resto e sem estimar votos.
  - **Série histórica:** o número da zona muda com os rezoneamentos. Comparar anos pela camada de setores; na zona × município, só onde `geom_estimada = FALSE`.
  - Quantos municípios têm mais de uma zona: medir na etapa 01 (contar `NR_ZONA` distintos por `CD_MUNICIPIO`). Checar também os municípios com uma zona "residual", isto é, poucos locais de outra zona, o que indica erro de cadastro ou divisa.

**Conclusão:** "vota no local mais próximo de casa" é uma **aproximação razoável em áreas urbanas densas**. Ela falha onde faltam locais (favelas, periferias), em áreas rurais extensas e nas seções especiais. O método precisa (a) se restringir ao município, (b) pesar pela população onde ela está de fato e (c) **medir onde a aproximação falha**.

### O que a literatura propõe

| Abordagem | Exemplos | Leitura para nós |
|---|---|---|
| **Voronoi dos locais recortado pelo município** | Estadão, "Como votou sua vizinhança" (2018; usa setores para caracterizar os polígonos); **Godoy, Prates & Yan (2025)**, *Spatial Statistics* 71:100949 (BH, eleição de 2018; Voronoi por local + dados do setor levados às células, supondo que "voters vote at the nearest voting stations") | Padrão brasileiro. Simples e defensável; ignora capacidade e zona |
| **Dasimetria** (redistribuir votos com dado auxiliar de domicílios/população) | Barcelona (*Applied Geography*, 2016), Budapeste (alocação dasimétrica por pontos para comparecimento), EUA (*Scientific Data*, 2025: votos de precinct → blocos do censo) | Supera a ponderação por área, principalmente em cidade densa. Nosso dado auxiliar natural é o **CNEFE 2022** |
| **Contornos a partir dos endereços dos eleitores** | França: INSEE/Etalab (Voronoi sobre os endereços do cadastro eleitoral, fundidos por *bureau*; `datagouv/bureau-vote`, MapvotR). Validado com os limites oficiais de Estrasburgo: próximo nas áreas densas, diverge nas áreas esparsas | Padrão-ouro, mas **exige os endereços dos eleitores**, que não são públicos no Brasil. Serve de inspiração para usar pontos de endereço (CNEFE) como unidade |
| **Crítica à coropleta por área administrativa** | Junckes et al. (2021, *Rev. Sociol. Polít.*) | Áreas densas "somem" no mapa. Reforça usar setor (pequena área) e simbologia consciente da densidade |

### Método proposto: "Voronoi dasimétrico" sobre os domicílios do CNEFE

Combina os três achados. A unidade de alocação é o **endereço residencial do CNEFE 2022**, que já está processado em `cnefe-etl/output/points/cnefe_points_all_mun.parquet` (5.571 municípios; colunas `COD_SETOR` e `COD_ESPECIE`).

1. **Pontos de domicílio:** CNEFE com espécie 1 (particular) e 2 (coletivo), usando os campos `COD_MUNICIPIO` e `COD_SETOR`.
2. **Locais elegíveis:** locais do ano, **excluindo** as seções de voto em trânsito e as especiais (presídios, internação). Esses locais ficam na camada de pontos, marcados, mas fora da distribuição.
3. **Atribuição:** cada domicílio vai ao **local mais próximo do mesmo município**, com KD-tree por município em **EPSG:5880** (SIRGAS 2000 / Brazil Polyconic, métrico nacional). É o Voronoi dos locais, avaliado nos domicílios.
4. **Pesos em adultos:** `adultos(L,s) = n_dom(L∩s) × pop18(s) / n_dom(s)`.
   - A `pop18(s)` sai do `cn22-pop`: `V0001 − V01031 − V01032 − V01033 − 0,6·V01034`, com a faixa de 15 a 19 anos proporcionalizada.
5. **Distribuição:** `votos(s) = Σ_L votos(L) × adultos(L,s) / Σ_s' adultos(L,s')`.
   - Distribuem-se **contagens** (votos por candidato, aptos, comparecimento, brancos, nulos) e os percentuais são recalculados no setor.
   - Os totais por local e por município são conservados.
6. **Casos-limite:**
   - local cuja célula não tem nenhum domicílio → seus votos vão ao setor que contém o ponto;
   - município sem CNEFE → fallback por população do setor (centróide → local mais próximo).
7. **Diagnóstico de falha:** `razao_eleitores = qt_eleitores(L) / Σ_s adultos(L,s)`, normalizada pela razão do município.
   - Valores muito fora de ~1 indicam que o local "atrai" eleitores de longe (tipo Maré) ou que a célula está superdimensionada.
   - Vira `flag_alocacao` no local e `qualidade_alocacao` no setor.

**A matriz de pesos `W_ano(id_local → cd_setor)` é pré-calculada** assim que os locais do ano estão fechados (os de 2026 já estão). Na noite da eleição, o resultado por setor é só `W × votos`, em segundos.

**v2 (pós-1º turno, bom tema para a disciplina):** alocação **com capacidade**, em que os adultos de cada célula ficam proporcionais a `qt_eleitores` do local.
- Técnica: problema de transporte por município, restrito aos *k* locais mais próximos, ou Voronoi aditivamente ponderado com ajuste iterativo.
- Uso: comparar com a v1 (sensibilidade do mapa por setor).
- Limitação: não há verdade de campo pública no Brasil, porque os endereços dos eleitores são sigilosos.

## Modelo de dados (id compartilhado)

**Geometrias** (EPSG:4674, só id + metadados de origem, sem resultados) em `dados/03_georedus/geometrias/`:

| Arquivo | Tipo | Id | Colunas extras |
|---|---|---|---|
| `geom_locais_votacao_{ano}.gpkg` | Point | `id_local` | `cd_mun`, `fonte_coord`, `precisao`, `flag_especial` |
| `geom_areas_locais_{ano}.gpkg` | Polygon | `id_local` | `cd_mun`; Voronoi dos locais elegíveis recortado pelo município ("área de abrangência estimada"). Locais com coordenada idêntica são fundidos antes, com `id_local` = o menor id do grupo |
| `geom_zona_mun_{ano}.gpkg` | Polygon | `id_zona_mun` | `cd_mun`, `nr_zona`, `n_zonas_mun`, `geom_estimada`. Município do IBGE quando o município tem uma só zona; dissolve de setores quando tem várias |
| Setores 2022 | Polygon | `cd_setor` | **Não é gerado:** já existe no banco (`ibge_malha_br_setor_censitario_2022`). Exportar uma cópia só para uso fora da plataforma (disciplina) |

**Resultados** (parquet + csv, sem geometria) em `dados/03_georedus/resultados/{ano}/`:

| Arquivo | Join com |
|---|---|
| `res_presidente_{ano}_t{t}_local.parquet` | `id_local` → pontos e áreas (valores observados) |
| `res_presidente_{ano}_t{t}_zona_mun.parquet` | `id_zona_mun` → zona × município (valores observados; soma dos locais, incluindo os especiais) |
| `res_presidente_{ano}_t{t}_setor.parquet` | `cd_setor` → malha de setores (valores estimados) |
| `pesos_local_setor_{ano}.parquet` | `id_local`, `cd_setor`, `n_dom`, `adultos`, `peso`; publicado por transparência/reuso |
| `secao_local_{ano}.parquet` | `id_secao` → `id_local` |

**Colunas dos resultados:**
- `qt_aptos`, `qt_comparecimento`, `qt_brancos`, `qt_nulos`, `qt_votos_{nr}`;
- `pc_comparecimento`, `pc_abstencao`, `pc_brancos`, `pc_nulos`, `pc_validos_{nr}` (0–1);
- `nr_vencedor`, `margem`;
- no setor, também `qualidade_alocacao`.

**Ids:**
- `id_local = {cd_mun_tse}{nr_zona:04}{nr_local:04}` (texto);
- `id_secao = {cd_mun_tse}{nr_zona:04}{nr_secao:04}`;
- `cd_mun` e `cd_setor` no formato IBGE (texto).

## Fontes de dados

CDN do TSE: `https://cdn.tse.jus.br/estatistica/sead/odsele/`. Os arquivos são CSV `;` em latin-1. O CDN devolveu 403 a partir deste ambiente, então **é preciso testar na rede do CEM**.

| Dado | Arquivo | Uso |
|---|---|---|
| Locais/seções | `eleitorado_locais_votacao/eleitorado_local_votacao_{ANO}.zip` | Etapas 01–02. O de 2026 já está publicado. Conferir se o de 2018 tem coordenadas |
| Resultados consolidados | `votacao_secao/votacao_secao_{ANO}_BR.zip` + `detalhe_votacao_secao_{ANO}.zip` | Caminho "oficial" |
| Boletim de urna | `bweb_{T}t_{UF}_*.zip` ("Resultados {ANO} – Boletim de Urna") | **Caminho rápido** para 2026 (em 2022 saiu em D+3) |
| Candidatos / conferência | `consulta_cand_{ANO}.zip` / `votacao_candidato_munzona_{ANO}.zip` | Nomes; totais para validar |
| Painel de locais geocodificados | `fdhidalgo/geocode_br_polling_stations` v0.16 (2006–2024) | Coordenadas de referência para 2018 e 2022 |
| Crosswalk TSE↔IBGE | `betafcc/Municipios-Brasileiros-TSE` ou `yuripassuelo/codigos_mun_tse_ibge` | Não existe nos repos; validar contra o IBGE |
| CNEFE 2022 | `cnefe-etl/output/points/cnefe_points_all_mun.parquet` | Pesos da distribuição. **A geometria é WKB cru sem metadados GeoParquet**, então é preciso decodificar à mão (ver `cnefe-etl/CLAUDE.md`). O CRS é 4674 |
| População por idade e setor | `cn22-pop/dados/01_indicadores/cn22_pop01_abs.csv`, `cn22_pop03_abs.csv` | `pop18(s)` |
| Malhas | Setores 2022 (IBGE, lidos em `/vsizip/`), municípios (geobr **1.9.1** fixado) | Recortes e checagens |
| Escolas geocodificadas | saída do `inep-escolas-geo` | Fallback de coordenada dos locais |

## Layout do repositório

```
dados_eleicoes/
├── README.md  PLAN.md  .gitignore  _quarto.yml  .Rprofile  renv.lock
├── 00_auxiliares.qmd              # R: crosswalk, malhas, pop18 por setor
├── 01_locais_votacao.qmd          # R: locais e seções por ano, chaves
├── 02_geocodificacao_locais.qmd   # R: validação + fallbacks
├── src/eleicoes/                  # Python
│   ├── config.py   download.py   fontes.py   resultados.py
│   ├── alocacao.py   # CNEFE → local mais próximo, pesos, Voronoi, diagnóstico
│   └── exporta.py    # arquivos de geometria/resultados, SQL de carga, cadastro
├── scripts/
│   ├── 03_pesos_alocacao.py       # 1× por ano (antes da eleição)
│   └── processa_turno.py          # noite da eleição: resultados → local → setor → export
├── notebooks/                     # exploração, mapas, sensibilidade v1×v2
├── dados/01_bruto/  dados/02_processado/  dados/03_georedus/{geometrias,resultados,carga}/
└── docs/                          # site Quarto + mapas Folium
```

`.gitignore`: `dados/`, `venv/`, `renv/library/`, `/.quarto/`, `*_files/`, `*_cache/`, `__pycache__/`, `.claude`.

## Etapas

**00 — Auxiliares (R).**
- Crosswalk `cd_mun_tse` ↔ `cd_mun`: validar os 5.570 municípios, cruzar nomes com `normaliza_texto()` de `compatibiliza_setores_censo/src/utils.py` e checar à mão os municípios novos. O exterior (`ZZ`) fica só nos totais.
- `pop18` por setor.
- Malhas com geobr 1.9.1.

**01 — Locais (R/Quarto).**
- Download com `httr2`: retry `2^n` e pular se o arquivo já existir (padrão de `inep-escolas-geo/index.qmd`).
- Leitura com `readr` latin-1, tudo como texto, + `janitor::clean_names()`.
- Chaves; **seções agregadas** mapeadas para o local da seção principal.
- `flag_especial` para trânsito, presídio e internação.
- Saídas: `secao_local_{ano}`, `locais_{ano}`.

**02 — Geocodificação (R/Quarto).**
- Validação da coordenada do TSE:
  - não pode ser nula, (0,0) ou (-1,-1);
  - tem de cair dentro do município (buffer de 500 m), no padrão de `datasus-estabelecimentos-saude/index.qmd` ~L1012–1050;
  - não pode ser repetida em ≥ 3 locais do município.
- Fallback, nesta ordem:
  1. outro ano;
  2. Hidalgo;
  3. escola (Jaro-Winkler ≥ 0,9);
  4. `geocodebr` (`definir_campos` + `geocode(resolver_empates=TRUE, cache=TRUE)`, como em `locais-nova-rfb-geocoding/index.qmd` L1520–1690), aceitando precisão até `logradouro`;
  5. centróide do município com flag.
- Mantém a coordenada do TSE se estiver a menos de 800 m da referência (regra do `geobr::read_polling_places`).
- **MVP antes de 04/10:** validação + outro ano + Hidalgo. Escola e `geocodebr` entram depois.

**03 — Pesos de alocação (Python, 1× por ano).**
- `alocacao.py` implementa o método descrito acima (passos 1–7): CNEFE lido com DuckDB (`ST_GeomFromWKB` → x/y) ou `shapely.from_wkb`, e `scipy.spatial.cKDTree` por município.
- Saídas:
  - `pesos_local_setor_{ano}`;
  - `geom_areas_locais_{ano}.gpkg`: Voronoi de `shapely.voronoi_polygons` por município, recortado pelo limite municipal;
  - diagnóstico `razao_eleitores`;
  - `geom_zona_mun_{ano}.gpkg`:
    - município com uma zona → polígono do IBGE;
    - município com várias zonas → setor atribuído à zona de maior peso em `W` → dissolve;
    - `geom_estimada` e `n_zonas_mun` preenchidos.
- Rodar para 2022 e 2026 antes de 04/10; 2018 depois.

**04 — Resultados (Python, `processa_turno.py`).**
- Adaptadores `ler_bweb()` e `ler_votacao_secao()` que devolvem o mesmo esquema longo, filtrando Presidente na leitura. Leitura com DuckDB `read_csv(encoding='latin-1', delim=';', all_varchar=true)`.
- Join com `secao_local` → `res_..._local` → soma por `id_zona_mun` → `res_..._zona_mun`; `W ×` → `res_..._setor`.

**05 — Exportação e carga (Python; Gil publica).**
- Geometrias e resultados conforme o modelo de dados acima.
- `dados/03_georedus/carga/`: artefatos no padrão de `redus-geo-api/packages/db-manager`.
  - Um `table.sh` (ogr2pg, `T_SRS=4674`), um `schema.sql` (ids `text`, indicadores `double precision`, índices btree/GIST) e um `down.sql` por tabela.
  - Uma view em `public_read_api/tse/<tabela>/`, fazendo o join geometria × resultados pelo id, no modelo de `public_read_api/cem/malha_escolas_publicas_2023/schema.sql`.
- Upload dos arquivos para `data-src` no S3, seguido da carga.
- `cadastro_georedus_{ano}.csv`: linhas para a nova aba "TSE – Eleições" da planilha de cadastro (`collection_id` dos pontos/áreas/setores, `variable_id` no padrão `tse26_pre01_t1_pc_validos_13`, `preset`, `colorScheme`, `measure_unit`, `metodology`).
- Notas metodológicas no formato de `georedus_metodologia` (metadados CSV + `modelos/*.txt` + `script_*.R`), incluindo a ressalva da distribuição e a leitura do `qualidade_alocacao`.
- Mapas Folium em `docs/` como prévia imediata.

## Noite da eleição

```bash
python scripts/processa_turno.py --ano 2026 --turno 1 --fonte bweb --baixar
```

1. Baixar os `bweb_1t_*` das 27 UFs.
2. Rodar 04 e 05. Os pesos de 2026 já estarão prontos. Meta: **< 1 h** do arquivo disponível à carga no banco.
3. Conferir os totais contra `votacao_candidato_munzona` e o site de resultados.
4. Subir no S3, carregar no banco e atualizar o cadastro.
5. Quando sair o `votacao_secao` consolidado, reprocessar com `--fonte votacao_secao` e comparar.

Plano B, se o D+3 for lento: coletor dos BUs por seção em `resultados.tse.jus.br/oficial/ele2026/arquivo-urna/...` (~500 mil seções).

## Cronograma

| Janela | Entregas |
|---|---|
| 25/09 – 03/10 | Setup; 00; 01–02 (MVP) para 2022 e 2026; 03 (pesos) para 2022 e 2026; 04–05 ponta a ponta com **2022** pelos dois adaptadores, cronometrado; ensaio completo de carga (S3 + banco + cadastro) com 2022; **publicar os locais de 2026 antes de 04/10** |
| 04 – 10/10 | 1º turno de 2026 publicado (locais + áreas + setores) |
| 11 – 24/10 | 2018; refinamento da geocodificação; notas metodológicas; v2 com capacidade (sensibilidade) |
| 25/10 em diante | 2º turno; série 2018–2022–2026 por setor; `CLAUDE.md` e README |

## Verificação

- **Totais:** a soma por município e por UF, em local **e** em setor, deve ser **idêntica** à de `votacao_candidato_munzona` em 2018 e 2022, contando também os locais especiais. Sanidade nacional para 2022: Lula tem 57.259.504 votos no 1º turno e 60.345.999 no 2º.
- **Zona × município:** `res_..._zona_mun` deve bater **linha a linha** com `votacao_candidato_munzona` (mesma agregação do TSE). Em cada município, a união das `geom_zona_mun` deve cobrir o município sem sobreposição. Nas capitais com tabela bairro × zona (Salvador, SP), medir a concordância com as zonas estimadas.
- **Conservação:** `Σ_s peso(L,s) = 1` para todo local elegível; diferença zero entre o total do setor e o total do local por município.
- **Chaves:** 100% das seções com `id_local`, excluindo o exterior. Todo `id_local` dos resultados existe na geometria e todo `cd_setor` existe na malha de 2022 (anti-join vazio).
- **Geocodificação:** % por `fonte_coord` × UF; nenhum ponto fora do município sem flag.
- **Alocação:** distribuição de `razao_eleitores` por UF. Inspecionar os casos extremos em SP (periferia) e no Rio (Maré, Alemão), onde o método deve **acusar** a falha, e em um município rural. Comparar os mapas por setor da v1 e da v2 no pós-1º turno.
- **Carga:** a view pública no banco devolve geometria + indicadores para um município de teste; os tiles aparecem na GeoReDUS.
- **Ensaio 2026:** pesos de 2026 + resultados fictícios (zeros) passando pelas etapas 04–05 antes de 04/10.

## Riscos e pendências

- Acesso ao CDN do TSE (403 aqui, **confirmado ok em 25/09/2026 fora do sandbox**).
- Qualquer chamada de rede em R (httr2, `url()`) trava com "segmentation fault" no
  ambiente onde este repositório foi montado — testado até com `base::url()`, sem
  geobr envolvido, então não é o bug documentado do geobr 2.x. Fora desse sandbox
  (confirmado pelo Gil em 25/09/2026) a rede em R funciona normalmente.
- **Confirmado (25/09/2026):** `geobr::read_municipality(..., showProgress = FALSE)`
  devolve `NULL` em silêncio ao renderizar via `quarto render`. Corrigido em
  `00_auxiliares.qmd` (deixar `showProgress` no padrão) — ver CLAUDE.md.
- **Resolvido (28/09/2026):** as etapas 00 e 01 rodaram de ponta a ponta com
  dados reais de 2022. Três bugs corrigidos no caminho — detalhes em
  CLAUDE.md: (1) crosswalk sem zero à esquerda em `cd_mun_tse` (join falhava
  para ~15% dos locais); (2) `01_locais_votacao.qmd` usava nomes de coluna
  adivinhados (`ds_situacao_local_votacao`) em vez dos reais (`ds_tipo_local`,
  com as categorias `Convencional`/`Temporário`/`Preso provisório`/`Voto em
  trânsito`); (3) o arquivo tem uma linha por seção **por turno**, e seções
  "Agregada" precisam ser resolvidas pela seção principal (não pelo próprio
  `NR_LOCAL_VOTACAO`, que diverge em ~2% dos casos). Resultado real: 92.393
  locais, 993.712 seções×turno, 262 flags especiais, 0 coordenadas nulas.
  `pop18` também usava nomes de variável do IBGE em vez dos indicadores já
  nomeados do `cn22-pop` — corrigido do mesmo jeito.
- **Resolvido (28/09/2026):** a etapa 02 também rodou de ponta a ponta com
  2022. Quatro correções — detalhes em CLAUDE.md: validação de coordenada
  vetorizada (o loop original não terminava em 5 min para ~90 mil locais);
  uma função de distância que não aguentava `NA` (`dplyr::if_else()` avalia
  os dois ramos inteiros, não só o escolhido); um `select()` que perdia a
  coluna `fonte_coord`; e o último degrau do fallback (centróide do
  município) que faltava — sem ele, locais não resolvidos publicavam a
  coordenada **inválida** do TSE (`-1,-1`) como se fosse real, só visível
  inspecionando os limites geográficos da saída. Também corrigido o esquema
  assumido do painel Hidalgo contra o real. Resultado: 92.185 pontos (92.393
  locais menos o exterior, fora do mapa por design); 91,5% TSE original,
  7,8% Hidalgo, 0,56% centróide do município, 0,12% TSE confirmado a <800m.
- Prazo de publicação do boletim de urna em 2026 (D+3 em 2022).
- 2018: confirmar se há coordenadas no arquivo do TSE; se não houver, usar o Hidalgo.
- O CNEFE 2022 envelhece para 2026 e é anacrônico para 2018 (bairros novos ou esvaziados). Aceitável, mas precisa ser documentado.
- A camada por setor é uma **estimativa**. A nota metodológica e a legenda devem deixar isso explícito, e a camada de locais (valores observados) deve ficar sempre disponível ao lado.
- Sigilo: nenhum dado individual é usado. CNEFE e TSE são públicos e agregados.

## Referências

- Res. TSE 23.659/2021 (cadastro eleitoral); Res. TSE 21.407/2003 (escolha do local dentro da zona).
- Silva et al. (2022). *Do Leme a Santa Cruz: a territorialização eleitoral de Jair Bolsonaro no município do Rio de Janeiro*. Opinião Pública.
- Nicolau, J.; Terron, S. (2012). Cartografia eleitoral do Rio de Janeiro.
- Godoy, L. C.; Prates, M. O.; Yan, J. (2025). *Voronoi linkage between mismatching voting stations and census tracts in analyzing the 2018 Brazilian presidential election data*. Spatial Statistics 71:100949.
- Estadão. *Como votou sua vizinhança* (github.com/estadao/como-votou-sua-vizinhanca).
- *Dasymetric distribution of votes in a dense city* (Barcelona). Applied Geography (2016).
- *A dataset of US precinct votes allocated to Census geographies with precision*. Scientific Data (2025).
- INSEE (2023). *"A voté": à chaque bureau de vote ses électeurs*; Etalab `datagouv/bureau-vote`.
- Junckes et al. (2021). *Representação espacial de dados eleitorais no Brasil: críticas e possibilidades*. Rev. Sociol. Polít. 29(79).
- Zolnerkevic, A.; Guarnieri, F. (2023). *Eleições municipais de 2016 e 2020 em São Paulo*. Opinião Pública.
- Hidalgo, F. D. et al. `geocode_br_polling_stations`; `geobr::read_polling_places`.
- TSE (2026). *Faltam 30 dias: Brasil tem mais de 158 milhões de eleitores aptos* (2.641 zonas, 517.206 seções, 5.571 municípios); `mapaslivres/zonas-eleitorais`.
