"""
Configuração centralizada — §36 e §37 do briefing.

A auditoria registrou como P3 (crítico) que não existia camada de configuração:
o IP da LAN doméstica do autor estava hardcoded em `presentation/main_window.py:51`
e versionado, e não havia onde colocar token do Discord ou chave de IA.

Regras que este módulo impõe:

1. **Segredo nenhum tem valor padrão.** Chave de IA e token do Discord vêm do
   ambiente ou não existem — nunca de um literal no código.
2. **Precedência explícita:** variável de ambiente > arquivo `.env` > padrão.
   O ambiente ganha para que produção e CI sobrescrevam sem editar arquivo.
3. **`__repr__` mascara segredos**, para que um log de configuração ou um
   traceback não vaze a chave — o vazamento acidental mais comum.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

ENV_PREFIX = "GT7_"


def _load_dotenv(path: Path) -> dict[str, str]:
    """Lê um `.env` simples (`CHAVE=valor`). Sem dependência externa.

    Ignora linhas vazias e comentários; não interpreta aspas nem expansão de
    variáveis, que é mais do que este projeto precisa e menos superfície para
    comportamento surpreendente.
    """
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class SecretStr:
    """String que não se revela em log, `repr()` ou traceback.

    Existe porque a forma mais comum de vazar uma chave não é commitá-la — é
    imprimir o objeto de configuração inteiro numa mensagem de diagnóstico.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str = "") -> None:
        self._value = value

    def reveal(self) -> str:
        """Devolve o valor real. Chamada explícita, fácil de auditar por grep."""
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "SecretStr('***')" if self._value else "SecretStr(empty)"

    __str__ = __repr__


@dataclass(slots=True)
class TelemetryConfig:
    """Rede e captura. Sem IP padrão apontando para a casa de ninguém."""

    source: str = "mock"          # mock | udp | replay
    ps_ip: str = ""               # vazio = não configurado (era 192.168.15.156)
    send_port: int = 33739
    receive_port: int = 33740
    sample_rate_hz: int = 60
    buffer_size: int = 4096       # quadros no ring buffer antes de descartar
    # Acelera o tempo simulado da fonte sintética. 1.0 é tempo real; 60.0 roda
    # uma sessão de 50 voltas em ~85 s, o que torna viável exercitar gravação e
    # retenção sem esperar uma hora e meia.
    mock_speed_multiplier: float = 1.0


@dataclass(slots=True)
class StorageConfig:
    database_path: Path = field(
        default_factory=lambda: Path.home() / ".hanna_gt7" / "hanna.db"
    )
    telemetry_path: Path = field(
        default_factory=lambda: Path.home() / ".hanna_gt7" / "telemetry"
    )
    # Retenção por pista — decisão do usuário: histórico limitado, não ilimitado.
    # 0 desliga a exclusão automática. Agora é configuração, não constante
    # enterrada no módulo de banco (era P8 na auditoria).
    keep_recent_per_track: int = 20
    keep_best_per_track: int = 5


@dataclass(slots=True)
class AIConfig:
    """IA. `api_key` nunca tem padrão — é `SecretStr` vinda do ambiente."""

    provider: str = "anthropic"
    model: str = "claude-opus-5"
    fast_model: str = "claude-haiku-4-5"   # nível 2, respostas em pilotagem
    api_key: SecretStr = field(default_factory=SecretStr)
    enabled: bool = False


@dataclass(slots=True)
class DiscordConfig:
    token: SecretStr = field(default_factory=SecretStr)
    command_prefix: str = "!engineer"
    enabled: bool = False


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    json_format: bool = False
    file_path: Path | None = None


@dataclass(slots=True)
class Settings:
    """Configuração da aplicação inteira, montada num lugar só."""

    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        """Monta a configuração a partir de ambiente + `.env`.

        Precedência: `os.environ` > arquivo `.env` > padrão do dataclass.
        Todas as variáveis usam o prefixo `GT7_` para não colidir com nada.
        """
        file_values = _load_dotenv(env_file or Path(".env"))

        def get(name: str) -> str | None:
            key = f"{ENV_PREFIX}{name}"
            return os.environ.get(key) or file_values.get(key)

        def get_int(name: str, default: int) -> int:
            raw = get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError:
                # Um valor inválido não pode derrubar o app na inicialização:
                # cai para o padrão. O log estruturado registra a substituição.
                return default

        def get_float(name: str, default: float) -> float:
            raw = get(name)
            if raw is None:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        def get_bool(name: str, default: bool) -> bool:
            raw = get(name)
            if raw is None:
                return default
            return raw.strip().lower() in ("1", "true", "yes", "on")

        telemetry = TelemetryConfig(
            source=get("TELEMETRY_SOURCE") or "mock",
            ps_ip=get("PS_IP") or "",
            send_port=get_int("SEND_PORT", 33739),
            receive_port=get_int("RECEIVE_PORT", 33740),
            sample_rate_hz=get_int("SAMPLE_RATE_HZ", 60),
            buffer_size=get_int("BUFFER_SIZE", 4096),
            mock_speed_multiplier=get_float("MOCK_SPEED", 1.0),
        )

        storage_defaults = StorageConfig()
        db_raw = get("DATABASE_PATH")
        tel_raw = get("TELEMETRY_PATH")
        storage = StorageConfig(
            database_path=Path(db_raw) if db_raw else storage_defaults.database_path,
            telemetry_path=Path(tel_raw) if tel_raw else storage_defaults.telemetry_path,
            keep_recent_per_track=get_int("KEEP_RECENT_PER_TRACK", 20),
            keep_best_per_track=get_int("KEEP_BEST_PER_TRACK", 5),
        )

        ai_key = get("AI_API_KEY") or ""
        ai = AIConfig(
            provider=get("AI_PROVIDER") or "anthropic",
            model=get("AI_MODEL") or "claude-opus-5",
            fast_model=get("AI_FAST_MODEL") or "claude-haiku-4-5",
            api_key=SecretStr(ai_key),
            # Só liga se houver chave: assim "IA desligada" é o padrão seguro e
            # o núcleo continua funcionando sem ela (§49).
            enabled=bool(ai_key) and get_bool("AI_ENABLED", True),
        )

        discord_token = get("DISCORD_TOKEN") or ""
        discord = DiscordConfig(
            token=SecretStr(discord_token),
            command_prefix=get("DISCORD_PREFIX") or "!engineer",
            enabled=bool(discord_token) and get_bool("DISCORD_ENABLED", True),
        )

        log_file = get("LOG_FILE")
        logging_config = LoggingConfig(
            level=(get("LOG_LEVEL") or "INFO").upper(),
            json_format=get_bool("LOG_JSON", False),
            file_path=Path(log_file) if log_file else None,
        )

        return cls(
            telemetry=telemetry,
            storage=storage,
            ai=ai,
            discord=discord,
            logging=logging_config,
        )

    def describe(self) -> dict[str, object]:
        """Configuração em forma serializável, **com segredos mascarados**.

        É o que o log de inicialização e a tela de diagnóstico devem usar —
        nunca `dataclasses.asdict`, que traria os valores reais.
        """
        result: dict[str, object] = {}
        for section in fields(self):
            value = getattr(self, section.name)
            result[section.name] = {
                f.name: (
                    repr(getattr(value, f.name))
                    if isinstance(getattr(value, f.name), SecretStr)
                    else getattr(value, f.name)
                )
                for f in fields(value)
            }
        return result
