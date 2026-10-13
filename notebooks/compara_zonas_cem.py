"""Compara as zonas estimadas (linha reta x tempo na rede) com a divisão do CEM.

Referência: EL2022_ZE_RMBRA_CEM_V1 (zonas eleitorais de 2022 das principais RMs).
Para cada município com mais de uma zona no CEM (lote `rms_cem_2022` da etapa 03,
mais Rio e São Paulo, rodados antes à parte):
  - concordância por setor (ponto representativo do setor dentro da zona CEM):
    em nº de setores, em população 18+ e em área;
  - IoU (interseção / união) de cada zona nossa com a zona CEM de mesmo número;
  - pedaços secundários de zona (ilhas) de cada versão;
  - mapas em dados/mapas_comparacao/.

Pré-requisito: setor_zona_2022_{rotulo|cd}[_rede].parquet (etapa 03, com e sem -P usa_rede:true).
Uso: python notebooks/compara_zonas_cem.py [rotulo]   (padrão: rms_cem_2022)
"""
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CRS_METRICO_BR = 5880
RES = "dados/03_georedus/resultados/"
OUT = "dados/mapas_comparacao/"
CEM_2022 = "dados/01_bruto/cem/ze2022_rmbra/EL2022_ZE_RMBRA_CEM_V1.shp"
ROTULO_LOTE = sys.argv[1] if len(sys.argv) > 1 else "rms_cem_2022"
SOLO = ("3304557", "3550308")  # Rio e São Paulo foram rodados antes, com o próprio código no sufixo
MIN_ZONAS_MAPA = 8  # só gera mapa de municípios com pelo menos este nº de zonas
ABORDAGENS = {
    "reta": "Linha reta",
    "rede": "Tempo na rede",
    "afrouxada": "Rede + limpeza afrouxada",  # -P limite_ponte:0.15 -P permite_cortar:true
}
SUFIXO_ARQUIVO = {"reta": "", "rede": "_rede", "afrouxada": "_rede_afrouxado"}

cem = gpd.read_file(CEM_2022, columns=["ZE_NUM", "CD_MUN_I", "MUN_NOME", "RME_NOME"]).to_crs(CRS_METRICO_BR)
cem["cd_mun"] = cem["CD_MUN_I"].astype(int).astype(str)
cem["geometry"] = shapely.make_valid(cem.geometry)
n_zonas_cem = cem.groupby("cd_mun")["ZE_NUM"].nunique()
MUNICIPIOS = {
    cd: (g["MUN_NOME"].iloc[0].title(), g["RME_NOME"].iloc[0].title())
    for cd, g in cem.groupby("cd_mun") if n_zonas_cem[cd] > 1
}
print(f"{len(MUNICIPIOS)} municípios com mais de uma zona no CEM")


def le_setor_zona(cd, suf):
    """Tabela setor -> zona de um município, do lote ou do arquivo próprio (Rio, São Paulo)."""
    solo = f"{RES}setor_zona_2022_{cd}{suf}.parquet"
    lote = f"{RES}setor_zona_2022_{ROTULO_LOTE}{suf}.parquet"
    if cd in SOLO and Path(solo).exists():
        return pd.read_parquet(solo)
    if not Path(lote).exists():
        return None
    t = pd.read_parquet(lote)
    return t.loc[t["cd_mun"] == cd]


pop18 = pd.read_parquet("dados/02_processado/aux/pop18_setor.parquet")
locais = gpd.read_file(
    "dados/03_georedus/geometrias/geom_locais_votacao_2022.gpkg"
).query("not flag_especial and not flag_sem_coord").to_crs(CRS_METRICO_BR)

