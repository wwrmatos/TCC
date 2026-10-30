"""Roda o pipeline de dados inteiro, na ordem certa, refazendo só o que mudou.

Cada etapa é um dos scripts de `scripts/`. Como num `make`, uma etapa roda
quando falta alguma saída ou quando alguma entrada (dados, o próprio script ou
os módulos de `tcc` que ele usa) é mais nova que a saída mais antiga. Uma etapa
refeita deixa as seguintes desatualizadas, que então rodam também.

As duas etapas de download (estimativas do IBGE e balanços da SSP-DF) só rodam
quando a saída não existe ou com `--baixar`: a data dos arquivos não diz se a
fonte mudou.

Ficam de fora os scripts de um ano só (`juntar_mj_censo.py`,
`juntar_dimensoes.py`), que `consolidar_mj.py` e `consolidar_ssp.py`
substituem, e o painel (`painel.py`). As dimensões `municipio.csv`,
`unidade_federacao.csv` e `regiao.csv` em `data/processed/` são entradas, não
saídas.

Uso:
    uv run python scripts/pipeline.py                  # o que estiver desatualizado
    uv run python scripts/pipeline.py --dry-run        # só mostra o que rodaria e por quê
    uv run python scripts/pipeline.py --baixar         # baixa de novo IBGE e SSP-DF
    uv run python scripts/pipeline.py mj feminicidio   # só essas etapas, mesmo atualizadas
    uv run python scripts/pipeline.py --forcar         # tudo, menos os downloads
"""

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from tcc.paths import (
    CENSO_IBGE_CSV,
    DATA_DIR,
    MJ_CENSO_GEO_PARQUET,
    MJ_DIR,
    MUNICIPIO_CSV,
    OUTPUT_DIR,
    PLANILHA_TCC,
    POPULACAO_CSV,
    POPULACAO_ESTIMADA_CSV,
    PROCESSED_DIR,
    RAIZ,
    REGIAO_CSV,
    SETORES_GPKG,
    SSP_DF_DIR,
    SSP_FEMINICIDIO_RA_CSV,
    SSP_RA_CONSOLIDADO_CSV,
    SSP_RA_GEO_PARQUET,
    UNIDADE_FEDERACAO_CSV,
)

SCRIPTS = RAIZ / "scripts"
TCC = RAIZ / "src" / "tcc"

# Caminhos que só os scripts independentes (`uv run --script`) definem.
SSP_RA_BRUTO_DIR = DATA_DIR / "ssp_df_ra_bruto"
CTMHF_DIR = SSP_DF_DIR / "feeminicidio"
SSP_FEMINICIDIO_CASOS_CSV = PROCESSED_DIR / "ssp_df_feminicidio_casos.csv"

DIMENSOES = [MUNICIPIO_CSV, UNIDADE_FEDERACAO_CSV, REGIAO_CSV]


@dataclass
class Etapa:
    nome: str
    script: str
    saidas: list[Path]
    # Arquivos ou padrões glob (relativos à raiz) dos quais a etapa depende,
    # além do próprio script.
    entradas: list[Path | str] = field(default_factory=list)
    # Script independente, com dependências no cabeçalho (`uv run --script`).
    independente: bool = False
    # Download: não roda só porque uma entrada é mais nova.
    download: bool = False

    @property
    def caminho(self) -> Path:
        return SCRIPTS / self.script

    def comando(self) -> list[str]:
        if self.independente:
            return ["uv", "run", "--script", str(self.caminho)]
        return [sys.executable, str(self.caminho)]

    def arquivos_de_entrada(self) -> list[Path]:
        arquivos = [self.caminho]
        for e in self.entradas:
            if isinstance(e, Path):
                arquivos.append(e)
            else:
                arquivos.extend(p for p in RAIZ.glob(e) if p.is_file())
        return arquivos


def _rel(p: Path) -> str:
    return p.relative_to(RAIZ).as_posix()


