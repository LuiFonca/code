"""
Curvas de uma volta: onde estão, como foram feitas e quanto custaram.

O gráfico de velocidade mostra a volta inteira; o que o piloto quer saber é
mais específico — *em qual curva* está perdendo tempo e *por quê*. Este módulo
corta a volta em curvas e extrai de cada uma os números que respondem isso.

Detecção
--------
Uma curva é um trecho em que a força lateral fica acima de um limiar por uma
distância mínima. O limiar separa curva de correção de volante em reta; a
distância mínima descarta solavancos. Trechos muito próximos são fundidos,
porque um "S" rápido aparece como dois picos com um vale curto no meio e é
vivido pelo piloto como uma coisa só — a menos que a força troque de lado, que
aí são duas curvas de verdade.

Não há mapa oficial de curvas no pacote do GT7. A numeração aqui é a ordem em
que as curvas aparecem na volta, e pode não bater com a numeração da pista.
"""

import bisect
from dataclasses import dataclass

GRAVIDADE = 9.81

# Força lateral que caracteriza curva. Abaixo disso é reta com correção.
LIMIAR_G_LATERAL = 0.35

# Comprimento mínimo para contar como curva, e vão máximo entre dois trechos
# para serem fundidos na mesma.
MIN_COMPRIMENTO_M = 15.0
MAX_VAO_M = 25.0

# Quanto antes da entrada se procura o início da frenagem, e a partir de
# quanto de pedal conta como frear (abaixo é roçar o pedal).
JANELA_FRENAGEM_M = 200.0
LIMIAR_FREIO_PCT = 10.0

# Razão entre a guinada medida e a que a curva exigiria. Dentro da faixa o
# carro está neutro; acima gira mais do que a trajetória pede (traseira
# saindo), abaixo gira menos (frente saindo).
FAIXA_NEUTRA = (0.85, 1.15)

EQUILIBRIO_NEUTRO = "neutro"
EQUILIBRIO_SOBRESTERCO = "sobresterço"
EQUILIBRIO_SUBESTERCO = "subesterço"


@dataclass(frozen=True, slots=True)
class Corner:
    """Uma curva da volta."""

    index: int                    # ordem na volta, a partir de 1
    start_m: float
    end_m: float
    apex_m: float                 # ponto de menor velocidade
    direction: str                # "esquerda" ou "direita"
    min_speed_kmh: float
    entry_speed_kmh: float
    exit_speed_kmh: float
    peak_g: float
    brake_point_m: float | None   # onde a frenagem começou; None se não freou
    balance: str | None           # None quando a volta não tem taxa de guinada

    @property
    def length_m(self) -> float:
        return self.end_m - self.start_m


@dataclass(frozen=True, slots=True)
class CornerDelta:
    """Uma curva comparada com a mesma curva na volta de referência."""

    corner: Corner
    time_delta_ms: float | None        # positivo = perdeu tempo na curva
    min_speed_delta_kmh: float | None  # positivo = passou mais rápido
    brake_point_delta_m: float | None  # positivo = freou mais tarde


def _trechos_em_curva(points) -> list[tuple[int, int]]:
    """Índices (início, fim) dos trechos acima do limiar, já fundidos."""
    brutos = []
    inicio = None
    for i, p in enumerate(points):
        em_curva = p.g_lateral is not None and abs(p.g_lateral) >= LIMIAR_G_LATERAL
        if em_curva and inicio is None:
            inicio = i
        elif not em_curva and inicio is not None:
            brutos.append((inicio, i - 1))
            inicio = None
    if inicio is not None:
        brutos.append((inicio, len(points) - 1))

    fundidos: list[tuple[int, int]] = []
    for ini, fim in brutos:
        if fundidos:
            ant_ini, ant_fim = fundidos[-1]
            vao = points[ini].distance_m - points[ant_fim].distance_m
            mesmo_lado = (points[ant_fim].g_lateral > 0) == (points[ini].g_lateral > 0)
            if vao <= MAX_VAO_M and mesmo_lado:
                fundidos[-1] = (ant_ini, fim)
                continue
        fundidos.append((ini, fim))

    return [
        (ini, fim) for ini, fim in fundidos
        if points[fim].distance_m - points[ini].distance_m >= MIN_COMPRIMENTO_M
    ]


