// Visualizador de mapas da página mapas.qmd.
// Lê os JSON gerados por scripts/05_exporta_visualizador.py (docs/mapas/dados/).
// A classe de cor de cada feição já vem calculada em Python (src/eleicoes/cores.py);
// aqui só se pinta, monta a legenda e o tooltip.
(function () {
  "use strict";

  const BASE = "mapas/dados/";

  const MAPAS = {
    pontos_presidente: { tipo: "pontos", classe: "c_pres", cores: "div", legendaPorAno: true },
    pontos_branco_nulo: { tipo: "pontos", classe: "c_bn", cores: "seq" },
    pontos_abstencao: { tipo: "pontos", classe: "c_abst", cores: "seq" },
    zona_mun_presidente: { tipo: "zona_mun", cores: "div", legendaPorAno: true },
    mudanca: { tipo: "mudanca", cores: "div" },
  };

  const el = (id) => document.getElementById(id);
  const cache = {};
  const carrega = (arquivo) =>
    (cache[arquivo] ??= fetch(BASE + arquivo).then((r) => {
      if (!r.ok) throw new Error(`${arquivo}: HTTP ${r.status}`);
      return r.json();
    }));

  const fmtPct = (v) =>
    v == null ? "—" : (v / 10).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
  const fmtPp = (v) =>
    v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") +
      (Math.abs(v) / 10).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " p.p.";
  const fmtNum = (v) => (v == null ? "—" : v.toLocaleString("pt-BR"));
  const escapa = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  let config, mapa, camada, legenda, tooltip, geracao = 0;

  // ==== Estado (seletores <-> #hash) =========================================

  function lerHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const v = (p.get("v") || "").split(",").map(Number);
    return {
      mapa: p.get("mapa"),
      ano: Number(p.get("ano")) || null,
      turno: Number(p.get("turno")) || null,
      vista: v.length === 3 && v.every(Number.isFinite) ? v : null,
    };
  }

  // O link guarda também a vista (centro e zoom), para compartilhar um recorte.
  function gravarHash(estado) {
    estado = estado || estadoAtual();
    const p = new URLSearchParams({ mapa: estado.mapa });
    if (estado.mapa !== "mudanca") {
      p.set("ano", estado.ano);
      p.set("turno", estado.turno);
    }
    const c = mapa.getCenter();
    p.set("v", `${c.lat.toFixed(4)},${c.lng.toFixed(4)},${mapa.getZoom()}`);
    history.replaceState(null, "", "#" + p.toString());
  }

  function combinacoes(chaveMapa) {
    const tipo = MAPAS[chaveMapa].tipo;
    return tipo === "pontos" ? config.disponivel.pontos : config.disponivel.zona_mun;
  }

  function preencheSeletores(estado) {
    const selAno = el("sel-ano");
    const selTurno = el("sel-turno");
    if (estado.mapa === "mudanca") {
      const [a, b] = config.disponivel.mudanca[0];
      selAno.innerHTML = `<option>${a} → ${b}</option>`;
      selTurno.innerHTML = "<option>2º</option>";
      selAno.disabled = selTurno.disabled = true;
      return estado;
    }
    selAno.disabled = selTurno.disabled = false;
    const combos = combinacoes(estado.mapa);
    const anos = [...new Set(combos.map(([a]) => a))].sort();
    if (!anos.includes(estado.ano)) estado.ano = anos[anos.length - 1];
    const turnos = combos.filter(([a]) => a === estado.ano).map(([, t]) => t).sort();
    if (!turnos.includes(estado.turno)) estado.turno = turnos[turnos.length - 1];
    selAno.innerHTML = anos.map((a) => `<option value="${a}">${a}</option>`).join("");
    selTurno.innerHTML = turnos.map((t) => `<option value="${t}">${t}º</option>`).join("");
    selAno.value = estado.ano;
    selTurno.value = estado.turno;
    return estado;
  }

  function estadoAtual() {
    return { mapa: el("sel-mapa").value, ano: Number(el("sel-ano").value), turno: Number(el("sel-turno").value) };
  }

  // ==== Legenda ==============================================================

  function specLegenda(estado) {
    const chave = estado.mapa;
    const spec = config.legendas[chave];
    return MAPAS[chave].legendaPorAno ? spec[String(estado.ano)] : spec;
  }

  function desenhaLegenda(spec) {
    const itens = spec.itens
      .map((i) => `<div class="leg-item"><span class="leg-cor" style="background:${i.cor}"></span>${escapa(i.rotulo)}</div>`)
      .join("");
    const semDado = `<div class="leg-item"><span class="leg-cor" style="background:${spec.sem_dado}"></span>Sem dado</div>`;
    const nota = spec.nota ? `<div class="leg-nota">${escapa(spec.nota)}</div>` : "";
    legenda.getContainer().innerHTML = `<div class="leg-titulo">${escapa(spec.titulo)}</div>${itens}${semDado}${nota}`;
  }

  // ==== Camadas ===============================================================

  function corDaClasse(classe, paleta) {
    const cores = paleta === "seq" ? config.cores_sequenciais : config.cores_divergentes;
    return classe >= 0 ? cores[classe] : config.cor_sem_dado;
  }

  const raioPonto = () => Math.max(1.5, Math.min(7, (mapa.getZoom() - 3) * 0.9));

  function mostraTooltip(latlng, html) {
    tooltip.setLatLng(latlng).setContent(html);
    if (!mapa.hasLayer(tooltip)) tooltip.addTo(mapa);
  }
  const escondeTooltip = () => tooltip.remove();

  function maisVotado(venc, candidatos, ano) {
    const conhecidos = [config.nr_pt, config.nr_bolsonaro[String(ano)]];
    if (venc == null || conhecidos.includes(venc)) return "";
    return `<br>Mais votado: ${escapa(candidatos[String(venc)] || venc)}`;
  }

  function linhaPresidente(pt, bo, ano) {
    return `${escapa(config.nome_pt[String(ano)])}: <b>${fmtPct(pt)}</b> · Bolsonaro: <b>${fmtPct(bo)}</b>`;
  }

  async function camadaPontos(estado) {
    const { ano, turno } = estado;
    const spec = MAPAS[estado.mapa];
    const [geo, res] = await Promise.all([carrega(`locais_${ano}.json`), carrega(`locais_${ano}_t${turno}.json`)]);
    const renderer = L.canvas({ padding: 0.5, tolerance: 3 });
    const grupo = L.featureGroup();
    const raio = raioPonto();
    const classes = res[spec.classe];

    for (let i = 0; i < geo.id.length; i++) {
      const marcador = L.circleMarker([geo.lat[i], geo.lon[i]], {
        renderer,
        radius: raio,
        stroke: false,
        fillColor: corDaClasse(classes[i], spec.cores),
        fillOpacity: 0.85,
      });
      marcador._i = i;
      grupo.addLayer(marcador);
    }

    const texto = (i) => {
      const cabecalho = `<b>${escapa(geo.nome[i] || geo.id[i])}</b><br>${escapa(geo.municipios[geo.mun[i]])}<br>`;
      if (estado.mapa === "pontos_branco_nulo")
        return cabecalho + `Brancos e nulos: <b>${fmtPct(res.bn[i])}</b><br>Comparecimento: ${fmtNum(res.comp[i])} votos`;
      if (estado.mapa === "pontos_abstencao")
        return cabecalho + `Abstenção: <b>${fmtPct(res.abst[i])}</b><br>Inscritos: ${fmtNum(res.aptos[i])}`;
      return cabecalho + linhaPresidente(res.pt[i], res.bo[i], ano) + maisVotado(res.venc[i], res.candidatos, ano);
    };
    grupo.on("mouseover", (e) => mostraTooltip(e.layer.getLatLng(), texto(e.layer._i)));
    grupo.on("mouseout", escondeTooltip);
    grupo._ajustaRaio = () => {
      const r = raioPonto();
      grupo.eachLayer((l) => l.setRadius(r));
    };
    return grupo;
  }

  async function camadaPoligonos(estado) {
    const ehMudanca = estado.mapa === "mudanca";
    const [anoAntes, anoDepois] = ehMudanca ? config.disponivel.mudanca[0] : [null, estado.ano];
    const arquivoDados = ehMudanca
      ? `mudanca_${anoAntes}_${anoDepois}_t2.json`
      : `zona_mun_${estado.ano}_t${estado.turno}.json`;
    const [geo, res] = await Promise.all([carrega(`zona_mun_${anoDepois}_geo.json`), carrega(arquivoDados)]);
    const dados = res.dados;

    const estilo = (f) => {
      const d = dados[f.properties.id];
      const virou = ehMudanca && d && d[4] === 1;
      return {
        fillColor: corDaClasse(d ? d[0] : -1, "div"),
        fillOpacity: 0.85,
        color: virou ? "#1f1f1f" : "#ffffff",
        weight: virou ? 1.6 : 0.4,
      };
    };

    const texto = (p) => {
      const d = dados[p.id];
      let html = `<b>${escapa(p.rot)}</b><br>`;
      if (!d) return html + "Sem dado";
      if (ehMudanca) {
        html +=
          `PT em ${anoAntes} (${escapa(config.nome_pt[String(anoAntes)])}): ${fmtPct(d[1])}<br>` +
          `PT em ${anoDepois} (${escapa(config.nome_pt[String(anoDepois)])}): ${fmtPct(d[2])}<br>` +
          `Variação: <b>${fmtPp(d[3])}</b>` +
          (d[4] === 1 ? "<br><b>Trocou de vencedor</b>" : "");
      } else {
        html += linhaPresidente(d[1], d[2], estado.ano) + maisVotado(d[5], res.candidatos, estado.ano) +
          `<br>Comparecimento: ${fmtNum(d[4])} votos`;
      }
      if (p.est) html += '<br><span class="tt-nota">Fronteira interna estimada (cidade com várias zonas)</span>';
      return html;
    };

    const renderer = L.canvas({ padding: 0.5 });
    const camadaGeo = L.geoJSON(geo, { renderer, style: estilo });
    camadaGeo.on("mouseover", (e) => {
      e.layer.setStyle({ weight: 2.2, color: "#1f1f1f" });
      mostraTooltip(e.latlng, texto(e.layer.feature.properties));
    });
    camadaGeo.on("mousemove", (e) => tooltip.setLatLng(e.latlng));
    camadaGeo.on("mouseout", (e) => {
      camadaGeo.resetStyle(e.layer);
      escondeTooltip();
    });
    return camadaGeo;
  }

  // ==== Orquestração ==========================================================

  async function desenha() {
    const estado = preencheSeletores(estadoAtual());
    gravarHash(estado);
    const minha = ++geracao;
    el("mapa-carregando").hidden = false;
    try {
      const nova = MAPAS[estado.mapa].tipo === "pontos" ? await camadaPontos(estado) : await camadaPoligonos(estado);
      if (minha !== geracao) return; // outra seleção chegou antes
      escondeTooltip();
      if (camada) mapa.removeLayer(camada);
      camada = nova.addTo(mapa);
      desenhaLegenda(specLegenda(estado));
    } catch (erro) {
      console.error(erro);
      el("mapa-erro").textContent = "Não foi possível carregar este mapa: " + erro.message;
      el("mapa-erro").hidden = false;
      return;
    } finally {
      if (minha === geracao) el("mapa-carregando").hidden = true;
    }
    el("mapa-erro").hidden = true;
  }

  async function inicia() {
    config = await carrega("config.json");

    mapa = L.map("mapa", { preferCanvas: true, zoomControl: true }).setView([-15.8, -52], 4);
    L.tileLayer(config.tiles.url, { attribution: config.tiles.attr, maxZoom: 18 }).addTo(mapa);
    L.control.scale({ imperial: false }).addTo(mapa);
    legenda = L.control({ position: "bottomright" });
    legenda.onAdd = () => L.DomUtil.create("div", "mapa-legenda");
    legenda.addTo(mapa);
    tooltip = L.tooltip({ direction: "top", offset: [0, -6], className: "mapa-tooltip" });
    mapa.on("zoomend", () => camada && camada._ajustaRaio && camada._ajustaRaio());

    const inicial = lerHash();
    if (inicial.vista) mapa.setView([inicial.vista[0], inicial.vista[1]], inicial.vista[2]);
    mapa.on("moveend", () => gravarHash());
    if (inicial.mapa && MAPAS[inicial.mapa]) el("sel-mapa").value = inicial.mapa;
    preencheSeletores({ mapa: el("sel-mapa").value, ano: inicial.ano, turno: inicial.turno });

    el("sel-mapa").addEventListener("change", desenha);
    el("sel-ano").addEventListener("change", desenha);
    el("sel-turno").addEventListener("change", desenha);
    await desenha();
  }

  inicia().catch((erro) => {
    console.error(erro);
    el("mapa-erro").textContent = "Não foi possível iniciar o visualizador: " + erro.message;
    el("mapa-erro").hidden = false;
  });
})();
