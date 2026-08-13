"""
Ângulo de deriva a partir da orientação do carro.

Deriva é o ângulo entre **para onde o carro aponta** e **para onde ele se
move**. Em linha reta os dois coincidem e o ângulo é zero; numa curva com o
traseiro saindo, o carro aponta para dentro e se move para fora, e o ângulo
cresce.

Por que isto só foi possível agora
-----------------------------------
Até a Fase 6 o app exibia um "índice de deslizamento": o campo `tire_slip` do
GT7, que é razão entre a velocidade da roda e a do solo — útil, mas sem unidade
física, e incapaz de dizer o ângulo do carro.

O que faltava era a orientação, e ela estava no pacote o tempo todo: os bytes
0x1C–0x38 (quaternion de rotação e velocidade angular) simplesmente não eram
lidos. Com o quaternion, o ângulo em graus passa a ser mensurável de verdade.

As duas medidas convivem: o índice diz quanto as rodas escorregam, o ângulo diz
quanto o carro está atravessado. São coisas diferentes.
"""

import math

# Abaixo desta velocidade o vetor de movimento é ruído: parado ou quase parado,
# qualquer oscilação produziria ângulos absurdos.
MIN_SPEED_MS = 2.0

# Ângulos acima disto não descrevem pilotagem — são trompadas, rodadas ou
# reposicionamento do carro pelo jogo. Saturar evita que um instante desses
# domine a escala do gráfico da volta inteira.
MAX_SLIP_ANGLE_DEG = 90.0


def forward_vector_xz(rot_i: float, rot_j: float, rot_k: float, rot_w: float):
    """Direção para onde o carro aponta, projetada no plano da pista.

    Rotaciona o vetor unitário de frente pelo quaternion e descarta a
    componente vertical. Descartar Y é intencional: subida e descida não têm
    nada a ver com deriva, e mantê-las faria uma ladeira virar ângulo.

    Fórmula de rotação por quaternion, sem montar a matriz:
        v' = v + 2·w·(q × v) + 2·(q × (q × v))
    """
    # Vetor de frente do carro no referencial local.
    vx, vy, vz = 0.0, 0.0, 1.0

    # q × v
    cx = rot_j * vz - rot_k * vy
    cy = rot_k * vx - rot_i * vz
    cz = rot_i * vy - rot_j * vx

    # q × (q × v)
    ccx = rot_j * cz - rot_k * cy
    ccy = rot_k * cx - rot_i * cz
    ccz = rot_i * cy - rot_j * cx

    fx = vx + 2.0 * (rot_w * cx + ccx)
    fz = vz + 2.0 * (rot_w * cz + ccz)
    return fx, fz


def slip_angle_deg(
    velocity_x: float,
    velocity_z: float,
    rot_i: float,
    rot_j: float,
    rot_k: float,
    rot_w: float,
) -> float | None:
    """Ângulo de deriva em graus, ou None quando não é mensurável.

    O sinal indica o lado: positivo com a traseira saindo para um lado,
    negativo para o outro. O valor absoluto é o que importa para leitura de
    pilotagem, mas o sinal permite distinguir curva à esquerda de curva à
    direita no gráfico.

    Devolve None — e não zero — quando o carro está parado demais para haver
    direção de movimento. Zero significaria "sem deriva", que é uma afirmação
    diferente de "não dá para saber".
    """
    velocidade = math.hypot(velocity_x, velocity_z)
    if velocidade < MIN_SPEED_MS:
        return None

    # A norma do quaternion precisa ser conferida ANTES de rotacionar. Um
    # quaternion nulo (voltas antigas, pacote sem o campo) produz um vetor de
    # frente aparentemente válido — a rotação de (0,0,1) por zeros devolve
    # (0,0,1) — e o ângulo sairia como zero, afirmando "sem deriva" onde a
    # resposta certa é "não dá para saber".
    norma = math.sqrt(rot_i * rot_i + rot_j * rot_j + rot_k * rot_k + rot_w * rot_w)
    if norma < 0.5:
        return None

    fx, fz = forward_vector_xz(rot_i, rot_j, rot_k, rot_w)
    if math.hypot(fx, fz) < 1e-6:
        # Carro apontando exatamente para cima ou para baixo: sem direção
        # horizontal, não há deriva definida no plano da pista.
        return None

    # Ângulo com sinal entre os dois vetores, via produto vetorial e escalar.
    # `atan2` sobre eles é estável mesmo perto de 0° e de 180°, ao contrário
    # de `acos` do produto escalar normalizado.
    # Ordem do produto vetorial escolhida para que desvio à direita do nariz
    # do carro dê ângulo positivo — invertê-la troca o lado no gráfico.
    cruzado = fz * velocity_x - fx * velocity_z
    escalar = fx * velocity_x + fz * velocity_z
    angulo = math.degrees(math.atan2(cruzado, escalar))

    if angulo > MAX_SLIP_ANGLE_DEG:
        return MAX_SLIP_ANGLE_DEG
    if angulo < -MAX_SLIP_ANGLE_DEG:
        return -MAX_SLIP_ANGLE_DEG
    return angulo
