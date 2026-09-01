"""
A volta deu a volta na pista?

Até aqui o programa aceitava qualquer volta que o jogo fechasse. Cortar uma
chicane, sair da pista e voltar com o *reset*, ou abandonar no meio produzia uma
volta gravada igual às outras — que entrava no recorde, na mediana, no perfil do
piloto e no resumo que sobe para o prompt do engenheiro sem nada dizendo que ela
não valia.

A Fase 2 trouxe de graça o número que resolve isso: o comprimento oficial da
pista, do catálogo do jogo. Comparado com a distância que o hodômetro mediu, ele
responde a pergunta de forma direta — uma volta que percorreu 92% de Suzuka não
deu a volta em Suzuka.

Por que só a volta curta é excluída
-----------------------------------
Uma volta **curta** é perigosa porque produz um tempo **rápido** que é falso, e
tempo rápido falso vira recorde — e recorde é a referência do delta, do alvo na
tela e da comparação. Um erro que se propaga.

Uma volta **longa** é auto-punida: rodar, sair da pista e voltar acrescenta
distância *e* tempo, então ela sai mais lenta e nunca disputa o recorde. Ela é
uma volta que o piloto realmente deu, só que ruim. Marcar sem excluir é o
tratamento honesto — quem quiser entender uma volta ruim precisa dela ali.

Sobre os limiares
-----------------
Estes números **não estão calibrados contra telemetria real** — foram escolhidos
para pegar só o caso grosseiro (volta abandonada, reset, corte grande), e não o
corte marginal de uma chicane, que fica dentro da faixa aceita.

A assimetria é deliberada e é a mesma do guarda da IA: marcar uma volta limpa
como suja custa mais caro que deixar passar uma suja. Uma volta limpa recusada
some do recorde sem explicação, e o piloto não tem como saber por quê.

Por isso a razão medida é **mostrada na tela** em vez de ficar só aqui dentro:
com voltas reais na mão dá para apertar os limites com base em medição, que é
como esse número deveria ter sido escolhido desde o começo.
"""

from __future__ import annotations

from enum import StrEnum

#: Abaixo disto a volta não cobriu a pista. 10% de Suzuka são 580 m — nenhuma
#: linha de corrida economiza isso; é corte grande, reset ou abandono.
MIN_COMPLETE_RATIO = 0.90

#: Acima disto sobrou distância: rodada, saída de pista com retorno, ou âncora
#: errada. Não invalida a volta, só a marca.
MAX_COMPLETE_RATIO = 1.15


class LapValidity(StrEnum):
    """O que se sabe sobre a cobertura da volta.

    `UNKNOWN` é um estado de primeira classe, e não um sinônimo de válido: pista
    fora do catálogo não tem com o que comparar, e afirmar "completa" nesse caso
    seria inventar uma verificação que não aconteceu.
    """

    UNKNOWN = "desconhecida"
    COMPLETE = "completa"
    INCOMPLETE = "incompleta"
    LONG = "longa"

    @property
    def counts_as_record(self) -> bool:
        """Pode disputar o recorde da pista?

        Só a incompleta fica de fora, e é a única que precisa: ela é a única que
        produz um tempo mais **rápido** do que o piloto realmente fez.
        """
        return self is not LapValidity.INCOMPLETE

    @property
    def label(self) -> str:
        return {
            LapValidity.UNKNOWN: "",
            LapValidity.COMPLETE: "",
            LapValidity.INCOMPLETE: "incompleta",
            LapValidity.LONG: "distância acima do traçado",
        }[self]


def lap_coverage(lap_distance_m: float | None, track_length_m: float | None) -> float | None:
    """Quanto da pista a volta percorreu, em fração. `None` sem com o que comparar."""
    if not lap_distance_m or lap_distance_m <= 0:
        return None
    if not track_length_m or track_length_m <= 0:
        return None
    return lap_distance_m / track_length_m


def classify_lap(
    lap_distance_m: float | None, track_length_m: float | None
) -> LapValidity:
    """Classifica a volta pela distância percorrida contra o traçado oficial."""
    coverage = lap_coverage(lap_distance_m, track_length_m)
    if coverage is None:
        return LapValidity.UNKNOWN
    if coverage < MIN_COMPLETE_RATIO:
        return LapValidity.INCOMPLETE
    if coverage > MAX_COMPLETE_RATIO:
        return LapValidity.LONG
    return LapValidity.COMPLETE


def describe_coverage(coverage: float | None) -> str:
    """A razão como texto para a tela. Travessão quando não houve comparação."""
    if coverage is None:
        return "—"
    return f"{coverage * 100:.1f}%"