resumo = []
for cd, (nome, rm) in MUNICIPIOS.items():
    s = gpd.read_file(
        "dados/01_bruto/aux/setores/BR_setores_CD2022.gpkg",
        where=f"CD_MUN = '{cd}'", columns=["CD_SETOR", "CD_MUN"],
    )
    s = s.dissolve(by="CD_SETOR", as_index=False).to_crs(CRS_METRICO_BR).reset_index(drop=True)
    s["area_km2"] = s.area / 1e6
    s = s.merge(pop18, left_on="CD_SETOR", right_on="cd_setor", how="left")
    s["pop18"] = s["pop18"].fillna(0)

    # Zona CEM do ponto representativo de cada setor.
    c = cem.loc[cem["cd_mun"] == cd, ["ZE_NUM", "geometry"]]
    pts = s[["CD_SETOR"]].assign(geometry=s.geometry.representative_point())
    pts = gpd.GeoDataFrame(pts, crs=s.crs)
    j = gpd.sjoin(pts, c, how="left", predicate="within").drop_duplicates("CD_SETOR")
    s = s.merge(j[["CD_SETOR", "ZE_NUM"]].rename(columns={"ZE_NUM": "z_cem"}), on="CD_SETOR", how="left")
    print(f"\n== {nome}: {len(s)} setores, {s['z_cem'].isna().sum()} sem zona CEM (ponto fora dos polígonos)")

    # Adjacência de setores (fronteira > 1 m), para os pedaços contínuos.
    par = gpd.sjoin(s[["geometry"]], s[["geometry"]], predicate="intersects")
    par = par.loc[par.index < par["index_right"]]
    a, b = par.index.to_numpy(), par["index_right"].to_numpy()
    bd = s.geometry.boundary.to_numpy()
    ok = shapely.length(shapely.intersection(bd[a], bd[b])) > 1
    a, b = a[ok], b[ok]
    com_local = gpd.sjoin(locais.loc[locais["cd_mun"] == cd, ["nr_zona", "geometry"]], s[["geometry"]], predicate="within")
    zona_local = set(zip(com_local["index_right"], com_local["nr_zona"]))

    abordagens = {}
    for chave, rotulo in ABORDAGENS.items():
        suf = SUFIXO_ARQUIVO[chave]
        sz = le_setor_zona(cd, suf)
        if sz is None or sz.empty:
            continue
        abordagens[chave] = rotulo
        sz = sz.rename(columns={"cd_setor": "CD_SETOR", "nr_zona": f"z_{chave}"})
        s = s.merge(sz[["CD_SETOR", f"z_{chave}"]], on="CD_SETOR", how="left")
        z = s[f"z_{chave}"].to_numpy()

        valido = s["z_cem"].notna()
        igual = (s[f"z_{chave}"] == s["z_cem"]) & valido
        # IoU por zona: nossa zona (setores dissolvidos) x zona CEM de mesmo número.
        nossa = s.dissolve(by=f"z_{chave}")["geometry"]
        cz = c.dissolve(by="ZE_NUM")["geometry"]
        iou = pd.Series({
            k: nossa.loc[k].intersection(cz.loc[k]).area / nossa.loc[k].union(cz.loc[k]).area
            for k in nossa.index if k in cz.index
        })

        # Pedaços contínuos de cada zona.
        m = z[a] == z[b]
        g = csr_matrix((np.ones(m.sum()), (a[m], b[m])), shape=(len(s),) * 2)
        _, rot = connected_components(g, directed=False)
        d = pd.DataFrame({
            "rot": rot, "zona": z, "setores": 1,
            "tem_local": [int((i, z[i]) in zona_local) for i in range(len(s))],
        })
        r = d.groupby("rot").agg(zona=("zona", "first"), setores=("setores", "sum"), locais=("tem_local", "sum"))
        r["secundario"] = r.groupby("zona")["setores"].transform("max") != r["setores"]
        r.loc[r.groupby("zona")["setores"].transform("max") == r["setores"], "secundario"] = False
        r = r.loc[~r.index.duplicated()]
        s[f"sec_{chave}"] = s["CD_SETOR"].index.map(lambda i: bool(r.loc[rot[i], "secundario"]))
        s[f"tipo_{chave}"] = pd.Series(rot, index=s.index).map(
            lambda k: "principal" if not r.loc[k, "secundario"] else (
                "sem local" if r.loc[k, "locais"] == 0 else ("1 local" if r.loc[k, "locais"] == 1 else "2+ locais"))
        )
        sec = r.loc[r["secundario"]]
        linha = {
            "rm": rm, "municipio": nome, "cd_mun": cd, "n_zonas": len(cz), "abordagem": rotulo,
            "n_setores": int(valido.sum()), "n_iguais": int(igual.sum()),
            "pop": s.loc[valido, "pop18"].sum(), "pop_igual": s.loc[igual, "pop18"].sum(),
            "area": s.loc[valido, "area_km2"].sum(), "area_igual": s.loc[igual, "area_km2"].sum(),
            "setores_iguais_%": 100 * igual.sum() / valido.sum(),
            "pop18_igual_%": 100 * s.loc[igual, "pop18"].sum() / s.loc[valido, "pop18"].sum(),
            "area_igual_%": 100 * s.loc[igual, "area_km2"].sum() / s.loc[valido, "area_km2"].sum(),
            "IoU_mediana": iou.median(), "IoU_min": iou.min(), "zonas_IoU<0.8": int((iou < 0.8).sum()),
            "pedacos": len(r), "secundarios": len(sec),
            "sec_sem_local": int((sec["locais"] == 0).sum()), "sec_1_local": int((sec["locais"] == 1).sum()),
            "sec_2+_locais": int((sec["locais"] >= 2).sum()),
        }
        resumo.append(linha)

    # ---- Mapas -----------------------------------------------------------
    if len(cz) < MIN_ZONAS_MAPA:
        continue
    x0, y0, x1, y1 = s.total_bounds
    simples = s.copy()
    simples["geometry"] = s.geometry.simplify(15)
    cores_tipo = {"principal": "#e6e6e6", "sem local": "#1f77b4", "1 local": "crimson", "2+ locais": "#ff9900"}

    fig, ax = plt.subplots(1, len(abordagens), figsize=(10 * len(abordagens), 9))
    for x, (chave, rotulo) in zip(np.atleast_1d(ax), abordagens.items()):
        simples.plot(color=simples[f"tipo_{chave}"].map(cores_tipo), ax=x, edgecolor="none")
        cem.loc[cem["cd_mun"] == cd].boundary.plot(ax=x, color="k", linewidth=0.4)
        x.set_title(f"{nome} - pedaços secundários: {rotulo}", fontsize=13)
        x.set_xlim(x0, x1); x.set_ylim(y0, y1); x.set_aspect("equal"); x.axis("off")
    handles = [plt.Line2D([], [], marker="s", ls="", color=v, label=k) for k, v in cores_tipo.items()]
    np.atleast_1d(ax)[0].legend(handles=handles, loc="lower left", title="pedaço (linhas pretas = zonas CEM)")
    fig.savefig(f"{OUT}{cd}_ilhas.png", dpi=90, bbox_inches="tight"); plt.close(fig)

    fig, ax = plt.subplots(1, len(abordagens), figsize=(10 * len(abordagens), 9))
    for x, (chave, rotulo) in zip(np.atleast_1d(ax), abordagens.items()):
        dif = simples["z_cem"].notna() & (simples[f"z_{chave}"] != simples["z_cem"])
        simples.plot(color="#e6e6e6", ax=x, edgecolor="none")
        simples.loc[dif].plot(color="crimson", ax=x, edgecolor="none")
        cem.loc[cem["cd_mun"] == cd].boundary.plot(ax=x, color="k", linewidth=0.4)
        x.set_title(f"{nome} - divergência com o CEM: {rotulo} ({100*dif.mean():.1f}%)", fontsize=13)
        x.set_xlim(x0, x1); x.set_ylim(y0, y1); x.set_aspect("equal"); x.axis("off")
    fig.savefig(f"{OUT}{cd}_divergencia_cem.png", dpi=90, bbox_inches="tight"); plt.close(fig)

tabela = pd.DataFrame(resumo)
tabela.round(2).to_csv(f"{OUT}comparacao_cem_municipios.csv", index=False)

# Por RM: soma dos setores/população/área (não a média das porcentagens).
soma = ["n_setores", "n_iguais", "pop", "pop_igual", "area", "area_igual", "pedacos", "secundarios", "sec_1_local"]
rm = tabela.groupby(["rm", "abordagem"])[soma].sum()
rm["setores_iguais_%"] = 100 * rm["n_iguais"] / rm["n_setores"]
rm["pop18_igual_%"] = 100 * rm["pop_igual"] / rm["pop"]
rm["area_igual_%"] = 100 * rm["area_igual"] / rm["area"]
rm["municipios"] = tabela.groupby(["rm", "abordagem"])["cd_mun"].nunique()
rm = rm[["municipios", "setores_iguais_%", "pop18_igual_%", "area_igual_%", "pedacos", "secundarios", "sec_1_local"]].round(2)
rm.to_csv(f"{OUT}comparacao_cem_por_rm.csv")
pd.set_option("display.width", 250)
print(rm.to_string())
