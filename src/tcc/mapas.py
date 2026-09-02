"""Mapas interativos (folium) de ocorrências por RA cruzadas com as delegacias.

Dois mapas, ambos com um painel lateral que filtra por natureza do crime:

- :func:`mapa_coropletico` — cor do polígono da RA proporcional à *taxa* de
  ocorrências (por mil habitantes, Censo 2022);
- :func:`mapa_bolhas` — círculo no centroide da RA com raio proporcional ao
  total absoluto, com a taxa no tooltip para comparação.
"""

import json
from dataclasses import dataclass

import folium
import geopandas as gpd
import polars as pl
from branca.element import MacroElement
from jinja2 import Template

from tcc.populacao import BASE_TAXA

# Prefixo do eixo "PRODUTIVIDADE POLICIAL". Tráfico, uso/porte de drogas,
# posse de arma e localização de veículo não são vitimização: o registro nasce
# da atuação policial, não da vítima procurando a delegacia. Somá-los à taxa
# mediria policiamento — e, num trabalho sobre distribuição de delegacias, isso
# é circular. Ficam disponíveis no painel, apenas desmarcados por padrão.
EIXO_FORA_DA_TAXA = "4."


@dataclass(frozen=True)
class DadosMapa:
    """Séries agregadas que alimentam o painel de filtros dos dois mapas."""

    crime_data: dict[str, dict[str, int]]
    ra_names: dict[str, str]
    n_dps: dict[str, int]
    naturezas: list[str]
    populacao: dict[str, int]
    grupos: list[dict]
    padrao: list[str]


def preparar_dados(ssp_dp: pl.DataFrame) -> DadosMapa:
    """Agrega o total de ocorrências por RA e natureza."""
    crime_data: dict[str, dict[str, int]] = {}
    for row in (
        ssp_dp.group_by("cd_subdist", "natureza")
        .agg(pl.col("total").cast(pl.Int64, strict=False).sum().alias("t"))
        .to_dicts()
    ):
        crime_data.setdefault(row["cd_subdist"], {})[row["natureza"]] = row["t"] or 0

    ra_names = {
        r["cd_subdist"]: r["ra"]
        for r in ssp_dp.select(["cd_subdist", "ra"]).unique().to_dicts()
    }
    n_dps = {
        r["cd_subdist"]: int(r["n_delegacias"] or 0)
        for r in ssp_dp.select(["cd_subdist", "n_delegacias"]).unique().to_dicts()
    }
    naturezas = sorted(ssp_dp["natureza"].drop_nulls().unique().to_list())
    grupos = _agrupar_por_eixo(ssp_dp)
    padrao = [
        n for g in grupos if not g["fora_taxa"] for n in g["naturezas"]
    ]
    populacao = {
        r["cd_subdist"]: int(r["populacao"])
        for r in ssp_dp.select(["cd_subdist", "populacao"]).unique().to_dicts()
        if r["populacao"] is not None
    }

    return DadosMapa(crime_data, ra_names, n_dps, naturezas, populacao, grupos, padrao)


def _agrupar_por_eixo(ssp_dp: pl.DataFrame) -> list[dict]:
    """Naturezas agrupadas pelo eixo da SSP-DF, na ordem em que a fonte os numera.

    O rótulo do eixo vem com espaçamento irregular nas planilhas (`2. C.C.P. -
    <muitos espaços> CRIMES...`), então normalizamos os espaços.
    """
    por_eixo: dict[str, set[str]] = {}
    for r in ssp_dp.select(["eixo", "natureza"]).drop_nulls().unique().to_dicts():
        eixo = " ".join(str(r["eixo"]).split())
        por_eixo.setdefault(eixo, set()).add(r["natureza"])

    return [
        {
            "eixo": eixo,
            "naturezas": sorted(nats),
            "fora_taxa": eixo.startswith(EIXO_FORA_DA_TAXA),
        }
        for eixo, nats in sorted(por_eixo.items())
    ]


# Substitui o CartoDB Positron, que passou a exigir API key.
_ESRI_CANVAS = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas"
_ESRI_ATTR = (
    'Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ | '
    'Dados: SSP-DF, IBGE (Censo 2022)'
)


