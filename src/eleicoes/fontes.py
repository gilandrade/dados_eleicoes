"""Adaptadores de resultados: cada fonte do TSE devolve o mesmo esquema longo.

Esquema comum (uma linha por seção × votável):

    ano, turno, sg_uf, cd_mun_tse, nr_zona, nr_secao, cd_cargo,
    nr_votavel, nm_votavel, qt_votos, qt_comparecimento, qt_aptos

`qt_comparecimento` (por seção): o voto para Presidente é único e obrigatório
para quem comparece, então `Σ qt_votos` de uma seção (válidos + brancos [95] +
nulos [96]) **é** o comparecimento daquela seção — confirmado sem exceção nas
944.150 linhas de `detalhe_votacao_secao_2022_BR.csv` (29/09/2026). Continua
calculado assim por padrão (não depende de nenhum arquivo extra), mas
`ler_votacao_secao(..., caminho_detalhe=...)` usa o valor do TSE quando o
arquivo está disponível — o de 2022 achou 2.496 aptos a mais (0,0016%) do que
a soma de `qt_eleitores` por local dava, provavelmente seções canceladas que
saem de `eleitorado_local_votacao` mas ficam em `detalhe_votacao_secao`.

O nome certo do arquivo (achado pelo Gil no portal humano, 29/09/2026, depois
de eu chutar `detalhe_votacao_secao_{ano}_BR.zip` e tomar 404 repetidas
vezes): `detalhe_votacao_secao/detalhe_votacao_secao_{ano}.zip` — **sem**
`_BR` no nome do zip; dentro dele é que tem um `detalhe_votacao_secao_{ano}_BR.csv`
(só Presidente, `TP_ABRANGENCIA='F'`) e um por UF. Padrão de nome do TSE não é
uniforme entre datasets — não adivinhar de novo sem checar o portal.

Ambos os leitores filtram o cargo na própria leitura (mais barato que filtrar
depois de ler o Brasil inteiro).

Eleições municipais (2020, 2024): não há `votacao_secao_{ano}_BR.zip`, só um zip
por UF com todos os cargos, e os CSV somam dezenas de GB. Cada zip vira um
parquet só com as colunas usadas (`converte_zip_em_parquet`), e os leitores
aceitam a lista de parquets no lugar do CSV. O `detalhe_votacao_secao` desses
anos tem uma linha por seção **por cargo** — o join filtra `cd_cargo`.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from .config import (
    BRUTO_DIR,
    CARGO_PRESIDENTE,
    CARGOS,
    CD_ELEICAO_DIVULGACAO,
    CODIGO_ANULADO_APURADO_SEPARADO,
    CODIGO_BRANCO,
    CODIGO_NULO,
    CODIGOS_NAO_CANDIDATO,
    UFS_27,
    URL_BWEB,
    URL_DETALHE_VOTACAO_SECAO,
    URL_DIVULGACAO,
    URL_VOTACAO_SECAO,
)
from .download import baixar

logger = logging.getLogger(__name__)

ESQUEMA_COLUNAS = [
    "ano",
    "turno",
    "sg_uf",
    "cd_mun_tse",
    "nr_zona",
    "nr_secao",
    "cd_cargo",
    "nr_votavel",
    "nm_votavel",
    "qt_votos",
    "qt_comparecimento",
    "qt_aptos",
]


def _sql_read_csv(caminho: Path) -> str:
    """SQL `read_csv(...)` para um CSV do TSE: `;`-separado, latin-1, tudo texto.

    Usa a função SQL (não o método Python `con.read_csv()`) porque, nesta
    versão do duckdb, o método Python ignora `encoding='latin-1'` e sempre
    exige UTF-8 — só a função SQL respeita o parâmetro.
    """
    caminho_sql = str(caminho).replace("'", "''")
    return (
        f"read_csv('{caminho_sql}', delim=';', encoding='latin-1', "
        "header=true, all_varchar=true)"
    )


# CD_TIPO_ELEICAO da eleição ordinária. Os arquivos dos anos municipais trazem
# junto as suplementares (37 em 2024, `CD_TIPO_ELEICAO = 1`, também com
# `NR_TURNO = 1`): sem o filtro, o município que refez a eleição soma as duas.
TIPO_ELEICAO_ORDINARIA = 2

# Colunas de `votacao_secao` e de `detalhe_votacao_secao` que os leitores usam —
# as únicas guardadas nos parquets dos anos municipais.
COLUNAS_VOTACAO_SECAO = (
    "cd_tipo_eleicao", "ds_eleicao", "sg_uf", "cd_municipio", "nr_zona", "nr_secao", "nr_turno",
    "cd_cargo", "nr_votavel", "nm_votavel", "qt_votos",
)
COLUNAS_DETALHE = (
    "cd_tipo_eleicao", "cd_municipio", "nr_zona", "nr_secao", "nr_turno", "cd_cargo", "qt_aptos",
    "qt_comparecimento",
)


def _sql_fonte(caminho: Path | list[Path]) -> str:
    """SQL de leitura de um CSV do TSE ou de um ou mais parquets convertidos
    por `converte_zip_em_parquet` (mesmas colunas, tudo texto)."""
    caminhos = caminho if isinstance(caminho, list) else [caminho]
    if caminhos[0].suffix != ".parquet":
        (unico,) = caminhos
        return _sql_read_csv(unico)
    lista = ", ".join("'" + str(c).replace("'", "''") + "'" for c in caminhos)
    return f"read_parquet([{lista}])"


def _executa(con: duckdb.DuckDBPyConnection, query: str, por_partido: bool) -> pd.DataFrame:
    """Executa a consulta de `ler_votacao_secao`, somando por partido se pedido."""
    if not por_partido:
        return con.execute(query).df()
    nao_candidato = ", ".join(str(c) for c in CODIGOS_NAO_CANDIDATO)
    return con.execute(
        f"""
        with base as ({query})
        select
            ano, turno, sg_uf, cd_mun_tse, nr_zona, nr_secao, cd_cargo,
            case
                when nr_votavel in ({nao_candidato}) then nr_votavel
                else cast(substr(cast(nr_votavel as varchar), 1, 2) as integer)
            end as nr_votavel,
            sum(qt_votos) as qt_votos,
            any_value(qt_comparecimento) as qt_comparecimento,
            any_value(qt_aptos) as qt_aptos
        from base
        group by all
        """
    ).df()


def ler_votacao_secao(
    caminho_votacao_secao: Path | list[Path],
    ano: int,
    turno: int,
    *,
    caminho_detalhe: Path | None = None,
    cd_cargo: int = CARGO_PRESIDENTE,
    por_partido: bool = False,
) -> pd.DataFrame:
    """Lê `votacao_secao_{ano}_BR.csv`, opcionalmente enriquecido com
    `detalhe_votacao_secao_{ano}_BR.csv` (ver módulo — nome do arquivo real,
    não `..._{ano}_BR.zip` que eu tinha chutado).

    Caminho "oficial", consolidado pelo TSE após a apuração. Filtra o cargo
    (`cd_cargo`, Presidente por padrão) e o turno pedido.

    Args:
        caminho_votacao_secao: CSV de `votacao_secao_{ano}_BR.zip` (votos por
            candidato/seção). Para os outros cargos, o CSV de
            `votacao_secao_{ano}_{UF}.zip`, que tem o mesmo cabeçalho, ou a
            lista dos parquets por UF (`converte_zip_em_parquet`).
        ano: ano da eleição.
        turno: 1 ou 2.
        cd_cargo: código TSE do cargo (`CD_CARGO`).
        por_partido: se True, soma no próprio SQL os votos de cada partido por
            seção (`nr_votavel` vira o número do partido, os 2 primeiros
            dígitos; branco, nulo e anulado ficam como estão) e a coluna
            `nm_votavel` sai. Evita materializar uma linha por candidato — o
            que importa para o país inteiro em cargo proporcional.
        caminho_detalhe: CSV de `detalhe_votacao_secao_{ano}_BR.csv` (dentro
            do zip `detalhe_votacao_secao_{ano}.zip`), com `QT_APTOS` e
            `QT_COMPARECIMENTO` reais por seção. Se `None` (padrão),
            `qt_aptos` fica NA — quem chama usa `locais.qt_eleitores` como
            aproximação (ver `resultados.agrega_por_local`) — e
            `qt_comparecimento` é calculado como soma de `qt_votos` (idêntico
            ao valor do TSE em todos os casos testados).

    Returns:
        DataFrame no esquema comum (ver `ESQUEMA_COLUNAS`).
    """
    con = duckdb.connect()
    sql_votos = _sql_fonte(caminho_votacao_secao)

    votos_query = f"""
        select
            {ano} as ano,
            {turno} as turno,
            -- alias explícito: sem ele o duckdb devolve o nome como está no
            -- CSV (SG_UF, NM_VOTAVEL), fora do esquema comum.
            sg_uf as sg_uf,
            -- CD_MUNICIPIO aqui não tem zero à esquerda (varia de 2 a 5
            -- dígitos: "1120", não "01120" como em eleitorado_local_votacao)
            -- — confirmado contra o arquivo real de 2022 em 29/09/2026.
            -- Sem o lpad, id_secao não bate com secao_local para ~10% das
            -- linhas (todo município cujo código tem 1+ zeros à esquerda).
            lpad(cd_municipio, 5, '0') as cd_mun_tse,
            cast(nr_zona as integer) as nr_zona,
            cast(nr_secao as integer) as nr_secao,
            cast(cd_cargo as integer) as cd_cargo,
            cast(nr_votavel as integer) as nr_votavel,
            nm_votavel as nm_votavel,
            cast(qt_votos as integer) as qt_votos
        from {sql_votos}
        where cast(cd_cargo as integer) = {cd_cargo}
            and cast(nr_turno as integer) = {turno}
            and cast(cd_tipo_eleicao as integer) = {TIPO_ELEICAO_ORDINARIA}
    """

    if caminho_detalhe is None:
        query = f"""
            with votos as ({votos_query})
            select
                *,
                sum(qt_votos) over (partition by cd_mun_tse, nr_zona, nr_secao) as qt_comparecimento,
                cast(NULL as integer) as qt_aptos
            from votos
        """
        return _executa(con, query, por_partido)

    sql_detalhe = _sql_fonte(caminho_detalhe)
    query = f"""
        with votos as ({votos_query}),
             detalhe as (
                select
                    lpad(cd_municipio, 5, '0') as cd_mun_tse,
                    cast(nr_zona as integer) as nr_zona,
                    cast(nr_secao as integer) as nr_secao,
                    cast(nr_turno as integer) as nr_turno,
                    cast(qt_aptos as integer) as qt_aptos,
                    cast(qt_comparecimento as integer) as qt_comparecimento
                from {sql_detalhe}
                where cast(nr_turno as integer) = {turno}
                    -- nos anos municipais o detalhe tem uma linha por seção
                    -- por cargo; sem o filtro o join duplicaria os votos.
                    and cast(cd_cargo as integer) = {cd_cargo}
                    and cast(cd_tipo_eleicao as integer) = {TIPO_ELEICAO_ORDINARIA}
            )
        select
            v.*,
            -- coalesce com a soma de qt_votos: nenhuma diferença nos ~944 mil
            -- casos testados em 2022, mas cobre seções que faltem no detalhe.
            coalesce(
                d.qt_comparecimento,
                sum(v.qt_votos) over (partition by v.cd_mun_tse, v.nr_zona, v.nr_secao)
            ) as qt_comparecimento,
            d.qt_aptos
        from votos v
        left join detalhe d
            on d.cd_mun_tse = v.cd_mun_tse and d.nr_zona = v.nr_zona and d.nr_secao = v.nr_secao
    """
    return _executa(con, query, por_partido)


def ler_bweb(caminho_bweb_uf: Path, ano: int, turno: int, sg_uf: str) -> pd.DataFrame:
    """Lê um arquivo de boletim de urna simplificado (`bweb`) de uma UF.

    Caminho rápido para a noite da eleição — em 2022 saiu ~D+3 antes do
    `votacao_secao` consolidado. `qt_comparecimento` é somado do mesmo jeito
    que em `ler_votacao_secao` (ver módulo): soma de `qt_votos` por seção.

    ATENÇÃO: o layout exato do JSON de boletim de urna publicado em
    `resultados.tse.jus.br` deve ser confirmado contra um arquivo real de 2022
    antes da eleição de 2026 (não foi possível baixar um daqui, ver
    `config.URL_BWEB` e PLAN.md > Riscos e pendências). Esta função assume o
    layout "dados simplificados" documentado publicamente: um JSON com uma
    lista `cand` por seção, com `cand.vap` = votos apurados, `cand.cs` = 2/95/96
    (válido/branco/nulo) e o local identificado por `lc`.

    Args:
        caminho_bweb_uf: caminho do JSON baixado para a UF.
        ano: ano da eleição.
        turno: 1 ou 2.
        sg_uf: sigla da UF (para preencher a coluna, caso o arquivo não a traga).

    Returns:
        DataFrame no esquema comum (ver `ESQUEMA_COLUNAS`), com `qt_aptos` e
        `qt_comparecimento` como NA.
    """
    import json

    with open(caminho_bweb_uf, encoding="latin-1") as f:
        dados = json.load(f)

    linhas = []
    for secao in dados.get("secoes", dados if isinstance(dados, list) else []):
        # zero à esquerda até 5 dígitos, como em eleitorado_local_votacao —
        # confirmar contra um bweb real antes da eleição (ver docstring).
        cd_mun = str(secao.get("cdmun", secao.get("cd_municipio", ""))).zfill(5)
        nr_zona = int(secao.get("nrzona", secao.get("nr_zona", 0)))
        nr_secao = int(secao.get("nrsecao", secao.get("nr_secao", 0)))
        for cand in secao.get("cand", []):
            linhas.append(
                {
                    "ano": ano,
                    "turno": turno,
                    "sg_uf": sg_uf,
                    "cd_mun_tse": cd_mun,
                    "nr_zona": nr_zona,
                    "nr_secao": nr_secao,
                    "cd_cargo": CARGO_PRESIDENTE,
                    "nr_votavel": int(cand.get("n", cand.get("nr_votavel", 0))),
                    "nm_votavel": cand.get("nm", cand.get("nm_votavel", "")),
                    "qt_votos": int(cand.get("vap", cand.get("qt_votos", 0))),
                }
            )

    colunas_base = [c for c in ESQUEMA_COLUNAS if c not in ("qt_comparecimento", "qt_aptos")]
    df = pd.DataFrame(linhas, columns=colunas_base)
    df["qt_comparecimento"] = df.groupby(["cd_mun_tse", "nr_zona", "nr_secao"])[
        "qt_votos"
    ].transform("sum")
    df["qt_aptos"] = pd.NA
    return df


def extrai_csv_do_zip(caminho_zip: Path, caminho_csv: Path, *, nome_interno: str | None = None) -> None:
    """Extrai um CSV de dentro de um zip do TSE, sem descompactar o resto.

    Args:
        caminho_zip: zip baixado do CDN.
        caminho_csv: onde salvar o CSV extraído.
        nome_interno: nome exato do arquivo dentro do zip. Se `None`, pega o
            primeiro `.csv` encontrado (zips com um CSV só, como
            `votacao_secao`). Zips com vários CSVs (como
            `detalhe_votacao_secao`, um por UF + BR + BRASIL) precisam do
            nome exato — pegar "o primeiro" pegaria uma UF qualquer.
    """
    if not caminho_zip.exists():
        raise FileNotFoundError(
            f"Nem {caminho_csv} nem {caminho_zip} existem — baixe manualmente "
            "(ver PLAN.md > Fontes de dados: o CDN do TSE só respondeu via "
            "R/httr2 daqui, não via curl direto)."
        )
    logger.info("Extraindo %s de %s...", nome_interno or "(primeiro .csv)", caminho_zip)
    with zipfile.ZipFile(caminho_zip) as z:
        alvo = nome_interno or next(n for n in z.namelist() if n.lower().endswith(".csv"))
        # copyfileobj: o CSV de uma UF grande passa de vários GB, não cabe
        # inteiro na memória.
        with z.open(alvo) as origem, open(caminho_csv, "wb") as saida:
            shutil.copyfileobj(origem, saida, length=1 << 24)


def _precisa_extrair(caminho_zip: Path, caminho_csv: Path) -> bool:
    """True se o CSV não existe ou é mais velho que o zip.

    O zip do 1º turno é substituído pelo dos dois turnos quando o TSE publica o
    2º; sem comparar as datas, o CSV extraído antes seria lido de novo e o 2º
    turno sairia vazio.
    """
    if not caminho_csv.exists():
        return True
    return caminho_zip.exists() and caminho_zip.stat().st_mtime > caminho_csv.stat().st_mtime


def converte_zip_em_parquet(
    caminho_zip: Path,
    caminho_parquet: Path,
    colunas: tuple[str, ...],
    *,
    nome_interno: str | None = None,
) -> Path:
    """Converte um CSV de dentro de um zip do TSE em parquet, só com `colunas`.

    O CSV é extraído ao lado do parquet e apagado no fim: os `votacao_secao`
    por UF dos anos municipais somam dezenas de GB descompactados, contra
    algumas centenas de MB em parquet. As colunas continuam todas texto, como
    em `_sql_read_csv`. Não refaz o que já existe.
    """
    if caminho_parquet.exists():
        return caminho_parquet
    caminho_csv = caminho_parquet.with_suffix(".csv")
    parcial = caminho_parquet.with_suffix(".parquet.part")
    extrai_csv_do_zip(caminho_zip, caminho_csv, nome_interno=nome_interno)
    try:
        duckdb.sql(
            f"""copy (select {", ".join(colunas)} from {_sql_read_csv(caminho_csv)})
                to '{str(parcial).replace("'", "''")}' (format parquet, compression zstd)"""
        )
    finally:
        caminho_csv.unlink(missing_ok=True)
    parcial.replace(caminho_parquet)
    return caminho_parquet


