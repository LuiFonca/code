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
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

from ..analytics.steering import DEFAULT_STEERING_RATIO, DEFAULT_WHEELBASE_M

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


def user_dir() -> Path:
    """Pasta do usuário onde o programa guarda o que é dele.

    O banco e a telemetria já moravam aqui; a configuração não, e essa
    assimetria custou uma conexão. Ela ficava em `./.env` — **relativo ao
    diretório de trabalho** —, então descompactar uma versão nova numa pasta
    nova começava do zero: fonte de volta em `mock`, IP vazio, e o programa
    subindo o gerador sintético sem nunca falar com o console.

    Função e não constante de módulo porque `Path.home()` precisa ser lido na
    chamada: os testes trocam `HOME` para não escrever na casa de quem roda a
    suíte, e uma constante avaliada no import congelaria o valor antigo.
    """
    return Path.home() / ".hanna_gt7"


#: Configuração ao lado do código, do jeito que sempre esteve. Continua valendo
#: quando existe — é o `.env` de uma cópia de desenvolvimento, e ignorá-lo
#: mandaria alguém editar um arquivo que o programa deixou de ler.
LOCAL_ENV = Path(".env")


def resolve_env_path() -> Path:
    """Qual `.env` mandar — e o resgate do que estava na pasta do programa.

    Regra: o `.env` local vence quando existe; senão, o do usuário. O local é
    uma declaração explícita ("esta pasta tem a sua própria configuração") e
    quem trabalha no código conta com ela.

    A semeadura é o que faz a troca não doer. Na primeira vez que se encontra um
    `.env` local sem contraparte na pasta do usuário, ele é **copiado** para lá.
    Copiado e não movido: mover mudaria o comportamento da pasta de onde o
    arquivo saiu, e é regra desta função não estragar o que já funciona. A cópia
    é semente, não espelho — ela deixa a próxima versão, descompactada numa
    pasta nova e portanto sem `.env` local, já nascer configurada.
    """
    do_usuario = user_dir() / ".env"
    if not LOCAL_ENV.is_file():
        return do_usuario

    if not do_usuario.exists():
        try:
            do_usuario.parent.mkdir(parents=True, exist_ok=True)
            do_usuario.write_text(
                LOCAL_ENV.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            # Disco cheio, permissão, home somente-leitura. Semear é conveniência
            # e nenhuma conveniência justifica impedir o programa de subir: o
            # `.env` local, que é o que vale agora, foi lido do mesmo jeito.
            pass

    return LOCAL_ENV


@dataclass(slots=True)
class StorageConfig:
    database_path: Path = field(default_factory=lambda: user_dir() / "hanna.db")
    telemetry_path: Path = field(default_factory=lambda: user_dir() / "telemetry")
    # Retenção por pista — decisão do usuário: histórico limitado, não ilimitado.
    # 0 desliga a exclusão automática. Agora é configuração, não constante
    # enterrada no módulo de banco (era P8 na auditoria).
    keep_recent_per_track: int = 20
    keep_best_per_track: int = 5


@dataclass(slots=True)
class AIConfig:
    """IA. O padrão é **local e gratuito**; a nuvem é opção com chave paga.

    A inversão em relação à primeira versão é deliberada. A análise da Fase 4
    já roda offline e de graça, e o que sobra para o modelo é redigir algumas
    linhas de diagnóstico — trabalho que um modelo pequeno rodando na máquina
    do piloto faz sem custo, sem rede e sem conta em lugar nenhum.

    `api_key` nunca tem padrão: é `SecretStr` vinda do ambiente, e sem ela o
    provedor `anthropic` simplesmente não é montado.
    """

    provider: str = "local"
    """`local` (padrão) ou `anthropic`."""

    # --- provedor local -----------------------------------------------------
    local_url: str = "http://localhost:11434/v1"
    """Endpoint compatível com OpenAI. Serve Ollama, llama.cpp, LM Studio."""

    local_model: str = "qwen3:4b"
    local_fast_model: str = "qwen3:4b"
    """Mesmo modelo nos dois níveis por padrão: com 4B, carregar um segundo
    modelo custa mais em troca de memória do que economiza em latência."""

    local_timeout_s: float = 30.0

    # --- provedor de nuvem (opcional, exige chave paga) ---------------------
    model: str = "claude-opus-5"
    fast_model: str = "claude-haiku-4-5"   # nível 1, respostas em pilotagem
    api_key: SecretStr = field(default_factory=SecretStr)

    enabled: bool = False
    """**Desligada por padrão.**

    Ligada, ela tenta falar com um servidor local que a maioria das instalações
    não tem de pé, e o resultado é um aviso no log a cada volta — ruído que
    parece defeito. A análise da Fase 4, que é determinística e gratuita,
    responde sem ela; a IA é um acréscimo, e acréscimo se liga por escolha.

    Quem quiser: marque "Usar modelo de linguagem" em Configurações, ou
    `GT7_AI_ENABLED=true` no `.env`.
    """

    @property
    def is_local(self) -> bool:
        return self.provider != "anthropic"


@dataclass(slots=True)
class DiscordConfig:
    token: SecretStr = field(default_factory=SecretStr)
    command_prefix: str = "!engineer"
    enabled: bool = False

    guild: str = ""
    """Nome do servidor. Vazio = qualquer um onde o bot esteja."""

    channel: str = ""
    """Nome do canal, sem `#`. Vazio = o primeiro onde o bot pode escrever.

    Nome e não ID: ninguém sabe de cor o ID numérico de um canal, e obtê-lo
    exige ligar o modo desenvolvedor do Discord. O nome é o que aparece na
    barra lateral, e é o que a pessoa vai digitar.

    O padrão vazio preserva o comportamento antigo — que era o **único**
    comportamento e é o defeito que estes campos existem para corrigir: sem
    destino configurado, o bot escrevia no primeiro canal onde tivesse
    permissão, em qualquer servidor. Num servidor com `#regras` antes de
    `#telemetria`, o debrief da sessão ia parar no lugar errado.
    """


@dataclass(slots=True)
class VoiceConfig:
    """Voz. Local e gratuita, como o resto — usa o sintetizador do sistema.

    Sem dependência nova: macOS tem `say`, Windows tem SAPI e Linux tem
    `espeak-ng` ou `spd-say`. Instalar um motor de TTS em Python para falar uma
    frase por volta seria pagar uma dependência (e memória, numa máquina que já
    está apertada) por conveniência nenhuma.
    """

    enabled: bool = False
    """Desligada por padrão: som que aparece sem ser pedido assusta."""

    voice: str = ""
    """Nome da voz do sistema. Vazio usa a padrão — que costuma seguir o idioma
    configurado, e portanto já fala português numa máquina brasileira."""

    rate_wpm: int = 200
    """Palavras por minuto. Acima do normal de propósito: o piloto está no meio
    de uma curva, e uma nota lenta chega depois de deixar de ser útil."""

    max_seconds: float = 8.0
    """Teto de duração de uma fala. Protege contra um conselho longo demais
    ocupar o rádio enquanto três curvas passam."""


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    json_format: bool = False
    file_path: Path | None = None


@dataclass(slots=True)
class UIConfig:
    """Preferências de interface.

    Moram no núcleo, e não em `gt7app`, porque configuração é configuração —
    quebrar a precedência ambiente > .env > padrão só para o tema seria
    inventar um segundo mecanismo. O núcleo guarda o **nome** do tema, uma
    string opaca para ele; quem sabe o que significa é o design system.
    """

    theme: str = "dark"
    start_page: str = "live"


@dataclass(slots=True)
class VehicleConfig:
    """As grandezas do carro que o GT7 **não transmite**.

    Nem o pacote nem o catálogo do jogo trazem dimensão de carro — o catálogo é
    nome, fabricante e nada mais. Estes dois números são o que falta para
    converter a curvatura da trajetória, que é medida, em ângulo de volante, que
    não é.

    São configuração, e não constante no código, exatamente porque são
    suposição: deixá-los editáveis é a diferença entre um número que o programa
    inventou e um número que você declarou. Quem corre sempre de GT3 acerta uma
    vez (2,55 m e 12:1) e o eixo passa a valer para o carro dele.

    Só escalam o gráfico de volante. Nada mais no programa os lê.

    Os padrões vêm de `analytics.steering`, e não repetidos aqui: é lá que mora
    o porquê de cada número, e um 2,6 escrito nos dois lugares é como os dois
    lados passam a discordar depois de alguém ajustar um deles.
    """

    wheelbase_m: float = DEFAULT_WHEELBASE_M
    steering_ratio: float = DEFAULT_STEERING_RATIO


@dataclass(slots=True)
class Settings:
    """Configuração da aplicação inteira, montada num lugar só."""

    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    vehicle: VehicleConfig = field(default_factory=VehicleConfig)

    env_path: Path = field(default_factory=lambda: user_dir() / ".env")
    """De onde esta configuração veio — e para onde a tela grava de volta.

    Guardado no objeto porque quem salva (a página de configuração) não é quem
    carrega, e reconstruir o caminho do outro lado significaria repetir a
    escolha do carregador. Repetir uma decisão é como as duas cópias divergem:
    bastaria alguém passar `env_file` num teste para a tela gravar num arquivo
    diferente do que o programa lê.
    """

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        """Monta a configuração a partir de ambiente + `.env`.

        Precedência: `os.environ` > arquivo `.env` > padrão do dataclass.
        Todas as variáveis usam o prefixo `GT7_` para não colidir com nada.
        """
        env_path = env_file or resolve_env_path()
        file_values = _load_dotenv(env_path)

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
        # Sem `GT7_AI_PROVIDER` explícito, a presença da chave decide: quem
        # exportou uma chave paga quer usá-la; quem não exportou quer o local.
        ai_provider = get("AI_PROVIDER") or ("anthropic" if ai_key else "local")
        ai = AIConfig(
            provider=ai_provider,
            local_url=get("AI_LOCAL_URL") or "http://localhost:11434/v1",
            local_model=get("AI_LOCAL_MODEL") or "qwen3:4b",
            local_fast_model=(
                get("AI_LOCAL_FAST_MODEL") or get("AI_LOCAL_MODEL") or "qwen3:4b"
            ),
            local_timeout_s=get_float("AI_LOCAL_TIMEOUT_S", 30.0),
            model=get("AI_MODEL") or "claude-opus-5",
            fast_model=get("AI_FAST_MODEL") or "claude-haiku-4-5",
            api_key=SecretStr(ai_key),
            # Desligada por padrão: sem servidor de pé, ligada só produz um
            # aviso por volta no log. A nuvem, além disso, exige chave — sem
            # ela, "ligada" não significaria nada.
            enabled=get_bool("AI_ENABLED", False)
            and (ai_provider != "anthropic" or bool(ai_key)),
        )

        discord_token = get("DISCORD_TOKEN") or ""
        discord = DiscordConfig(
            token=SecretStr(discord_token),
            command_prefix=get("DISCORD_PREFIX") or "!engineer",
            enabled=bool(discord_token) and get_bool("DISCORD_ENABLED", True),
            guild=get("DISCORD_GUILD") or "",
            channel=get("DISCORD_CHANNEL") or "",
        )

        voice = VoiceConfig(
            enabled=get_bool("VOICE_ENABLED", False),
            voice=get("VOICE_NAME") or "",
            rate_wpm=get_int("VOICE_RATE", 200),
            max_seconds=get_float("VOICE_MAX_SECONDS", 8.0),
        )

        log_file = get("LOG_FILE")
        logging_config = LoggingConfig(
            level=(get("LOG_LEVEL") or "INFO").upper(),
            json_format=get_bool("LOG_JSON", False),
            file_path=Path(log_file) if log_file else None,
        )

        ui = UIConfig(
            theme=(get("UI_THEME") or "dark").strip().lower(),
            start_page=(get("UI_START_PAGE") or "live").strip().lower(),
        )

        vehicle_defaults = VehicleConfig()
        vehicle = VehicleConfig(
            wheelbase_m=get_float("WHEELBASE_M", vehicle_defaults.wheelbase_m),
            steering_ratio=get_float(
                "STEERING_RATIO", vehicle_defaults.steering_ratio
            ),
        )

        return cls(
            telemetry=telemetry,
            storage=storage,
            ai=ai,
            discord=discord,
            voice=voice,
            logging=logging_config,
            ui=ui,
            vehicle=vehicle,
            env_path=env_path,
        )

    def describe(self) -> dict[str, object]:
        """Configuração em forma serializável, **com segredos mascarados**.

        É o que o log de inicialização e a tela de diagnóstico devem usar —
        nunca `dataclasses.asdict`, que traria os valores reais.
        """
        result: dict[str, object] = {}
        for section in fields(self):
            value = getattr(self, section.name)
            # Nem todo campo é uma seção: `env_path` é um `Path` solto, e
            # `fields()` sobre ele levanta TypeError. Descrever o valor direto
            # é o que se quer de qualquer forma — ele não tem sub-campos, e o
            # caminho do `.env` é exatamente o tipo de coisa que ajuda num
            # relato de problema.
            if not is_dataclass(value):
                result[section.name] = str(value)
                continue
            result[section.name] = {
                f.name: (
                    repr(getattr(value, f.name))
                    if isinstance(getattr(value, f.name), SecretStr)
                    else getattr(value, f.name)
                )
                for f in fields(value)
            }
        return result
