"""
Elevação: a altitude atravessa o domínio, e a gravidade entra na força G.

Estes testes valem por casos em que a resposta certa é **conhecida por
física**, não por captura do comportamento atual: um carro descendo uma rampa a
velocidade constante tem aceleração zero e mesmo assim está freando, e o número
que os pneus fazem é `sen(rampa)`. Um teste que só congela o que o código faz
hoje passa igual depois de o código quebrar.
"""

from __future__ import annotations

import math

import pytest

from gt7core.analytics.elevation import (
    bank_series,
    elevation_range_m,
    elevation_series,
    slope_series,
)
from gt7core.domain.models import TelemetryPoint
from gt7core.events.bus import EventBus
from gt7core.telemetry.engine import TelemetryEngine
from gt7core.telemetry.protocol import TelemetryFrame

GRAVIDADE = 9.81


def frame(**campos) -> TelemetryFrame:
    """Quadro mínimo, com o que a força G precisa e o resto em zero."""
    base = dict(
        speed_kmh=100.0, rpm=5000.0, gear=4, suggested_gear=4,
        throttle=50.0, brake=0.0, fuel=50.0, fuel_capacity=60.0,
        lap_count=1, total_laps=0,
        position_x=0.0, position_y=0.0, position_z=0.0,
        velocity_x=0.0, velocity_y=0.0, velocity_z=0.0,
        body_height=0.1, best_lap_ms=-1, last_lap_ms=-1,
        packet_id=1, day_progression_ms=0,
        tire_temp_fl=80.0, tire_temp_fr=80.0, tire_temp_rl=80.0, tire_temp_rr=80.0,
        suspension_fl=0.0, suspension_fr=0.0, suspension_rl=0.0, suspension_rr=0.0,
        tire_slip_fl=0.0, tire_slip_fr=0.0, tire_slip_rl=0.0, tire_slip_rr=0.0,
        turbo_boost=1.0, oil_pressure=5.0, oil_temp=90.0, water_temp=85.0,
        rpm_flashing_min=7000, rpm_flashing_max=7500, max_speed_kmh=300,
        flags=1, car_id=1,
    )
    base.update(campos)
    return TelemetryFrame(**base)


def ponto(**campos) -> TelemetryPoint:
    base = dict(
        elapsed_ms=0, distance_m=0.0, speed_kmh=100.0, rpm=5000.0, gear=4,
        throttle=0.0, brake=0.0, fuel_level=50.0,
        tire_temp_fl=80.0, tire_temp_fr=80.0, tire_temp_rl=80.0, tire_temp_rr=80.0,
        position_x=0.0, position_z=0.0, g_lateral=0.0, g_longitudinal=0.0,
        suspension_fl=0.0, suspension_fr=0.0, suspension_rl=0.0, suspension_rr=0.0,
        tire_slip_fl=0.0, tire_slip_fr=0.0, tire_slip_rl=0.0, tire_slip_rr=0.0,
        turbo_boost=1.0, oil_temp=90.0, water_temp=85.0,
    )
    base.update(campos)
    return TelemetryPoint(**base)


def normal_de_rampa(rampa: float, rumo: tuple[float, float]) -> tuple[float, float, float]:
    """Normal unitária de um asfalto com esta rampa, subindo na direção do rumo."""
    fx, fz = rumo
    cos = 1.0 / math.hypot(1.0, rampa)
    return (-rampa * fx * cos, cos, -rampa * fz * cos)


class TestValidacaoDaNormal:
    """A trava que impede o programa de inventar inclinação."""

    def test_normal_unitaria_passa(self):
        assert frame(road_plane_x=0.0, road_plane_y=1.0, road_plane_z=0.0).road_plane_is_valid

    def test_vetor_zerado_reprova(self):
        # É o que uma fonte que não preenche o campo entrega. Reprovar aqui é o
        # que faz o resto do programa cair no horizontal em vez de dividir por
        # zero achando que mediu alguma coisa.
        assert not frame().road_plane_is_valid
        assert frame().road_normal is None

    def test_vetor_de_comprimento_errado_reprova(self):
        curto = frame(road_plane_x=0.0, road_plane_y=0.7, road_plane_z=0.0)
        assert not curto.road_plane_is_valid

    def test_normal_deitada_reprova(self):
        # Unitária, mas apontando quase para o lado: seria uma rampa de 570%.
        # Sem esta segunda trava ela passaria na norma e explodiria a divisão.
        assert not frame(
            road_plane_x=0.985, road_plane_y=0.174, road_plane_z=0.0
        ).road_plane_is_valid


