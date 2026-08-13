# HANNA GT7 AI — v0.3 (dashboard, histórico, comparação e setores)

Interface gráfica que se conecta à telemetria UDP do Gran Turismo 7, exibe
velocidade, RPM, marcha, acelerador, freio, combustível, pneus e traçado da
volta em tempo real, e guarda um histórico de voltas por pista com
comparação lado a lado, delta e tempos de setor.


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


## Próximos passos

1. **Reavaliar arquitetura afim de melhorar o fluxo de dados,logica matematica e legibilidade do codigo**:
2. **Alertas em tempo real (Camada 1)**: regras de desvio de frenagem/acelerador
   com som de aviso.
3. **Coach por IA (Camada 2)**: resumo com assistente de voz ao fim de cada
   volta, usando os dados já capturados aqui (delta, setores, traçado).
4. **Identificação automática de carro**: 
5. **Detecção automática de jogador vs. replay/IA**
