# ARCHITECTURE REVIEW — GT7 Professional Telemetry Platform

**Fase 0 — Audit.** Documento de análise. Nenhum código de funcionalidade foi
alterado para produzi-lo.

Data: 2026-08-14 · Branch: `claude/gt7-telemetry-platform-suhmhr` · Commit base: `71805e9`

---

## 0. Sumário executivo

O repositório está em um estado melhor do que a maioria dos projetos nesta fase.
Já existe uma refatoração completa para Clean Architecture + MVVM em `src/`,
com separação de camadas real e verificável — rodei a checagem: **nenhum import
aponta para cima e `domain/` não conhece SQL**. O decodificador do protocolo GT7,
o motor de delta alinhado por distância e o schema com migrações incrementais
são de qualidade profissional e devem ser preservados.

O problema não é a arquitetura que existe. É o que **falta** para sustentar o
produto descrito no briefing:

| Bloqueador | Impacto |
|---|---|
| **Zero testes automatizados** | O README descreve validações extensas ("401 amostras × 27 campos idênticas") que não estão commitadas como teste executável. São verificações manuais irrepetíveis. |
| **`domain/` importa Qt** | `TelemetrySource` é um `QObject`. Isso impede rodar o núcleo headless — ou seja, **bloqueia Discord, IA e testes** de uma vez só. |
| **Nenhuma camada de configuração** | O IP da LAN doméstica do autor está hardcoded e commitado. Não há onde colocar token do Discord ou chave de IA. |
| **Nenhuma fonte de telemetria além do UDP real** | Sem mock e sem replay, não há como desenvolver ou testar sem um PS5 na rede. É a causa raiz da ausência de testes. |
| **Distância integrada sem correção de deriva** | Delta, setores e comparação de voltas dependem todos dela. É a fundação de todo o analytics e é a parte menos defendida do sistema. |

**Recomendação central:** não recomeçar. Evoluir `src/` extraindo um núcleo
headless sem Qt. Essa única mudança destrava simultaneamente testes, IA, Discord
e voz — e é pré-requisito de tudo o mais no roadmap.

---

## 1. Inventário do repositório

```
code/
├── .DS_Store                    ← commitado (não deveria)
└── hanna_gt7_v0.0/
    ├── requirements.txt         PySide6>=6.6, pycryptodome>=3.19
    ├── hanna_gt7_ai/            5.337 linhas — árvore LEGADA (monolito)
    └── src/                     7.077 linhas — árvore ATUAL (refatorada)
```

**Duas árvores paralelas coexistem.** `src/` é autossuficiente e é a canônica;
`hanna_gt7_ai/` foi mantida como rede de segurança durante a migração e continua
funcional.

### Stack atual

| Item | Situação |
|---|---|
| Linguagem | Python (`__pycache__` indica **3.14** em uso local; sintaxe exige ≥3.10 por `X \| None`) |
| UI | PySide6 (Qt6) + QtCharts |
| Cripto | pycryptodome (Salsa20 — obrigatório, o pacote GT7 é cifrado) |
| Banco | SQLite (stdlib), schema v5 com migrações |
| Catálogo | CSV — 527 carros, 105 pistas, 72 montadoras |
| Testes | **nenhum** |
| Lint / formatter | **nenhum** |
| CI | **nenhum** |
| Packaging | **nenhum** (sem `pyproject.toml`; `main.py` usa hack de `sys.path`) |
| Config / secrets | **nenhum** (sem `.env`, sem `.env.example`) |

### Higiene do repositório

- **56 de 148 arquivos versionados são `__pycache__/*.pyc`** — 38% do repositório é lixo de build.
- `.DS_Store` commitado na raiz.
- Não existe `.gitignore` na raiz (só dentro de `hanna_gt7_ai/`, cobrindo apenas aquela pasta).
- Dependências sem pin (`>=` apenas) — build não reprodutível.

---

## 2. Inventário de funcionalidades

### Implementado e funcionando