ETAPAS = [
    Etapa(
        "populacao", "baixar_populacao_ibge.py",
        saidas=[POPULACAO_ESTIMADA_CSV],
        entradas=[TCC / "mj.py"],
        download=True,
    ),
    Etapa(
        "ssp-bruto", "baixar_ssp_ra.py",
        saidas=[SSP_RA_BRUTO_DIR / "manifesto.csv"],
        independente=True, download=True,
    ),
    Etapa(
        "ssp-ra", "consolidar_ssp_ra.py",
        saidas=[SSP_RA_CONSOLIDADO_CSV],
        entradas=[f"{_rel(SSP_RA_BRUTO_DIR)}/**/*"],
        independente=True,
    ),
    Etapa(
        "mj", "consolidar_mj.py",
        saidas=[MJ_CENSO_GEO_PARQUET],
        entradas=[
            f"{_rel(MJ_DIR)}/BancoVDE *.xlsx", POPULACAO_ESTIMADA_CSV, CENSO_IBGE_CSV,
            *DIMENSOES, TCC / "mj.py", TCC / "dimensoes.py", TCC / "populacao.py",
        ],
    ),
    Etapa(
        "feminicidio", "extrair_feminicidio_ctmhf.py",
        saidas=[SSP_FEMINICIDIO_RA_CSV, SSP_FEMINICIDIO_CASOS_CSV],
        entradas=[f"{_rel(CTMHF_DIR)}/*/*.pdf", MJ_CENSO_GEO_PARQUET],
        independente=True,
    ),
    Etapa(
        "ssp", "consolidar_ssp.py",
        saidas=[SSP_RA_GEO_PARQUET],
        entradas=[
            SSP_RA_CONSOLIDADO_CSV, SSP_FEMINICIDIO_RA_CSV, MJ_CENSO_GEO_PARQUET,
            POPULACAO_ESTIMADA_CSV, POPULACAO_CSV, CENSO_IBGE_CSV, SETORES_GPKG, PLANILHA_TCC,
            *DIMENSOES, TCC / "dimensoes.py", TCC / "geo.py", TCC / "mj.py",
            TCC / "populacao.py", TCC / "ssp_df.py",
        ],
    ),
    Etapa(
        "mapas", "gerar_mapas.py",
        saidas=[
            PROCESSED_DIR / "ssp_dp.csv",
            OUTPUT_DIR / "mapa_ssp_delegacias.html",
            OUTPUT_DIR / "mapa_bolhas_delegacias.html",
        ],
        entradas=[
            f"{_rel(SSP_DF_DIR)}/*.xlsx", SETORES_GPKG, PLANILHA_TCC, POPULACAO_CSV,
            TCC / "geo.py", TCC / "mapas.py", TCC / "populacao.py", TCC / "ssp_df.py",
        ],
    ),
]
POR_NOME = {e.nome: e for e in ETAPAS}


def motivo(etapa: Etapa, refeitos: set[Path], forcar: bool, baixar: bool) -> str | None:
    """Por que a etapa precisa rodar, ou `None` se está em dia. `refeitos` são as
    saídas das etapas anteriores que rodam (ou rodariam, com `--dry-run`) agora."""
    faltam = [s for s in etapa.saidas if not s.exists()]
    if faltam:
        return f"falta {_rel(faltam[0])}"
    if etapa.download:
        return "--baixar" if baixar else None
    if forcar:
        return "--forcar"
    entradas = etapa.arquivos_de_entrada()
    refeita = next((e for e in entradas if e in refeitos), None)
    if refeita:
        return f"{_rel(refeita)} será refeito"
    mais_antiga = min(s.stat().st_mtime for s in etapa.saidas)
    nova = next((e for e in entradas if e.exists() and e.stat().st_mtime > mais_antiga), None)
    if nova:
        return f"{_rel(nova)} mudou"
    return None


def main(etapas: list[str], forcar: bool, baixar: bool, dry_run: bool) -> None:
    if any(e.independente for e in ETAPAS) and not shutil.which("uv"):
        sys.exit("O uv não está no PATH; ele roda os scripts independentes.")

    escolhidas = set(etapas)
    if desconhecidas := escolhidas - POR_NOME.keys():
        sys.exit(f"Etapa desconhecida: {', '.join(sorted(desconhecidas))}. "
                 f"Opções: {', '.join(POR_NOME)}")
    refeitos: set[Path] = set()
    rodadas = []
    for etapa in ETAPAS:
        if escolhidas:
            por_que = "pedida" if etapa.nome in escolhidas else None
        else:
            por_que = motivo(etapa, refeitos, forcar, baixar)
        if por_que is None:
            print(f"[{etapa.nome}] em dia")
            continue

        print(f"[{etapa.nome}] {etapa.script} — {por_que}")
        refeitos.update(etapa.saidas)
        if dry_run:
            continue
        inicio = time.perf_counter()
        r = subprocess.run(etapa.comando(), cwd=RAIZ)
        if r.returncode:
            sys.exit(f"[{etapa.nome}] falhou (código {r.returncode}); as etapas seguintes não rodaram.")
        duracao = time.perf_counter() - inicio
        rodadas.append(etapa.nome)
        print(f"[{etapa.nome}] ok em {duracao:.0f} s\n")

    if not dry_run:
        print(f"Pronto. Rodaram: {', '.join(rodadas)}" if rodadas else "Tudo em dia.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Etapas, na ordem: " + ", ".join(POR_NOME),
    )
    parser.add_argument("etapas", nargs="*", metavar="etapa",
                        help="roda só estas etapas, atualizadas ou não")
    parser.add_argument("--forcar", action="store_true",
                        help="roda todas as etapas, menos os downloads")
    parser.add_argument("--baixar", action="store_true",
                        help="baixa de novo as estimativas do IBGE e os balanços da SSP-DF")
    parser.add_argument("--dry-run", action="store_true",
                        help="só mostra o que rodaria e por quê")
    main(**vars(parser.parse_args()))