def mapa_base(gdf: gpd.GeoDataFrame, zoom_start: int = 10) -> folium.Map:
    centro = gdf.geometry.union_all().centroid
    # tiles=None: passar a URL direto no Map faz o controle de camadas usá-la como rótulo.
    mapa = folium.Map(location=[centro.y, centro.x], zoom_start=zoom_start, tiles=None)
    folium.TileLayer(
        tiles=f"{_ESRI_CANVAS}/World_Light_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}",
        attr=_ESRI_ATTR,
        name="Mapa base",
        control=False,
    ).add_to(mapa)
    folium.TileLayer(
        tiles=f"{_ESRI_CANVAS}/World_Light_Gray_Reference/MapServer/tile/{{z}}/{{y}}/{{x}}",
        attr=_ESRI_ATTR,
        name="Rótulos",
        overlay=True,
        control=False,
    ).add_to(mapa)
    return mapa


def _camada_delegacias(dp_joined: gpd.GeoDataFrame, radius: int) -> folium.FeatureGroup:
    layer = folium.FeatureGroup(name="Delegacias", show=True)
    for _, row in dp_joined.iterrows():
        folium.CircleMarker(
            location=[row["lat"], row["lon"]],
            radius=radius,
            color="#1d3557",
            fill=True,
            fill_color="#457b9d",
            fill_opacity=0.9,
            weight=2,
            tooltip=row["Delegacia"],
            popup=folium.Popup(
                f"<b>{row['Delegacia']}</b><br>Região: {row['Região Administrativa']}<br>"
                f"Subdistrito: {row.get('NM_SUBDIST', '—')}",
                max_width=260,
            ),
        ).add_to(layer)
    return layer


