# HANNA GT7 AI

Telemetria ao vivo e análise de voltas para **Gran Turismo 7**, em PySide6.

Captura os pacotes UDP que o PlayStation transmite, mostra o painel em tempo
real com delta contra a melhor volta e a volta anterior, grava cada volta num
banco local e analisa as voltas canal a canal e curva a curva.

## Instalação

Requer **Python 3.11 ou superior**.

```bash
pip3 install -r requirements.txt
```

No Python 3.11 o `requirements.txt` limita o PySide6 a versões abaixo da 6.12:
a 6.12.0 tem erros de contagem de referência que derrubam o processo nessa
versão do Python. Do 3.12 em diante o mesmo erro é inofensivo, e a 6.12 é a
única disponível para Python 3.15.

## Como rodar

```bash
python3 -m src.main
```

Também funcionam `python3 src/main.py` e `python3 src`.

### Primeira conexão

1. No console: **Configurações → Rede → Ver status da conexão**, anote o IP.
2. O GT7 precisa estar numa sessão ativa (corrida, contrarrelógio ou track
   day). No menu ele não transmite.
3. No app: digite o IP e clique em **Conectar**.

Computador e console precisam estar na mesma rede e faixa de IP. VPN ligada no
computador é a causa mais comum de falha.

## As quatro abas

| Aba | O que faz |
|---|---|
| **Ao Vivo** | Velocidade, marcha, RPM, pedais, pneus, combustível, **autonomia em voltas**, traçado e o delta duplo — contra a melhor volta e contra a anterior. |
| **Histórico** | Voltas da pista aberta com tempos e setores, e a **volta ideal** (melhor de cada setor). Excluir, invalidar, atribuir pista, **notas e acerto do carro por volta**, exportar em JSON ou **CSV**, importar, e **ajustar os setores da pista**. |
| **Telemetria** | Uma volta em detalhe: 20 gráficos sincronizados, mosaicos por roda, ângulo de deriva, **taxa de guinada** e a **tabela de curvas** — velocidade de entrada, mínima e de saída, ponto de frenagem, G de pico e equilíbrio (sub ou sobresterço). |
| **Comparação** | Duas voltas sobrepostas e até **duas extras** (C e D) contra a referência; delta por distância; setores; **tabela curva a curva** com tempo perdido, diferença de velocidade mínima e de ponto de frenagem; e o **traçado colorido** por onde se ganha e se perde. |

Na barra superior:

- **📊** — saúde da sessão: taxa de pacotes, interrupções, quedas de conexão e
  validade da orientação do carro.
- **⚙** — preferências.

## Comportamentos que vale conhecer

- **Volta sem pista é gravada.** O app tenta reconhecer o circuito pelo desenho
  do traçado; quando dois candidatos disputam de perto, ele não decide, e a
  volta fica no grupo "sem pista" até você atribuir pelo Histórico. Atribuir à
  mão também ensina o traçado para as próximas.
- **O app reabre na última pista e no último carro.**
- **Replay é sugerido, não decidido.** O pacote do GT7 não informa quem
  controla o carro. Quando o tempo da volta anda para trás — o que só acontece
  em replay — o app destaca a caixa *Replay / IA* e avisa. Marcar é com você.
- **Resumo ao desconectar.** Voltas, melhor tempo, média, consistência e a
  volta ideal da sessão.
- **Voltas parciais e inválidas não disputam recorde**, nem entram na volta
  ideal ou nas médias.
- **Numeração de curvas** é a ordem em que aparecem na volta; pode não bater
  com a oficial da pista. O **equilíbrio** só existe em voltas gravadas a
  partir do schema v10, que passou a guardar a taxa de guinada.

## Arquitetura

Clean Architecture com MVVM. As dependências apontam para dentro: `domain` não
conhece ninguém, `presentation` nunca fala com `infrastructure`.

```
src/
├── domain/              regras e contratos, sem framework
│   ├── models/          TelemetryPoint, Lap, Session, Track, Car
│   ├── interfaces/      contratos dos repositórios e da fonte de telemetria
│   ├── services/        análise de volta e de curvas, comparador de delta,
│   │                    deriva, reconhecimento de traçado, estatísticas de
│   │                    sessão, detector de replay, exportação CSV
│   └── config.py        AppConfig
├── application/         orquestração e estado de tela
│   ├── events/          barramento e eventos
│   ├── services/        TelemetryService, SessionManager, LapWriter,
│   │                    TrackIdentifier, SessionHealth, resumo de sessão
│   └── viewmodels/      um por aba
├── infrastructure/      adaptadores concretos
│   ├── telemetry/       protocolo GT7 e thread UDP
│   ├── repositories/    SQLite e catálogo CSV
│   └── storage/         volta em arquivo JSON
├── presentation/        telas, sem regra de negócio
├── labs/                bases de recursos ainda sem tela (ver abaixo)
├── tools/               diagnóstico de rede
└── main.py              composition root
```

`main.py` é o **único** arquivo que instancia classes concretas.

### Dados

Banco SQLite e preferências em `~/.hanna_gt7_ai/` (`laps.db` e `config.json`).
A variável `HANNA_GT7_HOME` aponta para outra pasta — é o que os testes usam
para não tocar nos dados reais. Schema versionado por `PRAGMA user_version`
(atual: **v10**), com migrações incrementais.

### Protocolo

Pacotes de 296 bytes cifrados com Salsa20, a ~60 Hz. O app envia um toque na
porta 33739 e escuta na 33740. Os offsets vêm de engenharia reversa da
comunidade — não há especificação oficial.

## `labs/` — terreno preparado

Três recursos maiores têm a base pronta e testada, mas ainda **não têm tela**.
Nada fora de `labs/` os importa (há um teste garantindo isso).

| Módulo | O que já faz | O que falta |
|---|---|---|
| `session_recording.py` | Grava os pacotes brutos da sessão em arquivo e os reproduz depois como se fosse o console, pelo mesmo contrato da fonte real. | Botão de gravar e de abrir uma gravação. |
| `coach.py` | Ordena as curvas pelo tempo perdido e descreve as piores em frases, a partir da comparação curva a curva. | Um lugar na aba Comparação para mostrar. |
| `overlay.py` | Janela compacta, sempre por cima, com velocidade, marcha e os dois deltas, alimentada pelo mesmo ViewModel da aba Ao Vivo. | Botão para abrir; posição e tamanho. |

## Testes

```bash
pip3 install -r requirements-dev.txt
python3 -m pytest
```

São **324 testes**, sem PS5: a fonte de telemetria é uma interface, então os
pacotes vêm de um gerador controlado pelo teste.

```bash
python3 tests/bite_check.py
```

A verificação de mordida desfaz cada correção no código de produção e confirma
que algum teste fica vermelho — um teste que passa com o código quebrado não
protege nada. Hoje: **43 de 43**.

## Ferramentas

```bash
python3 src/tools/diagnose.py 192.168.15.156
python3 src/tools/soak_check.py 192.168.15.156 --minutos 60
```

A primeira separa problema de rede de problema do app. A segunda mede a rede
por um período longo **com o app fechado** — UDP entrega cada pacote a um
socket só, então duas cópias na mesma porta não recebem o mesmo fluxo. Com o
app aberto, use o botão 📊.

## Limitações conhecidas

- A tabela do Histórico mostra sempre três colunas de setor, mesmo com outro
  número configurado.
- Mudar os setores da pista não recalcula os tempos de setor das voltas já
  gravadas; vale daí em diante.
- A exportação em JSON é chamada pela camada de aplicação diretamente na
  infraestrutura — o único ponto em que a regra de dependência é quebrada.
