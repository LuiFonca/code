# HANNA GT7 AI — v0.3 (dashboard, histórico, comparação e setores)

Interface gráfica que se conecta à telemetria UDP do Gran Turismo 7, exibe
velocidade, RPM, marcha, acelerador, freio, combustível, pneus e traçado da
volta em tempo real, e guarda um histórico de voltas por pista com
comparação lado a lado, delta e tempos de setor.

## Novidades desta rodada (correções + evolução)

- **Pista e carro podem ser trocados com o PS5 já conectado** — não é mais
  preciso desconectar/reconectar para começar a salvar numa pista nova.
- **Sem pista escolhida = sem histórico**: a telemetria continua aparecendo
  Ao Vivo, mas nada é gravado até uma pista válida ser definida.
- **Modo "Replay / IA"**: o GT7 não expõe um flag confiável para distinguir
  jogador de replay/IA (limitação do protocolo, não do app). Enquanto isso
  não existir, quem sabe que está vendo um replay/IA marca manualmente —
  a volta continua visível Ao Vivo, mas não conta para histórico/recordes.
- **Watchdog de telemetria**: se a transmissão parar (>1s sem frame novo),
  a interface passa para um estado neutro (`0 km/h`, `N`, `0%`...) em vez de
  congelar o último valor recebido, com indicador "● Conectado" / "○ Sem
  dados" no topo da janela.
- **Traçado da volta (posição real X-Z)**: o GT7 já envia a posição de
  mundo do carro em todo pacote; agora ela é gravada e usada para desenhar
  a trajetória percorrida — Ao Vivo (posição atual) e na Comparação
  (Volta A vs Volta B sobrepostas, sincronizado com os outros gráficos).
  Não é um mapa oficial da pista (o GT7 não fornece um) nem mostra curvas
  nomeadas — é a trajetória real, ponto a ponto.
- **Setores consistentes entre voltas**: os limites de setor agora usam uma
  distância de referência da pista (não a distância própria de cada volta),
  então "Setor 2" cai no mesmo trecho físico em voltas diferentes.
  Comparação por setor (tempo A/B/diferença) e "melhor combinação possível"
  somando o melhor setor de cada volta.
- **Comparação robusta a dados incompletos**: voltas com taxas de
  amostragem diferentes, números de pontos diferentes, ou canais ausentes
  (voltas salvas antes de combustível/pneus/posição existirem no banco)
  não quebram mais os gráficos — o canal ausente só não é desenhado.
- **Histórico com carro, ordenação e busca** por qualquer coluna.

Alertas inteligentes em tempo real e um "coach" por IA (resumo em linguagem
natural ao fim de cada volta) continuam como próximos passos — a base de
dados já registra o necessário (delta, setores, trajetória) para isso.

## Estrutura do projeto

```
HANNA GT7 AI/
├── main.py                      # ponto de entrada
├── requirements.txt
├── telemetry/
│   ├── gt7_protocol.py          # decodificação dos pacotes GT7 (Salsa20)
│   └── listener_thread.py       # captura em thread separada (não trava a UI)
├── analysis/                    # lógica pura, sem Qt — testável isoladamente
│   ├── lap_storage.py           # persistência SQLite (pistas/carros/voltas/setores)
│   ├── lap_recorder.py          # detecta início/fim de volta, decide o que persistir
│   ├── lap_comparator.py        # delta ao vivo vs. volta de referência
│   └── telemetry_series.py      # interpolação por distância p/ os gráficos de comparação
└── gui/
    ├── main_window.py           # barra de conexão (IP/pista/carro), watchdog
    ├── widgets.py, widgets_chart.py, widgets_tire.py
    └── tabs/
        ├── live_tab.py          # Ao Vivo
        ├── history_tab.py       # Histórico
        └── comparison_tab.py    # Comparação
```

## Rodando em desenvolvimento

```bash
pip install -r requirements.txt
python3 main.py
```

Na tela que abrir:
1. Digite o IP do seu PS5/PS4 na rede local.
2. Clique em "Conectar".
3. Entre em uma sessão de corrida/track day no GT7 — os dados começam a
   aparecer no dashboard.

## Gerando o .exe (Windows)

O empacotamento precisa ser feito em uma máquina Windows (o PyInstaller gera
executáveis para o mesmo sistema operacional em que roda).

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name "HANNA_GT7_AI" main.py
```

- `--noconsole`: não abre uma janela de terminal preta junto com a interface.
- `--onefile`: gera um único arquivo `HANNA_GT7_AI.exe` (mais fácil de distribuir,
  mas abre um pouco mais devagar que o modo `--onedir`, que gera uma pasta
  com vários arquivos e abre mais rápido).

O executável final aparece em `dist/HANNA_GT7_AI.exe`.

### Se o ícone dos pacotes Qt não for incluído automaticamente

Em alguns ambientes o PyInstaller não detecta sozinho todos os plugins do
Qt/PySide6. Se o `.exe` gerado abrir uma janela em branco ou der erro de
"plugin não encontrado", rode com a flag extra:

```bash
pyinstaller --noconsole --onefile --name "HANNA_GT7_AI" --collect-all PySide6 main.py
```

## Próximos passos

1. **Alertas em tempo real (Camada 1)**: regras de desvio de frenagem/acelerador
   com som de aviso.
2. **Coach por IA (Camada 2)**: resumo em linguagem natural ao fim de cada
   volta/setor, usando os dados já capturados aqui (delta, setores, traçado).
3. **Identificação automática de carro**: nenhum offset de `car_id` foi
   validado com dados reais nesta implementação — hoje o nome do carro é
   sempre manual. Se um offset confiável for confirmado, dá para preencher
   automaticamente sem mudar o schema (a coluna `laps.car_id` já existe).
4. **Detecção automática de jogador vs. replay/IA**: o protocolo do GT7 não
   expõe um flag confiável para isso nesta implementação — hoje é uma marcação
   manual (`LapRecorder.set_player_mode`). Se um sinal confiável aparecer,
   o gate já existe (`LapRecorder.can_persist`) e só precisa trocar a fonte.