class _CrimeFilterPanel(MacroElement):
    """Painel de filtros do mapa coroplético."""

    def __init__(self, dados: DadosMapa, gj_var: str):
        super().__init__()
        self._name = "CrimeFilterPanel"
        self.cd = json.dumps(dados.crime_data, ensure_ascii=False)
        self.rn = json.dumps(dados.ra_names, ensure_ascii=False)
        self.nd = json.dumps(dados.n_dps, ensure_ascii=False)
        self.nats = json.dumps(dados.naturezas, ensure_ascii=False)
        self.pop = json.dumps(dados.populacao, ensure_ascii=False)
        self.grupos = json.dumps(dados.grupos, ensure_ascii=False)
        self.padrao = json.dumps(dados.padrao, ensure_ascii=False)
        self.base = BASE_TAXA
        self.gj = gj_var
        self._template = Template("""
{% macro header(this, kwargs) %}
<style>
#cfp{position:fixed;top:80px;right:10px;z-index:9999;background:#fff;
  border-radius:8px;padding:10px 14px;box-shadow:0 2px 10px rgba(0,0,0,.25);
  max-height:78vh;overflow-y:auto;min-width:220px;font-family:Arial,sans-serif;font-size:12px;}
#cfp h4{margin:0 0 2px 0;font-size:13px;color:#222;}
#cfpModo{font-size:10px;color:#777;margin-bottom:6px;}
.pbtn{display:inline-block;padding:3px 8px;margin:2px 2px 6px 0;border-radius:4px;
  border:1px solid #bbb;cursor:pointer;font-size:11px;background:#f0f0f0;}
.pbtn:hover{background:#ddd;}
.pbtn.on{background:#457b9d;color:#fff;border-color:#1d3557;}
.cbr{display:flex;align-items:flex-start;margin-bottom:3px;}
.cbr input{margin:2px 5px 0 0;cursor:pointer;flex-shrink:0;}
.cbr label{cursor:pointer;font-size:11px;line-height:1.3;}
.div{border:none;border-top:1px solid #eee;margin:6px 0;}
.grp{display:flex;align-items:flex-start;margin:8px 0 3px 0;padding-top:6px;
  border-top:1px solid #eee;}
.grp:first-child{border-top:none;padding-top:0;margin-top:0;}
.grp input{margin:2px 5px 0 0;cursor:pointer;flex-shrink:0;}
.grp label{cursor:pointer;font-size:11px;font-weight:bold;color:#1d3557;line-height:1.3;}
.grpwarn{display:block;font-weight:normal;color:#9a6700;font-size:10px;}
.cbr{margin-left:14px;}
#cfpWarn{display:none;margin-top:8px;padding:6px 8px;font-size:10px;line-height:1.4;
  background:#fff8e5;border-left:3px solid #d4a72c;border-radius:4px;color:#7a5c00;}
#cfpSum{display:none;margin-top:8px;padding:8px 10px;
  background:#f7f9fc;border-radius:6px;border-left:3px solid #457b9d;}
#cfpGrand{font-size:13px;font-weight:bold;color:#1d3557;margin-bottom:3px;}
#cfpLines{font-size:11px;color:#444;line-height:1.7;}
</style>
{% endmacro %}

{% macro html(this, kwargs) %}
<div id="cfp">
  <h4>Natureza do crime</h4>
  <div id="cfpModo">Cor: taxa por {{ this.base }} habitantes</div>
  <button class="pbtn on" id="btnPad"  onclick="cfpPadrao()">Crimes (padrão)</button>
  <button class="pbtn"    id="btnAll"  onclick="cfpAll()">Todos</button>
  <button class="pbtn"    id="btnNone" onclick="cfpNone()">Limpar</button>
  <hr class="div">
  <div id="cfpCbs"></div>
  <div id="cfpWarn"></div>
  <div id="cfpSum">
    <div id="cfpGrand"></div>
    <div id="cfpLines"></div>
  </div>
</div>
{% endmacro %}

{% macro script(this, kwargs) %}
var _cd   = {{ this.cd }};
var _rn   = {{ this.rn }};
var _nd   = {{ this.nd }};
var _nats = {{ this.nats }};
var _pop  = {{ this.pop }};
var _grupos = {{ this.grupos }};
var _padrao = {{ this.padrao }};
var _base = {{ this.base }};
var _gj   = {{ this.gj }};

// Naturezas que a taxa padrão não inclui (eixo de produtividade policial).
var _foraTaxa = {};
_grupos.forEach(function(g){
  if(g.fora_taxa) g.naturezas.forEach(function(n){ _foraTaxa[n]=true; });
});

// Total geral por RA (soma de todas as naturezas, fixo)
var _raTotal = {};
Object.keys(_cd).forEach(function(cd){
  var s=0; Object.keys(_cd[cd]).forEach(function(n){ s+=(_cd[cd][n]||0); });
  _raTotal[cd]=s;
});

// Ocorrências por _base habitantes. Sem população conhecida não há taxa.
function _taxa(total,cd){
  var p=_pop[cd];
  return p ? total*_base/p : null;
}
function _fmtN(v){return v.toLocaleString('pt-BR');}
function _fmtT(v){
  return v==null ? '—'
    : v.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})
      +' /'+_fmtN(_base)+' hab.';
}

(function init(){
  var c=document.getElementById('cfpCbs');
  if(!c){setTimeout(init,100);return;}
  _grupos.forEach(function(g,gi){
    var h=document.createElement('div'); h.className='grp';
    var gcb=document.createElement('input');
    gcb.type='checkbox'; gcb.id='g'+gi;
    gcb.onchange=function(){cfpGrupo(gi,gcb.checked);};
    var gl=document.createElement('label'); gl.htmlFor='g'+gi;
    gl.appendChild(document.createTextNode(g.eixo));
    if(g.fora_taxa){
      var w=document.createElement('span'); w.className='grpwarn';
      w.textContent='registro gerado pela polícia — fora da taxa por padrão';
      gl.appendChild(w);
    }
    h.appendChild(gcb); h.appendChild(gl); c.appendChild(h);

    g.naturezas.forEach(function(n){
      var i=_nats.indexOf(n);
      var row=document.createElement('div'); row.className='cbr';
      var cb=document.createElement('input');
      cb.type='checkbox'; cb.id='c'+i; cb.value=n;
      cb.checked=_padrao.indexOf(n)>=0;
      cb.onchange=function(){_sync();cfpUpdate();};
      var lb=document.createElement('label'); lb.htmlFor='c'+i; lb.textContent=n;
      row.appendChild(cb); row.appendChild(lb);
      c.appendChild(row);
    });
  });
  _sync(); cfpUpdate();
})();

function _cb(i){return document.getElementById('c'+i);}
function _cbN(n){return _cb(_nats.indexOf(n));}
function _sel(){return _nats.filter(function(n,i){return _cb(i)&&_cb(i).checked;});}

function _marcar(nats,on){
  nats.forEach(function(n){var c=_cbN(n); if(c)c.checked=on;});
}
function cfpGrupo(gi,on){ _marcar(_grupos[gi].naturezas,on); _sync(); cfpUpdate(); }

function _sync(){
  // Estado dos checkboxes de eixo: marcado, vazio ou indeterminado.
  _grupos.forEach(function(g,gi){
    var m=g.naturezas.filter(function(n){var c=_cbN(n);return c&&c.checked;}).length;
    var gcb=document.getElementById('g'+gi);
    if(!gcb)return;
    gcb.checked = m===g.naturezas.length;
    gcb.indeterminate = m>0 && m<g.naturezas.length;
  });

  var sel=_sel();
  var eq=function(a){return sel.length===a.length&&a.every(function(n){return sel.indexOf(n)>=0;});};
  document.getElementById('btnPad').className=eq(_padrao)?'pbtn on':'pbtn';
  document.getElementById('btnAll').className=eq(_nats)?'pbtn on':'pbtn';
}
function cfpPadrao(){ _marcar(_nats,false); _marcar(_padrao,true); _sync(); cfpUpdate(); }
function cfpAll(){ _marcar(_nats,true); _sync(); cfpUpdate(); }
function cfpNone(){ _marcar(_nats,false); _sync(); cfpUpdate(); }

function _hex2rgb(h){return[parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)];}
function _lerp(c1,c2,t){
  var a=_hex2rgb(c1),b=_hex2rgb(c2);
  return 'rgb('+[0,1,2].map(function(i){return Math.round(a[i]+(b[i]-a[i])*t);}).join(',')+')';
}
function _color(v,mx){
  if(!mx||v==null)return'#dddddd';
  var t=Math.min(v/mx,1);
  return t<0.5?_lerp('#fff7ec','#fc8d59',t*2):_lerp('#fc8d59','#7f0000',(t-0.5)*2);
}

// Bloco de números do tooltip. O destaque é sempre a taxa — é ela que colore o
// mapa; o total absoluto entra como contexto, porque taxa alta em RA pequena
// pode vir de pouquíssimas ocorrências.
function _tipTotais(sel,nats,cd,filtered,raGrand,lines){
  if(!sel.length) return '<br><span style="color:#888">Nenhuma natureza selecionada</span>';
  var ctx = function(v){return '<span style="color:#888"> ('+_fmtN(v)+' ocorr.)</span>';};
  if(sel.length===nats.length)
    return '<br><b>Taxa geral da RA: '+_fmtT(_taxa(raGrand,cd))+'</b>'+ctx(raGrand);
  var rotulo = sel.length===1 ? sel[0] : 'Taxa filtrada ('+sel.length+' naturezas)';
  var t='<br><b>'+rotulo+': '+_fmtT(_taxa(filtered,cd))+'</b>'+ctx(filtered);
  if(lines.length) t+='<hr style="margin:3px 0">'+lines.join('<br>');
  t+='<br><span style="color:#888">Taxa geral da RA: '+_fmtT(_taxa(raGrand,cd))+ctx(raGrand)+'</span>';
  return t;
}

function cfpUpdate(){
  var sel=_sel();

  // Aviso quando a seleção inclui naturezas de produtividade policial: a taxa
  // deixa de medir só vitimização.
  var extras=sel.filter(function(n){return _foraTaxa[n];});
  var warn=document.getElementById('cfpWarn');
  if(warn){
    if(extras.length){
      warn.innerHTML='A taxa inclui '+extras.length+' natureza(s) de <b>produtividade '
        +'policial</b>, cujo registro nasce da atuação da polícia. O valor deixa de '
        +'medir apenas vitimização.';
      warn.style.display='block';
    } else { warn.style.display='none'; }
  }

  // Grand total por natureza (soma sobre todas as RAs) — para o painel
  var natTotals={}, grandTotal=0;
  sel.forEach(function(n){
    var s=0;
    Object.keys(_cd).forEach(function(cd){ s+=(_cd[cd][n]||0); });
    natTotals[n]=s; grandTotal+=s;
  });

  // População do DF considerada: só as RAs com dados da SSP-DF, senão a taxa
  // agregada ficaria diluída por RAs sem ocorrências registradas.
  var popDF=0;
  Object.keys(_cd).forEach(function(cd){ popDF+=(_pop[cd]||0); });

  var sumDiv=document.getElementById('cfpSum');
  if(sel.length>0){
    document.getElementById('cfpGrand').textContent=
      'Taxa DF: '+_fmtT(popDF?grandTotal*_base/popDF:null);
    var html='<span style="color:#666">Total DF: '+_fmtN(grandTotal)+' ocorr.</span><br>';
    if(sel.length>1) sel.forEach(function(n){ html+=n+': <b>'+_fmtN(natTotals[n])+'</b><br>'; });
    document.getElementById('cfpLines').innerHTML=html;
    sumDiv.style.display='block';
  } else {
    sumDiv.style.display='none';
  }

  // Escala de cor: máximo da taxa filtrada entre as RAs.
  var mx=0;
  Object.keys(_cd).forEach(function(cd){
    var s=0; sel.forEach(function(n){s+=(_cd[cd][n]||0);});
    var t=_taxa(s,cd); if(t!=null&&t>mx)mx=t;
  });

  _gj.eachLayer(function(layer){
    if(!layer.feature)return;
    var cd   = layer.feature.properties.CD_SUBDIST;
    var nm   = layer.feature.properties.NM_SUBDIST;
    var data = _cd[cd]||{};
    var raGrand = _raTotal[cd]||0;

    var filtered=0, lines=[];
    sel.forEach(function(n){
      var v=data[n]||0; filtered+=v;
      if(sel.length>1&&v>0) lines.push('<span style="color:#555">'+n+':</span> <b>'+v+'</b>');
    });

    var taxaF=_taxa(filtered,cd);

    layer.setStyle({
      // Sem população conhecida a RA fica cinza: não há taxa para representar.
      fillColor: sel.length&&taxaF!=null?_color(taxaF,mx):'#dddddd',
      color:'#333', weight:0.5,
      fillOpacity: sel.length&&taxaF!=null?0.75:0.3,
    });

    var tip='<b>'+nm+'</b><br><span style="color:#666">'+(_rn[cd]||'')+'</span>';
    tip+='<br>Nº delegacias: '+(_nd[cd]||0);
    tip+='<br>População: '+(_pop[cd]?_fmtN(_pop[cd]):'—');
    tip+=_tipTotais(sel,_nats,cd,filtered,raGrand,lines);

    layer.bindTooltip(tip,{sticky:true,direction:'right'});
  });
}
{% endmacro %}
""")


