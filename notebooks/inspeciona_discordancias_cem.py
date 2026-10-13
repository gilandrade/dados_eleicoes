"""Inspeciona os setores em que a zona estimada (tempo na rede) difere da zona CEM.

Só setores "inteiros" (>= 95% da área numa zona CEM), para isolar o erro do método.
Para cada município: distância do setor à fronteira da zona CEM, distância ao
local mais próximo da zona estimada e ao da zona CEM, qualidade da coordenada
do local, tamanho dos bolsões de discordância e checagem local a local
(zona CEM que contém a coordenada do local x zona do TSE).
Uso: python notebooks/inspeciona_discordancias_cem.py [RM ...]   (padrão: Salvador Belem Curitiba)
"""
import sys
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

CRS = 5880
RES = "dados/03_georedus/resultados/"
OUT = "dados/mapas_comparacao/"
RMS = [a.lower() for a in sys.argv[1:]] or ["salvador", "belem", "curitiba"]

cem = gpd.read_file("dados/01_bruto/cem/ze2022_rmbra/EL2022_ZE_RMBRA_CEM_V1.shp",
                    columns=["ZE_NUM", "CD_MUN_I", "MUN_NOME", "RME_NOME"]).to_crs(CRS)
cem["cd_mun"] = cem.CD_MUN_I.astype(int).astype(str)
cem["geometry"] = shapely.make_valid(cem.geometry)
cem["rm"] = cem.RME_NOME.str.title().str.normalize("NFKD").str.encode("ascii", "ignore").str.decode("ascii").str.lower()
locais = gpd.read_file("dados/03_georedus/geometrias/geom_locais_votacao_2022.gpkg").query(
    "not flag_especial and not flag_sem_coord").to_crs(CRS)
lote = pd.read_parquet(RES + "setor_zona_2022_rms_cem_2022_rede.parquet")
pop18 = pd.read_parquet("dados/02_processado/aux/pop18_setor.parquet")

linhas_loc, linhas_set = [], []
for cd in sorted(cem.loc[cem.rm.isin(RMS), "cd_mun"].unique()):
    c = cem[cem.cd_mun == cd]
    if c.ZE_NUM.nunique() < 2:
        continue
    nome, rm = c.MUN_NOME.iloc[0].title(), c.rm.iloc[0]
    # --- locais: zona do CEM que contém a coordenada x zona do TSE
    L = locais[locais.cd_mun == cd].copy()
    j = gpd.sjoin(L[["id_local", "geometry"]], c[["ZE_NUM", "geometry"]], how="left", predicate="within").drop_duplicates("id_local")
    L = L.merge(j[["id_local", "ZE_NUM"]].rename(columns={"ZE_NUM": "z_cem_pt"}), on="id_local", how="left")
    L["bate"] = L.z_cem_pt == L.nr_zona
    # --- setores
    s = gpd.read_file("dados/01_bruto/aux/setores/BR_setores_CD2022.gpkg", where=f"CD_MUN = '{cd}'", columns=["CD_SETOR", "CD_MUN"])
    s = s.dissolve(by="CD_SETOR", as_index=False).to_crs(CRS)
    s["geometry"] = shapely.make_valid(s.geometry); s["area"] = s.area
    i = gpd.overlay(s[["CD_SETOR", "geometry"]], c[["ZE_NUM", "geometry"]], how="intersection", keep_geom_type=True)
    i["a"] = i.area
    dom = i.sort_values("a", ascending=False).drop_duplicates("CD_SETOR").set_index("CD_SETOR")
    s = s.merge(dom[["ZE_NUM", "a"]].rename(columns={"ZE_NUM": "z_cem", "a": "a_dom"}), on="CD_SETOR", how="left")
    s["frac"] = s.a_dom / s.area
    s = s.merge(lote.loc[lote.cd_mun == cd, ["cd_setor", "nr_zona"]].rename(columns={"cd_setor": "CD_SETOR", "nr_zona": "z"}), on="CD_SETOR", how="left")
    s = s.merge(pop18, left_on="CD_SETOR", right_on="cd_setor", how="left"); s["pop18"] = s.pop18.fillna(0)
    s = s[s.z_cem.notna() & s.z.notna()].reset_index(drop=True)
    pts = gpd.GeoSeries(s.geometry.representative_point(), crs=CRS)
    s["disc"] = s.z != s.z_cem
    # distância do ponto do setor à fronteira da zona CEM dominante
    cz = c.dissolve(by="ZE_NUM").geometry
    s["d_fronteira_m"] = [pts.iloc[k].distance(cz.loc[zc].boundary) for k, zc in enumerate(s.z_cem)]
    # distância ao local mais próximo de cada zona (por TSE)
    def dmin(zona_col):
        out = np.full(len(s), np.nan)
        for z, idx in s.groupby(zona_col).groups.items():
            Lz = L[L.nr_zona == z]
            if len(Lz):
                out[np.asarray(idx)] = shapely.distance(np.asarray(pts.values)[np.asarray(idx)][:, None], np.asarray(Lz.geometry.values)[None, :]).min(axis=1)
        return out
    s["d_loc_nossa_m"], s["d_loc_cem_m"] = dmin("z"), dmin("z_cem")
    # bolsões de discordância (setores vizinhos discordantes)
    par = gpd.sjoin(s[["geometry"]], s[["geometry"]], predicate="intersects")
    par = par[(par.index < par.index_right)]
    a, b = par.index.to_numpy(), par.index_right.to_numpy()
    d = s.disc.to_numpy(); m = d[a] & d[b]
    g = csr_matrix((np.ones(m.sum()), (a[m], b[m])), shape=(len(s),) * 2)
    _, rot = connected_components(g, directed=False)
    s["bolsao"] = pd.Series(rot).map(pd.Series(rot).value_counts()).values
    s["cd_mun"], s["nome"], s["rm"] = cd, nome, rm
    inteiros = s[s.frac >= 0.95]
    di = inteiros[inteiros.disc]
    print(f"\n== {nome} ({rm}): {len(s)} setores, {len(inteiros)} inteiros, {len(di)} discordantes ({100*len(di)/len(inteiros):.1f}%)")
    print(f"   locais: {len(L)}, coordenada dentro da zona CEM do próprio TSE: {100*L.bate.mean():.1f}%")
    linhas_loc.append(L.assign(nome=nome, rm=rm)[["id_local", "cd_mun", "nome", "rm", "nr_zona", "z_cem_pt", "bate", "fonte_coord", "qt_eleitores"]])
    linhas_set.append(inteiros.drop(columns="geometry").assign(geometry=inteiros.geometry.values)[["CD_SETOR", "cd_mun", "nome", "rm", "z", "z_cem", "frac", "pop18", "disc", "d_fronteira_m", "d_loc_nossa_m", "d_loc_cem_m", "bolsao"]])

