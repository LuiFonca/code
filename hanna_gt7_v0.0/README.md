# HANNA GT7 — Professional Telemetry Platform

Plataforma de engenharia de corrida para Gran Turismo 7: telemetria em tempo
real, análise de voltas e um engenheiro de corrida com IA que **roda local e de
graça**, fala pelo rádio, e manda o debrief no Discord.

```bash
pip3 install pycryptodome
python3 -m gt7core.demo          # sessão completa, sem PS5 e sem interface
```

---

## O que ele faz

Você pilota. O programa recebe a telemetria do PS5 pela rede, grava cada volta,
e responde três perguntas que um piloto sozinho não consegue responder:

**Onde eu perdi tempo?** Não "você está 2 s atrás", mas *este trecho custou
0,401 s, porque você saiu 3 km/h mais devagar da Curva 1*.

**O que eu faço na próxima volta?** Uma correção por vez, com o trecho e o ganho
estimado. Durante a pilotagem chega como uma frase no rádio; com o carro parado,
como um debrief.

**O que se repete em mim?** Consistência, ponto de frenagem, estilo de
frenagem, travamentos e patinagens por volta, tendência de ritmo.

---

## Instalação

O núcleo depende **só** de `pycryptodome` (o protocolo do GT7 é criptografado
com Salsa20). Todo o resto é opcional e o programa funciona sem cada peça.

```bash
pip3 install -e .                 # núcleo: análise, gravação, linha de comando
pip3 install -e ".[app]"          # + interface gráfica (PySide6)
pip3 install -e ".[discord]"      # + bot do Discord
pip3 install -e ".[dev]"          # + pytest, ruff, mypy
```

A IA local não é um extra do Python — é um servidor de modelo na sua máquina:

```bash
ollama pull qwen3:4b && ollama serve
```

Copie `.env.example` para `.env` e ajuste. **Nada é obrigatório:** o programa
sobe sem nenhuma variável definida.

---

## Rodar

### Sem PS5, sem interface — 30 segundos

```bash
python3 -m gt7core.demo --laps 3
```

Simula uma sessão inteira e imprime o pipeline completo (fonte → motor →
eventos → análise) em Python puro:

```
  ★  Volta  2   1:42.000    3799.1 m    6120 amostras
     Volta  3   1:42.512    3799.1 m    6150 amostras   +0.512s vs melhor

  4 curvas detectadas na melhor volta:
    Curva 1  ápice    900 m   78.2 km/h  raio   224 m  lenta

  ONDE A VOLTA 4 FOI PERDIDA (contra a melhor)
    Diferença total: +1.030 s (recuperáveis: 1.030 s)
      Curva 1: 0.268 s perdidos — velocidade de passagem
```

### A interface

```bash
python3 -m gt7app
```

Sobe com telemetria sintética por padrão — dá para ver o painel funcionando sem
console nenhum. `GT7_MOCK_SPEED=20` acelera o tempo simulado.

Seis páginas: **Ao vivo**, **Análise**, **Comparar**, **Histórico**, **Piloto** e
**Configurações**. `⌘K` abre a paleta de comandos.

### Com o PS5

Precisa de um PlayStation com GT7 na mesma rede, **numa sessão ativa** — no menu
o jogo não transmite nada, e essa é a causa nº 1 de "não funciona".

Tudo pela interface, na página **Configurações**:

1. Fonte: **PS5 na rede**
2. IP do PlayStation (no console: Ajustes → Rede → Ver Status da Conexão)
3. **Testar conexão** — sonda a rede e diz o que está errado, em português
4. **Salvar e aplicar** — grava no `.env` e troca a fonte na hora, sem reiniciar

Enquanto a fonte for sintética, a página *Ao vivo* mostra um selo amarelo
**DADOS SINTÉTICOS**. Ele existe porque um painel com números convincentes e
inventados, sem dizer que são inventados, não é demonstração — é armadilha.

A mesma sondagem também roda no terminal, com um relatório mais longo:

```bash
python3 -m gt7core.tools.diagnose 192.168.1.50
```

