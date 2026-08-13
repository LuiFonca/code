# HANNA GT7 AI — Arquitetura

Telemetria de Gran Turismo 7 em PySide6, organizada em Clean Architecture
simplificada + MVVM, com injeção por construtor e barramento de eventos.

## Como rodar

```bash
pip install PySide6 pycryptodome
python -m src.main
```

`pycryptodome` não é opcional: os pacotes UDP do GT7 são cifrados com Salsa20.

`src/` é autossuficiente — não depende da pasta `hanna_gt7_ai/`. O catálogo do
jogo (`src/data/*.csv`) e o banco de voltas (`~/.hanna_gt7_ai/laps.db`, na sua
pasta pessoal) são as únicas fontes externas.

---

## A regra que sustenta tudo

As dependências apontam **para dentro**. Cada camada só conhece as de baixo:

```
presentation  ──▶  application  ──▶  domain  ◀──  infrastructure
```

`domain` não importa nada das outras. `infrastructure` implementa as interfaces
que o `domain` declara. `presentation` fala com `application`, nunca com banco.

O teste é direto: se `python -c "import src.main"` funciona e nenhum arquivo de
`domain/` importa `application`, `infrastructure` ou `presentation`, a regra está
de pé.

---

## Fluxo de dados

```
       PS5 (UDP :33740, 296 bytes cifrados, ~60 Hz)
                       │
   ┌───────────────────▼───────────────────┐
   │ infrastructure/telemetry              │
   │  _ListenerThread (QThread)            │  ← thread de rede
   │  salsa20_decode → TelemetryFrame      │    (DTO de fio, 41 campos)
   │  Gt7TelemetrySource (adaptador)       │
   └───────────────────┬───────────────────┘
                       │ Signal (troca de thread aqui)
   ┌───────────────────▼───────────────────┐
   │ application/services                  │
   │  TelemetryService                     │  ← thread da UI
   │   • detecta virada de volta           │
   │   • integra distância (velocidade→m)  │
   │   • deriva força G (Δvelocidade)      │
   │   • TelemetryFrame → TelemetryPoint   │
   │   • delta duplo (melhor + anterior)   │
   │  SessionManager: pode gravar?         │
   └───────────────────┬───────────────────┘
                       │ publish()
   ┌───────────────────▼───────────────────┐
   │ application/events/EventBus           │
   │  Signal(object) + despacho por tipo   │
   └──┬──────────────────────────────┬─────┘
      │                              │
┌─────▼──────────┐          ┌────────▼─────────────────┐
│  ViewModels    │          │ infrastructure/repos     │
│  Live/History/ │◀────────▶│  SQLite (voltas)         │
│  Comparison/   │          │  CSV (catálogo do jogo)  │
│  Telemetry     │          └──────────────────────────┘
└─────┬──────────┘
      │ Signal
┌─────▼──────────────────────────────┐
│ presentation (janela, abas, gráficos)│
└──────────────────────────────────────┘
```

**Por que o barramento usa `Signal` e não callbacks:** a captura roda numa
QThread. Com callbacks Python, o handler executaria na thread de rede — e código
de UI fora da thread principal é a receita clássica de crash intermitente em Qt.
O `Signal` faz o Qt usar conexão enfileirada e entregar na thread do assinante.

---

## O que cada arquivo faz

### `domain/` — regras e contratos (879 linhas)

Sem Qt, sem SQL, sem rede. A única exceção é `TelemetrySource`, que precisa de
`Signal` — o porquê está em `interfaces/__init__.py`.

| Arquivo | Responsabilidade |
|---|---|
| `models/telemetry_point.py` | Uma amostra normalizada (27 campos). `distance_m` e as forças G não vêm no pacote — são derivadas na entrada. |
| `models/lap.py` | Volta + métricas (`avg_speed`, `max_speed`, `fuel_used`). `points` fica vazio nas listagens (carga preguiçosa). |
| `models/session.py` | Carro + pista + janela de tempo + voltas. Dá lugar a "melhor volta desta sessão". |
| `models/car.py` `maker.py` `track.py` | Entidades do catálogo. `Track.length_m` é o que viabiliza a auto-identificação. |
| `interfaces/telemetry_source.py` | Contrato da fonte: `start`/`stop`/`is_running` + 3 sinais. Um replay de arquivo satisfaz o mesmo contrato. |
| `interfaces/*_repository.py` | Contratos de persistência (ABC puro, sem Qt). |
| `services/lap_comparator.py` | Delta ao vivo, alinhado por **distância**. Busca binária, roda 60x/s. |
| `services/lap_analysis.py` | `LapSeries`: consulta canal em distância arbitrária, interpolando. Setores e volta teórica ideal. |