def _ponto_de_frenagem(points, inicio: int) -> float | None:
    """Distância em que o freio foi acionado antes da curva.

    Anda para trás a partir da entrada: o ponto de frenagem é o começo do
    último trecho contínuo de freio antes da curva, não a primeira vez que o
    pedal foi tocado na reta.
    """
    limite = points[inicio].distance_m - JANELA_FRENAGEM_M
    i = inicio
    # Frenagem pode ter terminado um pouco antes da força lateral subir.
    while i > 0 and points[i].distance_m >= limite and (points[i].brake or 0) < LIMIAR_FREIO_PCT:
        i -= 1
    if (points[i].brake or 0) < LIMIAR_FREIO_PCT:
        return None
    while i > 0 and points[i - 1].distance_m >= limite and (points[i - 1].brake or 0) >= LIMIAR_FREIO_PCT:
        i -= 1
    return points[i].distance_m


def _equilibrio(points, inicio: int, fim: int) -> str | None:
    """Subesterço, sobresterço ou neutro, pela guinada medida contra a exigida.

    Numa curva em regime, a guinada que a trajetória pede é a aceleração
    lateral dividida pela velocidade. Se o carro gira mais do que isso, a
    traseira está saindo; se gira menos, é a frente que escapa.

    Usa a mediana das razões, não a média: a entrada e a saída da curva são
    transitórias e produzem razões extremas que arrastariam a média.
    """
    razoes = []
    for p in points[inicio:fim + 1]:
        if p.yaw_rate is None or p.g_lateral is None:
            continue
        velocidade = p.speed_kmh / 3.6
        if velocidade < 5.0:
            continue
        exigida = abs(p.g_lateral) * GRAVIDADE / velocidade
        if exigida < 0.02:
            continue
        razoes.append(abs(p.yaw_rate) / exigida)
    if len(razoes) < 5:
        return None
    razoes.sort()
    mediana = razoes[len(razoes) // 2]
    if mediana > FAIXA_NEUTRA[1]:
        return EQUILIBRIO_SOBRESTERCO
    if mediana < FAIXA_NEUTRA[0]:
        return EQUILIBRIO_SUBESTERCO
    return EQUILIBRIO_NEUTRO


def detect_corners(points) -> list[Corner]:
    """Curvas da volta, na ordem em que aparecem."""
    if len(points) < 10:
        return []

    curvas = []
    for numero, (ini, fim) in enumerate(_trechos_em_curva(points), start=1):
        trecho = points[ini:fim + 1]
        apice = min(trecho, key=lambda p: p.speed_kmh)
        pico = max(trecho, key=lambda p: abs(p.g_lateral))
        curvas.append(
            Corner(
                index=numero,
                start_m=points[ini].distance_m,
                end_m=points[fim].distance_m,
                apex_m=apice.distance_m,
                direction="direita" if pico.g_lateral > 0 else "esquerda",
                min_speed_kmh=apice.speed_kmh,
                entry_speed_kmh=points[ini].speed_kmh,
                exit_speed_kmh=points[fim].speed_kmh,
                peak_g=abs(pico.g_lateral),
                brake_point_m=_ponto_de_frenagem(points, ini),
                balance=_equilibrio(points, ini, fim),
            )
        )
    return curvas


def _tempo_em(distancias, tempos, d: float) -> float | None:
    if not distancias or d < distancias[0] or d > distancias[-1]:
        return None
    i = bisect.bisect_left(distancias, d)
    if i == 0:
        return tempos[0]
    d0, d1 = distancias[i - 1], distancias[i]
    if d1 == d0:
        return tempos[i - 1]
    return tempos[i - 1] + (d - d0) / (d1 - d0) * (tempos[i] - tempos[i - 1])


def compare_corners(reference_points, lap_points) -> list[CornerDelta]:
    """Curva a curva, o que a volta fez de diferente da referência.

    As curvas são as da **referência**: é contra ela que se mede, e usar as
    curvas de cada volta separadamente faria a curva 3 de uma cair num trecho
    diferente da curva 3 da outra.

    O tempo é medido de um pouco antes da entrada até um pouco depois da
    saída, porque frear mais tarde ou acelerar mais cedo acontece fora do
    trecho de força lateral — e é justamente aí que se ganha ou perde.
    """
    curvas_ref = detect_corners(reference_points)
    if not curvas_ref or len(lap_points) < 10:
        return []

    d_ref = [p.distance_m for p in reference_points]
    t_ref = [p.elapsed_ms for p in reference_points]
    d_lap = [p.distance_m for p in lap_points]
    t_lap = [p.elapsed_ms for p in lap_points]
    curvas_lap = detect_corners(lap_points)

    resultado = []
    for curva in curvas_ref:
        ini = max(curva.start_m - 50.0, 0.0)
        fim = min(curva.end_m + 50.0, d_ref[-1], d_lap[-1])

        delta_tempo = None
        marcas = [
            _tempo_em(d_ref, t_ref, ini), _tempo_em(d_ref, t_ref, fim),
            _tempo_em(d_lap, t_lap, ini), _tempo_em(d_lap, t_lap, fim),
        ]
        if fim > ini and all(m is not None for m in marcas):
            delta_tempo = (marcas[3] - marcas[2]) - (marcas[1] - marcas[0])

        # A curva correspondente na outra volta é a que contém o ápice desta.
        par = next(
            (c for c in curvas_lap if c.start_m - 30.0 <= curva.apex_m <= c.end_m + 30.0),
            None,
        )
        delta_vel = par.min_speed_kmh - curva.min_speed_kmh if par else None
        delta_freio = None
        if par and par.brake_point_m is not None and curva.brake_point_m is not None:
            delta_freio = par.brake_point_m - curva.brake_point_m

        resultado.append(
            CornerDelta(
                corner=curva,
                time_delta_ms=delta_tempo,
                min_speed_delta_kmh=delta_vel,
                brake_point_delta_m=delta_freio,
            )
        )
    return resultado


def delta_trace(reference_points, lap_points, segments: int = 60):
    """Onde a volta ganha e onde perde, trecho a trecho, com posição no mapa.

    Devolve `[(pontos_xz, ganho_ms)]`: cada item é um pedaço do traçado e
    quanto tempo foi ganho (negativo) ou perdido (positivo) *naquele pedaço*
    em relação à referência. É a derivada do delta — o delta acumulado diz
    quanto se está atrás; isto diz onde a diferença foi construída.
    """
    if len(reference_points) < 10 or len(lap_points) < 10:
        return []

    d_ref = [p.distance_m for p in reference_points]
    t_ref = [p.elapsed_ms for p in reference_points]
    d_lap = [p.distance_m for p in lap_points]
    t_lap = [p.elapsed_ms for p in lap_points]
    total = min(d_ref[-1], d_lap[-1])
    if total <= 0:
        return []

    passo = total / segments
    saida = []
    cursor = 0
    for k in range(segments):
        ini, fim = k * passo, (k + 1) * passo
        marcas = [
            _tempo_em(d_ref, t_ref, ini), _tempo_em(d_ref, t_ref, fim),
            _tempo_em(d_lap, t_lap, ini), _tempo_em(d_lap, t_lap, fim),
        ]
        if any(m is None for m in marcas):
            continue
        ganho = (marcas[3] - marcas[2]) - (marcas[1] - marcas[0])

        pedaco = []
        while cursor < len(lap_points) and lap_points[cursor].distance_m <= fim:
            p = lap_points[cursor]
            if p.position_x is not None and p.position_z is not None:
                pedaco.append((p.position_x, p.position_z))
            cursor += 1
        # O pedaço seguinte começa onde este termina, para não abrir buraco
        # no desenho do traçado.
        if cursor > 0:
            cursor -= 1
        if len(pedaco) >= 2:
            saida.append((pedaco, ganho))
    return saida