def _obtem_resultados_municipais(
    ano: int, turno: int, cargo: str, ufs: list[str] | None
) -> pd.DataFrame:
    """Resultados por seção de um cargo municipal, dos zips por UF (ver módulo).

    Os zips vêm de `03b_baixa_resultados.qmd` (o CDN devolve 403 ao Python).
    """
    destino = BRUTO_DIR / str(ano)
    # Sem DF: não tem eleição municipal.
    ufs = ufs or [uf for uf in UFS_27 if uf != "DF"]
    parquets = [
        converte_zip_em_parquet(
            destino / f"votacao_secao_{ano}_{uf}.zip",
            destino / f"votacao_secao_{ano}_{uf}.parquet",
            COLUNAS_VOTACAO_SECAO,
        )
        for uf in ufs
    ]

    caminho_zip_detalhe = destino / f"detalhe_votacao_secao_{ano}.zip"
    caminho_detalhe = destino / f"detalhe_votacao_secao_{ano}.parquet"
    if caminho_detalhe.exists() or caminho_zip_detalhe.exists():
        converte_zip_em_parquet(
            caminho_zip_detalhe, caminho_detalhe, COLUNAS_DETALHE,
            nome_interno=f"detalhe_votacao_secao_{ano}_BRASIL.csv",
        )
    else:
        logger.info("%s não encontrado — qt_aptos vem de locais.qt_eleitores.", caminho_zip_detalhe.name)
        caminho_detalhe = None

    return ler_votacao_secao(
        parquets, ano, turno, caminho_detalhe=caminho_detalhe,
        cd_cargo=CARGOS[cargo]["cd_cargo"], por_partido=CARGOS[cargo]["por_partido"],
    )