**Por que alinhar por distância e não por tempo:** comparando por tempo, um
trecho onde o piloto freia mais cedo desalinha toda a comparação dali em diante.

### `application/` — orquestração e estado de tela (1437 linhas)

| Arquivo | Responsabilidade |
|---|---|
| `services/telemetry_service.py` | Coração do fluxo. Detecta volta, integra distância, deriva G, converte DTO→domínio, publica eventos. Não conhece SQLite. |
| `services/session_manager.py` | Decide se a volta é gravada. Separa "sem pista" de "modo replay" — motivos diferentes, mensagens diferentes. |
| `events/event_bus.py` | `subscribe`/`unsubscribe`/`publish`. Handler que quebra não derruba os outros. |
| `events/events.py` | 13 eventos como dataclasses imutáveis. |
| `viewmodels/live_viewmodel.py` | Dois timers: repaint (desacopla renderização da chegada) e watchdog de stale. |
| `viewmodels/history_viewmodel.py` | Lista, filtro e exclusão. Consultas em lote. |
| `viewmodels/comparison_viewmodel.py` | Duas voltas → delta, setores, volta ideal. |
| `viewmodels/telemetry_viewmodel.py` | Detalhe de uma volta; centraliza a escolha de eixo distância/tempo. |

### `infrastructure/` — adaptadores (1550 linhas)

| Arquivo | Responsabilidade |
|---|---|
| `telemetry/gt7_protocol.py` | Salsa20 + 12 flags + offsets do pacote. Lógica pura, testável com pacote gravado. |
| `telemetry/listener_thread.py` | QThread UDP (heartbeat 10s) + `Gt7TelemetrySource` que a adapta ao contrato. |
| `repositories/sqlite_database.py` | Conexão, schema v5 e migrações. Caminho injetado (aceita `:memory:`). |
| `repositories/sqlite_lap_repository.py` | Voltas. `save` é uma transação única com rollback. Colunas derivadas do modelo. |
| `repositories/sqlite_{car,track}_repository.py` | Carros e pistas do usuário. |
| `repositories/csv_catalog.py` + 3 repos | Catálogo do jogo: 527 carros, 72 montadoras, 105 pistas. Somente leitura. |
| `storage/file_lap_storage.py` | Esqueleto JSON (exportar/importar volta). `NotImplementedError`. |

### `presentation/` — Views (2775 linhas)

| Arquivo | Responsabilidade |
|---|---|
| `main_window.py` | Só o chrome. Abas vêm de um dicionário de fábricas injetado. |
| `styles.py` | Paleta e folha de estilo. Cores de texto sempre explícitas. |
| `tabs/live_tab.py` | Dashboard ao vivo. Sem timer próprio. |
| `tabs/history_tab.py` | Tabela ordenável. Zero SQL. |
| `tabs/telemetry_tab.py` | 18 gráficos: canais + 3 mosaicos 2×2 (pneu, suspensão, deriva) + indicador. |
| `tabs/comparison_tab.py` | Delta no topo, canais A/B, grade de setores, traçado. |
| `widgets/` | `SyncedMiniChart` (zoom/pan/cursor), `TrackMapWidget`, cards, painel de pneus. |

### `main.py` — composition root

Único lugar com classes concretas. Monta: `EventBus` → repositórios →
`Gt7TelemetrySource` → `TelemetryService` + `SessionManager` → 4 ViewModels →
fábricas de abas → `MainWindow`.

---

## O que a refatoração corrigiu

