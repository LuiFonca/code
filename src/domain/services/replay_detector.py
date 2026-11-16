"""
Indícios de que a telemetria vem de um replay, e não de alguém pilotando.

O pacote do GT7 não diz quem controla o carro — não existe campo "replay".
O que existe é um comportamento que pilotagem ao vivo nunca produz: o tempo
da volta **andar para trás**. Quem assiste a um replay pausa, volta, avança;
quem pilota só vê o relógio subir, até a virada de volta zerá-lo.

Por isso este detector não afirma, sugere. Ele conta indícios e, passando de
um limite, avisa. A decisão de não gravar continua sendo do piloto — um falso
positivo que descartasse voltas reais seria muito pior que um replay gravado.
"""

# Recuo mínimo, em ms, para contar. Abaixo disso é reordenação de pacote UDP.
RECUO_MINIMO_MS = 500

# Tempo abaixo do qual um recuo é tratado como reinício de sessão, não como
# replay: reiniciar a corrida também volta o relógio, mas volta para zero.
LIMIAR_REINICIO_MS = 2000

# Quantos indícios até avisar. Um só pode ser coincidência.
INDICIOS_PARA_SUSPEITAR = 2


class ReplayDetector:
    """Acumula indícios de replay a partir da sequência de pacotes."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._last_lap: int | None = None
        self._last_ms: int | None = None
        self.evidence = 0
        self._announced = False

    def feed(self, lap_count: int, current_lap_ms: int) -> bool:
        """Processa um pacote. True **uma única vez**, ao cruzar o limite.

        Devolver True só na transição evita que o aviso seja repetido a cada
        pacote dali em diante.
        """
        indicio = False
        if self._last_lap is not None and self._last_ms is not None:
            mesma_volta = lap_count == self._last_lap
            recuo = self._last_ms - current_lap_ms
            if mesma_volta and recuo > RECUO_MINIMO_MS and current_lap_ms > LIMIAR_REINICIO_MS:
                # O relógio voltou para o meio da volta: rebobinaram.
                indicio = True
            elif lap_count < self._last_lap and lap_count > 0 and current_lap_ms > LIMIAR_REINICIO_MS:
                # O contador de voltas diminuiu e caiu no meio de outra volta.
                indicio = True

        self._last_lap, self._last_ms = lap_count, current_lap_ms

        if indicio:
            self.evidence += 1
            if self.evidence >= INDICIOS_PARA_SUSPEITAR and not self._announced:
                self._announced = True
                return True
        return False