| Área | Estado |
|---|---|
| Recepção UDP do GT7 | Completo — heartbeat adaptativo, reconexão, diagnóstico de erro de rede traduzido |
| Decodificação Salsa20 + parsing | Completo — 41 campos, 12 flags, validação por magic number |
| Detecção de volta | Completo — por virada do contador do jogo |
| Distância acumulada | Implementado — integração da velocidade |
| Força G lateral/longitudinal | Implementado — derivada do vetor velocidade projetada nos eixos do carro |
| Delta ao vivo | Completo — duplo (melhor volta + volta anterior), alinhado por distância |
| Gravação de voltas | Completo — transação única com rollback |
| Setores | Parcial — 3 setores por divisão geométrica de distância (aproximação) |
| Histórico | Completo — listagem, filtro por pista, exclusão, consultas em lote |
| Análise de volta única | Completo — ~18 gráficos de canais, mosaicos de pneu/suspensão/deriva |
| Comparação A/B | Completo — delta, canais sincronizados, grade de setores, traçado |
| Mapa de pista | Parcial — traçado por x/z, sem identificação de curvas |
| Catálogo de carros/pistas | Completo — auto-detecção de carro por `car_id`; sugestão de pista por comprimento |
| Modo replay/IA | Parcial — flag que impede gravação, não é replay de arquivo |

### Não implementado (do briefing)

Sessões persistidas · Análise de curvas · Análise de frenagem · Análise de
throttle · Análise de pneus · Perfil do piloto · Event engine em tempo real ·
IA / Race Engineer · Discord · Voz · Relatórios · Exportação · Command palette ·
Sistema de notificações · Configuração · Logging estruturado · Observabilidade ·
Replay · Mock telemetry · Testes

### Esqueleto morto

`src/infrastructure/storage/file_lap_storage.py` — 59 linhas que só levantam
`NotImplementedError`. Sugere que exportação existe. Não existe.

---

## 3. Inventário arquitetural

### O que está em pé

```
presentation  ──▶  application  ──▶  domain  ◀──  infrastructure
```

Verificado nesta auditoria, não apenas documentado:

- ✅ `python -m compileall src/` — limpo
- ✅ Nenhum import de `presentation` em `domain`, `application` ou `infrastructure`
- ✅ Nenhum `sqlite3` em `domain/`
- ✅ Composition root único (`src/main.py`) — é o único arquivo que instancia classes concretas
- ⚠️ **`domain/interfaces/telemetry_source.py` importa `PySide6.QtCore`** — única violação (documentada e justificada, mas é o bloqueador nº 1 deste projeto)

### Fluxo de dados atual

```
PS5 ─UDP:33740─▶ _ListenerThread (QThread)
                        │ Signal ← única troca de thread do sistema
                        ▼
                 TelemetryService  ┐
                        │          │
                 EventBus (Signal) │  TUDO ISTO roda na
                        │          ├─ thread principal
                 ViewModels        │  do Qt
                        │          │
                 Views (QtCharts)  ┘
```

**Este é o achado estrutural mais importante da auditoria.** Só a recepção UDP
sai da thread da UI. Conversão DTO→domínio, cálculo de força G, delta, publicação
de eventos, persistência SQLite e renderização acontecem todos na thread
principal. Funciona hoje porque a carga é pequena. Não sobrevive a adicionar
chamadas de IA (latência de segundos) ou o bot do Discord.

---

## 4. O que está correto — NÃO reescrever

Estes componentes são de qualidade profissional. Devem ser preservados e
migrados como estão (com ajustes cirúrgicos onde indicado).

| Componente | Por que preservar |
|---|---|
| `infrastructure/telemetry/gt7_protocol.py` | Lógica pura, sem rede, sem Qt, sem estado. Offsets corretos, nibbles de marcha, normalização de pedais, 12 flags. Testável com pacote gravado. **Reaproveitar integralmente.** |
| `domain/services/lap_comparator.py` | Alinhamento por **distância** e não por tempo — decisão de engenharia correta e não óbvia. Busca binária no caminho quente. **Reaproveitar integralmente.** |
| `domain/services/lap_analysis.py` (`LapSeries`) | Consulta interpolada por canal, cache por canal, nomes de canal derivados do dataclass (erro de digitação falha na hora). **Reaproveitar; é a base do analytics.** |
| `infrastructure/repositories/sqlite_database.py` | Migrações incrementais por `PRAGMA user_version`, caminho injetado (`:memory:` testável), lock de escrita. Ordem índices-depois-de-migrações está correta (e o comentário explica por quê). **Estender, não reescrever.** |
| `SqliteLapRepository.save` | Transação única com rollback; lista de colunas derivada do modelo. **Preservar o padrão.** |
| `application/events/event_bus.py` | Isolamento de handler (um assinante quebrado não derruba os outros) e travessia de thread correta. **Preservar o contrato; trocar o transporte** (ver §7). |
| `TelemetryService._compute_g_forces` | Projeta a aceleração nos eixos do carro em vez de deixar no referencial do mundo. Matematicamente correto e raro de ver feito certo. **Reaproveitar.** |
| Modelos de domínio | `slots=True`, propriedades derivadas, `has_points` para carga preguiçosa. Bem desenhados. |
| Comentários do código | Explicam **por quê**, não **o quê**. Padrão a manter em todo código novo. |

