# HANNA GT7 — Professional Telemetry Platform

Plataforma de engenharia de corrida para Gran Turismo 7: telemetria em tempo
real, análise de voltas e — nas fases seguintes — Race Engineer com IA, Discord
e voz.

**Estado: Fase 2 concluída.** O núcleo (`gt7core`) roda headless com 157 testes
e 80% de cobertura, e já tem as três fontes de telemetria (ao vivo, sintética e
replay) atrás do mesmo contrato. A interface (`src/`) ainda é a versão anterior:
o adaptador que a liga ao núcleo novo (`gt7app/adapters/qt_bus.py`) está pronto e
testado, mas a migração das abas é trabalho da Fase 3.

---

## Rodar em 30 segundos, sem PS5 e sem interface gráfica

```bash
pip3 install pycryptodome
python3 -m gt7core.demo
```

Isso simula uma sessão completa e imprime tempos de volta, melhor volta,
perfil de velocidade e delta — o pipeline inteiro (fonte → motor → eventos →
analytics) rodando em Python puro:

```
  ★  Volta  2   1:42.000    3799.1 m    6120 amostras
     Volta  3   1:42.512    3799.1 m    6150 amostras   +0.512s vs melhor

  Delta da volta 4 contra a melhor:
        950 m   +0.208 s
       1900 m   +0.488 s
       2849 m   +0.783 s
```

`--laps N` muda o número de voltas; `--verbose` liga o log detalhado.

## Rodar a interface gráfica (versão anterior, funcional)

```bash
pip3 install PySide6 pycryptodome
python3 -m src.main
```

Requer um PlayStation com GT7 na mesma rede. Se não receber telemetria,
diagnostique a rede antes de mexer em qualquer coisa:

```bash
python3 src/tools/diagnose.py <IP-do-PlayStation>
```

> **macOS:** a partir do Sonoma o sistema exige permissão de *Rede Local* por
> aplicativo. Sem ela o envio falha com o mesmo erro de um IP errado. Confira em
> Ajustes → Privacidade e Segurança → Rede Local.

## Desenvolvimento

```bash
pip3 install -e ".[dev]"

python3 -m pytest tests/            # 157 testes
python3 -m ruff check gt7core/      # lint
python3 -m mypy                     # tipos (strict em gt7core)
```

---

## Estrutura

```
gt7core/          Núcleo — Python puro, ZERO Qt. Roda headless.
  domain/           modelos (TelemetryPoint, Lap, Session, Car, Track)
  telemetry/        protocolo GT7, motor, fontes (mock/udp/replay)
  analytics/        delta alinhado por distância, consulta de canais
  events/           barramento publish/subscribe thread-safe
  config/           configuração centralizada + segredos mascarados
  observability/    logging estruturado + métricas de captura
  demo.py           demonstração executável

gt7app/           Casca de interface — a única parte que conhece Qt
  adapters/         QtEventBusAdapter (entrega eventos na thread da UI)

src/              Interface PySide6 (arquitetura anterior, funcional)
tests/            157 testes
docs/             ARCHITECTURE_REVIEW.md — a auditoria que originou este plano
```

### A regra que sustenta tudo

**`gt7core` não importa Qt, nem nada acima dele.** É isso que permite ao núcleo
rodar sem interface gráfica — e portanto ser usado por testes, por um bot do
Discord e por um worker de IA, todos previstos no roadmap.

A regra não depende de disciplina: `tests/test_architecture.py` varre cada
módulo do núcleo por AST e falha o build se alguém reintroduzir `PySide6`,
`gt7app`, `gt7ai` ou `gt7discord`. Um segundo teste prova a propriedade de forma
viva — bloqueia o import de Qt por meta path finder e verifica que o núcleo
inteiro sobe assim mesmo. Vale em qualquer ambiente, inclusive num container
onde o Qt está instalado (que é o caso quando se testa o adaptador).

### Fluxo de dados

```
    fonte de telemetria          Gt7UdpTelemetrySource (PS5 real)
    (mesma interface)            MockTelemetrySource   (sintética)
                                 ReplayTelemetrySource (arquivo gravado)
              │
              ▼
    TelemetryEngine        valida → decodifica → normaliza
                           → distância (trapézio) → forças G
              │
              ▼
    EventBus               TelemetryReceived, LapBoundaryDetected
         ┌────┴────┬─────────────┐
         ▼         ▼             ▼
    Analytics    UI          Gravação
```

