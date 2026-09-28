"""Adaptadores de resultados: cada fonte do TSE devolve o mesmo esquema longo.

Esquema comum (uma linha por seção × votável):

    ano, turno, sg_uf, cd_mun_tse, nr_zona, nr_secao, cd_cargo,
    nr_votavel, nm_votavel, qt_votos, qt_comparecimento

`qt_comparecimento` (por seção): o voto para Presidente é único e obrigatório
para quem comparece, então `Σ qt_votos` de uma seção (válidos + brancos [95] +
nulos [96]) **é** o comparecimento daquela seção — não precisa de um arquivo à
parte. `qt_aptos` não vem daqui: usar `qt_eleitores` de `locais_{ano}.parquet`
(etapa 01), que já soma `QT_ELEITOR_SECAO` por local — ver
`resultados.agrega_por_local`.

Esta função dependia de um `detalhe_votacao_secao_{ano}_BR.zip` que, ao
tentar baixar de verdade (29/09/2026), devolveu 403/404 em todos os nomes
testados — pode nem existir mais como arquivo próprio; o comparecimento por
seção não parece publicado separadamente no portal de dados abertos.

Ambos os leitores filtram o cargo Presidente na própria leitura (mais barato
que filtrar depois de ler o Brasil inteiro).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from .config import CARGO_PRESIDENTE

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


def ler_votacao_secao(caminho_votacao_secao: Path, ano: int, turno: int) -> pd.DataFrame:
    """Lê `votacao_secao_{ano}_BR.csv`.

    Caminho "oficial", consolidado pelo TSE após a apuração. Filtra o cargo
    Presidente e o turno pedido. `qt_comparecimento` é a soma de `qt_votos`
    de todas as linhas da mesma seção (ver módulo) — não depende de nenhum
    outro arquivo.

    Args:
        caminho_votacao_secao: CSV de `votacao_secao_{ano}_BR.zip` (votos por
            candidato/seção).
        ano: ano da eleição.
        turno: 1 ou 2.

    Returns:
        DataFrame no esquema comum (ver `ESQUEMA_COLUNAS`).
    """
    con = duckdb.connect()
    sql_votos = _sql_read_csv(caminho_votacao_secao)

    query = f"""
        with votos as (
            select
                {ano} as ano,
                {turno} as turno,
                sg_uf,
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
                nm_votavel,
                cast(qt_votos as integer) as qt_votos
            from {sql_votos}
            where cast(cd_cargo as integer) = {CARGO_PRESIDENTE}
                and cast(nr_turno as integer) = {turno}
        )
        select
            *,
            sum(qt_votos) over (partition by cd_mun_tse, nr_zona, nr_secao) as qt_comparecimento
        from votos
    """
    return con.execute(query).df()


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

    df = pd.DataFrame(linhas, columns=[c for c in ESQUEMA_COLUNAS if c != "qt_comparecimento"])
    df["qt_comparecimento"] = df.groupby(["cd_mun_tse", "nr_zona", "nr_secao"])[
        "qt_votos"
    ].transform("sum")
    return df
