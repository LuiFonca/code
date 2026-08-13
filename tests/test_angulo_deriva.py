"""
Ângulo de deriva — validação por geometria conhecida.

O roteiro previa que esta fase poderia terminar em "não dá", porque o ângulo
exige a orientação do carro e eu acreditava que o pacote não a transmitia.
O levantamento mostrou o contrário: os bytes 0x1C–0x38 carregam o quaternion
de rotação e simplesmente não eram lidos.

Com o dado disponível, a validação deixa de ser empírica e passa a ser
geométrica: para uma orientação e uma velocidade conhecidas, o ângulo tem um
valor único e verificável.
"""

import math

import pytest

from src.domain.services.slip_angle import (
    MAX_SLIP_ANGLE_DEG,
    forward_vector_xz,
    slip_angle_deg,
)


def _quaternion_yaw(graus: float):
    """Quaternion de uma rotação em torno do eixo vertical (Y).

    É a rotação que interessa: guinada. Rolagem e arfagem não produzem deriva.
    """
    meio = math.radians(graus) / 2.0
    return (0.0, math.sin(meio), 0.0, math.cos(meio))


# ------------------------------------------------------- vetor de frente
def test_sem_rotacao_aponta_para_frente():
    fx, fz = forward_vector_xz(0.0, 0.0, 0.0, 1.0)
    assert fx == pytest.approx(0.0, abs=1e-6)
    assert fz == pytest.approx(1.0, abs=1e-6)


@pytest.mark.parametrize("graus", [0, 30, 45, 90, 180, -45, 270])
def test_guinada_gira_o_vetor_de_frente(graus):
    """O vetor de frente precisa acompanhar a guinada, grau a grau."""
    i, j, k, w = _quaternion_yaw(graus)
    fx, fz = forward_vector_xz(i, j, k, w)

    esperado_x = math.sin(math.radians(graus))
    esperado_z = math.cos(math.radians(graus))
    assert fx == pytest.approx(esperado_x, abs=1e-5)
    assert fz == pytest.approx(esperado_z, abs=1e-5)


def test_vetor_de_frente_e_unitario():
    for graus in (0, 37, 123, -80):
        i, j, k, w = _quaternion_yaw(graus)
        fx, fz = forward_vector_xz(i, j, k, w)
        assert math.hypot(fx, fz) == pytest.approx(1.0, abs=1e-5)


# ------------------------------------------------------- ângulo de deriva
def test_reta_da_zero():
    """Carro apontando e movendo na mesma direção: sem deriva."""
    i, j, k, w = _quaternion_yaw(0)
    angulo = slip_angle_deg(velocity_x=0.0, velocity_z=50.0, rot_i=i, rot_j=j,
                            rot_k=k, rot_w=w)
    assert angulo == pytest.approx(0.0, abs=1e-4)


def test_reta_em_qualquer_direcao_da_zero():
    """O que importa é a diferença, não a direção absoluta."""
    for graus in (0, 45, 90, 137, -60):
        i, j, k, w = _quaternion_yaw(graus)
        vx = 50.0 * math.sin(math.radians(graus))
        vz = 50.0 * math.cos(math.radians(graus))
        angulo = slip_angle_deg(vx, vz, i, j, k, w)
        assert angulo == pytest.approx(0.0, abs=1e-3), f"guinada {graus}°"


@pytest.mark.parametrize("desvio", [5, 10, 20, 45, -15, -30])
def test_angulo_conhecido_e_recuperado(desvio):
    """Geometria montada com desvio exato: o cálculo tem que devolvê-lo.

    O carro aponta para 0° e se move numa direção deslocada de `desvio`.
    """
    i, j, k, w = _quaternion_yaw(0)
    vx = 50.0 * math.sin(math.radians(desvio))
    vz = 50.0 * math.cos(math.radians(desvio))

    angulo = slip_angle_deg(vx, vz, i, j, k, w)
    assert angulo == pytest.approx(desvio, abs=0.01)