loc = pd.concat(linhas_loc); st = pd.concat(linhas_set)
loc.to_csv(OUT + "inspecao_locais_vs_cem.csv", index=False)
st.to_csv(OUT + "inspecao_setores_discordantes.csv", index=False)
pd.set_option("display.width", 250)
print("\n### Locais: coordenada cai na zona CEM do próprio local, por fonte_coord")
print(loc.groupby(["rm", "fonte_coord"]).agg(n=("bate", "size"), bate_pc=("bate", lambda x: round(100 * x.mean(), 1))).to_string())
st["dist_fronteira"] = pd.cut(st.d_fronteira_m, [-1, 100, 300, 1000, 1e9], labels=["<100m", "100-300m", "300m-1km", ">1km"])
st["bolsao_tam"] = pd.cut(st.bolsao.where(st.disc), [0, 1, 3, 10, 1e9], labels=["isolado", "2-3", "4-10", ">10"])
st["nossa_mais_perto"] = st.d_loc_nossa_m <= st.d_loc_cem_m
for rm, g in st.groupby("rm"):
    d = g[g.disc]
    print(f"\n### {rm}: {len(d)} discordantes de {len(g)} (pop {d.pop18.sum():.0f})")
    print("distância à fronteira CEM:", (d.dist_fronteira.value_counts(normalize=True).sort_index() * 100).round(1).to_dict())
    print("taxa de discordância por faixa de distância à fronteira:", (g.groupby("dist_fronteira", observed=True).disc.mean() * 100).round(1).to_dict())
    print("tamanho do bolsão:", (d.bolsao_tam.value_counts(normalize=True).sort_index() * 100).round(1).to_dict())
    print("local da nossa zona mais perto que o da zona CEM:", round(100 * d.nossa_mais_perto.mean(), 1), "%",
          "| mediana d_nossa/d_cem (m):", round(d.d_loc_nossa_m.median()), "/", round(d.d_loc_cem_m.median()))
    print("pares (nossa -> CEM) mais comuns:\n", d.groupby(["z", "z_cem"]).size().sort_values(ascending=False).head(6).to_string())
