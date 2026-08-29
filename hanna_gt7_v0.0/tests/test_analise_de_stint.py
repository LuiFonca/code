"""
Análise de stint: escolher a janela por volta, e não por ordinal.

O que estes testes protegem é a **identidade** da escolha. Trocar o filtro de
carro reordena a lista de voltas; se a escolha fosse guardada por posição, a
mesma posição passaria a apontar para outra volta e o perfil mudaria sem
ninguém ter pedido — em silêncio, que é o pior jeito de um número mudar.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="são páginas Qt")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.application import build_core, build_gui  # noqa: E402
from gt7core.config.settings import Settings  # noqa: E402
from gt7core.domain.models import Lap  # noqa: E402
from tests.conftest import dispose_window  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _settings(tmp_path):  # noqa: ANN001
    s = Settings()
    s.storage.database_path = tmp_path / "t.db"
    s.storage.telemetry_path = tmp_path / "tel"
    s.env_path = tmp_path / ".env"
    return s


def _pontos(n: int = 40):  # noqa: ANN202
    from gt7core.domain.models import TelemetryPoint

    return [
        TelemetryPoint(
            elapsed_ms=i * 50, distance_m=float(i * 10), speed_kmh=120.0,
            rpm=5000.0, gear=4, throttle=60.0, brake=0.0, fuel_level=40.0,
            tire_temp_fl=80.0, tire_temp_fr=80.0, tire_temp_rl=80.0,
            tire_temp_rr=80.0, position_x=float(i), position_z=0.0,
            g_lateral=0.1, g_longitudinal=0.1,
            suspension_fl=0.0, suspension_fr=0.0, suspension_rl=0.0,
            suspension_rr=0.0, tire_slip_fl=33.0, tire_slip_fr=33.0,
            tire_slip_rl=33.0, tire_slip_rr=33.0,
            turbo_boost=1.0, oil_temp=90.0, water_temp=85.0,
        )
        for i in range(n)
    ]


def _gravar(core, track_id: int, car_id: int, tempo_ms: int) -> int:  # noqa: ANN001
    return core.laps.save(
        Lap(track_id=track_id, car_id=car_id, lap_time_ms=tempo_ms, points=_pontos())
    )


def _stint(window):  # noqa: ANN001, ANN202
    for pagina in window._pages:  # noqa: SLF001
        if pagina.page_id == "driver":
            return pagina
    raise AssertionError("página de stint não encontrada")


class TestSeletoresDaJanela:
    def test_as_voltas_saem_em_dropdown_com_o_tempo(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        """Um ordinal não identifica volta nenhuma; o tempo identifica."""
        core = build_core(_settings(tmp_path))
        try:
            tid = core.tracks.get_or_create("Interlagos")
            cid = core.cars.get_or_create("Fictício GT3")
            for ms in (92_000, 91_500, 91_200):
                _gravar(core, tid, cid, ms)

            window = build_gui(core)
            stint = _stint(window)
            stint.refresh()

            assert stint._from_combo.count() == 3  # noqa: SLF001
            rotulos = [
                stint._from_combo.itemText(i)  # noqa: SLF001
                for i in range(stint._from_combo.count())  # noqa: SLF001
            ]
            # Ordem cronológica e tempo de cada volta, exatos. Uma verificação
            # frouxa ("tem dois-pontos") passaria com os rótulos trocados, que é
            # justamente o defeito que faria escolher a janela errada.
            assert rotulos == [
                "1ª  1:32.000",
                "2ª  1:31.500",
                "3ª  1:31.200",
            ]
            # A janela inteira é o padrão: recortar é decisão de quem lê.
            assert stint._from_combo.currentIndex() == 0  # noqa: SLF001
            assert stint._to_combo.currentIndex() == 2  # noqa: SLF001
            dispose_window(window)
        finally:
            core.close()

    def test_o_filtro_de_carro_lista_so_os_desta_pista(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        core = build_core(_settings(tmp_path))
        try:
            interlagos = core.tracks.get_or_create("Interlagos")
            outra = core.tracks.get_or_create("Suzuka")
            gt3 = core.cars.get_or_create("Fictício GT3")
            rua = core.cars.get_or_create("Fictício Rua")
            longe = core.cars.get_or_create("Fictício Nunca Visto")

            _gravar(core, interlagos, gt3, 92_000)
            _gravar(core, interlagos, rua, 99_000)
            _gravar(core, outra, longe, 95_000)

            window = build_gui(core)
            stint = _stint(window)
            stint.refresh()
            indice = stint._track_combo.findData(interlagos)  # noqa: SLF001
            stint._track_combo.setCurrentIndex(indice)  # noqa: SLF001

            nomes = [
                stint._car_combo.itemText(i)  # noqa: SLF001
                for i in range(stint._car_combo.count())  # noqa: SLF001
            ]
            assert nomes[0] == "todos"
            assert "Fictício GT3" in nomes
            assert "Fictício Rua" in nomes
            # Oferecer um carro que esta pista não tem é oferecer um filtro que
            # zera a tela — um botão que só sabe apagar.
            assert "Fictício Nunca Visto" not in nomes
            dispose_window(window)
        finally:
            core.close()

    def test_filtrar_por_carro_recorta_a_janela(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        core = build_core(_settings(tmp_path))
        try:
            tid = core.tracks.get_or_create("Interlagos")
            gt3 = core.cars.get_or_create("Fictício GT3")
            rua = core.cars.get_or_create("Fictício Rua")
            _gravar(core, tid, gt3, 92_000)
            _gravar(core, tid, rua, 99_000)
            _gravar(core, tid, gt3, 91_800)

            window = build_gui(core)
            stint = _stint(window)
            stint.refresh()
            assert stint._from_combo.count() == 3  # noqa: SLF001

            stint._car_combo.setCurrentIndex(  # noqa: SLF001
                stint._car_combo.findData(gt3)  # noqa: SLF001
            )
            assert stint._from_combo.count() == 2  # noqa: SLF001
            dispose_window(window)
        finally:
            core.close()

    def test_a_escolha_segue_a_volta_e_nao_a_posicao(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        """O defeito que a guarda por id evita.

        Três voltas, a do meio de outro carro. Escolhendo a **terceira** e
        depois filtrando pelo carro das outras duas, a lista encolhe para
        duas — e a volta escolhida agora é a segunda. Guardada por posição, a
        escolha viraria "a terceira", que não existe mais, ou pior, apontaria
        para outra volta sem avisar.
        """
        core = build_core(_settings(tmp_path))
        try:
            tid = core.tracks.get_or_create("Interlagos")
            gt3 = core.cars.get_or_create("Fictício GT3")
            rua = core.cars.get_or_create("Fictício Rua")
            _gravar(core, tid, gt3, 92_000)
            _gravar(core, tid, rua, 99_000)
            terceira = _gravar(core, tid, gt3, 91_800)

            window = build_gui(core)
            stint = _stint(window)
            stint.refresh()

            stint._from_combo.setCurrentIndex(2)  # noqa: SLF001
            assert stint._from_combo.currentData() == terceira  # noqa: SLF001

            stint._car_combo.setCurrentIndex(  # noqa: SLF001
                stint._car_combo.findData(gt3)  # noqa: SLF001
            )
            # A posição mudou de 2 para 1; a volta é a mesma.
            assert stint._from_combo.currentIndex() == 1  # noqa: SLF001
            assert stint._from_combo.currentData() == terceira  # noqa: SLF001
            dispose_window(window)
        finally:
            core.close()

    def test_faixa_invertida_nao_esvazia_a_tela(
        self, app: QApplication, tmp_path  # noqa: ANN001, ARG002
    ) -> None:
        """Escolher o fim antes do início é engano comum, não erro fatal."""
        core = build_core(_settings(tmp_path))
        try:
            tid = core.tracks.get_or_create("Interlagos")
            cid = core.cars.get_or_create("Fictício GT3")
            for ms in (92_000, 91_500, 91_200, 91_000):
                _gravar(core, tid, cid, ms)

            window = build_gui(core)
            stint = _stint(window)
            stint.refresh()

            stint._from_combo.setCurrentIndex(3)  # noqa: SLF001
            stint._to_combo.setCurrentIndex(0)  # noqa: SLF001

            # Sem exceção e com o perfil montado: a faixa é lida ao contrário.
            assert not stint._badge.isVisible()  # noqa: SLF001
            dispose_window(window)
        finally:
            core.close()