class _BubbleFilterPanel(MacroElement):
    """Painel de filtros do mapa de bolhas."""

    def __init__(self, dados: DadosMapa, centroids: dict[str, dict], map_var: str):
        super().__init__()
        self._name = "BubbleFilterPanel"
        self.cd = json.dumps(dados.crime_data, ensure_ascii=False)
        self.rn = json.dumps(dados.ra_names, ensure_ascii=False)
        self.nd = json.dumps(dados.n_dps, ensure_ascii=False)
        self.nats = json.dumps(dados.naturezas, ensure_ascii=False)
        self.pop = json.dumps(dados.populacao, ensure_ascii=False)
        self.grupos = json.dumps(dados.grupos, ensure_ascii=False)
        self.padrao = json.dumps(dados.padrao, ensure_ascii=False)
        self.base = BASE_TAXA
        self.centroids = json.dumps(centroids, ensure_ascii=False)
        self.map_var = map_var
        self._template = Template("""
{% macro header(this, kwargs) %}
<style>
#bfp{position:fixed;top:80px;right:10px;z-index:9999;background:#fff;
  border-radius:8px;padding:10px 14px;box-shadow:0 2px 10px rgba(0,0,0,.25);
  max-height:78vh;overflow-y:auto;min-width:220px;font-family:Arial,sans-serif;font-size:12px;}
#bfp h4{margin:0 0 6px 0;font-size:13px;color:#222;}
.pbtn2{display:inline-block;padding:3px 8px;margin:2px 2px 6px 0;border-radius:4px;
  border:1px solid #bbb;cursor:pointer;font-size:11px;background:#f0f0f0;}
.pbtn2:hover{background:#ddd;}
.pbtn2.on{background:#e63946;color:#fff;border-color:#c1121f;}
.cbr2{display:flex;align-items:flex-start;margin-bottom:3px;}
.cbr2 input{margin:2px 5px 0 0;cursor:pointer;flex-shrink:0;}
.cbr2 label{cursor:pointer;font-size:11px;line-height:1.3;}
.div2{border:none;border-top:1px solid #eee;margin:6px 0;}
.grp2{display:flex;align-items:flex-start;margin:8px 0 3px 0;padding-top:6px;
  border-top:1px solid #eee;}
.grp2:first-child{border-top:none;padding-top:0;margin-top:0;}
.grp2 input{margin:2px 5px 0 0;cursor:pointer;flex-shrink:0;}
.grp2 label{cursor:pointer;font-size:11px;font-weight:bold;color:#c1121f;line-height:1.3;}
.grpwarn2{display:block;font-weight:normal;color:#9a6700;font-size:10px;}
.cbr2{margin-left:14px;}
#bfpWarn{display:none;margin-top:8px;padding:6px 8px;font-size:10px;line-height:1.4;
  background:#fff8e5;border-left:3px solid #d4a72c;border-radius:4px;color:#7a5c00;}
#bfpSum{display:none;margin-top:8px;padding:8px 10px;
  background:#fff5f5;border-radius:6px;border-left:3px solid #e63946;}
#bfpGrand{font-size:13px;font-weight:bold;color:#c1121f;margin-bottom:3px;}
#bfpLines{font-size:11px;color:#444;line-height:1.7;}
</style>
{% endmacro %}

{% macro html(this, kwargs) %}
<div id="bfp">
  <h4>Natureza do crime</h4>
  <button class="pbtn2 on" id="bPad"  onclick="bfPadrao()">Crimes (padrão)</button>
  <button class="pbtn2"    id="bAll"  onclick="bfAll()">Todos</button>
  <button class="pbtn2"    id="bNone" onclick="bfNone()">Limpar</button>
  <hr class="div2">
  <div id="bfpCbs"></div>
  <div id="bfpWarn"></div>
  <div id="bfpSum">
    <div id="bfpGrand"></div>
    <div id="bfpLines"></div>
  </div>
</div>
{% endmacro %}

{% macro script(this, kwargs) %}
var _bcd   = {{ this.cd }};
var _brn   = {{ this.rn }};
var _bnd   = {{ this.nd }};
var _bnats = {{ this.nats }};
var _bpop  = {{ this.pop }};
var _bgrupos = {{ this.grupos }};
var _bpadrao = {{ this.padrao }};
var _bbase = {{ this.base }};

var _bForaTaxa = {};
_bgrupos.forEach(function(g){
  if(g.fora_taxa) g.naturezas.forEach(function(n){ _bForaTaxa[n]=true; });
});
var _bcen  = {{ this.centroids }};

function _bTaxa(total,cd){
  var p=_bpop[cd];
  return p ? total*_bbase/p : null;
}
function _bFmtN(v){return v.toLocaleString('pt-BR');}
function _bFmtT(v){
  return v==null ? '—'
    : v.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})
      +' /'+_bFmtN(_bbase)+' hab.';
}
var _bmap  = {{ this.map_var }};

var MIN_R = 4, MAX_R = 38;
var _bubbles = {};  // cd_subdist → L.circleMarker

// Total geral fixo por RA
var _bRaTotal = {};
Object.keys(_bcd).forEach(function(cd){
  var s=0; Object.keys(_bcd[cd]).forEach(function(n){ s+=(_bcd[cd][n]||0); });
  _bRaTotal[cd]=s;
});

// Cria bolhas iniciais
(function initBubbles(){
  if(typeof L==='undefined'||!_bmap){setTimeout(initBubbles,200);return;}
  Object.keys(_bcen).forEach(function(cd){
    var c=_bcen[cd];
    var m=L.circleMarker([c.lat,c.lon],{
      radius:MIN_R, color:'#c1121f', weight:1.5,
      fillColor:'#e63946', fillOpacity:0.65,
    }).addTo(_bmap);
    _bubbles[cd]=m;
  });
  bfInitCbs();
})();

function bfInitCbs(){
  var c=document.getElementById('bfpCbs');
  if(!c){setTimeout(bfInitCbs,100);return;}
  _bgrupos.forEach(function(g,gi){
    var h=document.createElement('div'); h.className='grp2';
    var gcb=document.createElement('input');
    gcb.type='checkbox'; gcb.id='bg'+gi;
    gcb.onchange=function(){bfGrupo(gi,gcb.checked);};
    var gl=document.createElement('label'); gl.htmlFor='bg'+gi;
    gl.appendChild(document.createTextNode(g.eixo));
    if(g.fora_taxa){
      var w=document.createElement('span'); w.className='grpwarn2';
      w.textContent='registro gerado pela polícia — fora da taxa por padrão';
      gl.appendChild(w);
    }
    h.appendChild(gcb); h.appendChild(gl); c.appendChild(h);

    g.naturezas.forEach(function(n){
      var i=_bnats.indexOf(n);
      var row=document.createElement('div'); row.className='cbr2';
      var cb=document.createElement('input');
      cb.type='checkbox'; cb.id='b'+i; cb.value=n;
      cb.checked=_bpadrao.indexOf(n)>=0;
      cb.onchange=function(){_bSync();bfUpdate();};
      var lb=document.createElement('label'); lb.htmlFor='b'+i; lb.textContent=n;
      row.appendChild(cb); row.appendChild(lb);
      c.appendChild(row);
    });
  });
  _bSync(); bfUpdate();
}

function _bcb(i){return document.getElementById('b'+i);}
function _bcbN(n){return _bcb(_bnats.indexOf(n));}
function _bSel(){return _bnats.filter(function(n,i){return _bcb(i)&&_bcb(i).checked;});}

function _bMarcar(nats,on){
  nats.forEach(function(n){var c=_bcbN(n); if(c)c.checked=on;});
}
function bfGrupo(gi,on){ _bMarcar(_bgrupos[gi].naturezas,on); _bSync(); bfUpdate(); }

function _bSync(){
  _bgrupos.forEach(function(g,gi){
    var m=g.naturezas.filter(function(n){var c=_bcbN(n);return c&&c.checked;}).length;
    var gcb=document.getElementById('bg'+gi);
    if(!gcb)return;
    gcb.checked = m===g.naturezas.length;
    gcb.indeterminate = m>0 && m<g.naturezas.length;
  });
  var sel=_bSel();
  var eq=function(a){return sel.length===a.length&&a.every(function(n){return sel.indexOf(n)>=0;});};
  document.getElementById('bPad').className=eq(_bpadrao)?'pbtn2 on':'pbtn2';
  document.getElementById('bAll').className=eq(_bnats)?'pbtn2 on':'pbtn2';
}
function bfPadrao(){ _bMarcar(_bnats,false); _bMarcar(_bpadrao,true); _bSync(); bfUpdate(); }
function bfAll(){ _bMarcar(_bnats,true); _bSync(); bfUpdate(); }
function bfNone(){ _bMarcar(_bnats,false); _bSync(); bfUpdate(); }

// Aqui o raio é o volume absoluto, então o destaque é o total; a taxa entra
// logo abaixo para permitir comparar com o coroplético.
function _bTipTotais(sel,nats,cd,filtered,raGrand,lines){
  if(!sel.length) return '<br><span style="color:#888">Nenhuma natureza selecionada</span>';
  var taxaLinha = function(v){
    return '<br><span style="color:#888">Taxa: '+_bFmtT(_bTaxa(v,cd))+'</span>';
  };
  if(sel.length===nats.length)
    return '<br><b>Total geral da RA: '+_bFmtN(raGrand)+'</b>'+taxaLinha(raGrand);
  var rotulo = sel.length===1 ? sel[0] : 'Total filtrado ('+sel.length+' naturezas)';
  var t='<br><b>'+rotulo+': '+_bFmtN(filtered)+'</b>'+taxaLinha(filtered);
  if(lines.length) t+='<hr style="margin:3px 0">'+lines.join('<br>');
  t+='<br><span style="color:#888">Total geral da RA: '+_bFmtN(raGrand)
    +' &middot; taxa '+_bFmtT(_bTaxa(raGrand,cd))+'</span>';
  return t;
}

function bfUpdate(){
  var sel=_bSel();

  var extras=sel.filter(function(n){return _bForaTaxa[n];});
  var warn=document.getElementById('bfpWarn');
  if(warn){
    if(extras.length){
      warn.innerHTML='Inclui '+extras.length+' natureza(s) de <b>produtividade policial</b>, '
        +'cujo registro nasce da atuação da polícia.';
      warn.style.display='block';
    } else { warn.style.display='none'; }
  }

  // Painel: totais por natureza sobre todo DF
  var natTotals={}, grandTotal=0;
  sel.forEach(function(n){
    var s=0; Object.keys(_bcd).forEach(function(cd){ s+=(_bcd[cd][n]||0); });
    natTotals[n]=s; grandTotal+=s;
  });
  var sumDiv=document.getElementById('bfpSum');
  if(sel.length>0){
    document.getElementById('bfpGrand').textContent='Total DF: '+grandTotal.toLocaleString('pt-BR');
    var html='';
    if(sel.length>1) sel.forEach(function(n){ html+=n+': <b>'+natTotals[n].toLocaleString('pt-BR')+'</b><br>'; });
    document.getElementById('bfpLines').innerHTML=html;
    sumDiv.style.display='block';
  } else { sumDiv.style.display='none'; }

  // Escala de raio
  var mx=0;
  Object.keys(_bcd).forEach(function(cd){
    var s=0; sel.forEach(function(n){s+=(_bcd[cd][n]||0);}); if(s>mx)mx=s;
  });

  // Atualiza bolhas
  Object.keys(_bubbles).forEach(function(cd){
    var data=_bcd[cd]||{};
    var raGrand=_bRaTotal[cd]||0;
    var filtered=0, lines=[];
    sel.forEach(function(n){
      var v=data[n]||0; filtered+=v;
      if(sel.length>1&&v>0) lines.push('<span style="color:#555">'+n+':</span> <b>'+v+'</b>');
    });

    var r = sel.length && mx ? MIN_R + Math.sqrt(filtered/mx)*(MAX_R-MIN_R) : MIN_R;
    _bubbles[cd].setRadius(r);
    _bubbles[cd].setStyle({
      fillOpacity: sel.length && filtered>0 ? 0.65 : 0.15,
      opacity:     sel.length && filtered>0 ? 1 : 0.3,
    });

    var nm=_bcen[cd].nm;
    var tip='<b>'+nm+'</b><br><span style="color:#666">'+(_brn[cd]||'')+'</span>';
    tip+='<br>Nº delegacias: '+(_bnd[cd]||0);
    tip+='<br>População: '+(_bpop[cd]?_bFmtN(_bpop[cd]):'—');
    tip+=_bTipTotais(sel,_bnats,cd,filtered,raGrand,lines);
    _bubbles[cd].bindTooltip(tip,{sticky:true,direction:'right'});
  });
}
{% endmacro %}
""")