---

## 5. Problemas e dívida técnica

Ordenados por severidade. Referências são `arquivo:linha`.

### 🔴 Críticos — bloqueiam o roadmap

**P1 — Zero testes automatizados.**
Não existe um único arquivo de teste. O README descreve validações detalhadas
(paridade de 401 amostras, migração v3→v5, transação com rollback), mas nada
disso é executável hoje. São verificações manuais que não podem ser repetidas
nem em CI. O projeto está prestes a multiplicar de tamanho por 5 sem rede de
segurança.

**P2 — `domain/` depende do Qt.**
`src/domain/interfaces/telemetry_source.py:5` importa `QObject, Signal`. A
justificativa técnica está documentada e é honesta, mas a consequência é
estrutural: **o núcleo não roda sem um event loop do Qt**. Um bot do Discord, um
worker de IA, um teste unitário ou um servidor headless não podem usar o domínio.
Isso bloqueia as fases 7, 8 e 9 do roadmap inteiras.

**P3 — Nenhuma camada de configuração; segredo real commitado.**
`src/presentation/main_window.py:51` — `DEFAULT_PS_IP = "192.168.15.156"`.
Um arquivo de UI é dono de uma constante de rede, e é o IP da LAN doméstica do
autor, versionado. Repetido em `src/tools/diagnose.py:22` e na árvore legada.
Não há `.env`, `.env.example`, arquivo de settings nem persistência de
preferências. **Sem isso não há como adicionar token do Discord ou chave de IA
com segurança** (§36/§37 do briefing).

**P4 — Nenhuma fonte de telemetria além do UDP real.**
A interface `TelemetrySource` existe e está bem desenhada — mas tem uma única
implementação. Não há `MockTelemetryProvider` (§39) nem replay de arquivo (§40).
**Esta é a causa raiz de P1:** sem fonte sintética, testar o pipeline exige um
PS5 ligado na mesma rede.

### 🟠 Altos — comprometem a qualidade do produto

**P5 — Deriva na integração de distância.**
`telemetry_service.py` acumula `distância += (velocidade/3.6) × dt` — regra do
retângulo a 60 Hz. O erro é monotônico e acumula ao longo da volta. Como delta,
limites de setor e alinhamento de comparação **todos** dependem da distância,
esse erro se propaga para todo o analytics. As coordenadas `position_x/z` estão
disponíveis no pacote e não são usadas para corrigir.

**P6 — Setores são aproximação geométrica.**
`compute_sector_times` divide a volta em terços de distância iguais, ancorados
na mediana das últimas 10 voltas. Honesto e documentado — mas combinado com P5
significa que "setor 2" cai em um ponto físico ligeiramente diferente a cada
volta. Para análise de curvas (§12) isso é insuficiente: é preciso posição
relativa à pista derivada de x/z.

**P7 — Renderização de gráficos sem downsampling.**
`widgets_chart.py:148` — `for x, y in points: series.append(x, y)`. `QLineSeries.append()`
emite um sinal por ponto. Uma volta de 1:42 a 60 Hz ≈ **6.120 amostras**; a aba
de telemetria monta ~18 gráficos → **≈110.000 appends por plotagem**, dobrado no
modo comparação. Não há downsampling em lugar nenhum. `replace(lista)` é cerca
de uma ordem de grandeza mais rápido.

**P8 — Política de retenção apaga dados do usuário em silêncio.**
`sqlite_database.py` — `KEEP_BEST_PER_TRACK = 5`, `KEEP_RECENT_PER_TRACK = 50`.
Toda gravação apaga o que ficar fora dessa janela, sem aviso e sem controle do
usuário. Para uma plataforma que quer **modelo estatístico do piloto a partir de
histórico** (§16), destruir histórico é diretamente contraproducente.

**P9 — `Session` não é persistida.**
O modelo de domínio existe e é bom, mas não há tabela `sessions` nem
`SessionRepository`. Sessões vivem só em memória e morrem com o processo.
"Recuperar sessão após falha" (§8) é impossível hoje.

### 🟡 Médios — dívida a pagar antes de escalar