> **macOS:** a partir do Sonoma o sistema exige permissão de *Rede Local* por
> aplicativo. Sem ela o envio falha com o mesmo erro de um IP errado. Confira em
> Ajustes → Privacidade e Segurança → Rede Local.

---

## A ideia que organiza tudo

**O núcleo não conhece ninguém acima dele.**

```
gt7core/     Python puro, ZERO Qt. Roda headless.
   ▲   ▲   ▲   ▲
   │   │   │   └── gt7voice/     rádio falado
   │   │   └────── gt7discord/   bot
   │   └────────── gt7ai/        Race Engineer
   └────────────── gt7app/       interface Qt
```

As setas apontam numa direção só. Isso não depende de disciplina:
`tests/test_architecture.py` varre cada módulo do núcleo por AST e falha o build
se alguém reintroduzir `PySide6` ou qualquer plugin. Um segundo teste prova a
propriedade de forma viva — bloqueia o import de Qt por *meta path finder* e
verifica que o núcleo inteiro sobe assim mesmo. Um terceiro descobre os pacotes
por varredura do diretório e exige que cada um esteja na lista de proibições,
para que um plugin novo não crie um buraco silencioso no guarda.

É o que permite a mesma análise rodar na interface, num teste, no bot e na voz
sem duplicação — e é o que torna 620 testes possíveis, já que nenhum deles
precisa de servidor gráfico, rede ou console ligado.

### Cada camada degrada sozinha

Verificado numa instalação limpa, sem PySide6, sem `anthropic`, sem
`discord.py` e sem sintetizador de voz:

| Sem | O que acontece |
|---|---|
| servidor de IA | O debrief sai da análise numérica, marcado como `análise local` |
| `anthropic` | Idem — o provedor de nuvem nem é montado |
| token do Discord | `build_bot` devolve `None`, o resto roda |
| sintetizador | O programa roda em silêncio |
| PySide6 | O núcleo, a análise e o bot funcionam sem interface |

**Nenhum caminho deixa o piloto sem resposta.**

---

## O Race Engineer

Três níveis, que diferem em latência e formato:

| Nível | Quando | Formato |
|---|---|---|
| rádio | com você na pista | uma frase, falada em voz alta |
| debrief | volta terminada | JSON com ações e ganho estimado |
| relatório | fim da sessão | quatro parágrafos |

### A IA nunca vê telemetria bruta

É a decisão que organiza o resto. Uma volta tem ~6.270 amostras de 27 canais —
**169.290 números**. O que sobe para o modelo são **1.234 caracteres**: o
resultado da análise.

Não é só economia de token. Um modelo lendo uma coluna de 6.000 velocidades não
vai descobrir que você soltou o freio cedo na Curva 3 — os detectores já
descobriram, com aritmética, de graça e sem alucinar. O modelo faz o que faz
bem: priorizar, explicar, virar instrução.

### Local por padrão, e por quê

O trabalho difícil não é do modelo. Detectar a curva, atribuir a perda, medir o
trail braking — tudo isso roda offline e é exato. O que sobra é redigir algumas
linhas, e isso um modelo de 4B na sua máquina faz.

O cliente fala o dialeto compatível com OpenAI, então serve Ollama, llama.cpp,
LM Studio e vLLM mudando só a URL — e usa apenas a biblioteca padrão.

Duas adaptações reais para modelo pequeno, não configuração:

- **Prompt de três regras, não seis.** Regra que o modelo não segue é *pior* que
  regra ausente: dá falsa sensação de proteção.
- **O que saiu do prompt virou mecanismo.** "Devolva JSON" é imposto na
  decodificação pelo esquema. "Não invente número" virou `gt7ai/guard.py`, que
  confere se todo número citado tem origem no contexto e **descarta a resposta**
  quando não tem.

### A nuvem existe, mas não é grátis

A assinatura do claude.ai **não** dá acesso à API — são produtos separados, e a
API exige créditos comprados. Exportar `GT7_AI_API_KEY` troca o provedor
sozinho. Custo medido: US$ 0,0063 por debrief com 88% de acerto de cache,
~US$ 0,13 numa sessão de 20 voltas.

---

## O rádio, a tela e o celular

O mesmo conselho chega por três caminhos, e cada um recebe o que faz sentido no
seu momento.