def mapa_coropletico(
    gdf: gpd.GeoDataFrame,
    gdf_ra: gpd.GeoDataFrame,
    dp_joined: gpd.GeoDataFrame,
    dados: DadosMapa,
) -> folium.Map:
    """Mapa com a RA colorida conforme a taxa de ocorrências filtrada."""
    mapa = mapa_base(gdf)

    geojson_layer = folium.GeoJson(
        gdf_ra,
        style_function=lambda _: {
            "fillColor": "#dddddd",
            "color": "#333",
            "weight": 0.5,
            "fillOpacity": 0.6,
        },
        name="Crimes por RA",
    )
    geojson_layer.add_to(mapa)

    _camada_delegacias(dp_joined, radius=6).add_to(mapa)
    folium.LayerControl(collapsed=False).add_to(mapa)
    _CrimeFilterPanel(dados, geojson_layer.get_name()).add_to(mapa)

    return mapa


def mapa_bolhas(
    gdf: gpd.GeoDataFrame,
    gdf_ra: gpd.GeoDataFrame,
    dp_joined: gpd.GeoDataFrame,
    dados: DadosMapa,
    centroids: dict[str, dict],
) -> folium.Map:
    """Mapa com uma bolha por RA, de raio proporcional ao total filtrado."""
    mapa = mapa_base(gdf)

    folium.GeoJson(
        gdf_ra,
        style_function=lambda _: {
            "fillColor": "#eeeeee",
            "color": "#aaaaaa",
            "weight": 0.5,
            "fillOpacity": 0.4,
        },
        name="Regiões",
    ).add_to(mapa)

    _camada_delegacias(dp_joined, radius=5).add_to(mapa)
    folium.LayerControl(collapsed=False).add_to(mapa)
    _BubbleFilterPanel(dados, centroids, mapa.get_name()).add_to(mapa)

    return mapa