A aplicação **não sabe** se a fonte é ao vivo, sintética ou replay. As três
satisfazem o mesmo contrato e a escolha acontece num único lugar
(`sources/factory.py`), a partir da configuração — é o que atende ao replay
(§40) e ao suporte a outros simuladores (§42) sem código adicional.

### Gravar e reproduzir uma sessão

```python
from gt7core.telemetry.recording import SessionRecorder, ReplayTelemetrySource

with SessionRecorder("sessao.gt7rec") as recorder:
    source.on_frame(recorder.record)      # grava enquanto pilota
    source.start()

replay = ReplayTelemetrySource("sessao.gt7rec", speed_multiplier=4.0)
```

O replay entrega **exatamente os mesmos quadros** da captura original — há um
teste que compara os dois lado a lado. Como respeita os intervalos originais,
exercita watchdogs e taxas de repintura no mesmo ritmo do ao vivo.

---

## Configuração

Copie `.env.example` para `.env` e ajuste. Nada é obrigatório: o programa sobe
sem nenhuma variável definida.

```bash
cp .env.example .env
```

**Segredos nunca têm valor padrão e nunca aparecem em log.** Chave de IA e token
do Discord vêm do ambiente ou não existem; o tipo `SecretStr` mascara o valor em
`repr()`, `str()` e em qualquer traceback. Sem chave, IA e Discord ficam
desligados e o resto funciona igual — conforme o princípio de que a IA é módulo
adicional, nunca o núcleo.

Precedência: variável de ambiente > arquivo `.env` > padrão do código.

---

## Roadmap

| Fase | Escopo | Estado |
|---|---|---|
| 0 | Auditoria arquitetural | ✅ concluída (`docs/ARCHITECTURE_REVIEW.md`) |
| 1 | Núcleo headless, config, logging, testes, mock | ✅ concluída |
| 2 | Fonte UDP no novo contrato, replay, adaptador Qt, métricas | ✅ concluída |
| 3 | Migrar as abas para o núcleo; sessões persistidas, Parquet | ⬜ |
| 4 | Frenagem, throttle, curvas, pneus, perfil do piloto | ⬜ |
| 5 | Design system, navegação por páginas, command palette | ⬜ |
| 6 | Mapa de pista, detecção de curvas, perda de tempo | ⬜ |
| 7 | Race Engineer (IA em três níveis) | ⬜ |
| 8-10 | Discord, voz, hardening | ⬜ |

O que a Fase 1 resolveu, com a numeração da auditoria:

- **P1** zero testes → 102 testes na Fase 1 (157 hoje)
- **P2** domínio dependia de Qt → núcleo headless + teste de arquitetura
- **P3** sem configuração; IP de LAN commitado → `Settings` + `SecretStr` + `.env.example`
- **P4** só a fonte UDP real → gerador sintético determinístico
- **P11** sem logging estruturado → formatter JSON, `print()` eliminado
- **P12** strings mágicas de estado → `ConnectionState` (StrEnum)
- **P14** 56 `.pyc` e `.DS_Store` versionados → `.gitignore`, índice limpo

E o que a Fase 2 acrescentou:

- **§40 replay** → `SessionRecorder` + `ReplayTelemetrySource` + formato `.gt7rec`
- **§35 observabilidade** → contadores de pacote (recebidos, inválidos, descartados, pkt/s)
- **P2 (continuação)** → fonte UDP portada para `threading` puro, sem `QThread`
- **Adaptador Qt** → a garantia de thread da interface preservada numa peça só

---

## Uma correção da própria auditoria

O documento de Fase 0 classificou como severidade alta (P5) que a distância era
integrada pela regra do retângulo, afirmando que o erro *"acumula ao longo da
volta"*. **Isso está errado**, e a medição feita ao escrever os testes mostra
por quê: o erro por trecho monotônico é `(v_fim − v_ini)·dt/2`, e numa volta
fechada — que termina na mesma velocidade em que começou — a soma telescopa e se
cancela. Sobre uma volta completa: diferença de 0,000 m entre os dois métodos.

O efeito real é local, não cumulativo: ~0,5 m dentro de uma zona de frenagem,
que é justamente onde o delta é lido. A troca para a regra do trapézio ficou
(é exata para velocidade linear e não custa nada), mas a severidade era menor do
que registrei. O teste `test_erro_do_retangulo_cancela_na_volta_fechada` fixa o
comportamento real.

---

## Licença e origem

Projeto pessoal. O protocolo GT7 vem de engenharia reversa da comunidade — não
há especificação oficial, e os offsets em `gt7core/telemetry/protocol.py` foram
validados contra pacotes reais.