class TestGravidadeNaForcaG:
    """A conta que a versão anterior não fazia."""

    def _duas_amostras(self, primeiro: TelemetryFrame, segundo: TelemetryFrame):
        motor = TelemetryEngine(EventBus())
        motor._compute_g_forces(primeiro, 0.0)
        return motor._compute_g_forces(segundo, 100.0)

    def test_pista_plana_da_o_mesmo_de_antes(self):
        # A garantia de não-regressão: sem inclinação, o termo novo é
        # multiplicado por zero. Freada de 5 m/s em 0,1 s = 50 m/s² — absurda de
        # propósito, para o número ser conferível na mão: 50/9,81 = 5,097 g.
        g_lat, g_long = self._duas_amostras(
            frame(velocity_x=30.0, road_plane_y=1.0),
            frame(velocity_x=25.0, road_plane_y=1.0, speed_kmh=90.0),
        )
        assert g_long == pytest.approx(-50.0 / GRAVIDADE, rel=1e-6)
        assert g_lat == pytest.approx(0.0, abs=1e-9)

    def test_descida_a_velocidade_constante_e_frenagem(self):
        """O caso que prova a correção: aceleração zero, pneus freando.

        Rampa de 10% descendo. A velocidade escalar não muda, então a conta
        antiga daria 0,000 g — "não freou". Os pneus, porém, estão segurando o
        carro contra a gravidade, e a força que fazem é `sen(θ)` da rampa.
        """
        rampa = -0.10
        rumo = (1.0, 0.0)
        normal = normal_de_rampa(rampa, rumo)
        # Velocidade repartida entre plano e vertical, como no mundo real.
        cos = 1.0 / math.hypot(1.0, rampa)
        vx, vy = 30.0 * cos, 30.0 * rampa * cos
        parado = frame(
            velocity_x=vx, velocity_y=vy,
            road_plane_x=normal[0], road_plane_y=normal[1], road_plane_z=normal[2],
        )
        g_lat, g_long = self._duas_amostras(parado, parado)

        seno_da_rampa = rampa / math.hypot(1.0, rampa)
        assert g_long == pytest.approx(seno_da_rampa, rel=1e-6)
        assert g_long < 0, "descida a velocidade constante é frenagem"
        assert g_lat == pytest.approx(0.0, abs=1e-9)

    def test_subida_a_velocidade_constante_e_traçao(self):
        rampa = 0.10
        normal = normal_de_rampa(rampa, (1.0, 0.0))
        cos = 1.0 / math.hypot(1.0, rampa)
        parado = frame(
            velocity_x=30.0 * cos, velocity_y=30.0 * rampa * cos,
            road_plane_x=normal[0], road_plane_y=normal[1], road_plane_z=normal[2],
        )
        _, g_long = self._duas_amostras(parado, parado)
        assert g_long > 0, "subir a velocidade constante exige tração"
        assert g_long == pytest.approx(rampa / math.hypot(1.0, rampa), rel=1e-6)

    def test_sobrelevacao_alivia_a_carga_lateral(self):
        """Curva com banking: parte da força vem do asfalto, não do pneu.

        Mesma trajetória, mesma aceleração lateral. Com o asfalto inclinado
        para dentro, o pneu precisa fazer **menos** — que é a razão de existir
        uma curva sobrelevada.
        """
        def lateral(normal):
            motor = TelemetryEngine(EventBus())
            comum = dict(
                road_plane_x=normal[0], road_plane_y=normal[1], road_plane_z=normal[2],
            )
            motor._compute_g_forces(frame(velocity_x=30.0, velocity_z=0.0, **comum), 0.0)
            # Guinada à esquerda: a velocidade ganha componente −Z.
            g_lat, _ = motor._compute_g_forces(
                frame(velocity_x=30.0, velocity_z=-3.0, **comum), 100.0
            )
            return g_lat

        plana = lateral((0.0, 1.0, 0.0))
        # A geometria, explícita porque o sinal é fácil de errar: andando em +X
        # com Y para cima, a direita do carro é +Z. Numa curva à esquerda o
        # asfalto tem que **subir para a direita**, e a normal de uma superfície
        # que sobe para +Z se inclina para −Z. Daí o menos.
        inclinacao = 0.15
        cos = 1.0 / math.hypot(1.0, inclinacao)
        sobrelevada = lateral((0.0, cos, -inclinacao * cos))
        contra_peraltada = lateral((0.0, cos, inclinacao * cos))

        assert abs(sobrelevada) < abs(plana), (
            f"banking deveria aliviar o pneu: plana={plana:.3f} g, "
            f"sobrelevada={sobrelevada:.3f} g"
        )
        # E o contrário também, que é o que separa "a conta usa a inclinação"
        # de "a conta sempre diminui": asfalto caindo para fora **carrega** o
        # pneu, e é por isso que uma curva contra-peraltada assusta.
        assert abs(contra_peraltada) > abs(plana), (
            f"asfalto ao contrário deveria carregar o pneu: plana={plana:.3f} g, "
            f"contra={contra_peraltada:.3f} g"
        )


