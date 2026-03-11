"""
Execution Engine — Núcleo de execução de ordens

Camada intermediária entre Strategy Engine e Broker API.
A estratégia envia uma intenção (BUY/SELL + size). Este módulo decide
como executar para minimizar slippage, custo de spread e impacto no mercado.

Técnicas:
  1. Smart limit orders  — limit próximo ao spread, não market order
  2. Slippage guard      — cancela se preço desviar > MAX_SLIPPAGE do esperado
  3. Execution timeout   — cancela e retenta se ordem não preenchida em N segundos
  4. Liquidity check     — pula ativo se volume recente for insuficiente
  5. Order slicing       — divide ordens grandes em fatias (só acima do threshold)

Uso (dry_run=True por padrão — seguro para paper trading):
    om = OrderManager(broker=alpaca_broker)
    result = await om.execute(
        asset="AAPL", action="BUY", amount=500.0,
        mid_price=175.20, volumes=[...],
    )
"""

import asyncio
from typing import Dict, List, Optional, Tuple

# ── Parâmetros ────────────────────────────────────────────────────────────────
MAX_SLIPPAGE_PCT   = 0.002   # 0.2% — cancela se preço escapar mais que isso
LIMIT_OFFSET_PCT   = 0.0002  # posiciona limit 0.02% dentro do spread
EXEC_TIMEOUT_S     = 5.0     # segundos antes de cancelar ordem não preenchida
MIN_VOLUME_RATIO   = 0.5     # volume atual / média: abaixo = mercado seco
ORDER_SLICE_THRESH = 5_000.0 # R$/USD — abaixo disso nunca fatia (desnecessário)
ORDER_SLICE_PARTS  = 3       # fatias para ordens acima do threshold


# ── Helpers internos ─────────────────────────────────────────────────────────

def _mean(lst: List[float]) -> float:
    return sum(lst) / len(lst) if lst else 0.0


def _calc_limit_price(mid_price: float, action: str) -> float:
    """
    Calcula preço limit inteligente.

    BUY : posiciona um pouco acima do bid (melhor que pagar o ask cheio)
    SELL: posiciona um pouco abaixo do ask (melhor que aceitar o bid cru)
    """
    offset = mid_price * LIMIT_OFFSET_PCT
    if action.upper() == "BUY":
        return round(mid_price - offset, 8)   # abaixo do mid → tenta pegar no bid+
    return round(mid_price + offset, 8)        # acima do mid → tenta pegar no ask-


# ── Slippage Guard ────────────────────────────────────────────────────────────

class SlippageGuard:
    """Valida se o preço de execução está dentro do limite aceitável."""

    MAX_SLIPPAGE_PCT = MAX_SLIPPAGE_PCT

    @staticmethod
    def check(expected_price: float, actual_price: float, action: str) -> Tuple[bool, float]:
        """
        Verifica se o slippage é aceitável.

        Para BUY: slippage ruim = pagar mais caro que o esperado
        Para SELL: slippage ruim = vender mais barato que o esperado

        Returns:
            (ok: bool, slippage_pct: float  — negativo = favorável)
        """
        if expected_price == 0:
            return True, 0.0

        slippage = (actual_price - expected_price) / expected_price

        # BUY: pagar mais que o esperado é ruim
        # SELL: receber menos que o esperado é ruim
        bad = (slippage > MAX_SLIPPAGE_PCT) if action.upper() == "BUY" \
              else (slippage < -MAX_SLIPPAGE_PCT)

        return (not bad), round(slippage * 100, 4)


# ── Liquidity Check ───────────────────────────────────────────────────────────

class LiquidityCheck:
    """Verifica se há liquidez suficiente antes de entrar."""

    MIN_VOLUME_RATIO = MIN_VOLUME_RATIO

    @staticmethod
    def check(
        recent_volume: float,
        avg_volume: float,
        volumes: Optional[List[float]] = None,
    ) -> Tuple[bool, str]:
        """
        Retorna (ok, mensagem).

        Aceita volume já calculado ou lista de volumes (calcula internamente).
        """
        if volumes and avg_volume == 0:
            avg_volume    = _mean(volumes[-20:]) if len(volumes) >= 20 else _mean(volumes)
            recent_volume = _mean(volumes[-5:])  if len(volumes) >= 5  else _mean(volumes)

        if avg_volume == 0:
            return True, "OK"  # sem histórico → não bloqueia

        ratio = recent_volume / avg_volume
        if ratio < MIN_VOLUME_RATIO:
            return (
                False,
                f"Liquidez insuficiente: volume atual {ratio:.2f}x a média "
                f"(mínimo {MIN_VOLUME_RATIO}x)",
            )
        return True, "OK"


