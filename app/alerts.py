"""
Sistema de Alertas - Telegram e Discord
Integra com o bot de day trade para enviar notificações
"""

import json
import httpx
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class AlertLevel(Enum):
    INFO = "ℹ️"
    WARNING = "⚠️"
    ALERT = "🚨"
    SUCCESS = "✅"


class AlertChannel:
    """Base para canais de alerta"""
    
    async def send(self, title: str, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        raise NotImplementedError


class TelegramAlert(AlertChannel):
    """Alerta via Telegram"""
    
    def __init__(self, bot_token: str, chat_id: str):
        """
        Args:
            bot_token: Token do bot do Telegram (obter em @BotFather)
            chat_id: ID do chat/grupo para enviar mensagens
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}"
        self.enabled = bool(bot_token and chat_id)
    
    async def send(self, title: str, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        if not self.enabled:
            print(f"[Telegram] Desabilitado (token ou chat_id faltando)")
            return False
        
        try:
            timestamp = datetime.now().strftime("%H:%M:%S")
            full_message = f"""{level.value} *{title}*
_{timestamp}_

{message}
"""
            
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{self.api_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": full_message,
                        "parse_mode": "Markdown"
                    }
                )
                
                if response.status_code == 200:
                    print(f"[Telegram] ✅ Mensagem enviada: {title}")
                    return True
                else:
                    print(f"[Telegram] ❌ Erro {response.status_code}: {response.text}")
                    return False
        
        except Exception as e:
            print(f"[Telegram] Erro ao enviar: {e}")
            return False


class DiscordAlert(AlertChannel):
    """Alerta via Discord"""
    
    def __init__(self, webhook_url: str):
        """
        Args:
            webhook_url: URL do webhook do Discord
                        (Configurar em Server Settings > Integrations > Webhooks)
        """
        self.webhook_url = webhook_url
        self.enabled = bool(webhook_url)
    
    async def send(self, title: str, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        if not self.enabled:
            print(f"[Discord] Desabilitado (webhook_url faltando)")
            return False
        
        try:
            color_map = {
                AlertLevel.INFO: 3447003,      # Azul
                AlertLevel.WARNING: 15105570,  # Amarelo
                AlertLevel.ALERT: 15158332,    # Vermelho
                AlertLevel.SUCCESS: 3066993,   # Verde
            }
            
            embed = {
                "title": f"{level.value} {title}",
                "description": message,
                "color": color_map.get(level, 3447003),
                "timestamp": datetime.now().isoformat(),
                "footer": {"text": "Day Trade Bot"}
            }
            
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.webhook_url,
                    json={"embeds": [embed]}
                )
                
                if response.status_code in [200, 204]:
                    print(f"[Discord] ✅ Mensagem enviada: {title}")
                    return True
                else:
                    print(f"[Discord] ❌ Erro {response.status_code}")
                    return False
        
        except Exception as e:
            print(f"[Discord] Erro ao enviar: {e}")
            return False


class AlertManager:
    """Gerenciador centralizado de alertas"""
    
    def __init__(self):
        self.channels: Dict[str, AlertChannel] = {}
        self.alert_history = []
        self.max_history = 100
        self.irq_threshold_warning = 0.7
        self.irq_threshold_critical = 0.9
        self.last_alerts = {}  # Para evitar spam
    
    def add_telegram(self, name: str, bot_token: str, chat_id: str) -> bool:
        """Adiciona canal Telegram"""
        self.channels[name] = TelegramAlert(bot_token, chat_id)
        print(f"[AlertManager] Canal Telegram '{name}' registrado")
        return self.channels[name].enabled
    
    def add_discord(self, name: str, webhook_url: str) -> bool:
        """Adiciona canal Discord"""
        self.channels[name] = DiscordAlert(webhook_url)
        print(f"[AlertManager] Canal Discord '{name}' registrado")
        return self.channels[name].enabled
    
    async def send_alert(
        self,
        event: str,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        channels: Optional[list] = None
    ) -> bool:
        """
        Envia alerta para canais configurados
        
        Args:
            event: Identificador do evento (para rastrear spam)
            title: Título do alerta
            message: Mensagem do alerta
            level: Nível de severidade
            channels: Lista de canais específicos (ou None para todos)
        """
        
        # Anti-spam: não enviar mesmo alerta em menos de 5 minutos
        now = datetime.now().timestamp()
        if event in self.last_alerts:
            if now - self.last_alerts[event] < 300:  # 5 minutos
                print(f"[AlertManager] Alerta '{event}' ignorado (anti-spam)")
                return False
        
        self.last_alerts[event] = now
        
        # Registrar histórico
        alert_record = {
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "title": title,
            "level": level.name,
            "message": message
        }
        self.alert_history.append(alert_record)
        if len(self.alert_history) > self.max_history:
            self.alert_history.pop(0)
        
        # Determinar canais
        target_channels = channels or list(self.channels.keys())
        
        # Enviar
        results = []
        for channel_name in target_channels:
            if channel_name in self.channels:
                result = await self.channels[channel_name].send(title, message, level)
                results.append(result)
        
        return any(results)
    
    async def alert_risk_level(self, irq_score: float, portfolio_status: Dict[str, Any]):
        """Alerta automático baseado no nível de risco (IRQ)"""
        
        if irq_score > self.irq_threshold_critical:
            await self.send_alert(
                event="irq_critical",
                title="RISCO CRÍTICO NO MERCADO",
                message=f"""
IRQ: {irq_score*100:.1f}% (CRÍTICO)

⚠️ O índice de risco atingiu nível crítico!
⚠️ Recomenda-se SAIR DO MERCADO imediatamente
⚠️ Proteção recomendada: SAÍDA TOTAL

Portfolio atual:
{json.dumps(portfolio_status, indent=2)}
""",
                level=AlertLevel.ALERT
            )
        
        elif irq_score > self.irq_threshold_warning:
            await self.send_alert(
                event="irq_warning",
                title="ALERTA DE RISCO ELEVADO",
                message=f"""
IRQ: {irq_score*100:.1f}% (ELEVADO)

⚠️ Risco do mercado acima do normal
⚠️ Recomenda-se reduzir posições
⚠️ Proteção recomendada: -50%

Portfolio atual:
{json.dumps(portfolio_status, indent=2)}
""",
                level=AlertLevel.WARNING
            )
    
    async def alert_momentum(self, asset: str, classification: str, score: float):
        """Alerta quando há mudança de classificação de momentum"""
        
        if classification == "FORTE_ALTA" and score > 0.7:
            await self.send_alert(
                event=f"momentum_forte_{asset}",
                title=f"🚀 {asset} - FORTE ALTA",
                message=f"""
Ativo: {asset}
Momentum Score: {score:.2f}
Classificação: {classification}

✅ Sinal positivo detectado!
Considere entrar nesta posição.""",
                level=AlertLevel.SUCCESS,
                channels=["telegram_main"]
            )
        
        elif classification == "QUEDA" and score < -0.5:
            await self.send_alert(
                event=f"momentum_queda_{asset}",
                title=f"📉 {asset} - QUEDA",
                message=f"""
Ativo: {asset}
Momentum Score: {score:.2f}
Classificação: {classification}

❌ Sinal negativo detectado!
Considere sair desta posição.""",
                level=AlertLevel.WARNING,
                channels=["telegram_main"]
            )
    
    async def alert_trade_executed(self, asset: str, action: str, amount: float, price: float):
        """Alerta quando um trade é executado"""
        
        await self.send_alert(
            event=f"trade_{asset}_{datetime.now().timestamp()}",
            title=f"💰 Trade Executado - {asset}",
            message=f"""
Ação: {action}
Ativo: {asset}
Quantidade: {amount:.4f}
Preço: R$ {price:.2f}
Total: R$ {amount * price:.2f}

Transação confirmada no sistema.""",
            level=AlertLevel.SUCCESS
        )
    
    async def alert_stop_loss_triggered(self, asset: str, price: float, loss_pct: float):
        """Alerta quando stop loss é acionado"""
        
        await self.send_alert(
            event=f"stoploss_{asset}",
            title=f"🛑 STOP LOSS Acionado - {asset}",
            message=f"""
Ativo: {asset}
Preço: R$ {price:.2f}
Perda: {loss_pct:.2f}%

Stop loss foi acionado.
Posição foi encerrada automaticamente.""",
            level=AlertLevel.WARNING
        )
    
    def get_alert_history(self, limit: int = 20) -> list:
        """Retorna histórico de alertas"""
        return self.alert_history[-limit:]
    
    def get_status(self) -> Dict[str, Any]:
        """Retorna status dos canais"""
        return {
            "channels": {
                name: {
                    "type": type(ch).__name__,
                    "enabled": ch.enabled
                }
                for name, ch in self.channels.items()
            },
            "total_alerts_sent": len(self.alert_history),
            "recent_alerts": self.get_alert_history(5)
        }


# Instância global
alert_manager = AlertManager()


# Exemplo de uso
async def example_setup():
    """Exemplo de configuração"""
    
    # Setup (preencher com valores reais)
    alert_manager.add_telegram(
        "telegram_main",
        bot_token="COLOCAR_TOKEN_AQUI",  # @BotFather no Telegram
        chat_id="COLOCAR_CHAT_ID_AQUI"
    )
    
    alert_manager.add_discord(
        "discord_main",
        webhook_url="COLOCAR_WEBHOOK_URL_AQUI"  # Server Settings > Webhooks
    )
    
    # Usar
    await alert_manager.alert_momentum("BTC", "FORTE_ALTA", 0.85)
    await alert_manager.alert_risk_level(0.75, {"BTC": 100, "ETH": 50})


if __name__ == "__main__":
    import asyncio
    print("Sistema de Alertas carregado!")
    print("Use: from alerts import alert_manager")
    print("\nExemplo:")
    print("  await alert_manager.send_alert('event', 'Título', 'Mensagem')")