class TestSerieDeInclinacao:
    def _reta_com_rampa(self, rampa: float, n: int = 20) -> list[TelemetryPoint]:
        normal = normal_de_rampa(rampa, (1.0, 0.0))
        return [
            ponto(
                distance_m=float(i), position_x=float(i), position_z=0.0,
                position_y=rampa * i,
                road_plane_x=normal[0], road_plane_y=normal[1], road_plane_z=normal[2],
            )
            for i in range(n)
        ]

    def test_subida_da_rampa_positiva(self):
        serie = slope_series(self._reta_com_rampa(0.08))
        medidos = [v for v in serie if v is not None]
        assert medidos, "a série não deveria sair toda vazia"
        assert all(v == pytest.approx(8.0, abs=0.01) for v in medidos)

    def test_descida_da_rampa_negativa(self):
        serie = slope_series(self._reta_com_rampa(-0.08))
        medidos = [v for v in serie if v is not None]
        assert all(v == pytest.approx(-8.0, abs=0.01) for v in medidos)

    def test_sem_normal_gravada_a_serie_e_vazia_e_nao_zero(self):
        # Volta antiga. Zero afirmaria "pista plana", que ninguém mediu — e um
        # gráfico reto em zero é indistinguível de um dado real.
        pontos = [ponto(distance_m=float(i), position_x=float(i)) for i in range(10)]
        assert slope_series(pontos) == [None] * 10

    def test_rampa_absurda_e_descartada(self):
        # Normal unitária e em pé o bastante para passar na trava do quadro, mas
        # com rampa de 80% — o dobro do que existe em asfalto.
        rampa = 0.8
        normal = normal_de_rampa(rampa, (1.0, 0.0))
        pontos = [
            ponto(
                distance_m=float(i), position_x=float(i),
                road_plane_x=normal[0], road_plane_y=normal[1], road_plane_z=normal[2],
            )
            for i in range(10)
        ]
        assert all(v is None for v in slope_series(pontos))

    def test_sobrelevacao_aparece_no_banking_e_nao_na_rampa(self):
        # Asfalto que cai para o lado, plano na direção da marcha.
        inclinacao = 0.12
        cos = 1.0 / math.hypot(1.0, inclinacao)
        pontos = [
            ponto(
                distance_m=float(i), position_x=float(i), position_z=0.0,
                road_plane_x=0.0, road_plane_y=cos, road_plane_z=inclinacao * cos,
            )
            for i in range(10)
        ]
        rampas = [v for v in slope_series(pontos) if v is not None]
        bancos = [v for v in bank_series(pontos) if v is not None]
        assert all(v == pytest.approx(0.0, abs=1e-6) for v in rampas)
        assert bancos and all(v == pytest.approx(-12.0, abs=0.01) for v in bancos)


class TestPerfilDeElevacao:
    def test_altitude_e_relativa_ao_ponto_mais_baixo(self):
        # A origem do mundo do GT7 é arbitrária; o que se lê é a diferença.
        pontos = [ponto(position_y=h) for h in (410.0, 415.0, 408.0, 412.0)]
        assert elevation_series(pontos) == [2.0, 7.0, 0.0, 4.0]
        assert elevation_range_m(pontos) == pytest.approx(7.0)

    def test_volta_sem_altitude_nao_inventa_perfil(self):
        pontos = [ponto() for _ in range(4)]
        assert elevation_series(pontos) == [None] * 4
        assert elevation_range_m(pontos) is None


class TestGravacao:
    def test_a_altitude_sobrevive_ao_disco(self, tmp_path):
        """O defeito original: o dado chegava e era descartado na fronteira."""
        from gt7core.domain.models import Lap
        from gt7core.storage.database import SqliteDatabase
        from gt7core.storage.repositories import SqliteLapRepository

        db = SqliteDatabase(tmp_path / "t.db")
        repo = SqliteLapRepository(db)
        volta = Lap(
            track_id=None, car_id=None, lap_time_ms=90_000,
            points=[
                ponto(
                    elapsed_ms=i * 16, distance_m=float(i), position_y=400.0 + i,
                    road_plane_x=0.0, road_plane_y=1.0, road_plane_z=0.0,
                )
                for i in range(5)
            ],
        )
        lap_id = repo.save(volta)
        lidos = repo.load_points(lap_id)

        assert [p.position_y for p in lidos] == [400.0, 401.0, 402.0, 403.0, 404.0]
        assert all(p.has_road_normal for p in lidos)
        db.close()

    def test_volta_antiga_le_como_nao_medido(self, tmp_path):
        from gt7core.domain.models import Lap
        from gt7core.storage.database import SqliteDatabase
        from gt7core.storage.repositories import SqliteLapRepository

        db = SqliteDatabase(tmp_path / "t.db")
        repo = SqliteLapRepository(db)
        lap_id = repo.save(
            Lap(track_id=None, car_id=None, lap_time_ms=90_000,
                points=[ponto(elapsed_ms=i, distance_m=float(i)) for i in range(3)])
        )
        lidos = repo.load_points(lap_id)
        assert all(p.position_y is None for p in lidos)
        assert not any(p.has_road_normal for p in lidos)
        db.close()
