# HANNA GT7 AI — v0.1 (Dashboard ao vivo)

Interface gráfica que se conecta à telemetria UDP do Gran Turismo 7 e exibe
velocidade, RPM, marcha, throttle, freio, combustível e informações de volta
em tempo real.

Este é o **Passo 1 com interface**: captura + visualização. Os alertas
inteligentes (Camada 1 do coach) e a análise por IA (Camada 2) entram nas
próximas etapas, já com esta base pronta para receber.

## Estrutura do projeto

```
app/
├── main.py                      # ponto de entrada
├── requirements.txt
├── telemetry/
│   ├── gt7_protocol.py          # decodificação dos pacotes GT7 (Salsa20)
│   └── listener_thread.py       # captura em thread separada (não trava a UI)
└── gui/
    └── main_window.py           # interface PySide6 (tema escuro)
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

1. **Volta de referência**: salvar a melhor volta e permitir comparação.
2. **Alertas em tempo real (Camada 1)**: regras de desvio de frenagem/acelerador
   com som de aviso.
3. **Coach por IA (Camada 2)**: resumo em linguagem natural ao fim de cada
   volta/setor, usando os dados já capturados aqui.
