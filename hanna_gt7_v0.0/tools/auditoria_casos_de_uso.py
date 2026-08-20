"""
Auditoria de casos de uso — exercita o programa como alguém o usaria.

Não lê código: **roda**. Cada caso monta o programa de verdade, faz o que uma
pessoa faria, e reporta o que quebra ou o que mente. É o método que achou os
defeitos que importaram até aqui — a distância 0,0 m, a aderência 0%, o campo
que se preenchia sozinho.

Cada caso é independente e nenhum derruba os outros: o objetivo é sair com a
lista inteira, não parar no primeiro.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/home/user/code/hanna_gt7_v0.0")

import sqlite3  # noqa: E402
import tempfile  # noqa: E402
from datetime import datetime  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

from gt7app.application import build_core, build_gui  # noqa: E402
from gt7core.config.settings import Settings  # noqa: E402
from gt7core.domain.models import Lap  # noqa: E402
from gt7core.telemetry.sources.mock import synthetic_lap  # noqa: E402

ACHADOS: list[tuple[str, str, str]] = []   # (gravidade, caso, descrição)


def achado(gravidade: str, caso: str, descricao: str) -> None:
    ACHADOS.append((gravidade, caso, descricao))


def novo_core(tmp: Path, **tel):  # noqa: ANN003, ANN201
    s = Settings()
    s.storage.database_path = tmp / "h.db"
    s.storage.telemetry_path = tmp / "tel"
    s.env_path = tmp / ".env"
    for k, v in tel.items():
        setattr(s.telemetry, k, v)
    return build_core(s)


def gravar(core, track_id, ms, car_id=None):  # noqa: ANN001, ANN201
    core.engine.reset()
    for f in synthetic_lap(lap_time_ms=ms):
        core.engine.on_frame(f)
    return core.laps.save(
        Lap(
            track_id=track_id,
            car_id=car_id,
            lap_time_ms=ms,
            start_time=datetime.now(),
            points=list(core.engine._buffer),  # noqa: SLF001
        )
    )


def caso(nome):  # noqa: ANN001, ANN201
    """Decorador: isola o caso e converte exceção em achado crítico."""

    def wrap(fn):  # noqa: ANN001, ANN202
        def run(app):  # noqa: ANN001, ANN202
            tmp = Path(tempfile.mkdtemp())
            try:
                fn(app, tmp)
            except Exception:  # noqa: BLE001
                achado("CRÍTICO", nome, "estourou:\n" + traceback.format_exc())
        run.__name__ = nome
        return run

    return wrap


# ---------------------------------------------------------------- casos


@caso("1. primeira execução, sem banco e sem .env")
def caso_primeira_execucao(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        w = build_gui(core)
        for i, p in enumerate(w._pages):  # noqa: SLF001
            w._stack.setCurrentIndex(i)  # noqa: SLF001
            p.on_enter()
            app.processEvents()
        w.close()
        w.deleteLater()
        app.processEvents()
    finally:
        core.close()


@caso("2. abas de análise sem nenhuma volta gravada")
def caso_sem_voltas(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        w = build_gui(core)
        for indice in (1, 2, 3, 4):
            p = w._pages[indice]  # noqa: SLF001
            p.refresh()
            app.processEvents()
        # Passar o mouse num gráfico vazio não pode estourar.
        w._pages[1]._on_hover(500.0)  # noqa: SLF001
        w._pages[2]._on_hover(500.0)  # noqa: SLF001
        w._pages[1]._on_click(500.0)  # noqa: SLF001
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("3. uma volta só — comparação impossível")
def caso_uma_volta(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        gravar(core, tid, 92_000)
        w = build_gui(core)
        comp = w._pages[2]  # noqa: SLF001
        comp.refresh()
        app.processEvents()
        if not comp._hint.text():  # noqa: SLF001
            achado("MÉDIO", "3. uma volta só",
                   "a comparação fica vazia sem dizer por quê")
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("4. excluir todas as voltas com a análise aberta")
def caso_excluir_tudo(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        for ms in (92_000, 93_000):
            gravar(core, tid, ms)
        w = build_gui(core)
        analise = w._pages[1]  # noqa: SLF001
        analise.refresh()
        app.processEvents()

        for lap in core.laps.get_by_track(tid):
            core.laps.delete(lap.id)

        analise.refresh()
        app.processEvents()
        analise._on_hover(500.0)  # noqa: SLF001
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("5. renomear pista fundindo com outra existente")
def caso_renomear(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        errada = core.tracks.get_or_create("192.168.15.156")
        certa = core.tracks.get_or_create("Interlagos")
        gravar(core, errada, 92_000)
        gravar(core, certa, 93_000)

        final = core.tracks.rename(errada, "Interlagos")
        if final != certa:
            achado("ALTO", "5. renomear", "não fundiu com a pista existente")
        if len(core.laps.get_by_track(certa)) != 2:
            achado("ALTO", "5. renomear", "as voltas não migraram na fusão")

        w = build_gui(core)
        for indice in (1, 2, 3, 4):
            w._pages[indice].refresh()  # noqa: SLF001
        app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("6. volta com uma amostra só")
def caso_volta_degenerada(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        core.engine.reset()
        for f in synthetic_lap(lap_time_ms=92_000):
            core.engine.on_frame(f)
        um_ponto = [list(core.engine._buffer)[0]]  # noqa: SLF001
        lap_id = core.laps.save(
            Lap(track_id=tid, lap_time_ms=92_000,
                start_time=datetime.now(), points=um_ponto)
        )
        w = build_gui(core)
        analise = w._pages[1]  # noqa: SLF001
        analise.refresh()
        analise._on_lap_selected(lap_id)  # noqa: SLF001
        app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("7. banco antigo (v5) migrando")
def caso_banco_antigo(app, tmp):  # noqa: ANN001
    caminho = tmp / "antigo.db"
    old = sqlite3.connect(caminho)
    old.executescript("""
        CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);
        CREATE TABLE cars (id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);
        CREATE TABLE laps (id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER, car_id INTEGER, is_player INTEGER NOT NULL DEFAULT 1,
            lap_time_ms INTEGER NOT NULL, recorded_at REAL NOT NULL,
            frame_count INTEGER NOT NULL);
        INSERT INTO tracks (name, created_at) VALUES ('Suzuka', 1000);
        INSERT INTO laps (track_id, lap_time_ms, recorded_at, frame_count)
            VALUES (1, 95000, 1000, 10);
        PRAGMA user_version = 5;
    """)
    old.commit(); old.close()

    s = Settings()
    s.storage.database_path = caminho
    s.storage.telemetry_path = tmp / "tel"
    s.env_path = tmp / ".env"
    core = build_core(s)
    try:
        w = build_gui(core)
        for i in range(len(w._pages)):  # noqa: SLF001
            w._stack.setCurrentIndex(i)  # noqa: SLF001
            w._pages[i].on_enter()  # noqa: SLF001
            app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("8. salvar configurações e reler")
def caso_configuracoes(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        w = build_gui(core)
        cfg = w._pages[5]  # noqa: SLF001
        cfg.refresh()
        cfg._ps_ip.setText("192.168.1.50")  # noqa: SLF001
        cfg._ai_enabled.setChecked(False)  # noqa: SLF001
        cfg._on_save()  # noqa: SLF001
        app.processEvents()

        env = (tmp / ".env").read_text() if (tmp / ".env").exists() else ""
        if "192.168.1.50" not in env:
            achado("ALTO", "8. configurações", "o IP não foi gravado no .env")
        if "gt7_ai_enabled=false" not in env.lower().replace(" ", ""):
            achado("MÉDIO", "8. configurações",
                   f"a IA desmarcada não foi gravada como false; .env tem: "
                   f"{[l for l in env.splitlines() if 'AI_ENABLED' in l]}")
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("9. fechar com a captura rodando")
def caso_fechar_capturando(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        w = build_gui(core)
        core.start()
        app.processEvents()
        w.close()
        w.deleteLater()
        app.processEvents()
        if core.source.is_running:
            achado("ALTO", "9. fechar capturando",
                   "a fonte continua rodando depois de fechar a janela")
    finally:
        core.close()


@caso("10. eixo de tempo em todas as abas")
def caso_eixo_tempo(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        for ms in (92_000, 93_000):
            gravar(core, tid, ms)
        w = build_gui(core)
        analise = w._pages[1]  # noqa: SLF001
        analise.refresh()
        analise._x_selector.setCurrentIndex(1)  # tempo  # noqa: SLF001
        app.processEvents()
        analise._on_hover(30.0)  # noqa: SLF001
        analise._on_click(30.0)  # noqa: SLF001
        analise._map_channel.setCurrentIndex(1)  # noqa: SLF001
        app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("11. duas pistas, trocar entre elas")
def caso_duas_pistas(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        a = core.tracks.get_or_create("Interlagos")
        b = core.tracks.get_or_create("Suzuka Circuit")
        gravar(core, a, 92_000)
        gravar(core, a, 93_000)
        gravar(core, b, 85_000)

        w = build_gui(core)
        for indice in (1, 2, 3, 4):
            p = w._pages[indice]  # noqa: SLF001
            p.refresh()
            app.processEvents()

        hist = w._pages[3]  # noqa: SLF001
        for i in range(hist._track_combo.count()):  # noqa: SLF001
            hist._track_combo.setCurrentIndex(i)  # noqa: SLF001
            app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("12. perfil do piloto com faixa de voltas")
def caso_piloto(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        for ms in (92_000, 93_000, 94_000, 95_000):
            gravar(core, tid, ms)
        w = build_gui(core)
        piloto = w._pages[4]  # noqa: SLF001
        piloto.refresh()
        app.processEvents()
        # Faixa invertida: fim antes do início.
        piloto._from_spin.setValue(4)  # noqa: SLF001
        piloto._to_spin.setValue(1)  # noqa: SLF001
        app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


def main() -> int:
    app = QApplication.instance() or QApplication([])

    casos = [v for k, v in sorted(globals().items()) if k.startswith("caso_")]
    for c in casos:
        print(f"  · {c.__name__}")
        c(app)

    print("\n" + "=" * 70)
    if not ACHADOS:
        print("Nenhum achado.")
        return 0

    ordem = {"CRÍTICO": 0, "ALTO": 1, "MÉDIO": 2, "BAIXO": 3}
    for gravidade, nome, desc in sorted(ACHADOS, key=lambda a: ordem.get(a[0], 9)):
        print(f"[{gravidade}] {nome}\n    {desc}")
    print(f"\n{len(ACHADOS)} achado(s).")
    return 1




# ------------------------------------------------- segunda leva: casos duros


@caso("13. trocar de fonte com a captura rodando")
def caso_trocar_fonte(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        w = build_gui(core)
        core.start()
        app.processEvents()
        core.settings.telemetry.source = "udp"
        core.settings.telemetry.ps_ip = "127.0.0.1"
        core.reconfigure_source()
        app.processEvents()
        if not core.source.is_running:
            achado("ALTO", "13. trocar de fonte",
                   "a captura parou ao trocar de fonte, sem ninguém pedir")
        core.stop()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("14. fonte inválida não pode deixar o programa sem captura")
def caso_fonte_invalida(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        w = build_gui(core)
        core.start()
        antiga = core.source
        core.settings.telemetry.source = "udp"
        core.settings.telemetry.ps_ip = ""          # inválido de propósito
        try:
            core.reconfigure_source()
        except Exception:
            pass
        if core.source is not antiga:
            achado("ALTO", "14. fonte inválida",
                   "a fonte foi trocada mesmo com configuração inválida")
        if not core.source.is_running:
            achado("ALTO", "14. fonte inválida",
                   "ficou sem captura por causa de um erro de digitação")
        core.stop()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("15. pacote curto e pacote de lixo")
def caso_pacote_ruim(app, tmp):  # noqa: ANN001
    from gt7core.telemetry.protocol import TelemetryFrame, salsa20_decode

    if salsa20_decode(b"\x00" * 296) is not None:
        achado("ALTO", "15. pacote ruim", "lixo de 296 bytes foi aceito")
    try:
        TelemetryFrame.from_bytes(b"\x00" * 40)
        achado("ALTO", "15. pacote ruim", "pacote curto não levantou")
    except Exception:
        pass


@caso("16. comparar uma volta com ela mesma")
def caso_comparar_igual(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        lap = gravar(core, tid, 92_000)
        gravar(core, tid, 93_000)
        w = build_gui(core)
        comp = w._pages[2]  # noqa: SLF001
        comp.refresh()
        comp._reference_selector.select_lap(lap)  # noqa: SLF001
        comp._analysed_selector.select_lap(lap)  # noqa: SLF001
        app.processEvents()
        if "diferentes" not in comp._hint.text():  # noqa: SLF001
            achado("MÉDIO", "16. volta contra si mesma",
                   f"não avisou; diz: {comp._hint.text()!r}")  # noqa: SLF001
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("17. nome de pista com caracteres estranhos")
def caso_nome_estranho(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        nomes = ["Nürburgring 24h", "A" * 200, "pista/com\\barras", "'; DROP TABLE laps;--"]
        for nome in nomes:
            tid = core.tracks.get_or_create(nome)
            gravar(core, tid, 92_000)
        if len(core.tracks.get_all()) != len(nomes):
            achado("ALTO", "17. nomes estranhos", "alguma pista não foi criada")
        w = build_gui(core)
        for indice in (1, 2, 3, 4):
            w._pages[indice].refresh()  # noqa: SLF001
        app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("18. duas voltas com o mesmo tempo")
def caso_tempos_iguais(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        gravar(core, tid, 92_000)
        gravar(core, tid, 92_000)
        w = build_gui(core)
        for indice in (1, 2, 3, 4):
            w._pages[indice].refresh()  # noqa: SLF001
        app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("19. retenção apagando voltas por baixo da tela")
def caso_retencao(app, tmp):  # noqa: ANN001
    s = Settings()
    s.storage.database_path = tmp / "h.db"
    s.storage.telemetry_path = tmp / "tel"
    s.env_path = tmp / ".env"
    s.storage.keep_recent_per_track = 2
    s.storage.keep_best_per_track = 1
    core = build_core(s)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        w = build_gui(core)
        hist = w._pages[3]  # noqa: SLF001
        for ms in (95_000, 94_000, 93_000, 92_000, 91_000):
            gravar(core, tid, ms)
            hist.refresh()
            app.processEvents()
        restantes = core.laps.get_by_track(tid)
        if len(restantes) > 3:
            achado("MÉDIO", "19. retenção",
                   f"a retenção não aplicou: sobraram {len(restantes)}")
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("20. alternar entre as seis abas repetidas vezes")
def caso_alternar_abas(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        for ms in (92_000, 93_000):
            gravar(core, tid, ms)
        w = build_gui(core)
        for _ in range(3):
            for i in range(len(w._pages)):  # noqa: SLF001
                w._activate(i)  # noqa: SLF001
                app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("21. configuração com valores inválidos")
def caso_config_invalida(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        w = build_gui(core)
        cfg = w._pages[5]  # noqa: SLF001
        cfg.refresh()
        cfg._ps_ip.setText("isso não é um ip")  # noqa: SLF001
        cfg._on_save()  # noqa: SLF001
        app.processEvents()
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()

# ------------------------------- terceira leva: o que a tela **afirma**
#
# Os casos acima perguntam "quebra?". Estes perguntam "mente?", que é a
# pergunta que pegou os defeitos caros deste projeto: distância 0,0 m e
# aderência 0% não estouravam — desenhavam com confiança um número errado.


@caso("22. a volta gravada tem distância de verdade")
def caso_distancia(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        lap_id = gravar(core, tid, 92_000)
        pontos = core.laps.load_points(lap_id)
        distancia = pontos[-1].distance_m
        if distancia < 100.0:
            achado("CRÍTICO", "22. distância",
                   f"volta gravada com {distancia:.1f} m — foi este o defeito de 0x70")
    finally:
        core.close()


@caso("23. a aderência média fica perto de 100%")
def caso_aderencia(app, tmp):  # noqa: ANN001
    from gt7core.analytics.tyres import infer_slip_convention, slip_ratio

    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        lap_id = gravar(core, tid, 92_000)
        pontos = core.laps.load_points(lap_id)
        convencao = infer_slip_convention(pontos)
        razoes = [
            r for p in pontos for roda in ("fl", "fr", "rl", "rr")
            if (r := slip_ratio(p, roda, convencao)) is not None
        ]
        media = sum(razoes) / len(razoes) if razoes else 0.0
        if not 0.6 <= media <= 1.6:
            achado("CRÍTICO", "23. aderência",
                   f"média {media * 100:.0f}% — o canal não está chegando certo")
    finally:
        core.close()


@caso("24. o delta tem o sinal certo")
def caso_delta(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        rapida = gravar(core, tid, 92_000)
        lenta = gravar(core, tid, 95_000)
        w = build_gui(core)
        comp = w._pages[2]  # noqa: SLF001
        comp.refresh()
        comp._reference_selector.select_lap(rapida)  # noqa: SLF001
        comp._analysed_selector.select_lap(lenta)  # noqa: SLF001
        app.processEvents()

        leituras = comp._delta_chart.value_at(  # noqa: SLF001
            comp._reference[-1].distance_m * 0.9  # noqa: SLF001
        )
        if not leituras:
            achado("ALTO", "24. delta", "o gráfico de delta ficou sem dados")
        elif leituras[0][1] <= 0:
            achado("CRÍTICO", "24. delta",
                   f"a volta mais lenta aparece com delta {leituras[0][1]:+.3f} s")
        w.close(); w.deleteLater(); app.processEvents()
    finally:
        core.close()


@caso("25. a melhor volta é mesmo a mais rápida")
def caso_melhor(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        for ms in (95_000, 91_500, 93_000):
            gravar(core, tid, ms)
        melhor = core.laps.get_best(tid)
        todas = core.laps.get_by_track(tid)
        real = min(l.lap_time_ms for l in todas)
        if melhor is None or melhor.lap_time_ms != real:
            achado("ALTO", "25. melhor volta",
                   f"apontou {melhor.lap_time_ms if melhor else None}, real {real}")
    finally:
        core.close()


@caso("26. os setores somam o tempo da volta")
def caso_setores(app, tmp):  # noqa: ANN001
    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        lap_id = gravar(core, tid, 92_000)
        setores = core.laps.get_sector_times(lap_id)
        if not setores or any(s is None for s in setores):
            achado("MÉDIO", "26. setores", f"setores incompletos: {setores}")
            return
        soma = sum(s for s in setores if s is not None)
        lap = core.laps.get_by_id(lap_id)
        if abs(soma - lap.lap_time_ms) > lap.lap_time_ms * 0.05:
            achado("ALTO", "26. setores",
                   f"soma {soma} ms contra volta de {lap.lap_time_ms} ms")
    finally:
        core.close()


@caso("27. a guinada acompanha as curvas detectadas")
def caso_guinada(app, tmp):  # noqa: ANN001
    from gt7core.analytics.corners import detect_corners
    from gt7core.analytics.steering import peak_yaw_rate, yaw_rate_series

    core = novo_core(tmp)
    try:
        tid = core.tracks.get_or_create("Interlagos")
        lap_id = gravar(core, tid, 92_000)
        pontos = core.laps.load_points(lap_id)
        curvas = detect_corners(pontos)
        pico = peak_yaw_rate(yaw_rate_series(pontos))
        if curvas and pico < 1.0:
            achado("ALTO", "27. guinada",
                   f"{len(curvas)} curvas detectadas mas pico de guinada {pico:.2f} °/s")
        if pico > 400.0:
            achado("ALTO", "27. guinada", f"pico absurdo: {pico:.0f} °/s")
    finally:
        core.close()

# --------------------------- quarta leva: passando pelo decodificador de verdade
#
# As checagens acima usam a fonte sintética, que constrói `TelemetryFrame`
# direto e **nunca passa pelos offsets**. Descobri isso mutando o offset da
# aderência de volta para 0xE4: a auditoria não piscou. É a mesma cegueira que
# deixou o defeito de 0x70 sobreviver — "não aparecia com a fonte sintética".
#
# Um caso de uso só cobre o protocolo se o byte vier de um pacote.


#: Raio de pneu do pacote de teste, em metros.
TIRE_RADIUS_M = 0.35


def _monta_pacote(*, speed_ms: float, packet_id: int) -> bytes:
    """Um pacote GT7 em claro, montado byte a byte nos offsets reais.

    **Escrito aqui, e não importado do `tests/`**, por duas razões. A auditoria
    precisa rodar contra a árvore da v1.0, que não traz os testes; e um pacote
    de referência que mora junto do leitor tende a copiar os offsets do leitor,
    que é como o defeito de 0x70 sobreviveu à suíte inteira.
    """
    import struct as _struct

    from gt7core.telemetry.protocol import MAGIC_NUMBER

    p = bytearray(296)
    _struct.pack_into("<I", p, 0x00, MAGIC_NUMBER)
    _struct.pack_into("<fff", p, 0x04, 10.0, 20.0, 30.0)        # posição
    _struct.pack_into("<fff", p, 0x10, speed_ms, 0.0, 0.0)      # velocidade
    _struct.pack_into("<f", p, 0x3C, 6400.0)                    # rpm
    _struct.pack_into("<I", p, 0x40, 0x0000_1111)               # IV
    _struct.pack_into("<f", p, 0x4C, speed_ms)
    _struct.pack_into("<f", p, 0x50, 1.8)                       # turbo
    _struct.pack_into("<ffff", p, 0x60, 80.0, 82.0, 78.0, 79.0)  # temp. pneus
    _struct.pack_into("<i", p, 0x70, packet_id)                 # tick
    _struct.pack_into("<h", p, 0x74, 1)                         # volta
    _struct.pack_into("<i", p, 0x78, 100_250)                   # melhor tempo
    _struct.pack_into("<i", p, 0x7C, 101_500)                   # último tempo
    _struct.pack_into("<H", p, 0x8E, 0x0009)                    # flags
    _struct.pack_into("<B", p, 0x90, (5 << 4) | 4)              # marchas
    _struct.pack_into("<B", p, 0x91, 255)                       # acelerador
    limpa = speed_ms / TIRE_RADIUS_M
    _struct.pack_into("<ffff", p, 0xA4, limpa, limpa, limpa, limpa * 0.8)
    _struct.pack_into("<ffff", p, 0xB4, *([TIRE_RADIUS_M] * 4))
    _struct.pack_into("<ffff", p, 0xC4, 0.11, 0.12, 0.13, 0.14)  # suspensão
    _struct.pack_into("<i", p, 0x124, 1234)                      # carro
    return bytes(p)


def _cifra(claro: bytes) -> bytes:
    """Mesmo esquema do GT7: Salsa20 com o IV em claro no offset 0x40."""
    from Crypto.Cipher import Salsa20

    from gt7core.telemetry.protocol import GT7_KEY

    oiv = claro[0x40:0x44]
    iv1 = int.from_bytes(oiv, "little")
    nonce = (iv1 ^ 0xDEADBEAF).to_bytes(4, "little") + iv1.to_bytes(4, "little")
    cifrado = bytearray(Salsa20.new(key=GT7_KEY[:32], nonce=nonce).encrypt(claro))
    cifrado[0x40:0x44] = oiv
    return bytes(cifrado)


def _frames_de_pacote(n: int = 200):  # noqa: ANN202
    """Quadros nascidos de pacotes montados byte a byte, como o PS5 manda."""
    from gt7core.telemetry.protocol import TelemetryFrame, salsa20_decode

    quadros = []
    for i in range(n):
        claro = salsa20_decode(_cifra(_monta_pacote(speed_ms=55.0, packet_id=i)))
        assert claro is not None
        quadros.append(TelemetryFrame.from_bytes(claro))
    return quadros


@caso("28. o pacote real produz aderência plausível")
def caso_aderencia_do_pacote(app, tmp):  # noqa: ANN001
    quadros = _frames_de_pacote(20)
    for q in quadros[:3]:
        rodas = [q.tire_slip_fl, q.tire_slip_fr, q.tire_slip_rl, q.tire_slip_rr]
        if all(r == 0.0 for r in rodas):
            achado("CRÍTICO", "28. aderência do pacote",
                   "as quatro rodas em 0,000 — offset apontando para o bloco não usado")
            return
        razoes = [r / 55.0 for r in rodas]
        if not all(0.5 <= x <= 1.5 for x in razoes):
            achado("CRÍTICO", "28. aderência do pacote",
                   f"razões implausíveis: {[f'{x:.2f}' for x in razoes]}")
            return


@caso("29. o pacote real produz suspensão plausível")
def caso_suspensao_do_pacote(app, tmp):  # noqa: ANN001
    q = _frames_de_pacote(1)[0]
    valores = [q.suspension_fl, q.suspension_fr, q.suspension_rl, q.suspension_rr]
    if all(v == 0.0 for v in valores) or len(set(valores)) == 1:
        achado("ALTO", "29. suspensão do pacote",
               f"as quatro iguais ou zeradas: {valores}")


@caso("30. volta montada a partir de pacotes tem distância")
def caso_distancia_do_pacote(app, tmp):  # noqa: ANN001
    from gt7core.events.bus import EventBus
    from gt7core.telemetry.engine import TelemetryEngine

    engine = TelemetryEngine(EventBus(), sample_rate_hz=60)
    for q in _frames_de_pacote(300):
        engine.on_frame(q)

    if engine.current_distance_m < 100.0:
        achado("CRÍTICO", "30. distância do pacote",
               f"{engine.current_distance_m:.1f} m depois de 300 quadros — "
               "é o defeito de 0x70 de volta")


@caso("31. o tick do pacote é o relógio, não o melhor tempo")
def caso_tick(app, tmp):  # noqa: ANN001
    quadros = _frames_de_pacote(5)
    ticks = [q.packet_id for q in quadros]
    if ticks != sorted(ticks) or len(set(ticks)) != len(ticks):
        achado("CRÍTICO", "31. tick",
               f"o contador de quadros não avança: {ticks}")
    if len({q.best_lap_ms for q in quadros}) != 1:
        achado("ALTO", "31. tick", "o melhor tempo está mudando quadro a quadro")

if __name__ == "__main__":
    raise SystemExit(main())
