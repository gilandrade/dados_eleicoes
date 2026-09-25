"""Adaptadores de resultados: cada fonte do TSE devolve o mesmo esquema longo.

Esquema comum (uma linha por seção × votável):

    ano, turno, sg_uf, cd_mun_tse, nr_zona, nr_secao, cd_cargo,
    nr_votavel, nm_votavel, qt_votos, qt_aptos, qt_comparecimento

`qt_aptos`/`qt_comparecimento` só vêm preenchidos quando a fonte os traz por
seção (`votacao_secao` + `detalhe_votacao_secao`); ficam NA em `ler_bweb` e são
completados depois, se necessário, por `detalhe_votacao_secao` isolado.

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
    "qt_aptos",
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


def ler_votacao_secao(
    caminho_votacao_secao: Path,
    caminho_detalhe_votacao_secao: Path,
    ano: int,
    turno: int,
) -> pd.DataFrame:
    """Lê `votacao_secao_{ano}_BR.csv` + `detalhe_votacao_secao_{ano}_BR.csv`.

    Caminho "oficial", consolidado pelo TSE após a apuração. Filtra o cargo
    Presidente e o turno pedido.

    Args:
        caminho_votacao_secao: CSV de `votacao_secao_{ano}_BR.zip` (votos por
            candidato/seção).
        caminho_detalhe_votacao_secao: CSV de `detalhe_votacao_secao_{ano}_BR.zip`
            (aptos, comparecimento, brancos e nulos por seção).
        ano: ano da eleição.
        turno: 1 ou 2.

    Returns:
        DataFrame no esquema comum (ver `ESQUEMA_COLUNAS`).
    """
    con = duckdb.connect()
    sql_votos = _sql_read_csv(caminho_votacao_secao)
    sql_detalhe = _sql_read_csv(caminho_detalhe_votacao_secao)

    query = f"""
        with votos as (select * from {sql_votos}),
             detalhe as (select * from {sql_detalhe})
        select
            {ano} as ano,
            {turno} as turno,
            v.sg_uf,
            v.cd_municipio as cd_mun_tse,
            cast(v.nr_zona as integer) as nr_zona,
            cast(v.nr_secao as integer) as nr_secao,
            cast(v.cd_cargo as integer) as cd_cargo,
            cast(v.nr_votavel as integer) as nr_votavel,
            v.nm_votavel,
            cast(v.qt_votos as integer) as qt_votos,
            cast(d.qt_aptos as integer) as qt_aptos,
            cast(d.qt_comparecimento as integer) as qt_comparecimento
        from votos v
        left join detalhe d
            on d.cd_municipio = v.cd_municipio
            and d.nr_zona = v.nr_zona
            and d.nr_secao = v.nr_secao
        where cast(v.cd_cargo as integer) = {CARGO_PRESIDENTE}
            and cast(v.nr_turno as integer) = {turno}
    """
    return con.execute(query).df()


def ler_bweb(caminho_bweb_uf: Path, ano: int, turno: int, sg_uf: str) -> pd.DataFrame:
    """Lê um arquivo de boletim de urna simplificado (`bweb`) de uma UF.

    Caminho rápido para a noite da eleição — em 2022 saiu ~D+3 antes do
    `votacao_secao` consolidado. Não traz `qt_aptos`/`qt_comparecimento`
    diretamente no mesmo nível de granularidade em todas as publicações; essas
    colunas ficam NA aqui e podem ser completadas por `detalhe_votacao_secao`
    quando ele sair.

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
        cd_mun = str(secao.get("cdmun", secao.get("cd_municipio", "")))
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
                    "qt_aptos": pd.NA,
                    "qt_comparecimento": pd.NA,
                }
            )

    return pd.DataFrame(linhas, columns=ESQUEMA_COLUNAS)