def obtem_resultados_secao(
    ano: int,
    turno: int,
    fonte: str,
    deve_baixar: bool,
    cargo: str = "presidente",
    ufs: list[str] | None = None,
) -> pd.DataFrame:
    """Baixa (se pedido) e lê os resultados por seção, na fonte escolhida.

    `cargo` é uma chave de `config.CARGOS`. Os cargos municipais só existem em
    `votacao_secao`, um zip por UF já em disco (`fonte` e `deve_baixar` não se
    aplicam); `ufs` restringe a leitura a algumas UFs, para testar.
    """
    if cargo != "presidente":
        return _obtem_resultados_municipais(ano, turno, cargo, ufs)

    destino = BRUTO_DIR / str(ano)

    if fonte == "bweb":
        partes = []
        for uf in UFS_27:
            caminho_uf = destino / f"bweb_{turno}t_{uf}.json"
            if deve_baixar:
                url = URL_BWEB.format(ano=ano, pleito=f"{turno}t", uf=uf.lower())
                try:
                    baixar(url, caminho_uf)
                except Exception:
                    logger.exception("Falha ao baixar bweb de %s — pulando.", uf)
                    continue
            if caminho_uf.exists():
                partes.append(ler_bweb(caminho_uf, ano, turno, uf))
        if not partes:
            raise RuntimeError("Nenhum arquivo bweb disponível — ver logs acima.")
        return pd.concat(partes, ignore_index=True)

    if fonte == "votacao_secao":
        caminho_zip = destino / f"votacao_secao_{ano}_BR.zip"
        caminho_votos = destino / f"votacao_secao_{ano}_BR.csv"
        if deve_baixar:
            baixar(URL_VOTACAO_SECAO.format(ano=ano), caminho_zip)
        if _precisa_extrair(caminho_zip, caminho_votos):
            extrai_csv_do_zip(caminho_zip, caminho_votos)

        # detalhe_votacao_secao é opcional: dá qt_aptos real por seção em vez
        # da aproximação por local (locais.qt_eleitores). Nome do zip NÃO tem
        # "_BR" (diferente de votacao_secao), achado no portal humano em
        # 29/09/2026 depois de eu chutar errado. Não baixa sozinho (243 MB,
        # opcional) — só usa se já estiver em disco.
        caminho_zip_detalhe = destino / f"detalhe_votacao_secao_{ano}.zip"
        caminho_detalhe = destino / f"detalhe_votacao_secao_{ano}_BR.csv"
        if caminho_zip_detalhe.exists() and _precisa_extrair(caminho_zip_detalhe, caminho_detalhe):
            extrai_csv_do_zip(
                caminho_zip_detalhe, caminho_detalhe,
                nome_interno=f"detalhe_votacao_secao_{ano}_BR.csv",
            )
        if not caminho_detalhe.exists():
            logger.info(
                "%s não encontrado — qt_aptos vem da aproximação por local "
                "(locais.qt_eleitores), não do TSE direto. Não bloqueia. Para "
                "baixar (243 MB, opcional): %s",
                caminho_detalhe.name, URL_DETALHE_VOTACAO_SECAO.format(ano=ano),
            )
            caminho_detalhe = None

        return ler_votacao_secao(caminho_votos, ano, turno, caminho_detalhe=caminho_detalhe)

    raise ValueError(f"Fonte desconhecida: {fonte!r} (use 'bweb' ou 'votacao_secao')")


