# /// script
# requires-python = ">=3.13"
# dependencies = ["playwright>=1.55", "polars>=1.30", "openpyxl>=3.1", "pypdf>=5.0"]
# ///
"""Baixa, com Playwright, os balanços criminais do Distrito Federal e de cada
Região Administrativa da página "Dados por Região Administrativa" da SSP-DF.

A página lista, para o DF e para cada RA, os anos com links para PDF e/ou XLS (até 2017 só
há PDF; em 2018 há dois PDFs). Os arquivos vão para
`data/ssp_df_ra_bruto/<ano>/<ra>.<ext>` e cada link vira uma linha em
`data/ssp_df_ra_bruto/manifesto.csv`.

O site tem vários links errados, então nada é confiado ao rótulo do link:
- o tipo (pdf/xlsx/xls) vem dos bytes baixados (o "PDF" de Brasília 2023 é um .xlsx);
- o ano é lido do conteúdo do arquivo; se o ano esperado não aparece (ex.: o
  "XLS 2024" de Águas Claras é a planilha de 2022), o arquivo não é salvo e a
  linha fica com `ano_confere = false`;
- arquivos de mesmo conteúdo em pares RA/ano diferentes ficam marcados em
  `conteudo_duplicado` (ex.: Sobradinho I e II em 2015), para revisão manual.

A coluna `tem_excel` diz quais pares RA/ano têm uma planilha válida; os demais
precisam de extração do PDF.

Script independente do projeto (dependências declaradas acima). Uso:
    uv run --script scripts/baixar_ssp_ra.py [--ano-inicial 2015] [--ano-final 2026]
        [--so-listar] [--refazer]

Na primeira vez, instale o navegador:
    uvx playwright install chromium
"""

import argparse
import hashlib
import io
import re
import unicodedata
from pathlib import Path

import openpyxl
import polars as pl
from playwright.sync_api import APIRequestContext, sync_playwright
from pypdf import PdfReader

SSP_DF_RA_BRUTO_DIR = Path(__file__).resolve().parents[1] / "data" / "ssp_df_ra_bruto"

URL = "https://www.ssp.df.gov.br/dados-por-regiao-administrativa"
BASE = "https://www.ssp.df.gov.br"

EMOJI_RA = "🏙"
EMOJI_ANO = "🗓"

# Percorre o conteúdo da página em ordem e devolve uma sequência de tokens:
# textos, links para /documents/ e âncoras de seção (#DF, #RAs, #RISPS...).
_JS_TOKENS = """
() => {
  const raiz = document.querySelector('#conteudo') ?? document.body;
  const walker = document.createTreeWalker(
    raiz, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  const tokens = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.nodeType === Node.TEXT_NODE) {
      if (node.parentElement.closest('a[href*="/documents/"]')) continue;
      const v = node.textContent.trim();
      if (v) tokens.push({t: 'texto', v});
    } else if (node.tagName === 'A') {
      if (node.id) tokens.push({t: 'ancora', v: node.id});
      const href = node.getAttribute('href') ?? '';
      if (href.includes('/documents/')) {
        tokens.push({t: 'link', v: href, rotulo: node.textContent.trim()});
      }
    }
  }
  return tokens;
}
"""

_MAGIC = {b"%PDF": "pdf", b"PK\x03\x04": "xlsx", b"\xd0\xcf\x11\xe0": "xls"}


def slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")


def extrair_links(tokens: list[dict]) -> list[dict]:
    """Associa cada link das seções do DF e das RAs à RA e ao ano que o precedem.

    O DF entra como uma "RA" a mais, de nome "DISTRITO FEDERAL"."""
    links = []
    secao = None
    ra = ano = None
    modo = None  # "ra" enquanto lê o nome da RA, "ano" enquanto procura o ano
    buffer = ""

    for tok in tokens:
        if tok["t"] == "ancora":
            secao = tok["v"]
            continue
        if secao not in ("DF", "RAs"):
            continue

        if tok["t"] == "link":
            links.append({"ra": ra, "ano": ano, "rotulo": tok["rotulo"], "href": tok["v"]})
            continue

        # Separa o texto nos emojis, que marcam início de RA e de ano.
        for parte in re.split(f"({EMOJI_RA}|{EMOJI_ANO})", tok["v"]):
            if parte == EMOJI_RA:
                modo, buffer = "ra", ""
            elif parte == EMOJI_ANO:
                if modo == "ra":
                    ra = buffer.replace("️", "").strip(" :")
                modo, buffer, ano = "ano", "", None
            elif modo == "ra":
                buffer += " " + parte
            elif modo == "ano":
                buffer += parte
                if "Agregado" in buffer:
                    ano, modo = "agregado", None
                elif m := re.search(r"\b(20\d\d)\b", buffer):
                    ano, modo = int(m.group(1)), None

    for link in links:
        link["ra"] = re.sub(r"\s+", " ", link["ra"] or "").strip()
    return links


def baixar(request: APIRequestContext, href: str) -> tuple[bytes, str]:
    resp = request.get(BASE + href, timeout=120_000)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status} em {href}")
    corpo = resp.body()
    ext = next((e for magic, e in _MAGIC.items() if corpo.startswith(magic)), "bin")
    return corpo, ext