| Antes (`hanna_gt7_ai/`) | Agora |
|---|---|
| 3 das 4 abas chamavam `lap_storage` direto — SQL dentro do widget | Views só conhecem ViewModels |
| `LapRecorder`: 283 linhas, 5 responsabilidades, `init_db()` no construtor | `TelemetryService` + `SessionManager` |
| `lap_storage`: 18 funções de módulo sobre `DB_PATH` global | Classes com caminho injetado (testável com `:memory:`) |
| `gt7_catalog`: caches em globais de módulo | Atributos de instância |
| `CHANNELS`: nome→índice de tupla (ordem do SQLite vazava até os gráficos) | Acesso por atributo; colunas derivadas do modelo |
| Falha ao salvar virava `print()` | Evento `LapSaveFailed` visível na interface |
| `save_lap` com dois commits (volta podia ficar sem setores) | Transação única com rollback |
| Histórico: 101 consultas para 50 voltas | 2 consultas em lote |

---

## Bugs encontrados durante a migração

Três só apareceram porque o smoke test exercitou o app montado de verdade:

1. **App não abria em banco existente.** Os índices eram criados antes das
   migrações e referenciavam `is_player`, coluna que só existe a partir da v4.
   Num banco na v3 (o caso do banco real): `no such column: is_player`.

2. **Pista errada gravada.** `_resolve_track_name` lia `currentData()` primeiro,
   mas num `QComboBox` editável com `NoInsert` o `setCurrentText()` não move o
   `currentIndex` — `currentData()` devolve sempre o item 0. Com o catálogo de
   105 pistas carregado, qualquer pista digitada seria gravada como a primeira em
   ordem alfabética. **Este bug está presente no `hanna_gt7_ai/` atual.**

3. **Render abortado no meio.** `set_sector_lines` espera pares
   `(posição, rótulo)`; recebia floats, e a exceção interrompia o desenho.

Observação de schema (mantida como está): `laps.track_id` não tem FOREIGN KEY,
enquanto `car_id` tem. Adicionar a restrição agora rejeitaria voltas órfãs em
bancos existentes.

---

## Verificação

```bash
python -m compileall -q src/          # compila
python -c "import src.main"           # sem ciclos de dependência
python -m src.main                    # abre a janela
```

Verificado nesta migração:

- **Protocolo** — pacote sintético cifrado com Salsa20 real: todos os campos
  byte-exatos (velocidade, nibbles de marcha, normalização de pedais, flags).
- **Força G** — frenagem de 50→40 m/s em 0,5 s produz −2,039 g (esperado −2,04).
- **Delta** — volta 10% mais lenta gera delta monotônico até +8,96 s.
- **Pausa** — 101 frames válidos + 100 pausados resultam em 101 amostras.
- **Transação** — falha de FK reverte 500 amostras, zero órfãos.
- **Migração** — v3→v5 em cópia do banco real, dados preservados.
- **Paridade** — app antigo e `src/` sobre o mesmo banco: mesma melhor volta,
  mesmos setores, mesma ordem, **401 amostras × 27 campos idênticas**.
- **Bidirecional** — `src/` grava e o app antigo lê corretamente, e vice-versa.
- **Smoke test** — com `excepthook`: zero exceções em 24 gráficos, troca de eixo,
  busca, comparação e criação de pista nova.

`hanna_gt7_ai/` continua intacto e funcionando (`python hanna_gt7_ai/main.py`).

---

## Decisões que divergem da especificação original

| Pedido | Feito | Motivo |
|---|---|---|
| PyQt5 | **PySide6** | Os 20 arquivos existentes são PySide6; PyQt5 exigiria reescrever protocolo, storage e todos os widgets. |
| Só stdlib + PyQt5 | + `pycryptodome`, QtCharts | Salsa20 é obrigatório para decodificar o pacote; QtCharts é o que os gráficos já usam. |
| Estrutura fixa | + `domain/services/` | `LapComparator` e `LapSeries` (~240 linhas) são regra de domínio pura e não cabiam em nenhuma pasta prevista. |
| Só esqueletos | Lógica real portada | Decisão do usuário: `src/` roda de verdade, não é só estrutura. |
