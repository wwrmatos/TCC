"""Previsão das séries mensais do painel com SARIMA.

O modelo é um SARIMA(p,d,q)(P,D,Q)₁₂ ajustado sobre `log1p` da contagem mensal
(o log estabiliza a variância das contagens e impede previsão negativa). As
ordens mudam conforme a série, então são escolhidas a cada filtro:

- `d` pelo teste KPSS (diferencia uma vez se a série não for estacionária);
- `D` pela força da sazonalidade na decomposição STL (Wang, Smith & Hyndman,
  2006): diferencia sazonalmente quando ela passa de 0,64, o mesmo critério do
  `nsdiffs` do pacote `forecast` do R;
- `p`, `q` ∈ {0, 1, 2} e `P`, `Q` ∈ {0, 1} pelo menor AIC, com `d` e `D`
  fixos (o AIC não compara modelos com diferenciações diferentes). A busca é a
  stepwise de Hyndman & Khandakar (2008), a do `auto.arima`: parte de quatro
  modelos e anda para o vizinho (±1 numa ordem) enquanto o AIC cair.

Meses sem dado (`None`) entram como faltantes: o filtro de Kalman do SARIMAX
os atravessa sem precisar imputar. A qualidade é medida fora da amostra: o
modelo escolhido é reajustado sem os últimos 12 meses e comparado, nesses
meses, ao sazonal ingênuo (repetir o mesmo mês do ano anterior).
"""

import warnings
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from statsmodels.tools.sm_exceptions import ConvergenceWarning, InterpolationWarning
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.stattools import kpss

PERIODO = 12
HORIZONTE = 12
NIVEL = 0.95

# Abaixo disso a série é curta ou rala demais para o SARIMA: com média de
# poucas ocorrências por mês, o modelo passa a prever ruído de contagem.
MESES_MIN = 48
MEDIA_MIN = 5.0
ZEROS_MAX = 0.3

# Limiar de força da sazonalidade para diferenciar sazonalmente.
FORCA_SAZONAL_MIN = 0.64

# Ordens máximas da busca: p, q, P, Q.
ORDENS_MAX = (2, 2, 1, 1)


@dataclass(frozen=True)
class Previsao:
    media: list[float]
    inferior: list[float]
    superior: list[float]
    ordem: tuple[int, int, int]
    ordem_sazonal: tuple[int, int, int]
    # Erro absoluto médio nos 12 meses deixados de fora, do SARIMA e do sazonal
    # ingênuo. `None` quando não há meses suficientes para a validação.
    erro: float | None
    erro_ingenuo: float | None

    @property
    def descricao(self) -> str:
        p, d, q = self.ordem
        P, D, Q = self.ordem_sazonal
        return f"SARIMA({p},{d},{q})({P},{D},{Q})₁₂"

    @property
    def supera_ingenuo(self) -> bool | None:
        if self.erro is None or self.erro_ingenuo is None:
            return None
        return self.erro < self.erro_ingenuo


def motivo_sem_previsao(valores: list[float | None]) -> str | None:
    """Por que a série não recebe previsão, ou `None` se recebe."""
    v = np.array([np.nan if x is None else x for x in valores], dtype=float)
    validos = v[~np.isnan(v)]
    if len(validos) < MESES_MIN:
        return f"menos de {MESES_MIN} meses com dado"
    if not np.isfinite(v[-1]):
        return "o último mês não tem dado"
    if validos.mean() < MEDIA_MIN:
        return f"menos de {MEDIA_MIN:.0f} ocorrências por mês, em média"
    if (validos == 0).mean() > ZEROS_MAX:
        return "meses zerados demais"
    return None


def prever(valores: list[float | None]) -> Previsao | None:
    """Previsão dos :data:`HORIZONTE` meses seguintes ao último de `valores`
    (série mensal contínua, `None` nos meses sem dado), ou `None` quando
    :func:`motivo_sem_previsao` recusa a série."""
    if motivo_sem_previsao(valores) is not None:
        return None
    return _prever(tuple(None if x is None else float(x) for x in valores))


