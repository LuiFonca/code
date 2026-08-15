# HANNA GT7 — Professional Telemetry Platform

Plataforma de engenharia de corrida para Gran Turismo 7: telemetria em tempo
real, análise de voltas e — nas fases seguintes — Race Engineer com IA, Discord
e voz.

**Estado: Fase 5 concluída.** O núcleo (`gt7core`) roda headless com 303 testes
e mypy strict sobre 60 arquivos. As três fontes de telemetria ficam atrás do
mesmo contrato, sessões e voltas são persistidas, a análise de engenharia de
pista da Fase 4 está completa — e agora tem interface: cinco páginas sobre um
design system, com navegação lateral e paleta de comandos (⌘K).

A migração das abas terminou junto: `histórico`, `telemetria` e `comparação`
agora rodam sobre o núcleo, e a aplicação em `src/` deixou de ser necessária —
segue no repositório como referência até a Fase 6.

---

## Rodar em 30 segundos, sem PS5 e sem interface gráfica

```bash
pip3 install pycryptodome
python3 -m gt7core.demo
```

Isso simula uma sessão completa e imprime tempos de volta, melhor volta, perfil
de velocidade, delta e o relatório de engenharia de pista — o pipeline inteiro
(fonte → motor → eventos → analytics) rodando em Python puro:

```
  ★  Volta  2   1:42.000    3799.1 m    6120 amostras
     Volta  3   1:42.512    3799.1 m    6150 amostras   +0.512s vs melhor

  4 curvas detectadas na melhor volta:
    Curva 1  ápice    900 m   78.2 km/h  raio   224 m  lenta
    Curva 3  ápice   2580 m   61.7 km/h  raio   147 m  lenta

  Frenagens:
    Zona 1  início    669 m  0.56 g  pico 100%  trail 0.19

  Saídas de curva:
    Curva 3  acelera +4 m do ápice, não chegou a pedal cheio, 2 patinagem(ns)

  ONDE A VOLTA 4 FOI PERDIDA (contra a melhor)
    Diferença total: +1.030 s (recuperáveis: 1.030 s)
      Curva 1: 0.268 s perdidos — velocidade de passagem
```

`--laps N` muda o número de voltas; `--verbose` liga o log detalhado.

## Rodar a interface

```bash
pip3 install PySide6 pycryptodome

python3 -m gt7app          # interface nova, sobre o núcleo
python3 -m src.main        # aplicação anterior, com as 4 abas
```

A interface nova sobe com telemetria sintética por padrão — dá para ver o painel
funcionando sem console nenhum. Para acelerar o tempo simulado:

```bash
GT7_MOCK_SPEED=20 python3 -m gt7app
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

python3 -m pytest tests/            # 303 testes
python3 -m ruff check gt7core/      # lint
python3 -m mypy                     # tipos (strict em gt7core e gt7app)
```

---

## Estrutura

```
gt7core/          Núcleo — Python puro, ZERO Qt. Roda headless.
  domain/           modelos (TelemetryPoint, Lap, Session, Car, Track)
  telemetry/        protocolo GT7, motor, fontes (mock/udp/replay)
  analytics/        delta, curvas, frenagem, acelerador, pneus,
                    perda de tempo, perfil do piloto
  events/           barramento publish/subscribe thread-safe
  config/           configuração centralizada + segredos mascarados
  observability/    logging estruturado + métricas de captura
  demo.py           demonstração executável

gt7core/
  session/          SessionManager (política) + RecordingService (mecânica)
  storage/          SQLite: banco, migrações e repositórios

gt7app/           Casca de interface — a única parte que conhece Qt
  design/           tokens + folha de estilo (Python puro, sem Qt)
  widgets/          cartões, gráficos, mapa de pista, paleta de comandos
  pages/            ao vivo, análise, comparação, histórico, piloto
  adapters/         QtEventBusAdapter (entrega eventos na thread da UI)
  viewmodels/       estado de tela, sem widgets
  application.py    composition root: monta o grafo de baixo para cima
  commands.py       registro de comandos (Python puro)
  shell.py          janela: navegação lateral + páginas + ⌘K

src/              Interface PySide6 (arquitetura anterior, funcional)
tests/            303 testes
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
| 3 | Sessões persistidas, retenção, composition root | ✅ concluída |
| 3b | Migrar as 3 abas restantes para o núcleo | ✅ concluída na Fase 5 |
| 4 | Curvas, frenagem, throttle, pneus, perda de tempo, perfil | ✅ concluída |
| 5 | Design system, navegação por páginas, command palette | ✅ concluída |
| 6 | Mapa de pista 2D, sobreposição de traçados | ⬜ |
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

E a Fase 3:

- **P9 sessões efêmeras** → tabela `sessions`, migração v6 e `find_unfinished()`
  para a recuperação após falha do §8
- **P8 retenção silenciosa** → configurável (padrão 20 recentes + 5 melhores por
  pista); `0` desliga. O recorde nunca é apagado, mesmo saindo da janela
- **Composition root** → `gt7app/application.py` monta o grafo inteiro; o Qt só
  entra nos dois últimos passos

E a Fase 4 — a camada de engenharia de pista:

- **§12 curvas** → `detect_corners()` pelos mínimos locais do perfil de
  velocidade, com raio estimado pela curvatura do traçado
- **§13 frenagem** → zonas contínuas, pressão de pico, `trail_braking_ratio`,
  desaceleração em g e comparação com a referência
- **§14 acelerador** → ponto de retomada em relação ao ápice, tempo até pedal
  cheio, contagem de alívios e patinagem na saída
- **§15 pneus** → temperatura por roda e desequilíbrio entre eixos/lados,
  travamento e patinagem como eventos localizados, degradação ao longo do stint
- **§20/§31 perda de tempo** → `analyse_time_loss()` fatia a volta em curvas e
  retas e atribui a cada trecho o tempo ganho ou perdido ali dentro
- **§16 perfil do piloto** → estatística sobre a janela de voltas: consistência,
  repetibilidade das referências, estilo de frenagem, taxa de erro, tendência

### Analisar uma volta

```python
from gt7core.analytics import analyse_time_loss, build_profile, detect_corners