def test_sinal_distingue_os_lados():
    """Traseira saindo para um lado ou para o outro precisa ser distinguível."""
    i, j, k, w = _quaternion_yaw(0)
    direita = slip_angle_deg(10.0, 50.0, i, j, k, w)
    esquerda = slip_angle_deg(-10.0, 50.0, i, j, k, w)

    assert direita > 0 and esquerda < 0
    assert direita == pytest.approx(-esquerda, abs=1e-6)


def test_independe_da_orientacao_absoluta():
    """Mesmo desvio em pontos diferentes da pista dá o mesmo ângulo."""
    resultados = []
    for guinada in (0, 90, 200, -130):
        i, j, k, w = _quaternion_yaw(guinada)
        direcao = guinada + 12.0
        vx = 40.0 * math.sin(math.radians(direcao))
        vz = 40.0 * math.cos(math.radians(direcao))
        resultados.append(slip_angle_deg(vx, vz, i, j, k, w))

    for r in resultados:
        assert r == pytest.approx(12.0, abs=0.01)


# ------------------------------------------------------- casos-limite
def test_parado_devolve_none():
    """Sem movimento não há direção de movimento — e isso não é zero."""
    i, j, k, w = _quaternion_yaw(0)
    assert slip_angle_deg(0.0, 0.0, i, j, k, w) is None
    assert slip_angle_deg(0.5, 0.5, i, j, k, w) is None


def test_quaternion_degenerado_devolve_none():
    """Volta antiga ou pacote sem orientação não pode virar ângulo inventado."""
    assert slip_angle_deg(0.0, 50.0, 0.0, 0.0, 0.0, 0.0) is None


def test_valores_absurdos_sao_saturados():
    """Rodada completa não pode esticar a escala do gráfico da volta."""
    i, j, k, w = _quaternion_yaw(0)
    # Movendo-se quase para trás em relação a onde aponta.
    angulo = slip_angle_deg(0.0, -50.0, i, j, k, w)
    assert abs(angulo) <= MAX_SLIP_ANGLE_DEG


# ------------------------------------- integração com o serviço
def test_servico_grava_o_angulo(service, source, on_track, flush):
    """O ângulo precisa chegar às amostras gravadas."""
    from tests.conftest import FakeFrame

    i, j, k, w = _quaternion_yaw(0)
    service.start()
    for n in range(20):
        # Carro apontando para 0°, movendo-se com 10° de desvio.
        source.feed(
            FakeFrame(
                lap_count=1,
                current_lap_ms=n * 16,
                velocity_x=50.0 * math.sin(math.radians(10)),
                velocity_z=50.0 * math.cos(math.radians(10)),
                rotation_i=i, rotation_j=j, rotation_k=k, rotation_w=w,
                speed_kmh=180.0,
            )
        )
    flush(0.1)

    angulos = [p.slip_angle_deg for p in service._buffer if p.slip_angle_deg is not None]
    assert angulos, "o serviço deve derivar o ângulo"
    assert angulos[-1] == pytest.approx(10.0, abs=0.5)


def test_ida_e_volta_pelo_banco(laps, tracks, make_lap):
    """O ângulo precisa sobreviver à gravação e à leitura.

    As colunas da tabela de amostras são derivadas dos campos do
    `TelemetryPoint`, então um campo novo passa a ser persistido sem alterar o
    repositório — mas isso é justamente o tipo de coisa que ninguém confere.
    """
    track_id = tracks.get_or_create("T")
    lap_id = laps.save(make_lap(track_id=track_id, samples=30, slip_angle_deg=7.5))

    pontos = laps.load_points(lap_id)
    assert pontos
    assert all(p.slip_angle_deg == pytest.approx(7.5) for p in pontos)


# ------------------------------------- exibição na aba Telemetria
@pytest.fixture
def aba_com_angulo(qapp, laps, tracks, cars, make_lap):
    """Aba Telemetria com uma volta que tem ângulo gravado."""
    from src.application.events.event_bus import EventBus
    from src.application.viewmodels.telemetry_viewmodel import TelemetryViewModel
    from src.presentation.tabs.telemetry_tab import TelemetryTab

    track_id = tracks.get_or_create("Pista")
    car_id = cars.get_or_create("Carro")
    laps.save(
        make_lap(
            track_id=track_id, car_id=car_id, lap_time_ms=90000, samples=200,
            slip_angle_deg=-4.25,
        )
    )

    bus = EventBus()
    vm = TelemetryViewModel(laps, bus, tracks)
    aba = TelemetryTab(vm)
    vm.set_track(track_id)
    aba._lap_combo.setCurrentIndex(0)
    aba._on_plot_clicked()
    yield aba, vm
    vm.dispose()
    bus.clear()