@lru_cache(maxsize=256)
def _prever(valores: tuple[float | None, ...]) -> Previsao:
    y = np.log1p(np.array([np.nan if x is None else x for x in valores], dtype=float))
    # Faltantes no começo não informam nada e atrapalham os testes.
    y = y[np.argmax(np.isfinite(y)):]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", (ConvergenceWarning, InterpolationWarning,
                                         RuntimeWarning, UserWarning))
        d = _ordem_d(y)
        D = _ordem_sazonal_d(y)
        ordem, sazonal = _escolher_ordens(y, d, D)
        res = _ajustar(y, ordem, sazonal)
        prev = res.get_forecast(HORIZONTE)
        media = prev.predicted_mean
        ic = prev.conf_int(alpha=1 - NIVEL)
        erro, erro_ingenuo = _validar(y, ordem, sazonal)

    # De volta à escala original: expm1 da média do log é a mediana prevista.
    def para_contagem(a) -> list[float]:
        return np.clip(np.expm1(np.asarray(a)), 0, None).tolist()

    return Previsao(
        media=para_contagem(media),
        inferior=para_contagem(ic[:, 0]),
        superior=para_contagem(ic[:, 1]),
        ordem=ordem,
        ordem_sazonal=sazonal,
        erro=erro,
        erro_ingenuo=erro_ingenuo,
    )


def _ordem_d(y: np.ndarray) -> int:
    # KPSS: hipótese nula de estacionariedade; p < 5% pede diferenciar.
    _, p, *_ = kpss(_interpolar(y), regression="c", nlags="auto", result_object=False)
    return int(p < 0.05)


def _ordem_sazonal_d(y: np.ndarray) -> int:
    if len(y) < 3 * PERIODO:
        return 0
    stl = STL(_interpolar(y), period=PERIODO, robust=True).fit()
    resto = np.var(stl.resid)
    forca = max(0.0, 1 - resto / np.var(stl.resid + stl.seasonal))
    return int(forca > FORCA_SAZONAL_MIN)


def _interpolar(y: np.ndarray) -> np.ndarray:
    """Preenche os faltantes por interpolação linear (só para os testes de
    diferenciação, que não aceitam faltantes; o SARIMAX recebe a série original)."""
    idx = np.arange(len(y))
    ok = np.isfinite(y)
    return np.interp(idx, idx[ok], y[ok])


def _ajustar(y: np.ndarray, ordem, sazonal):
    modelo = SARIMAX(y, order=ordem, seasonal_order=(*sazonal, PERIODO),
                     trend="c" if ordem[1] + sazonal[1] == 0 else "n",
                     concentrate_scale=True)
    return modelo.fit(disp=False, maxiter=50)


def _escolher_ordens(y: np.ndarray, d: int, D: int):
    aic: dict[tuple[int, int, int, int], float] = {}

    def avaliar(c):
        if c not in aic:
            try:
                aic[c] = _ajustar(y, (c[0], d, c[1]), (c[2], D, c[3])).aic
            except (np.linalg.LinAlgError, ValueError):
                aic[c] = np.inf
            if not np.isfinite(aic[c]):
                aic[c] = np.inf
        return aic[c]

    iniciais = [(2, 2, 1, 1), (0, 0, 0, 0), (1, 0, 1, 0), (0, 1, 0, 1)]
    melhor = min(iniciais, key=avaliar)
    while True:
        vizinhos = [
            tuple(o + (delta if i == j else 0) for j, o in enumerate(melhor))
            for i in range(4) for delta in (-1, 1)
        ]
        vizinhos = [v for v in vizinhos
                    if all(0 <= o <= m for o, m in zip(v, ORDENS_MAX))]
        candidato = min(vizinhos, key=avaliar)
        if avaliar(candidato) >= avaliar(melhor):
            break
        melhor = candidato
    p, q, P, Q = melhor
    return (p, d, q), (P, D, Q)


def _validar(y: np.ndarray, ordem, sazonal) -> tuple[float | None, float | None]:
    treino, teste = y[:-HORIZONTE], y[-HORIZONTE:]
    ingenuo = y[-HORIZONTE - PERIODO:-PERIODO]
    ok = np.isfinite(teste) & np.isfinite(ingenuo)
    if np.isfinite(treino).sum() < MESES_MIN - HORIZONTE or ok.sum() < HORIZONTE // 2:
        return None, None
    try:
        prev = _ajustar(treino, ordem, sazonal).forecast(HORIZONTE)
    except (np.linalg.LinAlgError, ValueError):
        return None, None
    real = np.expm1(teste[ok])
    erro = np.abs(np.expm1(prev[ok]) - real).mean()
    erro_ingenuo = np.abs(np.expm1(ingenuo[ok]) - real).mean()
    return float(erro), float(erro_ingenuo)