corners = detect_corners(melhor_volta)
print(analyse_time_loss(melhor_volta, volta_de_hoje).summary())
print(build_profile(ultimas_20_voltas).summary())
```

### Três coisas que a Fase 4 corrigiu no que já existia

**O campo de escorregamento não tinha convenção definida.** `tire_slip_*` não
tem especificação oficial e a aplicação anterior o tratava como um valor
adimensional, admitindo no comentário que era aproximação. Como a análise de
pneus depende disso, a escolha virou explícita: `SlipConvention` nomeia as duas
leituras plausíveis (velocidade de superfície em m/s ou razão já normalizada) e
`infer_slip_convention()` decide olhando uma volta inteira — as duas hipóteses
estão a ordens de grandeza de distância, então a inferência é segura. O gerador
sintético passou a emitir m/s, que é a leitura fisicamente derivável. **Se algum
dia um pacote real resolver a questão, há um lugar só para corrigir.**

**O casamento de eventos entre voltas permitia atribuição dupla.** A versão
ingênua — "para cada curva, pegue a mais próxima na outra volta" — deixa duas
referências reclamarem o mesmo evento. Numa chicane isso acontece de verdade, e
o relatório apontaria a mesma freada duas vezes enquanto a que sumiu passaria
despercebida. `matching.py` centraliza a atribuição gulosa por proximidade
global, com cada evento consumido uma vez só.

**O gerador sintético produzia um piloto fisicamente impossível.** A velocidade
era interpolada linearmente entre os pontos do perfil, o que torna a aceleração
constante em cada trecho e os pedais retângulos perfeitos: freio que nunca é
liberado progressivamente (`trail_braking_ratio` zero em toda freada) e
acelerador que nunca chega a fundo. Pior, o acelerador era derivado da
aceleração — inversão de causalidade, já que na realidade o pedal é a entrada e
é o arrasto que faz a aceleração cair com o pé no fundo. O gerador ganhou
interpolação suave, faixa morta de inércia entre freio e acelerador, e uma
catraca no pedal. Sem isso o mock não exercitaria nenhum dos detectores novos —
os saturaria.

E a Fase 5 — a interface:

- **Design system** → `gt7app/design/`: tokens (paleta, espaçamento, tipografia)
  e a folha de estilo gerada a partir deles. **Python puro, sem Qt**, o que
  permite testar a coerência visual headless
- **Cinco páginas** → ao vivo, análise de volta, comparação, histórico e perfil
  do piloto, todas sobre o núcleo. A Fase 4 deixou de existir só no terminal
- **Paleta de comandos (⌘K)** → busca por subsequência (`cmp` acha "Comparar");
  o registro é Python puro e servirá ao Discord e à voz, que operam sobre o
  mesmo vocabulário de ações
- **Widgets próprios** → gráfico por distância com cursor sincronizado e mapa de
  pista, ambos em QPainter
- **mypy estendido ao `gt7app`** → 35 → 60 arquivos em strict

### Duas escolhas da Fase 5 que valem explicação

**Páginas em vez de abas.** Com abas, as quatro telas ficam vivas o tempo todo e
as quatro se atualizam enquanto se olha para uma só. O contrato de `Page` tem
`on_enter`/`on_leave`, e só a página visível trabalha.

**QPainter em vez de QtCharts.** O QtCharts está disponível e a aplicação
anterior o usava. A troca não é gosto: o QtCharts traz a própria linguagem
visual — margens, fontes de eixo, cor de grade — que só se dobra aos tokens até
certo ponto. Misturar as duas produz telas que *quase* combinam, que é pior do
que duas telas assumidamente diferentes. Custou ~200 linhas e entregou controle
total; o que se perde é zoom e pan, que o QtCharts dava de graça.

### O guarda que mantém o sistema sendo um sistema

`tests/test_design_system.py` varre a folha de estilo atrás de hexadecimais que
não venham da paleta e falha o build se encontrar algum. É o equivalente visual
do teste de arquitetura que impede o núcleo de importar Qt — sem ele, um ajuste
apressado escreve `#2a2e3a` direto no QSS, ninguém percebe, e seis meses depois
existem três bordas cinza levemente diferentes que ninguém escolheu.

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
