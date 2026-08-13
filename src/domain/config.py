"""
Configuração do aplicativo.

Antes desta centralização, oito valores de comportamento viviam como constantes
de módulo espalhadas por seis arquivos em três camadas: IP padrão na janela,
intervalo do heartbeat na infraestrutura de rede, limites de retenção no banco,
teto de força G no serviço, e mais três nos ViewModels. Nenhum deles era
ajustável sem editar código.

Fica no domínio porque é regra do aplicativo, não detalhe de nenhuma camada —
e porque, estando aqui, qualquer camada pode recebê-la sem inverter dependência.

A validação é deliberadamente permissiva: valor inválido cai no padrão em vez
de levantar. Um arquivo de configuração corrompido não pode impedir o app de
abrir — o pior caso aceitável é o app abrir com o comportamento de fábrica.
"""

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".hanna_gt7_ai" / "config.json"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Valores que governam o comportamento do app.

    Imutável de propósito: config alterada em runtime por engano produz
    comportamento que muda no meio da sessão e não se reproduz depois.
    """

    # --- rede ---
    ps_ip: str = "192.168.15.156"
    heartbeat_interval_s: float = 10.0

    # --- histórico ---
    keep_best_per_track: int = 5
    keep_recent_per_track: int = 50
    num_sectors: int = 3

    # --- telemetria ---
    # Carro de corrida real não passa de ~2 g; 5 g dá folga e ainda barra lixo
    # vindo de pacote perdido ou reset de posição no jogo.
    max_g: float = 5.0
    # Silêncio a partir do qual a tela vai para o estado neutro, para não
    # confundir "carro parado" com "transmissão perdida".
    stale_timeout_s: float = 1.0

    # --- exibição ---
    # Valor bruto de slip em que o índice satura em 100 %.
    slip_saturation: float = 1.0
    # Acima disto não há pixel na tela para distinguir os pontos.
    max_plot_points: int = 2000

    # --- reconexão (Fase 4) ---
    auto_reconnect: bool = True
    reconnect_initial_delay_s: float = 2.0
    reconnect_max_delay_s: float = 60.0


# Faixas aceitáveis por campo. Fora delas, o valor do arquivo é ignorado.
_LIMITES = {
    "heartbeat_interval_s": (0.5, 120.0),
    "keep_best_per_track": (1, 1000),
    "keep_recent_per_track": (1, 10000),
    "num_sectors": (1, 10),
    "max_g": (0.5, 50.0),
    "stale_timeout_s": (0.1, 60.0),
    "slip_saturation": (0.01, 10.0),
    "max_plot_points": (100, 100000),
    "reconnect_initial_delay_s": (0.5, 60.0),
    "reconnect_max_delay_s": (1.0, 3600.0),
}


def _coerce(name: str, raw, default):
    """Converte e valida um campo, caindo no padrão quando não dá.

    Recebe o valor cru do JSON, que pode ser de qualquer tipo — inclusive texto
    onde se espera número, que é o erro mais comum quando alguém edita o
    arquivo à mão.
    """
    tipo = type(default)
    try:
        if tipo is bool:
            if not isinstance(raw, bool):
                return default
            valor = raw
        elif tipo is int:
            # `True` é int em Python; aceitar aqui produziria retenção = 1.
            if isinstance(raw, bool):
                return default
            valor = int(raw)
        elif tipo is float:
            if isinstance(raw, bool):
                return default
            valor = float(raw)
        elif tipo is str:
            valor = str(raw).strip()
            if not valor:
                return default
        else:
            return default
    except (TypeError, ValueError):
        return default

    faixa = _LIMITES.get(name)
    if faixa is not None and not (faixa[0] <= valor <= faixa[1]):
        return default
    return valor


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Lê a configuração do disco. Sempre devolve algo utilizável."""
    caminho = Path(path)
    if not caminho.exists():
        return AppConfig()

    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return AppConfig()

    if not isinstance(dados, dict):
        return AppConfig()

    padrao = AppConfig()
    valores = {}
    for campo in fields(AppConfig):
        default = getattr(padrao, campo.name)
        valores[campo.name] = (
            _coerce(campo.name, dados[campo.name], default)
            if campo.name in dados
            else default
        )
    return AppConfig(**valores)


def save_config(config: AppConfig, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    """Grava a configuração, criando o diretório se necessário.

    Escreve num arquivo temporário e renomeia: interromper a gravação no meio
    deixaria um JSON truncado, e o app abriria com o padrão sem explicar por quê.
    """
    caminho = Path(path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(".json.tmp")
    temporario.write_text(
        json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporario.replace(caminho)
