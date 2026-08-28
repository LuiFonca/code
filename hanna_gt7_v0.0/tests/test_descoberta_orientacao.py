"""
A sonda que investiga os 28 bytes desconhecidos precisa achar o que procura.

Uma ferramenta de descoberta que não detecta o alvo desperdiça a viagem de quem
a roda — e, pior, um resultado negativo dela seria lido como "a hipótese está
errada" quando o errado era o detector. Aqui os bytes são **plantados**: sabe-se
a resposta antes de perguntar, e o teste falha se a sonda não a encontrar.

O contrário também é prendido: alimentada com ruído, ela não pode anunciar
descoberta. Uma sonda que sempre confirma não é sonda, é espelho.
"""

from __future__ import annotations

import math
import random

from tools.descobre_orientacao import (
    INICIO,
    Amostra,
    correlacao,
    guinada_da_trajetoria,
)


def _volta_circular(
    *, raio_m: float, velocidade_ms: float, amostras: int = 600
) -> tuple[list[Amostra], float]:
    """Carro em círculo, com quaternion e velocidade angular **plantados**.

    Num círculo a guinada é conhecida: `v / R` rad/s, constante. É a mesma
    referência fechada que os testes de `analytics.steering` usam — sem ela,
    um teste de guinada só compara o código consigo mesmo.
    """
    omega = velocidade_ms / raio_m
    dt = 1.0 / 60.0

    saida: list[Amostra] = []
    for i in range(amostras):
        t = i * dt
        angulo = omega * t

        # Quaternion de rotação em torno do eixo vertical, normalizado por
        # construção: (0, sin(θ/2), 0, cos(θ/2)).
        meio = angulo / 2.0
        quaternion = (0.0, math.sin(meio), 0.0, math.cos(meio))
        velocidade_angular = (0.0, omega, 0.0)

        saida.append(
            Amostra(
                tick=i,
                x=raio_m * math.cos(angulo),
                z=raio_m * math.sin(angulo),
                speed_kmh=velocidade_ms * 3.6,
                desconhecidos=quaternion + velocidade_angular,
            )
        )
    return saida, omega


class TestASondaAchaOQueFoiPlantado:
    def test_reconhece_o_quaternion_pela_norma(self) -> None:
        amostras, _ = _volta_circular(raio_m=60.0, velocidade_ms=25.0)

        normas = [
            math.sqrt(sum(a.desconhecidos[k] ** 2 for k in range(4)))
            for a in amostras
        ]

        assert max(abs(n - 1.0) for n in normas) < 0.01

    def test_acha_a_guinada_no_float_certo(self) -> None:
        """O float plantado com a guinada tem de ser o que correlaciona.

        A referência sai da **posição**, não do bloco sob teste: é o que dá ao
        teste o direito de reprovar. Se a correlação viesse do mesmo lugar que o
        candidato, qualquer coisa passaria.
        """
        amostras, omega = _volta_circular(raio_m=60.0, velocidade_ms=25.0)

        referencia = guinada_da_trajetoria(amostras)
        assert len(referencia) > 100, "o círculo tem amostras de sobra"

        indices = [i for i, _ in referencia]
        esperado = [v for _, v in referencia]
        # A guinada foi plantada no índice 5 (0x2C + 4 = ang.vel y).
        candidato = [amostras[i].desconhecidos[5] for i in indices]

        assert abs(correlacao(esperado, candidato)) > 0.90 or all(
            abs(v - omega) < 0.01 for v in candidato
        )

    def test_a_referencia_bate_com_a_guinada_conhecida(self) -> None:
        """A conta da própria sonda tem de dar `v/R` — senão ela mede errado.

        Sem isto, uma sonda com a derivada trocada reprovaria a hipótese certa e
        mandaria procurar em outro lugar do pacote.
        """
        amostras, omega = _volta_circular(raio_m=80.0, velocidade_ms=30.0)

        medido = [v for _, v in guinada_da_trajetoria(amostras)]
        media = sum(medido) / len(medido)

        assert abs(media - omega) < 0.02 * omega

    def test_a_sonda_olha_o_mesmo_bloco_que_o_leitor(self) -> None:
        """Sonda e leitor precisam falar do mesmo pedaço do pacote.

        Este teste já reprovou uma vez, e por bom motivo: ele guardava que o
        protocolo **não** lia 0x1C, e disparou no instante em que passou a
        ler. Agora guarda o acordo entre os dois — a sonda existe para
        confirmar contra um console de verdade a interpretação que o leitor
        já adota, e apontar para offsets diferentes tornaria a confirmação
        inútil sem que nada denunciasse.
        """
        import inspect

        from gt7core.telemetry.protocol import TelemetryFrame

        fonte = inspect.getsource(TelemetryFrame.from_bytes)

        assert INICIO == 0x1C
        assert "d[0x1C:0x28]" in fonte, "o leitor mudou de offset"
        assert "d[0x2C:0x38]" in fonte, "a velocidade angular mudou de offset"


class TestASondaNaoInventa:
    def test_ruido_nao_vira_quaternion(self) -> None:
        """Sonda que sempre confirma não é sonda, é espelho."""
        aleatorio = random.Random(7)
        amostras = [
            Amostra(
                tick=i, x=float(i), z=0.0, speed_kmh=150.0,
                desconhecidos=tuple(aleatorio.uniform(-1.0, 1.0) for _ in range(7)),
            )
            for i in range(400)
        ]

        normas = [
            math.sqrt(sum(a.desconhecidos[k] ** 2 for k in range(4)))
            for a in amostras
        ]

        assert max(abs(n - 1.0) for n in normas) > 0.01

    def test_ruido_nao_correlaciona_com_a_guinada(self) -> None:
        aleatorio = random.Random(11)
        amostras, _ = _volta_circular(raio_m=60.0, velocidade_ms=25.0)
        amostras = [
            Amostra(
                tick=a.tick, x=a.x, z=a.z, speed_kmh=a.speed_kmh,
                desconhecidos=tuple(aleatorio.uniform(-1.0, 1.0) for _ in range(7)),
            )
            for a in amostras
        ]

        referencia = guinada_da_trajetoria(amostras)
        indices = [i for i, _ in referencia]
        esperado = [v for _, v in referencia]

        for f in range(7):
            candidato = [amostras[i].desconhecidos[f] for i in indices]
            assert abs(correlacao(esperado, candidato)) < 0.5

    def test_carro_parado_nao_gera_referencia(self) -> None:
        """Parado, `atan2` de dois zeros é ruído puro — e viraria falso achado."""
        amostras = [
            Amostra(tick=i, x=0.0, z=0.0, speed_kmh=0.0, desconhecidos=(0.0,) * 7)
            for i in range(200)
        ]

        assert guinada_da_trajetoria(amostras) == []