# ==== Portal de divulgação (resultado por zona × município) ====================


def baixa_divulgacao(ano: int, turno: int, *, workers: int = 8) -> Path:
    """Baixa do portal de divulgação o resultado de Presidente por zona × município.

    Um JSON por (município, zona) do índice `config/mun-e{cd}-cm.json` (6.292 no
    2º turno de 2026, com as 186 cidades do exterior), mais o total do país
    (`br-...-u.json`) para a conferência. Grava em
    `dados/01_bruto/{ano}/divulgacao_t{turno}/` e não baixa de novo o que já
    estiver lá — para atualizar (apuração ainda não totalizada), apagar a pasta.

    Returns:
        A pasta com os JSON.
    """
    from concurrent.futures import ThreadPoolExecutor

    cd = CD_ELEICAO_DIVULGACAO[ano, turno]
    base = URL_DIVULGACAO.format(ano=ano, cd_eleicao=cd)
    pasta = BRUTO_DIR / str(ano) / f"divulgacao_t{turno}"
    indice = baixar(f"{base}/config/mun-e{cd:06d}-cm.json", pasta / "indice_municipios.json")
    baixar(f"{base}/dados/br/br-c0001-e{cd:06d}-u.json", pasta / "br.json")

    pedidos = [
        (
            f"{base}/dados/{uf['cd']}/{uf['cd']}{mun['cd']}-z{zona}-c0001-e{cd:06d}-u.json",
            pasta / uf["cd"] / f"{mun['cd']}_{zona}.json",
        )
        for uf in _le_json(indice)["abr"]
        for mun in uf["mu"]
        for zona in mun["z"]
    ]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda p: baixar(*p), pedidos))
    logger.info("%d arquivos de zona × município em %s", len(pedidos), pasta)
    return pasta