**P10 — Telemetria de alta frequência em linhas SQL.**
`lap_frames` guarda uma linha por amostra × 27 colunas. ~6k linhas/volta; com a
retenção atual, ~336k linhas por pista. Funciona agora; não sobrevive a sessões
de endurance. A solução híbrida SQLite + Parquet sugerida no briefing (§9) é a
escolha certa, e o schema ainda não está pronto para ela.

**P11 — Nenhum logging estruturado.**
`event_bus.py` usa `print()` como único destino de erro de handler. Sem níveis,
sem arquivo, sem correlação. §35 inteiramente não implementado — não há
contagem de pacotes, perda, latência ou throughput.

**P12 — Strings mágicas em português como estado de conexão.**
`"conectando"`, `"recebendo"`, `"sem_sinal"`, `"desconectado"` são comparadas
como literais em `listener_thread.py`, `live_viewmodel.py`, `main_window.py` e
`styles.py`. Um erro de digitação falha em silêncio. Deveria ser `enum`.

**P13 — Duplicação entre as duas árvores.**
Verificado byte a byte: `widgets_chart.py` (715 linhas), `widgets.py`,
`widgets_tire.py` e os três CSVs do catálogo são **idênticos** nas duas árvores.
A árvore legada ainda contém o bug de seleção de pista descrito no README
(qualquer pista digitada é gravada como a primeira em ordem alfabética).

**P14 — Higiene de repositório.**
56 `.pyc` versionados, `.DS_Store` commitado, sem `.gitignore` na raiz, sem
`pyproject.toml`, dependências sem pin.

**P15 — Esqueleto morto.** `file_lap_storage.py` (§2).

**P16 — Sem packaging.** Roda só a partir do fonte, com manipulação de
`sys.path` em `main.py:27`.

---

## 6. Riscos

| # | Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|---|
| R1 | Qt no domínio impede módulos headless | **Certa** | Bloqueia fases 7–9 | Extrair núcleo sem Qt — **primeira tarefa da Fase 1** |
| R2 | Thread única satura ao adicionar IA/Discord | Alta | UI trava durante corrida | Fronteira assíncrona + workers |
| R3 | Deriva de distância invalida analytics comparativo | Alta | Conclusões erradas do Race Engineer | Trapézio + correção por x/z |
| R4 | Crescimento 5× sem testes | **Certa** | Regressões silenciosas | Testes antes de expandir |
| R5 | Vazamento de segredo ao adicionar Discord/IA | Média | Token público | `.gitignore` + `.env` antes de qualquer chave |
| R6 | IA inventa dados que não existem no protocolo | Alta | Perda de confiança do usuário | `ContextBuilder` só emite fatos medidos; ausência é explícita |
| R7 | Retenção apaga histórico necessário ao modelo do piloto | Média | Perda irreversível | Rever política antes de coletar dados |

---

## 7. Arquitetura alvo

### Princípio condutor

> **Núcleo headless em Python puro. Qt é uma casca. IA, Discord e voz são
> plugins que dependem do núcleo — nunca o contrário.**