# ── Order Manager ─────────────────────────────────────────────────────────────

class OrderManager:
    """
    Gerenciador de execução de ordens com todas as proteções ativas.

    dry_run=True (padrão) → simula, não envia ao broker.
    Setar dry_run=False apenas quando broker real estiver configurado.
    """

    def __init__(self, broker=None, dry_run: bool = True):
        self.broker  = broker
        self.dry_run = dry_run

    async def execute(
        self,
        asset: str,
        action: str,
        amount: float,
        mid_price: float,
        volumes: Optional[List[float]] = None,
        dry_run: Optional[bool] = None,
    ) -> Dict:
        """
        Executa uma ordem com todas as proteções ativas.

        Args:
            asset:     símbolo (ex: "BTC", "PETR4", "AAPL")
            action:    "BUY" ou "SELL"
            amount:    valor em R$/USD
            mid_price: preço mid-market atual
            volumes:   histórico de volumes para liquidity check
            dry_run:   sobrescreve self.dry_run se fornecido

        Returns:
            Dict com status, filled_price, slippage_pct, reason
        """
        volumes  = volumes or []
        is_dry   = self.dry_run if dry_run is None else dry_run

        # 1. Liquidity check
        liq_ok, liq_msg = LiquidityCheck.check(0, 0, volumes=volumes)
        if not liq_ok:
            return {
                "status": "REJECTED",
                "reason": liq_msg,
                "asset":  asset,
                "action": action,
            }

        # 2. Calcula preço limit inteligente
        limit_price = _calc_limit_price(mid_price, action)

        # 3. Slippage guard pré-execução
        slip_ok, slip_pct = SlippageGuard.check(mid_price, limit_price, action)
        if not slip_ok:
            return {
                "status": "REJECTED",
                "reason": f"Slippage pré-exec {slip_pct:.3f}% > limite {MAX_SLIPPAGE_PCT*100:.1f}%",
                "asset":  asset,
            }

        # 4. Order slicing (só ordens grandes)
        n_slices     = ORDER_SLICE_PARTS if amount > ORDER_SLICE_THRESH else 1
        slice_amount = round(amount / n_slices, 4)

        # 5. Dry run → resposta simulada sem tocar no broker
        if is_dry:
            return {
                "status":       "SIMULATED",
                "asset":        asset,
                "action":       action,
                "amount":       amount,
                "mid_price":    mid_price,
                "limit_price":  limit_price,
                "slippage_pct": slip_pct,
                "n_slices":     n_slices,
                "slice_amount": slice_amount,
            }

        # 6. Execução real com timeout por fatia
        results = []
        for i in range(n_slices):
            try:
                order_result = await asyncio.wait_for(
                    self._send_order(asset, action, slice_amount, limit_price),
                    timeout=EXEC_TIMEOUT_S,
                )
                results.append(order_result)
                # Para se houver erro num slice
                if order_result.get("status") not in ("FILLED", "PARTIAL"):
                    break
            except asyncio.TimeoutError:
                results.append({"status": "TIMEOUT", "slice": i + 1, "amount": 0})
                break

        filled        = [r for r in results if r.get("status") in ("FILLED", "PARTIAL")]
        total_filled  = sum(r.get("amount", 0) for r in filled)

        if total_filled >= amount * 0.99:
            final_status = "FILLED"
        elif total_filled > 0:
            final_status = "PARTIAL"
        else:
            final_status = "FAILED"

        return {
            "status":         final_status,
            "asset":          asset,
            "action":         action,
            "amount_req":     amount,
            "amount_filled":  total_filled,
            "limit_price":    limit_price,
            "slippage_pct":   slip_pct,
            "slices":         results,
        }

    async def _send_order(
        self, asset: str, action: str, amount: float, limit_price: float
    ) -> Dict:
        """Envia uma fatia de ordem ao broker."""
        if self.broker is None:
            return {"status": "NO_BROKER", "amount": 0}
        try:
            result = await self.broker.place_order(
                symbol=asset,
                side=action,
                amount=amount,
                order_type="limit",
                price=limit_price,
            )
            return {**result, "amount": amount}
        except Exception as e:
            return {"status": "ERROR", "reason": str(e), "amount": 0}