**A tela** mostra o cartão com título, raciocínio e ações — e um selo dizendo se
veio do modelo ou da aritmética. Numa máquina apertada o segundo caso é
frequente, e a confiança que se deposita em cada um deveria ser diferente.

**A voz** fala só o nível 1. Ler quatro parágrafos em movimento ocupa o canal
por meio minuto e você não retém nada. Nota nova interrompe a anterior, sem
fila: enfileirar significaria falar da Curva 1 quando você já está na 3, e
conselho fora de hora manda corrigir a curva errada.

**O Discord** serve o momento parado. Uma sessão de 5 voltas produz 5 mensagens:

```
**Sessão iniciada** — Suzuka Circuit
★ **1:43.553**  melhor da sessão
★ **1:42.000**  melhor da sessão
**Sessão encerrada** — Suzuka Circuit · 4 volta(s) · melhor 1:42.000
**Relatório de sessão** …
```

Os eventos ao vivo **não** viram mensagem: são doze numa sessão de duas voltas, e
no celular isso é spam — spam ensina a silenciar o canal, o que mata junto as
mensagens que importavam.

Comandos do bot são descobertos por varredura de diretório. Um arquivo novo em
`commands/` aparece sozinho no `help`, sem lista para atualizar.

**Para onde ele escreve** se configura em *Configurações* (servidor e canal, por
nome). Deixando vazio, o bot usa o primeiro canal onde consegue escrever — o que
funciona por acidente com um servidor só. Se você pedir um canal que não existe,
ele fica **em silêncio** e registra o motivo no log: publicar num canal que você
não escolheu seria pior, porque você teria motivo para achar que configurou certo.

---

## Estrutura

```
gt7core/          Núcleo — Python puro, ZERO Qt
  domain/           modelos (TelemetryPoint, Lap, Session, Car, Track)
  telemetry/        protocolo GT7, motor, fontes (mock/udp/replay)
  analytics/        delta, curvas, frenagem, acelerador, pneus, perda de
                    tempo, perfil do piloto, detecção ao vivo
  session/          SessionManager (política) + RecordingService (mecânica)
  storage/          SQLite: banco, migrações, repositórios
  catalog/          527 carros e 105 circuitos do GT7
  events/           barramento publish/subscribe thread-safe
  config/           configuração centralizada + segredos mascarados
  observability/    logging estruturado + métricas
  tools/            diagnóstico de rede, sem interface
  demo.py           demonstração executável

gt7app/           Interface — a única parte que conhece Qt
  design/           tokens + folha de estilo (Python puro, sem Qt)
  widgets/          cartões, gráficos, mapa de pista, conselho, rádio, ⌘K
  pages/            ao vivo, análise, comparação, histórico, piloto
  services/         engenheiro fora da thread da UI
  adapters/         QtEventBusAdapter (entrega eventos na thread certa)
  application.py    composition root

gt7ai/            Race Engineer          gt7discord/    Bot
  local.py    provedor padrão              sink.py       fronteira testável
  client.py   nuvem (chave paga)           formatting.py domínio → texto
  guard.py    barra número inventado       notifier.py   o que vira mensagem
  prompts.py  análise, nunca telemetria    commands/     um arquivo por comando
  engineer.py os três níveis               bot.py        discord.py

gt7voice/         Rádio falado
  speaker.py   quem fala (protocolo)
  system.py    say / SAPI / espeak-ng      tests/    620 testes
  radio.py     o que vira fala             docs/     a auditoria de origem
```

### Fluxo de dados

```
    fonte          Gt7UdpTelemetrySource (PS5) │ Mock (sintética) │ Replay
      │            as três atrás do mesmo contrato
      ▼
    TelemetryEngine    valida → decodifica → normaliza → distância → forças G
      │
      ▼
    EventBus       TelemetryReceived · LapBoundaryDetected · RaceEventDetected
      ├──────────┬─────────────┬──────────────┬────────────┐
      ▼          ▼             ▼              ▼            ▼
   Analytics    UI         Gravação      Race Engineer   Discord
```

A aplicação **não sabe** se a fonte é ao vivo, sintética ou replay. A escolha
acontece num lugar só (`sources/factory.py`), a partir da configuração.