Isso atende diretamente ao §49 do briefing ("a aplicação deve funcionar
perfeitamente sem IA") e resolve R1, R2 e R4 de uma vez.

### Estrutura em pacotes

```
gt7core/          # Python puro — ZERO Qt, ZERO I/O de UI
  domain/         #   modelos, protocolos (typing.Protocol), exceções
  telemetry/      #   protocolo GT7, decoder, normalizer, fontes
  session/        #   session manager, lap manager, sector manager
  analytics/      #   funções puras sobre LapSeries
  events/         #   bus agnóstico de transporte
  storage/        #   repositories (SQLite + Parquet)
  config/         #   configuração centralizada + segredos
  observability/  #   logging estruturado, métricas

gt7app/           # PySide6 — casca de UI
  adapters/       #   QtEventBusAdapter (re-emite no thread da UI)
  viewmodels/
  design/         #   design system (tokens + componentes)
  pages/
  widgets/

gt7ai/            # plugin — depende só de gt7core
gt7discord/       # plugin — depende só de gt7core
gt7voice/         # plugin — depende de gt7core + gt7ai
```

**Regra de dependência, verificável em CI:**
`gt7core` não importa `gt7app`, `gt7ai`, `gt7discord` nem `PySide6`. Um teste de
arquitetura falha o build se alguém quebrar isso.

### A mudança que destrava tudo: event bus agnóstico

Hoje o `EventBus` é `QObject` e usa `Signal` para atravessar threads. A solução
é manter exatamente esse comportamento na UI, mas tirar o Qt do contrato:

```
gt7core/events/bus.py       ← EventBus puro (thread-safe, sem Qt)
                                publish() / subscribe() / unsubscribe()
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
gt7app/adapters/            gt7discord/               tests/
QtEventBusAdapter           (asyncio)                 (síncrono, direto)
 └ reemite via Signal
   na thread da UI
```

O `QtEventBusAdapter` é a **única** peça que conhece `Signal`. A garantia de
thread da UI é preservada; o núcleo passa a ser testável sem `QApplication` e
utilizável por um bot.

`TelemetrySource` deixa de ser `QObject` e vira `typing.Protocol` com callbacks
— o adaptador Qt converte para sinais.

### Pipeline de telemetria alvo

```
GT7 (UDP :33740)
      │
 ┌────▼──────────────────┐
 │ TelemetrySource       │  Protocol — 3 implementações:
 │  · Gt7UdpSource       │   real
 │  · ReplaySource       │   arquivo gravado  (§40)
 │  · MockSource         │   sintético        (§39)
 └────┬──────────────────┘
      │ thread produtora
 ┌────▼──────────────────┐
 │ Ring buffer limitado  │  ← contrapressão; descarta o mais antigo,
 └────┬──────────────────┘     nunca cresce sem limite (§34)
      │
 ┌────▼──────────────────┐
 │ TelemetryEngine       │  validação → decode → parse → normalize
 │                       │  → distância (trapézio + correção x/z)
 │                       │  → forças G → TelemetryFrame normalizado
 └────┬──────────────────┘
      │
 ┌────▼──────────────────┐
 │ EventBus              │
 └──┬────────┬────────┬──┘
    │        │        │
 Analytics  UI      Recording        ← consumidores independentes;
    │                                   qualquer um pode cair sem
 RealTimeEventEngine (§18)              derrubar os outros (§41)
    │
 RaceEngineer (§21)
```

**A UI não sabe se a fonte é live ou replay** — exatamente o §40. Como as três
fontes satisfazem o mesmo protocolo, isso sai de graça.

---

## 8. Estratégia de armazenamento

**Recomendação: híbrido SQLite + Parquet**, como o briefing sugere em §9 — e
pelas razões certas, não por moda.

| Camada | Tecnologia | Conteúdo | Por quê |
|---|---|---|---|
| Metadados | SQLite | sessões, voltas, setores, curvas, resultados de análise, catálogo | Consultas relacionais, transações, já implementado e migrado |
| Alta frequência | Parquet (1 arquivo por volta) | 27+ canais × ~6k amostras | Colunar e comprimido: ler só `speed_kmh` de uma volta não carrega os outros 26 canais. ~10× menor que linhas SQL. |

A volta no SQLite guarda um ponteiro para o arquivo Parquet. `LapRepository`
mantém a interface atual — **as camadas acima não percebem a mudança**, que é
exatamente o valor do repository pattern já implementado.

**Migração:** um passo de schema (v6) que exporta `lap_frames` existente para
Parquet e mantém leitura das duas formas durante uma versão. Nenhum dado do
usuário é perdido.

**Retenção (P8):** trocar a exclusão automática por (a) sem limite por padrão,
(b) arquivamento opcional configurável, (c) aviso explícito antes de qualquer
exclusão. Parquet torna guardar tudo barato, o que remove a razão original da
política.

---

## 9. Estratégia de processamento da telemetria

| Preocupação | Decisão |
|---|---|
| Recepção | Thread dedicada (mantém o desenho atual, que está correto) |
| Contrapressão | Ring buffer limitado — descarta o mais antigo, nunca cresce sem limite |
| Caminho quente | Sem alocação por frame além do `TelemetryFrame`; `slots=True` em toda amostra |
| Distância | Regra do trapézio + correção contra comprimento do traçado x/z (corrige P5) |
| Posição na pista | Distância normalizada 0..1 relativa à volta de referência — base para setores e curvas estáveis (corrige P6) |
| UI | Taxa de repaint desacoplada da taxa de chegada (o `LiveViewModel` atual já faz isso corretamente — preservar) |
| Gráficos | `replace()` em vez de `append()` por ponto + downsampling LTTB para a largura do gráfico (corrige P7) |
| Persistência | Escrita em lote fora da thread da UI, ao fechar a volta |
| Observabilidade | Contadores de pacotes recebidos/descartados/por segundo e latência de processamento no próprio pipeline (§35) |

---

## 10. Arquitetura de analytics

Todas as análises são **funções puras** sobre `LapSeries` — sem I/O, sem estado,
sem Qt. Consequência prática: cada uma é testável com uma lista de amostras
sintéticas e nada mais.

```
gt7core/analytics/
  lap.py         tempo, melhor/pior, consistência (desvio padrão)
  sector.py      setores ancorados em distância normalizada
  delta.py       motor de delta com referências plugáveis (§17)
  corner.py      detecção de curva por curvatura do traçado x/z + volante
  braking.py     ponto, pressão, duração, trail braking, consistência
  throttle.py    aplicação, tempo até 50/100%, wheelspin por slip
  tire.py        slip, temperatura, tendência ao longo da sessão
  driver.py      perfil estatístico agregado por histórico
  events.py      RealTimeEventEngine (§18)
```

**Detecção de curvas (§12)** — a peça nova mais importante. Abordagem: derivar
curvatura do traçado x/z, segmentar em retas e curvas por limiar, e persistir a
geometria por pista em uma tabela `corners`. Uma vez que as curvas de uma pista
estão identificadas, toda volta subsequente na mesma pista é automaticamente
segmentada — que é o que viabiliza "você perdeu 0,231s na curva 4".

**RealTimeEventEngine (§18)** — determinístico, sem IA: travamento de roda,
patinagem, frenagem cedo/tarde, saída lenta, slip excessivo, melhor pessoal,
melhor setor. É o que alimenta a IA depois, e o que dá alertas em tempo real
com latência zero.

---

## 11. Arquitetura da IA

Estritamente modular, conforme §19 e §49: **o núcleo não conhece a IA**.

```
gt7ai/
  providers/
    base.py          AIProvider (Protocol): complete(), stream()
    anthropic.py     ┐
    openai.py        ├─ intercambiáveis por configuração
    local.py         ┘
  context/
    builder.py       telemetria + volta + histórico + pista + eventos
                     → EngineeringContext
  engineer/
    race_engineer.py perguntas e respostas fundamentadas em dados
    tiers.py         os 3 níveis do §22
  prompts/           versionados em arquivo, não embutidos em código
```

### Os três níveis (§22)

| Nível | Mecanismo | Latência | Custo |
|---|---|---|---|
| 1 — Alertas determinísticos | `RealTimeEventEngine` | < 10 ms | zero |
| 2 — Análise estatística | funções de analytics | < 100 ms | zero |
| 3 — Interpretação por IA | LLM sob demanda | segundos | por chamada |

Durante a pilotagem só os níveis 1 e 2 rodam. O nível 3 é acionado por pergunta
do usuário ou ao fim da sessão. **Telemetria bruta nunca vai para o LLM** — só o
`EngineeringContext` destilado, no formato do exemplo do §20.

### Regra contra alucinação (§21)

O `ContextBuilder` só emite fatos que foram **medidos**. Campos ausentes entram
no contexto marcados explicitamente como indisponíveis, e o prompt instrui a
responder *"não tenho dados de telemetria suficientes para determinar isso"*.
Como o `TelemetryFrame` já representa ausência com `null`, essa garantia é
estrutural e não depende de o modelo se comportar bem.

**Isolamento de falha (§41):** IA indisponível → telemetria, analytics e UI
continuam. O núcleo não tem dependência de compilação nem de execução com `gt7ai`.

---

## 12. Arquitetura Discord e Voz

```
gt7discord/                        gt7voice/
  bot.py         cliente           stt/         speech-to-text (provider)
  commands/      1 arquivo por     tts/         text-to-speech (provider)
                 comando (§23)     pipeline.py  áudio → texto → engineer → áudio
  notifications/ eventos do bus
  voice/         ponte de áudio
```

Comandos novos são arquivos novos registrados por descoberta — **adicionar
comando não toca no núcleo** (§23). O bot assina o `EventBus` do núcleo em
processo, ou consome uma API local se o usuário quiser rodá-lo separado; ambos
os desenhos ficam abertos porque o bus é agnóstico de transporte.

Fluxo de voz, exatamente como o §24:

```
Discord Voice → STT → CommandParser → contexto de telemetria
              → RaceEngineer → TTS → Discord Voice
```

STT e TTS atrás de protocolos, como o `AIProvider` — trocar de provedor é
configuração.

---

## 13. Design System e UX

### Direção estética

Princípios da Apple aplicados a software de engenharia: hierarquia clara,
espaçamento generoso, tipografia como estrutura, movimento sutil e com propósito.
**Sem** neon, RGB, gradientes excessivos ou estética gamer.

A paleta atual (`styles.py`) já está no caminho certo — superfícies escuras
estratificadas (`#12141a` → `#1a1d25` → `#23262f`), sem preto absoluto, cores de
texto sempre explícitas. **Aproveitar como base**, formalizando em tokens.

### Tokens

```
gt7app/design/
  tokens.py       cores, tipografia, espaçamento (escala 4px), raio, sombra, duração
  components/     Button, Card, Table, Input, Tabs, Modal, Toast, Badge, Chart
  theme.py        Dark (padrão) · Light · System (§27)
```

Regra: **nenhum estilo declarado fora do design system**. Hoje `styles.py` é uma
folha global de QSS — bom começo, mas widgets aplicam estilos próprios em vários
pontos. Consolidar.

Cor semântica no motorsport tem significado fixo e não deve variar por tela:
verde = ganho/melhor pessoal, amarelo = atenção, vermelho = perda, roxo = melhor
absoluto. Convenção da categoria — respeitá-la é clareza, não falta de identidade.

### Navegação (§28)

Sidebar de ícones + rótulo, com command palette (⌘K / Ctrl+K, §45) por cima.
Sidebar dá orientação constante; palette dá velocidade ao usuário avançado. As
abas atuais viram páginas:

```
Dashboard · Live Telemetry · Sessions · Lap Analysis · Comparison
Driver · Cars · Tracks · Analytics · AI Engineer · Settings
```

**Notificações (§46):** serviço central assinando o `EventBus` — nenhuma página
implementa notificação própria.

**Listas grandes (§43):** virtualização + paginação em sessões, voltas e
registros de telemetria.

---

## 14. Estratégia de testes

O `MockTelemetryProvider` (§39) vem **primeiro**, não por último: é ele que
torna todo o resto testável sem PS5.

| Camada | Tipo | Ferramenta | Casos que importam |
|---|---|---|---|
| Protocolo | unitário | pytest | pacote sintético cifrado com Salsa20 real; **corrompido, curto, duplicado, chave errada** |
| Analytics | unitário + propriedade | pytest + hypothesis | volta vazia, volta incompleta, canal ausente, divisão por zero |
| Delta | unitário | pytest | referência vazia, distância além da referência, voltas de comprimentos diferentes |
| Repositories | integração | pytest + SQLite `:memory:` | rollback, migração v3→v5, banco indisponível |
| Event engine | unitário | pytest | ordenação, handler que levanta exceção |
| Contexto de IA | unitário | pytest | **dados insuficientes → recusa explícita** |
| Pipeline | integração | pytest + MockSource | GT7 desconecta no meio, pausa, sessão vazia |
| Arquitetura | estrutural | pytest custom | **`gt7core` não importa Qt** — falha o build se alguém regredir |
| UI | fumaça | pytest-qt | monta a janela, exercita as páginas, zero exceções |

Meta de cobertura: **≥85% em `gt7core`**. A UI fica com testes de fumaça — o
retorno de testar widget a widget não paga o custo.

**Portar as validações do README para testes executáveis é tarefa da Fase 1.**
O trabalho de verificação já foi feito uma vez; falta torná-lo repetível.

---

## 15. Tecnologias propostas

| Necessidade | Escolha | Justificativa |
|---|---|---|
| Runtime | Python 3.12+ (fixado) | `slots`, tipos modernos; evita a ponta 3.14 para não brigar com rodas de dependência |
| UI | **PySide6** (manter) | 20 arquivos já são PySide6; trocar exigiria reescrever tudo sem ganho |
| Gráficos | QtCharts + downsampling; avaliar **pyqtgraph** | QtCharts já está integrado; pyqtgraph é mensuravelmente mais rápido se 18 gráficos sincronizados continuarem pesados após otimizar |
| Colunar | **pyarrow** | Parquet; padrão de fato |
| Config | **pydantic-settings** + python-dotenv | validação de tipo + `.env`; resolve P3 |
| Logging | **structlog** | logs estruturados com contexto (§35) |
| Testes | **pytest** + pytest-qt + hypothesis | hypothesis acha os casos extremos que o §38 pede |
| Lint/format | **ruff** | substitui flake8 + black + isort, ordens de grandeza mais rápido |
| Tipos | **mypy** (strict em `gt7core`) | type safety pedida no §51 |
| Discord | **discord.py** | suporte a voz maduro |
| Async | **asyncio** | Discord e IA são I/O-bound |
| Dependências | **uv** + `pyproject.toml` | resolução reprodutível e rápida |
| Empacotamento | **PyInstaller** | distribuição desktop |
| CI | GitHub Actions | lint + tipos + testes + teste de arquitetura a cada push |

**Deliberadamente fora:** ORM (SQL direto atrás de repositories já funciona e é
mais rápido), framework de DI (composition root manual está correto neste
tamanho — §51 pede evitar abstração desnecessária), microserviços.

---

## 16. Roadmap

Mapeado às fases do briefing, marcando o que já existe.

| Fase | Escopo | Estado | Entrega |
|---|---|---|---|
| **0 — Audit** | Este documento | ✅ concluído | — |
| **1 — Fundação** | `.gitignore`, `pyproject.toml`, ruff, mypy, pytest, CI; **extrair `gt7core` sem Qt**; config + segredos; logging estruturado; **testes portados do README** | ⬜ | Núcleo headless com testes verdes |
| **2 — Telemetria** | Migrar protocolo/decoder/normalizer; **MockSource + ReplaySource**; ring buffer; correção de deriva de distância; estatísticas de pacote | 🟡 parcial | Pipeline testável sem PS5 |
| **3 — Gravação** | `SessionRepository`; Parquet + migração v6; rever retenção; recuperação após falha | 🟡 parcial | Sessões persistidas |
| **4 — Analytics** | Volta, setor, delta (existem); **frenagem, throttle, curva, pneu, piloto** (novos) | 🟡 parcial | Motor de análise completo |
| **5 — UI profissional** | Design system em tokens; navegação por páginas; command palette; notificações; virtualização | 🟡 parcial | Interface de produto |
| **6 — Analytics avançado** | Mapa de pista, detecção de curvas, banco de curvas por pista, perda de tempo, modelo do piloto | ⬜ | "Você perdeu 0,231s na curva 4" |
| **7 — IA** | AIProvider, ContextBuilder, RaceEngineer, 3 níveis, relatórios | ⬜ | Engenheiro virtual |
| **8 — Discord** | Bot, comandos, notificações | ⬜ | Consulta remota |
| **9 — Voz** | STT, parser, TTS, voz no Discord | ⬜ | Engenheiro por voz |
| **10 — Hardening** | Performance, stress, memória, recuperação, segurança, UX, arquitetura | ⬜ | Pronto para produção |

**Caminho crítico:** Fase 1 destrava tudo. Extrair o núcleo sem Qt e criar a
fonte mock são pré-requisitos de todas as fases posteriores — não são
"preparação", são a fundação.

Cada fase entrega, conforme §57: implementado · testes · performance · problemas
conhecidos · dívida técnica · recomendações · próximo passo.

---

## 17. Decisões que peço confirmação antes de implementar

Estas mudam materialmente o trabalho e são suas para decidir:

1. **Reestruturação em pacotes** (`gt7core` / `gt7app` / plugins) — é a mudança
   estrutural principal. Alternativa mais conservadora: manter `src/` e apenas
   remover o Qt do domínio. Recomendo a reestruturação: o custo é parecido agora
   e muito maior depois.

2. **Destino da árvore `hanna_gt7_ai/`** — arquivar em tag e remover, ou manter?
   Recomendo arquivar: `src/` é autossuficiente e superior, e a árvore legada
   ainda contém o bug de seleção de pista. O histórico do git preserva tudo.

3. **Política de retenção (P8)** — confirmar a troca da exclusão automática por
   retenção ilimitada com arquivamento opcional. Afeta dados já gravados.

4. **Provedor de IA padrão** — Anthropic, OpenAI ou local. A arquitetura suporta
   os três; a escolha define qual implementar primeiro.

5. **Idioma da interface e do código** — hoje há mistura (código e comentários em
   português, alguns termos em inglês). Recomendo: **código e identificadores em
   inglês, interface em português**, com strings de UI centralizadas para
   permitir tradução depois.

---

## Veredito

A fundação é sólida e foi construída com cuidado real — as decisões difíceis
(alinhamento por distância, projeção das forças G nos eixos do carro, transação
única, migrações incrementais) estão **corretas**, e o código explica o porquê
de cada uma. Isso é raro e é o ativo mais valioso do repositório.

O que falta não é qualidade, é **infraestrutura para crescer**: testes,
configuração, observabilidade e um núcleo que rode sem interface gráfica.

Nenhuma reescrita é necessária. O caminho é extrair, testar e expandir.

**Aguardando sua instrução para iniciar a Fase 1.**