def anos_no_conteudo(corpo: bytes, ext: str) -> set[int]:
    """Anos citados no cabeçalho do arquivo (primeiras linhas / primeira página)."""
    try:
        if ext == "xlsx":
            ws = openpyxl.load_workbook(io.BytesIO(corpo), read_only=True).active
            texto = " ".join(
                str(c) for row in ws.iter_rows(max_row=8, values_only=True) for c in row if c
            )
        elif ext == "pdf":
            texto = PdfReader(io.BytesIO(corpo)).pages[0].extract_text() or ""
        else:
            return set()
    except Exception:  # noqa: BLE001 — arquivo ilegível: não dá para checar
        return set()
    return {int(a) for a in re.findall(r"\b(20[12]\d)\b", texto)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ano-inicial", type=int, default=2015)
    parser.add_argument("--ano-final", type=int, default=2026)
    parser.add_argument("--so-listar", action="store_true", help="só lista os links, sem baixar")
    parser.add_argument("--refazer", action="store_true", help="sobrescreve arquivos já existentes")
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(locale="pt-BR")
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector('a[href*="/documents/"]')
        links = extrair_links(page.evaluate(_JS_TOKENS))

        links = [
            l for l in links
            if isinstance(l["ano"], int) and args.ano_inicial <= l["ano"] <= args.ano_final
        ]
        # Um mesmo link às vezes aparece duas vezes no mesmo par RA/ano.
        vistos = set()
        links = [
            l for l in links
            if (k := (l["ra"], l["ano"], l["href"])) not in vistos and not vistos.add(k)
        ]

        if args.so_listar:
            for l in links:
                print(f"{l['ano']}  {l['ra']:<30} {l['rotulo']:<5} {l['href']}")
            browser.close()
            return

        contagem: dict[tuple, int] = {}
        for i, link in enumerate(links, 1):
            link.update(arquivo=None, tipo=None, md5=None, anos_no_arquivo=None,
                        ano_confere=None, erro=None)
            print(f"[{i}/{len(links)}] {link['ano']} {link['ra']} ({link['rotulo']})", flush=True)
            try:
                corpo, ext = baixar(context.request, link["href"])
            except Exception as e:  # noqa: BLE001 — registra e segue para o próximo
                link["erro"] = str(e)
                print(f"    erro: {e}")
                continue

            anos = anos_no_conteudo(corpo, ext)
            link.update(
                tipo=ext,
                md5=hashlib.md5(corpo).hexdigest(),
                anos_no_arquivo=",".join(map(str, sorted(anos))),
                ano_confere=(link["ano"] in anos) if anos else None,
            )
            if link["ano_confere"] is False:
                print(f"    descartado: arquivo traz {sorted(anos)}, não {link['ano']}")
                continue

            pasta = SSP_DF_RA_BRUTO_DIR / str(link["ano"])
            base = slug(link["ra"])
            n = contagem[(link["ano"], base, ext)] = contagem.get((link["ano"], base, ext), 0) + 1
            destino = pasta / (f"{base}.{ext}" if n == 1 else f"{base}_{n}.{ext}")
            if args.refazer or not destino.exists():
                pasta.mkdir(parents=True, exist_ok=True)
                destino.write_bytes(corpo)
            link["arquivo"] = destino.relative_to(SSP_DF_RA_BRUTO_DIR).as_posix()

        browser.close()

    salvo = pl.col("arquivo").is_not_null()
    manifesto = pl.DataFrame(
        links,
        schema={
            "ra": pl.String, "ano": pl.Int64, "rotulo": pl.String, "href": pl.String,
            "arquivo": pl.String, "tipo": pl.String, "md5": pl.String,
            "anos_no_arquivo": pl.String, "ano_confere": pl.Boolean, "erro": pl.String,
        },
    ).with_columns(
        conteudo_duplicado=salvo
        & (pl.struct("ra", "ano").filter(salvo).n_unique().over("md5") > 1),
        tem_excel=(salvo & pl.col("tipo").is_in(["xlsx", "xls"])).any().over("ra", "ano"),
    )
    SSP_DF_RA_BRUTO_DIR.mkdir(parents=True, exist_ok=True)
    manifesto.write_csv(SSP_DF_RA_BRUTO_DIR / "manifesto.csv")

    resumo = manifesto.group_by("ano").agg(
        ras=pl.col("ra").n_unique(),
        excel=(salvo & pl.col("tipo").is_in(["xlsx", "xls"])).sum(),
        pdf=(salvo & (pl.col("tipo") == "pdf")).sum(),
        ras_so_pdf=pl.col("ra").filter(~pl.col("tem_excel")).n_unique(),
        descartados=(pl.col("ano_confere") == False).sum(),  # noqa: E712
        duplicados=pl.col("conteudo_duplicado").sum(),
        erros=pl.col("erro").is_not_null().sum(),
    ).sort("ano")
    with pl.Config(tbl_rows=-1, fmt_str_lengths=60):
        print(resumo)
        print("Conteúdo repetido entre pares RA/ano (revisar):")
        print(
            manifesto.filter("conteudo_duplicado")
            .sort("md5", "ano", "ra")
            .select("ano", "ra", "rotulo", "arquivo")
        )


if __name__ == "__main__":
    main()