---

## Desenvolvimento

```bash
pip3 install -e ".[dev]"

python3 -m pytest              # 620 testes, ~2min15
python3 -m ruff check .        # lint
python3 -m mypy                # tipos (strict, 93 arquivos)
```

Num Linux sem servidor gráfico o `conftest.py` já liga o `QT_QPA_PLATFORM=offscreen`
sozinho — sem isso o Qt não falharia, **abortaria** o interpretador e levaria
junto o relatório dos testes que já tinham passado.

Três coisas que este projeto trata como não-negociáveis:

**Nenhum teste precisa de rede, chave ou hardware.** O modelo é substituído por
`ScriptedClient`, o Discord por `RecordingSink`, a voz por `RecordingSpeaker`, o
PS5 por `MockTelemetrySource`. Um teste que precisa de token é um teste que
ninguém roda.

**Medir antes de otimizar, e depois de afirmar.** A Fase 8 prometia "a interface
não congela"; a medição encontrou 938 ms de bloqueio — e não era a IA, era um
cálculo quadrático no gráfico que estava lá desde a Fase 5.

**Renderizar de verdade.** Vários defeitos de interface só apareceram ao gravar
a tela em PNG e olhar: um cartão invisível, um rádio preso em "pensando", uma
nota que sumia sozinha. "Monta sem estourar" não é verificação.

---

## Configuração

Tudo por ambiente ou `.env`, com precedência `ambiente > .env > padrão`.
**Segredo nenhum tem valor padrão**, e `SecretStr` mascara o valor em log,
`repr()` e traceback — porque a forma mais comum de vazar uma chave não é
commitá-la, é imprimir o objeto de configuração numa mensagem de diagnóstico.

| Variável | Padrão | O quê |
|---|---|---|
| `GT7_TELEMETRY_SOURCE` | `mock` | `mock` · `udp` · `replay` |
| `GT7_PS_IP` | *(vazio)* | IP do PlayStation |
| `GT7_AI_ENABLED` | `true` | O engenheiro |
| `GT7_AI_PROVIDER` | `local` | `local` · `anthropic` |
| `GT7_AI_LOCAL_MODEL` | `qwen3:4b` | Modelo no Ollama |
| `GT7_AI_LOCAL_TIMEOUT_S` | `30` | Máquina lenta pode precisar de mais |
| `GT7_AI_API_KEY` | *(vazio)* | Chave paga; sua presença troca o provedor |
| `GT7_VOICE_ENABLED` | `false` | Falar a nota em voz alta |
| `GT7_DISCORD_TOKEN` | *(vazio)* | Token do bot |
| `GT7_KEEP_RECENT_PER_TRACK` | `20` | Retenção; `0` desliga |

Ver `.env.example` para a lista completa, com os porquês.

---

## Origem

Este projeto nasceu de uma **auditoria arquitetural** (`docs/ARCHITECTURE_REVIEW.md`)
de uma aplicação anterior que funcionava e não tinha um único teste. A auditoria
encontrou, entre outros:

- **P1** — zero testes → hoje 549
- **P2** — o domínio importava Qt, o que bloqueava bot, IA e testes de uma vez
- **P3** — o IP da LAN doméstica do autor, hardcoded e versionado
- **R2** — thread única saturando ao adicionar IA → fronteira assíncrona medida

A árvore antiga foi removida depois que a migração terminou — mas só depois de
portar o que ainda tinha valor: o catálogo do jogo (o protocolo manda um
`car_id` numérico e silêncio sobre a pista) e a ferramenta de diagnóstico de
rede.

Uma correção que vale registrar, porque contraria a própria auditoria: o
documento classificou como severidade alta que a distância era integrada por
retângulos. A correção para trapézio foi feita, mas a medição mostrou que o erro
real era de ~0,05% — a severidade estava errada, e está anotada como errada.

**Os dados de carros e circuitos vêm da comunidade do GT7, não da Polyphony
Digital.** Podem envelhecer a cada atualização do jogo; um id desconhecido
devolve `None` em vez de erro.

## Licença

Uso pessoal. Não afiliado à Sony, à Polyphony Digital ou à Gran Turismo.