def _le_json(caminho: Path) -> dict:
    import json

    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def candidatos_divulgacao(dados: dict) -> list[dict]:
    """Candidatos a Presidente de um JSON da divulgação: número, nome e votos."""
    (cargo,) = [c for c in dados["carg"] if int(c["cd"]) == CARGO_PRESIDENTE]
    return [
        {"nr_votavel": int(cand["n"]), "nm_votavel": cand["nm"], "qt_votos": int(cand["vap"])}
        for agr in cargo["agr"]
        for par in agr["par"]
        for cand in par["cand"]
    ]


def totais_divulgacao(dados: dict) -> dict[str, int]:
    """Votos por candidato, brancos, nulos, comparecimento e aptos, no formato
    largo dos resultados (`qt_votos_{nr}`).

    Nulos é `tvn` (nulos + nulos técnicos); anulados apurados em separado (`van`)
    só viram coluna quando houver algum, como no `votacao_secao`.
    """
    linha = {f"qt_votos_{c['nr_votavel']}": c["qt_votos"] for c in candidatos_divulgacao(dados)}
    linha[f"qt_votos_{CODIGO_BRANCO}"] = int(dados["v"]["vb"])
    linha[f"qt_votos_{CODIGO_NULO}"] = int(dados["v"]["tvn"])
    if int(dados["v"]["van"]):
        linha[f"qt_votos_{CODIGO_ANULADO_APURADO_SEPARADO}"] = int(dados["v"]["van"])
    linha["qt_comparecimento"] = int(dados["e"]["c"])
    linha["qt_aptos"] = int(dados["e"]["te"])
    return linha


def ler_divulgacao_zona_mun(pasta: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lê os JSON de `baixa_divulgacao`.

    Returns:
        Uma linha por (cd_mun_tse, nr_zona), com `sg_uf` e as colunas de
        `totais_divulgacao` (0 onde o candidato não teve voto), e os candidatos
        (`nr_votavel`, `nm_votavel`) do país.
    """
    linhas = []
    for caminho in sorted(pasta.glob("*/*.json")):
        cd_mun_tse, nr_zona = caminho.stem.split("_")
        dados = _le_json(caminho)
        if dados.get("tf") != "s":
            logger.warning("%s: apuração não totalizada (tf=%s)", caminho.name, dados.get("tf"))
        linhas.append(
            {"sg_uf": caminho.parent.name.upper(), "cd_mun_tse": cd_mun_tse, "nr_zona": int(nr_zona), **totais_divulgacao(dados)}
        )
    res = pd.DataFrame(linhas)
    colunas_qt = [c for c in res.columns if c.startswith("qt_")]
    res[colunas_qt] = res[colunas_qt].fillna(0).astype("int64")
    nomes = pd.DataFrame(candidatos_divulgacao(_le_json(pasta / "br.json")))[["nr_votavel", "nm_votavel"]]
    return res, nomes
