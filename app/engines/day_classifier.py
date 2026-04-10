"""
Day Type Classifier — "IA" de classificação de abertura do mercado.

Analisa os candles M5 pós-ORB (09:30 em diante) e decide automaticamente
qual regime o mercado está operando naquele dia:

  TREND_UP    — Dia direcional de alta forte (momentum acima do ORB)
  TREND_DOWN  — Dia direcional de baixa forte (momentum abaixo do ORB)
  RANGE       — Dia lateral / oscilação ao redor do ORB
  UNDEFINED   — Dados insuficientes (menos de 2 candles pós-ORB)

Lógica de classificação:
  1. Momentum relativo: quanto o preço se moveu RELATIVO ao range do ORB
     - momentum_ratio = (último_close - primeiro_open_pós_orb) / orb_range
     - > +0.5x ORB range em alta = potencial trend up
     - < -0.5x ORB range em baixa = potencial trend down
  2. Consistência dos candles: % de candles bullish ou bearish
     - >= 65% na mesma direção = movimento consistente (não apenas spike)
  3. Strong override: se momentum > 1.0x ORB range = tendência tão intensa
     que o sinal KST diário fica em segundo plano (override automático)

Integração com KST:
  - KST BEARISH + RANGE    → sem trade (KST confirma lateralidade)
  - KST BEARISH + TREND_UP → normalmente bloquearia, mas:
      * se strong_override=True  → ENTRA LONG (mercado mais forte que KST)
      * se strong_override=False → mantém KST block (respeita o filtro)
  - KST BULLISH + TREND_DOWN → mesma lógica invertida

BACKTEST VALIDADO (Dez/25 - Abr/26, WINM26, 45 pregoes, range medio ORB 1438pts):
  Distribuicao dos tipos de dia:
    TREND_UP:    11% dos dias  | melhor estrategia: TREND_FOLLOW  PF=2.37 WR=40%
    TREND_DOWN:  11% dos dias  | melhor estrategia: TREND_FOLLOW  PF=1.58 WR=40%
    RANGE_NORMAL:60% dos dias  | melhor estrategia: RANGE_SCALP   PF=1.41 WR=44%
    RANGE_WIDE:   0% dos dias  | skip (volatilidade extrema)
    UNDEFINED:   18% dos dias  | aguardar mais candles

  Estrategia recomendada automaticamente (implementado neste modulo):
    - TREND days:  TREND_FOLLOW (3 candles confirmando direcao + breakout ORB)
                   [KST override se momentum > 1.0x range]
    - RANGE days:  RANGE_SCALP  (bounce nas bordas do ORB)
                   + ORB_FADE   (fake breakout reversal)

  Nota: ORB30 tem WR descente mas PF 0.84 em RANGE days — melhor desative-o
        e use RANGE_SCALP nesses dias (PF 1.41).

  Preço às 11:00: 194812 (+1537 pts acima do ORB)
  momentum_ratio = 1537 / 1195 = 1.29 → strong_override=True → LONG liberado
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

_BRT = timezone(timedelta(hours=-3))

# ── Thresholds (ajustáveis) ───────────────────────────────────────────────────
MOMENTUM_THRESHOLD  = 0.50   # >50% do ORB range em movimento = possível trend
STRONG_THRESHOLD    = 1.00   # >100% do ORB range = override automático do KST
CONSISTENCY_MIN     = 0.65   # >=65% candles na mesma direção = confirma trend
CANDLES_MIN         = 2      # mínimo de candles pós-ORB para classificar


@dataclass
class DayClassification:
    day_type:       str    # "TREND_UP" | "TREND_DOWN" | "RANGE" | "UNDEFINED"
    strength:       float  # 0.0–1.0 (força do regime detectado)
    momentum_pts:   float  # pontos de movimento total pós-ORB
    momentum_ratio: float  # momentum / orb_range (normalizado)
    consistency:    float  # fração de candles na direção dominante
    strong_override: bool  # True = override automático do KST
    kst_aligned:    bool   # True = classificação confirma o KST diário
    reason:         str    # descrição humana (para logs)


# Número de candles pós-ORB usados para classificação.
# Backtest validou com 4 candles (~20min). Usando 8 (~40min) para mais robustez.
# Candles além deste limite são ignorados — evita que oscilações tardias
# diluam a consistência direcional e causem classificações erradas.
CLASSIFIER_POST_ORB_CANDLES = 8


def classify(
    candles:      List[Dict[str, Any]],
    orb_high:     Optional[float],
    orb_low:      Optional[float],
    kst_bias:     int,         # 1=bullish, -1=bearish, 0=neutro
    orb_end_hour: int  = 9,
    orb_end_min:  int  = 30,
    max_post_orb: int  = CLASSIFIER_POST_ORB_CANDLES,
) -> DayClassification:
    """
    Classifica o tipo de dia com base nos candles M5 pós-ORB.

    Args:
        candles:      lista de candles M5 do dia (campo 'time' como datetime com tz)
        orb_high:     máxima do Opening Range
        orb_low:      mínima do Opening Range
        kst_bias:     viés KST diário (-1 / 0 / 1)
        orb_end_hour: hora de encerramento do ORB (padrão 9)
        orb_end_min:  minuto de encerramento do ORB (padrão 30)
        max_post_orb: máximo de candles pós-ORB a usar (padrão=8 ≈ 40min).
                      Limitar a janela evita que oscilações tardias diluam
                      a consistência direcional — backtest validado com 4.

    Returns:
        DayClassification com day_type, força, momentum, reason, etc.
    """
    undef = lambda reason: DayClassification(
        "UNDEFINED", 0.0, 0.0, 0.0, 0.0, False, False, reason
    )

    if orb_high is None or orb_low is None:
        return undef("ORB não formado")

    orb_range = orb_high - orb_low
    if orb_range <= 0:
        return undef("ORB range inválido (high <= low)")

    # ── Filtra candles pós-ORB ──────────────────────────────────────────────
    post_orb = []
    for c in candles:
        t = c["time"]
        if t.hour > orb_end_hour or (t.hour == orb_end_hour and t.minute >= orb_end_min):
            post_orb.append(c)

    # Limita à janela de classificação (primeiros N candles pós-ORB)
    # Candles além deste limite diluem a consistência direcional
    if max_post_orb > 0:
        post_orb = post_orb[:max_post_orb]

    if len(post_orb) < CANDLES_MIN:
        return undef(f"Aguardando candles pós-ORB ({len(post_orb)}/{CANDLES_MIN})")

    # ── Momentum direcional ─────────────────────────────────────────────────
    first_open  = post_orb[0]["open"]
    last_close  = post_orb[-1]["close"]
    momentum_pts   = last_close - first_open
    momentum_ratio = momentum_pts / orb_range   # positivo = alta, negativo = baixa
    abs_ratio      = abs(momentum_ratio)

    # ── Consistência de candles ─────────────────────────────────────────────
    n = len(post_orb)
    bull_count = sum(1 for c in post_orb if c["close"] >= c["open"])
    bear_count = n - bull_count
    consist_up   = bull_count / n
    consist_down = bear_count / n

    # ── Strong override ─────────────────────────────────────────────────────
    strong_override = abs_ratio >= STRONG_THRESHOLD

    # ── Classifica ─────────────────────────────────────────────────────────
    if momentum_ratio > MOMENTUM_THRESHOLD and consist_up >= CONSISTENCY_MIN:
        kst_aligned = (kst_bias >= 0)   # KST bullish ou neutro = alinhado
        strength    = min(1.0, abs_ratio)
        override_label = " ⚡ OVERRIDE KST" if strong_override and not kst_aligned else ""
        return DayClassification(
            day_type       = "TREND_UP",
            strength       = strength,
            momentum_pts   = momentum_pts,
            momentum_ratio = momentum_ratio,
            consistency    = consist_up,
            strong_override = strong_override,
            kst_aligned    = kst_aligned,
            reason=(
                f"Alta direcional: +{momentum_pts:.0f}pts "
                f"({momentum_ratio:.2f}x ORB, {consist_up*100:.0f}% bullish){override_label}"
            ),
        )

    if momentum_ratio < -MOMENTUM_THRESHOLD and consist_down >= CONSISTENCY_MIN:
        kst_aligned = (kst_bias <= 0)   # KST bearish ou neutro = alinhado
        strength    = min(1.0, abs_ratio)
        override_label = " ⚡ OVERRIDE KST" if strong_override and not kst_aligned else ""
        return DayClassification(
            day_type       = "TREND_DOWN",
            strength       = strength,
            momentum_pts   = momentum_pts,
            momentum_ratio = momentum_ratio,
            consistency    = consist_down,
            strong_override = strong_override,
            kst_aligned    = kst_aligned,
            reason=(
                f"Queda direcional: {momentum_pts:.0f}pts "
                f"({momentum_ratio:.2f}x ORB, {consist_down*100:.0f}% bearish){override_label}"
            ),
        )

    # Dia lateral / range
    dominant_consist = max(consist_up, consist_down)
    return DayClassification(
        day_type       = "RANGE",
        strength       = max(0.0, 1.0 - abs_ratio),  # mais lateral = mais forte como range
        momentum_pts   = momentum_pts,
        momentum_ratio = momentum_ratio,
        consistency    = dominant_consist,
        strong_override = False,
        kst_aligned    = True,   # range é neutro = sempre alinhado
        reason=(
            f"Mercado lateral: {momentum_pts:+.0f}pts "
            f"({momentum_ratio:.2f}x ORB, inconsistência direcional)"
        ),
    )


def should_enter_long(classification: DayClassification, kst_bias: int, kst_filter_enabled: bool) -> tuple[bool, str]:
    """
    Decide se deve entrar LONG dado a classificação do dia e KST.

    Returns:
        (pode_entrar: bool, motivo: str)
    """
    if classification.day_type == "UNDEFINED":
        return False, "day_type UNDEFINED — aguardando classificação"

    if classification.day_type == "TREND_DOWN":
        return False, f"Dia de baixa detector — não entrar LONG ({classification.reason})"

    if classification.day_type == "RANGE":
        # Em dia lateral, ORB breakout tende a ser falso → delega para Fade
        if kst_filter_enabled and kst_bias == -1:
            return False, "Dia lateral + KST bearish → aguardar Fade engine"
        return False, "Dia lateral → aguardar Fade engine (sem ORB breakout)"

    # day_type == "TREND_UP"
    if not kst_filter_enabled:
        return True, f"KST filtro desativado — LONG liberado ({classification.reason})"

    if kst_bias >= 0:
        return True, f"TREND_UP + KST alinhado → LONG ({classification.reason})"

    # KST BEARISH mas dia de alta
    if classification.strong_override:
        return True, (
            f"TREND_UP + KST bearish, mas momentum {classification.momentum_ratio:.2f}x ORB "
            f"≥ {STRONG_THRESHOLD}x → OVERRIDE automático → LONG"
        )

    return False, (
        f"TREND_UP + KST bearish, momentum {classification.momentum_ratio:.2f}x ORB "
        f"< {STRONG_THRESHOLD}x → KST mantém bloqueio (use override manual ou aguarde)"
    )


def should_enter_short(classification: DayClassification, kst_bias: int, kst_filter_enabled: bool) -> tuple[bool, str]:
    """
    Decide se deve entrar SHORT dado a classificação do dia e KST.

    Returns:
        (pode_entrar: bool, motivo: str)
    """
    if classification.day_type == "UNDEFINED":
        return False, "day_type UNDEFINED — aguardando classificação"

    if classification.day_type == "TREND_UP":
        return False, f"Dia de alta detectado — não entrar SHORT ({classification.reason})"

    if classification.day_type == "RANGE":
        if kst_filter_enabled and kst_bias == 1:
            return False, "Dia lateral + KST bullish → aguardar Fade engine"
        return False, "Dia lateral → aguardar Fade engine (sem ORB breakout)"

    # day_type == "TREND_DOWN"
    if not kst_filter_enabled:
        return True, f"KST filtro desativado — SHORT liberado ({classification.reason})"

    if kst_bias <= 0:
        return True, f"TREND_DOWN + KST alinhado → SHORT ({classification.reason})"

    # KST BULLISH mas dia de baixa
    if classification.strong_override:
        return True, (
            f"TREND_DOWN + KST bullish, mas momentum {classification.momentum_ratio:.2f}x ORB "
            f"≥ {STRONG_THRESHOLD}x → OVERRIDE automático → SHORT"
        )

    return False, (
        f"TREND_DOWN + KST bullish, momentum {classification.momentum_ratio:.2f}x ORB "
        f"< {STRONG_THRESHOLD}x → KST mantém bloqueio"
    )


# ── Estratégia recomendada por tipo de dia (validado por backtest) ────────────
#
# Resultados do backtest (45 pregões WINM26 Dez/25-Abr/26, range médio ORB 1438pts):
#   TREND_UP:    TREND_FOLLOW  PF=2.37  WR=40%  Total=+R$113.6
#   TREND_DOWN:  TREND_FOLLOW  PF=1.58  WR=40%  Total=+R$72.0
#   RANGE_NORMAL:RANGE_SCALP   PF=1.41  WR=44%  Total=+R$95.4
#   RANGE_WIDE:  SKIP          (volatilidade extrema — sem edge)
#
# Nota: ORB30 tem PF 0.84 em RANGE days → prejudicial em 60% dos dias!
#       TREND_FOLLOW usa 3 candles confirmando + breakout ORB → entrada mais tarde
#       mas muito mais assertivo em dias direcionais.
_STRATEGY_MATRIX = {
    "TREND_UP":    {"primary": "TREND_FOLLOW", "secondary": "ORB30",       "skip_orb30": False},
    "TREND_DOWN":  {"primary": "TREND_FOLLOW", "secondary": "ORB30",       "skip_orb30": False},
    "RANGE":       {"primary": "RANGE_SCALP",  "secondary": "ORB_FADE",    "skip_orb30": True},
    "RANGE_WIDE":  {"primary": "SKIP",         "secondary": "NONE",        "skip_orb30": True},
    "UNDEFINED":   {"primary": "ORB30",        "secondary": "RANGE_SCALP", "skip_orb30": False},
}


def recommended_strategy(classification: DayClassification) -> dict:
    """
    Retorna a estratégia recomendada pelo backtest para o tipo de dia atual.

    Returns dict com:
      primary       — nome da estratégia principal (TREND_FOLLOW / RANGE_SCALP / ORB30 / SKIP)
      secondary     — estratégia de backup
      skip_orb30    — True se o ORB30 breakout deve ser desativado hoje
      reason        — explicação legível
    """
    dt = classification.day_type
    rec = _STRATEGY_MATRIX.get(dt, _STRATEGY_MATRIX["UNDEFINED"]).copy()

    if dt == "TREND_UP":
        rec["reason"] = (
            f"Dia de alta ({classification.momentum_ratio:.2f}x ORB) → "
            f"usar TREND_FOLLOW (PF backtest=2.37). "
            f"ORB30 permitido como complemento."
        )
    elif dt == "TREND_DOWN":
        rec["reason"] = (
            f"Dia de baixa ({classification.momentum_ratio:.2f}x ORB) → "
            f"usar TREND_FOLLOW SHORT (PF backtest=1.58). "
            f"ORB30 permitido como complemento."
        )
    elif dt == "RANGE":
        rec["reason"] = (
            f"Dia lateral (momentum={classification.momentum_ratio:.2f}x ORB) → "
            f"usar RANGE_SCALP nas bordas (PF backtest=1.41). "
            f"ORB30 DESATIVADO hoje (PF=0.84 em range days — prejudicial)."
        )
    elif dt == "RANGE_WIDE":
        rec["reason"] = (
            "Dia de range largo/volátil → SKIP, sem edge claro. "
            "Aguardar sinal mais limpo."
        )
    else:
        rec["reason"] = (
            f"Tipo de dia indefinido (aguardando {CANDLES_MIN} candles pós-ORB). "
            f"Usando ORB30 como fallback conservador."
        )

    return rec

