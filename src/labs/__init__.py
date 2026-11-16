"""
Bases para recursos ainda não expostos na interface.

Cada módulo aqui é uma peça pronta e testada de um recurso maior que ainda não
tem tela. Ficam separados do resto de propósito: nada em `src/` fora deste
pacote os importa, então dá para ver de relance o que é produto e o que é
terreno preparado.

- `session_recording` — grava a sessão bruta em arquivo e a reproduz depois
  como se fosse o console. Falta: botão de gravar e de abrir gravação.
- `overlay` — janela compacta, sempre visível, com velocidade, marcha e delta.
  Falta: botão para abrir e opção de posição/tamanho.
- `coach` — transforma a comparação curva a curva em frases. Falta: um lugar
  na aba Comparação para mostrá-las.
"""