@pytest.fixture
def aba_sem_angulo(qapp, laps, tracks, cars, make_lap):
    """A mesma aba, com uma volta anterior à medida existir."""
    from src.application.events.event_bus import EventBus
    from src.application.viewmodels.telemetry_viewmodel import TelemetryViewModel
    from src.presentation.tabs.telemetry_tab import TelemetryTab

    track_id = tracks.get_or_create("Pista")
    car_id = cars.get_or_create("Carro")
    laps.save(make_lap(track_id=track_id, car_id=car_id, lap_time_ms=90000, samples=200))

    bus = EventBus()
    vm = TelemetryViewModel(laps, bus, tracks)
    aba = TelemetryTab(vm)
    vm.set_track(track_id)
    aba._lap_combo.setCurrentIndex(0)
    aba._on_plot_clicked()
    yield aba, vm
    vm.dispose()
    bus.clear()


def test_aba_exibe_o_grafico_de_angulo(aba_com_angulo):
    aba, _ = aba_com_angulo
    series = aba.chart_slip_angle.chart().series()
    assert series, "o gráfico de ângulo deve ter série"
    assert series[0].count() > 0


def test_indice_de_deslizamento_continua_exibido(aba_com_angulo):
    """O ângulo entra ao lado do índice, não no lugar dele."""
    aba, _ = aba_com_angulo
    assert set(aba.slip_charts) == {"fl", "fr", "rl", "rr"}
    for chart in aba.slip_charts.values():
        assert chart.chart().series()
    assert "%" in aba.slip_indicator._value.text()


def test_resumo_traz_pico_com_sinal(aba_com_angulo):
    aba, _ = aba_com_angulo
    texto = aba._slip_angle_summary.text()
    assert "-4.2" in texto or "-4.3" in texto, texto
    assert "esquerda" in texto


def test_secao_some_em_volta_sem_angulo(aba_sem_angulo):
    """Gráfico zerado afirmaria 'sem deriva'; a seção precisa sumir.

    A asserção é sobre `isHidden`, e não `isVisible`: numa janela que nunca foi
    exibida, `isVisible` é False para tudo — o teste passaria mesmo com a seção
    presente, que é exatamente o tipo de teste que não protege nada.
    """
    aba, vm = aba_sem_angulo
    assert not vm.has_slip_angle()
    assert aba.chart_slip_angle.isHidden()
    assert aba._slip_angle_header.isHidden()
    assert not aba._slip_angle_absent.isHidden(), "o aviso de ausência deve aparecer"
    assert not aba.chart_slip_angle.chart().series()


def test_secao_aparece_em_volta_com_angulo(aba_com_angulo):
    """Contraprova do teste acima: com dado, a seção fica e o aviso sai."""
    aba, _ = aba_com_angulo
    assert not aba.chart_slip_angle.isHidden()
    assert not aba._slip_angle_header.isHidden()
    assert aba._slip_angle_absent.isHidden()


def test_volta_antiga_sem_orientacao_nao_quebra(laps, tracks, make_lap):
    """Voltas gravadas antes desta medida têm o campo nulo, e isso é aceitável."""
    from src.domain.services.lap_analysis import LapSeries

    track_id = tracks.get_or_create("T")
    lap_id = laps.save(make_lap(track_id=track_id, samples=50))

    pontos = laps.load_points(lap_id)
    assert all(p.slip_angle_deg is None for p in pontos)

    serie = LapSeries(pontos)
    assert not serie.has_channel("slip_angle_deg"), "canal ausente, não zerado"
    assert serie.has_channel("speed_kmh"), "os demais canais seguem funcionando"
