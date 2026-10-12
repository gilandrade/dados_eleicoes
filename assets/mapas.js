// Visualizador de mapas da página mapas.qmd.
// Lê os JSON gerados por 06_exporta_visualizador.qmd (docs/mapas/dados/).
// A classe de cor de cada feição já vem calculada em Python (src/eleicoes/cores.py);
// aqui só se pinta, monta a legenda e o tooltip.
(function () {
  "use strict";

  const BASE = "mapas/dados/";

  // `tamanho`: coluna que define a área da bolinha — votos válidos (Presidente),
  // votos totais (brancos e nulos) e inscritos (abstenção), o denominador de cada %.
  const MAPAS = {
    pontos_presidente: {
      tipo: "pontos", classe: "c_pres", cores: "div", legendaPorAno: true,
      tamanho: "val", rotuloTamanho: "votos válidos",
    },
    pontos_branco_nulo: {
      tipo: "pontos", classe: "c_bn", cores: "seq", tamanho: "comp", rotuloTamanho: "votos (total)",
    },
    pontos_abstencao: {
      tipo: "pontos", classe: "c_abst", cores: "seq", tamanho: "aptos", rotuloTamanho: "inscritos",
    },
    zona_mun_presidente: { tipo: "zona_mun", cores: "div", legendaPorAno: true },
    mudanca: { tipo: "mudanca", cores: "mud" },
    mudanca_tipo: { tipo: "mudanca", cores: "tipo", posClasse: 5 },
  };
  const ehMapaMudanca = (chave) => MAPAS[chave].tipo === "mudanca";

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
  const fmtCresc = (v) =>
    v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") +
      (Math.abs(v) / 10).toLocaleString("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
  const fmtNum = (v) => (v == null ? "—" : v.toLocaleString("pt-BR"));
  const escapa = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  let config, mapa, camada, legenda, tooltip, geracao = 0;
  // Filtros de território (bairro só nos mapas de pontos). Guardam nomes (não
  // índices): os índices mudam de um ano para outro, os nomes não.
  const filtro = { uf: "", mun: "", bairro: "" };
  let ajustarVista = false; // enquadra o recorte filtrado só quando o usuário mexe no filtro

  // ==== Estado (seletores <-> #hash) =========================================

  function lerHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const v = (p.get("v") || "").split(",").map(Number);
    return {
      mapa: p.get("mapa"),
      ano: Number(p.get("ano")) || null,
      turno: Number(p.get("turno")) || null,
      par: p.get("par"),
      uf: p.get("uf") || "",
      mun: p.get("mun") || "",
      bairro: p.get("bairro") || "",
      vista: v.length === 3 && v.every(Number.isFinite) ? v : null,
    };
  }

  // O link guarda também a vista (centro e zoom), para compartilhar um recorte.
  function gravarHash(estado) {
    estado = estado || estadoAtual();
    const p = new URLSearchParams({ mapa: estado.mapa });
    if (ehMapaMudanca(estado.mapa)) p.set("par", estado.par);
    else {
      p.set("ano", estado.ano);
      p.set("turno", estado.turno);
    }
    for (const k of ["uf", "mun", "bairro"]) if (filtro[k]) p.set(k, filtro[k]);
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
    if (ehMapaMudanca(estado.mapa)) {
      // Aqui "ano" é o par de eleições ("2018-2022-2"): o turno vem junto.
      const pares = config.disponivel.mudanca.map(([a, b, t]) => ({ id: `${a}-${b}-${t}`, a, b, t }));
      if (!pares.some((p) => p.id === estado.par)) estado.par = pares[pares.length - 1].id;
      selAno.innerHTML = pares.map((p) => `<option value="${p.id}">${p.a} → ${p.b} (${p.t}º turno)</option>`).join("");
      selAno.value = estado.par;
      const atual = pares.find((p) => p.id === estado.par);
      selTurno.innerHTML = `<option>${atual.t}º</option>`;
      selAno.disabled = false;
      selTurno.disabled = true;
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
    const mapaSel = el("sel-mapa").value;
    if (ehMapaMudanca(mapaSel)) return { mapa: mapaSel, par: el("sel-ano").value };
    return { mapa: mapaSel, ano: Number(el("sel-ano").value), turno: Number(el("sel-turno").value) };
  }

  // ==== Filtros (UF > município > bairro) ======================================

  // `ufDe`, `munDe` e `bairroDe` devolvem o nome da feição `i`. Sem `bairroDe`
  // (mapas de zonas) o seletor de bairro fica desativado.
  function preencheFiltros(n, ufDe, munDe, bairroDe) {
    const ufs = new Set(), muns = new Set(), bairros = new Set();
    for (let i = 0; i < n; i++) {
      const uf = ufDe(i);
      const mun = munDe(i);
      ufs.add(uf);
      if (filtro.uf && uf !== filtro.uf) continue;
      muns.add(mun);
      if (filtro.mun && mun !== filtro.mun) continue;
      if (bairroDe) bairros.add(bairroDe(i));
    }
    const ordena = (c) => [...c].filter(Boolean).sort((a, b) => a.localeCompare(b, "pt-BR"));
    const monta = (id, valores, chave, rotuloTodos) => {
      if (filtro[chave] && !valores.includes(filtro[chave])) filtro[chave] = "";
      el(id).innerHTML = `<option value="">${rotuloTodos}</option>` +
        valores.map((v) => `<option value="${escapa(v)}">${escapa(v)}</option>`).join("");
      el(id).value = filtro[chave];
      el(id).disabled = false;
    };
    monta("sel-uf", ordena(ufs), "uf", "Todas");
    monta("sel-mun", ordena(muns), "mun", "Todos");
    if (bairroDe) monta("sel-bairro", ordena(bairros), "bairro", "Todos");
    else el("sel-bairro").disabled = true;
  }

  // UF e município de cada zona, tirados do rótulo ("Município – UF · zona N") —
  // os mesmos nomes dos filtros dos mapas de pontos.
  function recorteZonas(geo) {
    return (geo._recorte ??= geo.features.map((f) => {
      const rot = f.properties.rot;
      const mun = rot.slice(0, rot.lastIndexOf(" · zona "));
      const corte = mun.lastIndexOf(" – ");
      return { uf: corte < 0 ? "" : mun.slice(corte + 3), mun };
    }));
  }

  const passaFiltro = (geo, i) =>
    (!filtro.uf || geo.ufs[geo.uf[i]] === filtro.uf) &&
    (!filtro.mun || geo.municipios[geo.mun[i]] === filtro.mun) &&
    (!filtro.bairro || geo.bairros[geo.bairro[i]] === filtro.bairro);

  // ==== Legenda ==============================================================

  function specLegenda(estado) {
    const chave = estado.mapa;
    const spec = config.legendas[chave];
    if (ehMapaMudanca(chave)) return spec[estado.par];
    return MAPAS[chave].legendaPorAno ? spec[String(estado.ano)] : spec;
  }

  function desenhaLegenda(spec, estado) {
    const itens = spec.itens
      .map((i) => `<div class="leg-item"><span class="leg-cor" style="background:${i.cor}"></span>${escapa(i.rotulo)}</div>`)
      .join("");
    const semDado = `<div class="leg-item"><span class="leg-cor" style="background:${spec.sem_dado}"></span>Sem dado</div>`;
    const nota = spec.nota ? `<div class="leg-nota">${escapa(spec.nota)}</div>` : "";
    const tamanho = MAPAS[estado.mapa].tamanho ? legendaTamanho(MAPAS[estado.mapa]) : "";
    legenda.getContainer().innerHTML =
      `<div class="leg-titulo">${escapa(spec.titulo)}</div>${itens}${semDado}${nota}${tamanho}`;
  }

  // Círculos de referência desenhados com o mesmo raio do mapa no zoom atual.
  function legendaTamanho(spec) {
    const raios = VALORES_LEGENDA_TAMANHO.map(raioPorValor);
    const lado = 2 * Math.ceil(Math.max(...raios)) + 2;
    const itens = VALORES_LEGENDA_TAMANHO.map((v, k) => {
      const r = raios[k];
      return `<div class="leg-item leg-tamanho">
        <svg width="${lado}" height="${lado}" aria-hidden="true">
          <circle cx="${lado / 2}" cy="${lado / 2}" r="${r}" fill="#898781" fill-opacity="0.55" stroke="#52514e" stroke-width="0.8"/>
        </svg>${fmtNum(v)}</div>`;
    }).join("");
    return `<div class="leg-titulo leg-titulo-tamanho">Tamanho: ${escapa(spec.rotuloTamanho)}</div>${itens}`;
  }

  // ==== Camadas ===============================================================

  function corDaClasse(classe, paleta) {
    const cores = paleta === "seq" ? config.cores_sequenciais
      : paleta === "tipo" ? config.cores_tipo_mudanca
      : paleta === "mud" ? config.cores_mudanca : config.cores_divergentes;
    return classe >= 0 ? cores[classe] : config.cor_sem_dado;
  }

  // Raio (px) de um local com REF_TAMANHO votos: cresce ~25% por nível de zoom.
  // Mais rápido que isso, as cidades densas viram uma massa só ao aproximar.
  const raioPonto = () => Math.min(10, 0.9 * Math.pow(1.25, mapa.getZoom() - 4));
  const RAIO_MINIMO_PX = 1;
  const RAIO_MINIMO_CONTORNO_PX = 3;

  // Símbolo proporcional: a área é proporcional ao valor (raio ∝ √valor), com
  // referência fixa — um local com REF_TAMANHO votos tem o raio base do zoom —
  // para o mesmo tamanho valer o mesmo nos dois anos e turnos. Piso e teto
  // mantêm os locais minúsculos visíveis e os gigantes (~20 mil votos) sem
  // cobrir o bairro inteiro.
  const REF_TAMANHO = 1000;
  const VALORES_LEGENDA_TAMANHO = [250, 1000, 5000];
  function raioPorValor(valor) {
    const base = raioPonto();
    const escala = valor > 0 ? Math.min(3.5, Math.max(0.35, Math.sqrt(valor / REF_TAMANHO))) : 0.35;
    return Math.max(RAIO_MINIMO_PX, base * escala);
  }

  // Contorno branco só nas bolinhas maiores: nas pequenas ele engole a cor.
  const estiloTamanho = (raio) => ({ radius: raio, weight: raio >= RAIO_MINIMO_CONTORNO_PX ? 0.6 : 0 });

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
    return `${escapa(config.nome_pt[String(ano)])}: <b>${fmtPct(pt)}</b> · ${escapa(config.nome_bolsonaro[String(ano)])}: <b>${fmtPct(bo)}</b>`;
  }

  async function camadaPontos(estado) {
    const { ano, turno } = estado;
    const spec = MAPAS[estado.mapa];
    const [geo, res] = await Promise.all([carrega(`locais_${ano}.json`), carrega(`locais_${ano}_t${turno}.json`)]);
    preencheFiltros(
      geo.id.length, (i) => geo.ufs[geo.uf[i]], (i) => geo.municipios[geo.mun[i]], (i) => geo.bairros[geo.bairro[i]]
    );
    const renderer = L.canvas({ padding: 0.5, tolerance: 3 });
    const grupo = L.featureGroup();
    const classes = res[spec.classe];
    const valores = res[spec.tamanho];

    // Maiores primeiro: os pequenos ficam por cima e continuam clicáveis.
    const ordem = geo.id.map((_, i) => i).sort((a, b) => (valores[b] ?? 0) - (valores[a] ?? 0));
    const limites = [];
    for (const i of ordem) {
      if (!passaFiltro(geo, i)) continue;
      limites.push([geo.lat[i], geo.lon[i]]);
      const marcador = L.circleMarker([geo.lat[i], geo.lon[i]], {
        renderer,
        ...estiloTamanho(raioPorValor(valores[i])),
        color: "#ffffff",
        opacity: 0.8,
        fillColor: corDaClasse(classes[i], spec.cores),
        fillOpacity: 0.8,
      });
      marcador._i = i;
      grupo.addLayer(marcador);
    }
    if (ajustarVista && limites.length) mapa.fitBounds(limites, { maxZoom: 15, padding: [30, 30] });
    ajustarVista = false;

    const texto = (i) => {
      const cabecalho = `<b>${escapa(geo.nome[i] || geo.id[i])}</b><br>${escapa(geo.municipios[geo.mun[i]])}<br>`;
      if (estado.mapa === "pontos_branco_nulo")
        return cabecalho + `Brancos e nulos: <b>${fmtPct(res.bn[i])}</b><br>Comparecimento: ${fmtNum(res.comp[i])} votos`;
      if (estado.mapa === "pontos_abstencao")
        return cabecalho + `Abstenção: <b>${fmtPct(res.abst[i])}</b><br>Inscritos: ${fmtNum(res.aptos[i])}`;
      return cabecalho + linhaPresidente(res.pt[i], res.bo[i], ano) + maisVotado(res.venc[i], res.candidatos, ano) +
        `<br>Votos válidos: ${fmtNum(res.val[i])}`;
    };
    grupo.on("mouseover", (e) => mostraTooltip(e.layer.getLatLng(), texto(e.layer._i)));
    grupo.on("mouseout", escondeTooltip);
    grupo._ajustaRaio = () => {
      grupo.eachLayer((l) => l.setStyle(estiloTamanho(raioPorValor(valores[l._i]))));
      desenhaLegenda(specLegenda(estado), estado);
    };
    return grupo;
  }

  async function camadaPoligonos(estado) {
    const ehMudanca = ehMapaMudanca(estado.mapa);
    const posClasse = MAPAS[estado.mapa].posClasse || 0;
    const paleta = MAPAS[estado.mapa].cores;
    const [anoAntes, anoDepois, turnoMud] = ehMudanca ? estado.par.split("-").map(Number) : [null, estado.ano, null];
    const arquivoDados = ehMudanca
      ? `mudanca_${anoAntes}_${anoDepois}_t${turnoMud}.json`
      : `zona_mun_${estado.ano}_t${estado.turno}.json`;
    const [geo, res] = await Promise.all([carrega(`zona_mun_${anoDepois}_geo.json`), carrega(arquivoDados)]);
    const dados = res.dados;
    const recorte = recorteZonas(geo);
    preencheFiltros(recorte.length, (i) => recorte[i].uf, (i) => recorte[i].mun, null);
    const visiveis = !filtro.uf && !filtro.mun ? geo : {
      type: "FeatureCollection",
      features: geo.features.filter((_, i) =>
        (!filtro.uf || recorte[i].uf === filtro.uf) && (!filtro.mun || recorte[i].mun === filtro.mun)),
    };

    const estilo = (f) => {
      const d = dados[f.properties.id];
      const virou = ehMudanca && d && d[4] === 1;
      return {
        fillColor: corDaClasse(d ? d[posClasse] : -1, paleta),
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
        const [aptA, aptD, ptA, ptD, boA, boD, valA, valD] = d.slice(11, 19);
        const nomePt = escapa(config.nome_pt[String(anoDepois)]);
        const nomeBo = escapa(config.nome_bolsonaro[String(anoDepois)]);
        const seta = (a, b) => `${fmtNum(a)} → ${fmtNum(b)}`;
        const permil = (n, den) => (n == null || !den ? null : Math.round((1000 * n) / den));
        const dif = (x, y) => (x == null || y == null ? null : x - y);
        const nvA = dif(aptA, ptA == null || boA == null ? null : ptA + boA);
        const nvD = dif(aptD, ptD == null || boD == null ? null : ptD + boD);
        const pctVal = (n, v) => fmtPct(permil(n, v));
        const dpBo = dif(permil(boD, valD), permil(boA, valA));
        // 3º lugar (só no 1º turno): o mais votado fora os dois, em cada ano.
        const [nr3A, nr3D, pc3A, pc3D] = d.slice(19, 23);
        const nome3 = (ano, nr) => escapa((res.candidatos[String(ano)] || {})[String(nr)] || nr);
        const linhaTerceiro = nr3A == null || nr3D == null ? "" :
          `<br>3º lugar: ${nome3(anoAntes, nr3A)} ${fmtPct(pc3A)} → ${nome3(anoDepois, nr3D)} ${fmtPct(pc3D)} ` +
          `(<b>${fmtPp(dif(pc3D, pc3A))}</b>)`;
        // d[0] é a classe em `cores_mudanca`: 0–4 Lula/Haddad, 5 empate, 6–10 Bolsonaro, 11–12 3º lugar.
        const cresceuMais = d[0] < 0 ? "" :
          d[0] > 10 ? `<br><b>${nomePt} e ${nomeBo} caíram</b>` :
          d[0] === 5 ? "<br><b>Cresceram igual</b>" :
          `<br>Cresceu mais: <b>${d[0] < 5 ? nomePt : nomeBo}</b>`;
        // Cada mapa mostra só a base que dá a sua cor: inscritos no "de onde
        // veio a mudança", votos válidos no de mudança entre eleições.
        html += (estado.mapa === "mudanca_tipo"
          ? `<span class="tt-nota">Sobre o eleitorado inscrito — inscritos ${anoAntes} → ${anoDepois}: ` +
            `${seta(aptA, aptD)} (${fmtCresc(permil(dif(aptD, aptA), aptA))})</span>` +
            `<br>${nomePt}: ${seta(ptA, ptD)} votos (<b>${fmtPp(d[6])}</b>)` +
            `<br>${nomeBo}: ${seta(boA, boD)} votos (<b>${fmtPp(d[7])}</b>)` +
            `<br>Abstenção, brancos e nulos${turnoMud === 1 ? " e outros candidatos" : ""}: ` +
            `${seta(nvA, nvD)} (<b>${fmtPp(d[8])}</b>)`
          : `<span class="tt-nota">Sobre os votos válidos — válidos ${anoAntes} → ${anoDepois}: ` +
            `${seta(valA, valD)} (${fmtCresc(permil(dif(valD, valA), valA))})</span>` +
            `<br>${nomePt}: ${seta(ptA, ptD)} votos ` +
            `(${pctVal(ptA, valA)} → ${pctVal(ptD, valD)}; <b>${fmtPp(d[3])}</b>)` +
            `<br>${nomeBo}: ${seta(boA, boD)} votos ` +
            `(${pctVal(boA, valA)} → ${pctVal(boD, valD)}; <b>${fmtPp(dpBo)}</b>)` +
            linhaTerceiro + cresceuMais) +
          (d[4] === 1 ? "<br><b>Trocou de vencedor</b>" : "");
      } else {
        html += linhaPresidente(d[1], d[2], estado.ano) + maisVotado(d[5], res.candidatos, estado.ano) +
          `<br>Comparecimento: ${fmtNum(d[4])} votos`;
      }
      if (p.est) html += '<br><span class="tt-nota">Fronteira interna estimada (cidade com várias zonas)</span>';
      return html;
    };

    const renderer = L.canvas({ padding: 0.5 });
    const camadaGeo = L.geoJSON(visiveis, { renderer, style: estilo });
    if (ajustarVista && visiveis.features.length) mapa.fitBounds(camadaGeo.getBounds(), { padding: [30, 30] });
    ajustarVista = false;
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
      const ehPontos = MAPAS[estado.mapa].tipo === "pontos";
      const nova = ehPontos ? await camadaPontos(estado) : await camadaPoligonos(estado);
      if (minha !== geracao) return; // outra seleção chegou antes
      escondeTooltip();
      if (camada) mapa.removeLayer(camada);
      camada = nova.addTo(mapa);
      desenhaLegenda(specLegenda(estado), estado);
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
    Object.assign(filtro, { uf: inicial.uf, mun: inicial.mun, bairro: inicial.bairro });
    if (inicial.mapa && MAPAS[inicial.mapa]) el("sel-mapa").value = inicial.mapa;
    preencheSeletores({ mapa: el("sel-mapa").value, ano: inicial.ano, turno: inicial.turno, par: inicial.par });

    el("sel-mapa").addEventListener("change", desenha);
    el("sel-ano").addEventListener("change", desenha);
    el("sel-turno").addEventListener("change", desenha);
    // Trocar um nível zera os de baixo; o recorte é enquadrado no mapa.
    const aoFiltrar = (chave, filhos) => () => {
      filtro[chave] = el("sel-" + (chave === "mun" ? "mun" : chave)).value;
      for (const f of filhos) filtro[f] = "";
      ajustarVista = true;
      desenha();
    };
    el("sel-uf").addEventListener("change", aoFiltrar("uf", ["mun", "bairro"]));
    el("sel-mun").addEventListener("change", aoFiltrar("mun", ["bairro"]));
    el("sel-bairro").addEventListener("change", aoFiltrar("bairro", []));
    await desenha();
  }

  inicia().catch((erro) => {
    console.error(erro);
    el("mapa-erro").textContent = "Não foi possível iniciar o visualizador: " + erro.message;
    el("mapa-erro").hidden = false;
  });
})();
