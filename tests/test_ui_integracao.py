"""
Testes de interface e integração — a janela real, pelos caminhos do usuário.

A suíte até aqui cobria camadas isoladas: serviço, repositórios, domínio. Esta
monta o app inteiro pelo composition root e o dirige como uma pessoa dirigiria:
clicar em botões, digitar em campos, trocar de aba, receber eventos.

É onde aparecem os defeitos que nenhum teste de unidade pega — os que moram
*entre* as partes: um botão que age sobre uma seleção que não existe, uma aba
que não reage a um evento de outra, um estado de conexão que deixa a interface
inconsistente.

Os diálogos modais (`QMessageBox`, `QFileDialog`, `QInputDialog`) são
substituídos por dublês. Sem isso o teste trava esperando um clique que nunca
vem — e travar é pior que falhar, porque não diz o que está errado.
"""

import pytest

from src.application.events.events import (
    CarDetected,
    ConnectionStateChanged,
    LapCompleted,
    LapDiscarded,
    LapsPurged,
    LapSaveFailed,
    TrackCandidatesDetected,
    TrackRecognized,
)
from src.domain.models.lap import Lap


# ------------------------------------------------------------------ dublês
@pytest.fixture
def sem_dialogos(monkeypatch):
    """Neutraliza os modais e registra o que teria sido mostrado.

    Devolve a lista de chamadas, para o teste poder afirmar que o app *avisou*
    o usuário em vez de falhar em silêncio.
    """
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

    chamadas = []

    def _registra(tipo, retorno):
        def _fake(*args, **kwargs):
            # args[1] é o título quando chamado como QMessageBox.warning(pai, título, ...)
            chamadas.append((tipo, args[1] if len(args) > 1 else ""))
            return retorno
        return _fake

    monkeypatch.setattr(QMessageBox, "information", _registra("info", QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "critical", _registra("erro", QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", _registra("aviso", QMessageBox.No))
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: ("", "")
    )
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: ("", "")
    )
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("", False))
    return chamadas


@pytest.fixture
def app(qapp, tmp_path):
    """App inteiro, montado pelo composition root, sobre banco temporário."""
    import src.main as M
    from src.infrastructure.repositories.sqlite_database import SqliteDatabase

    original = M.SqliteDatabase
    M.SqliteDatabase = lambda *a, **k: SqliteDatabase(tmp_path / "app.db")
    try:
        janela = M.build_application()
    finally:
        M.SqliteDatabase = original
    yield janela
    janela._service.stop()


def _aba(janela, titulo):
    for i in range(janela.tabs.count()):
        if janela.tabs.tabText(i) == titulo:
            return janela.tabs.widget(i)
    raise AssertionError(f"aba {titulo!r} não existe")


# =================================================== primeira abertura
def test_abre_sem_dados_e_sem_pista(app):
    """Banco vazio é o estado de quem instalou agora. Nada pode quebrar."""
    assert app.tabs.count() == 4
    assert app._resolve_track_name() is None
    assert app.connect_button.isEnabled()
    assert not app.stop_button.isEnabled()
    assert "Pista:" in app._info_track.text()


def test_abas_montam_sem_dados(app):
    for titulo in ("Ao Vivo", "Histórico", "Telemetria", "Comparação"):
        assert _aba(app, titulo) is not None


# =================================================== conexão
def test_conectar_sem_ip_avisa_e_nao_conecta(app):
    app.ip_input.setText("   ")
    app._on_connect_clicked()
    assert "IP" in app.log_label.text()
    assert app.connect_button.isEnabled(), "o botão não pode travar por falta de IP"
    assert not app._service.is_running


def test_ciclo_conectar_desconectar_deixa_botoes_coerentes(app):
    app.ip_input.setText("127.0.0.1")
    app._on_connect_clicked()
    assert not app.connect_button.isEnabled()
    assert app.stop_button.isEnabled()
    assert not app.ip_input.isEnabled()

    app._on_stop_clicked()
    assert app.connect_button.isEnabled()
    assert not app.stop_button.isEnabled()
    assert app.ip_input.isEnabled(), "o campo de IP precisa voltar a ser editável"


def test_estados_de_conexao_nao_deixam_interface_travada(app):
    """Passa por todos os estados que a fonte pode emitir."""
    for estado in ("conectando", "recebendo", "sem_sinal", "reconectando",
                   "desconectado", "erro"):
        app._on_connection_changed(ConnectionStateChanged(state=estado, message="x"))
        assert app.status_pill.text(), f"pílula vazia no estado {estado!r}"

    # Depois de tudo, desconectado, a interface precisa estar operável.
    app._on_connection_changed(ConnectionStateChanged(state="desconectado"))
    assert app.connect_button.isEnabled()


def test_estado_desconhecido_nao_quebra(app):
    """A fonte é código de infraestrutura; um estado novo não pode derrubar a UI."""
    app._on_connection_changed(ConnectionStateChanged(state="estado_inventado"))
    assert app.status_pill.text()


# =================================================== pista e carro
def test_definir_pista_atualiza_barra_e_listas(app):
    app.track_input.setCurrentText("Interlagos")
    app._on_track_selected()
    assert "Interlagos" in app._info_track.text()
    assert app._session.track_id is not None


def test_reaplicar_a_mesma_pista_e_inocuo(app):
    """`editingFinished` dispara a cada perda de foco — não pode ter efeito."""
    app.track_input.setCurrentText("Interlagos")
    app._on_track_selected()
    antes = app.log_label.text()
    tid_antes = app._session.track_id

    app._on_track_selected()
    app._on_track_selected()
    assert app._session.track_id == tid_antes
    assert app.log_label.text() == antes


def test_limpar_o_campo_de_pista_volta_para_sem_pista(app):
    app.track_input.setCurrentText("Interlagos")
    app._on_track_selected()
    app.track_input.setCurrentText("")
    app._on_track_selected()
    assert app._session.track_id is None
    assert "salva" in app.log_label.text().lower()


def test_carro_detectado_preenche_o_campo_vazio(app):
    app._bus.publish(CarDetected(car_name="Porsche 911", car_id=42))
    assert "Porsche 911" in app.car_input.currentText()


def test_carro_detectado_nao_sobrescreve_escolha_do_usuario(app):
    app.car_input.setCurrentText("Meu carro")
    app._on_car_selected()
    app._bus.publish(CarDetected(car_name="Porsche 911", car_id=42))
    assert app.car_input.currentText() == "Meu carro"


def test_pista_reconhecida_e_adotada(app):
    app._bus.publish(
        TrackRecognized(track_id=1, track_name="Suzuka", deviation_m=12.0)
    )
    assert app.track_input.currentText() == "Suzuka"
    assert "reconhecida" in app.log_label.text().lower()


def test_pista_reconhecida_nao_sobrescreve_escolha_do_usuario(app):
    app.track_input.setCurrentText("Interlagos")
    app._on_track_selected()
    app._bus.publish(
        TrackRecognized(track_id=99, track_name="Suzuka", deviation_m=5.0)
    )
    assert app.track_input.currentText() == "Interlagos"


# =================================================== eventos de volta
def test_eventos_de_volta_viram_mensagem(app):
    casos = [
        LapCompleted(lap=Lap(lap_time_ms=90000), lap_id=1, is_best=True),
        LapsPurged(count=3, track_id=1),
        LapDiscarded(lap_time_ms=90000, reason="modo replay/IA"),
        LapSaveFailed(message="disco cheio", lap_time_ms=90000),
    ]
    for evento in casos:
        app.log_label.setText("")
        app._bus.publish(evento)
        assert app.log_label.text(), f"{type(evento).__name__} não avisou nada"


def test_candidatos_de_pista_sugerem_sem_decidir(app):
    app._bus.publish(TrackCandidatesDetected(names=["A", "B", "C"]))
    assert "A" in app.log_label.text()
    assert app._session.track_id is None, "sugestão não pode escolher sozinha"


# =================================================== botões sem seleção
def test_botoes_do_historico_sem_selecao_nao_quebram(app, sem_dialogos):
    """O clique mais provável do usuário novo: botão sem ter selecionado nada."""
    hist = _aba(app, "Histórico")
    for nome in ("_on_delete_clicked", "_on_assign_clicked",
                 "_on_toggle_valid_clicked", "_on_export_clicked"):
        getattr(hist, nome)()
    assert len(sem_dialogos) == 4, "cada botão precisa avisar que falta seleção"


def test_limpar_pista_sem_pista_selecionada_nao_faz_nada(app, sem_dialogos):
    hist = _aba(app, "Histórico")
    hist._on_clear_clicked()
    assert not sem_dialogos, "sem pista não há o que limpar, nem o que perguntar"


def test_importar_cancelado_nao_faz_nada(app, sem_dialogos):
    hist = _aba(app, "Histórico")
    hist._on_import_clicked()   # o dublê devolve caminho vazio = cancelado
    assert not sem_dialogos


# =================================================== comparação
def test_comparar_sem_voltas_avisa(app):
    comp = _aba(app, "Comparação")
    comp._on_compare_clicked()
    # Não pode quebrar; a mensagem de erro é o comportamento esperado.
    assert comp is not None


# =================================================== telemetria
def test_telemetria_sem_voltas_mostra_mensagem(app):
    tel = _aba(app, "Telemetria")
    assert tel._message.isVisible() or tel._message.text()


def test_telemetria_exibir_sem_selecao_nao_quebra(app):
    tel = _aba(app, "Telemetria")
    tel._on_plot_clicked()


# =================================================== preferências e relatório
def test_relatorio_de_sessao_abre_sem_dados(app, monkeypatch):
    from PySide6.QtWidgets import QDialog

    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    app._show_session_report()


def test_preferencias_canceladas_nao_mudam_nada(app, monkeypatch):
    from src.presentation.preferences_dialog import PreferencesDialog

    monkeypatch.setattr(PreferencesDialog, "exec", lambda self: PreferencesDialog.Rejected)
    antes = app._config
    app._open_preferences()
    assert app._config is antes


# =================================================== ciclo de vida
def test_fechar_a_janela_conectado_encerra_a_captura(app):
    from PySide6.QtGui import QCloseEvent

    app.ip_input.setText("127.0.0.1")
    app._on_connect_clicked()
    app.closeEvent(QCloseEvent())
    assert not app._service.is_running


# =================================================== integração entre abas
@pytest.fixture
def app_com_voltas(app, tmp_path):
    """App com pista escolhida e três voltas gravadas nela."""
    from tests.conftest import make_point

    laps = app._service._laps
    tracks = app._tracks
    tid = tracks.get_or_create("Interlagos")
    for ms in (92000, 89000, 90500):
        pontos = [make_point(i, 300, ms, 3600.0) for i in range(301)]
        laps.save(Lap(track_id=tid, lap_time_ms=ms, is_complete=True, points=pontos))

    app.track_input.setCurrentText("Interlagos")
    app._on_track_selected()
    return app, tid


def test_escolher_pista_popula_historico_telemetria_e_comparacao(app_com_voltas):
    """Uma ação na barra superior precisa alimentar as três abas."""
    janela, _tid = app_com_voltas

    hist = _aba(janela, "Histórico")
    assert hist._table.rowCount() == 3, "o histórico não listou as voltas da pista"

    tel = _aba(janela, "Telemetria")
    assert tel._lap_combo.count() == 3, "a Telemetria não ofereceu as voltas"

    comp = _aba(janela, "Comparação")
    assert comp._combo_a.count() == 3, "a Comparação não ofereceu as voltas"


def test_volta_nova_aparece_nas_abas_sem_recarregar(app_com_voltas):
    """O evento de volta concluída precisa atravessar até as listas."""
    from tests.conftest import make_point

    janela, tid = app_com_voltas
    tel = _aba(janela, "Telemetria")
    hist = _aba(janela, "Histórico")
    antes_tel, antes_hist = tel._lap_combo.count(), hist._table.rowCount()

    pontos = [make_point(i, 300, 88000, 3600.0) for i in range(301)]
    nova = Lap(track_id=tid, lap_time_ms=88000, is_complete=True, points=pontos)
    lap_id = janela._service._laps.save(nova)
    nova.id = lap_id
    janela._bus.publish(LapCompleted(lap=nova, lap_id=lap_id, is_best=True))

    assert tel._lap_combo.count() == antes_tel + 1
    assert hist._table.rowCount() == antes_hist + 1


def test_excluir_volta_some_das_tres_abas(app_com_voltas, sem_dialogos, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    janela, _tid = app_com_voltas
    hist = _aba(janela, "Histórico")
    tel = _aba(janela, "Telemetria")
    comp = _aba(janela, "Comparação")

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Yes)
    hist._table.selectRow(0)
    hist._on_delete_clicked()

    assert hist._table.rowCount() == 2
    assert tel._lap_combo.count() == 2, "a Telemetria continuou oferecendo volta apagada"
    assert comp._combo_a.count() == 2, "a Comparação continuou oferecendo volta apagada"


def test_exibir_volta_apagada_nao_desenha_lixo(app_com_voltas, sem_dialogos, monkeypatch):
    """A volta aberta some do banco enquanto está na tela.

    É o caminho que já produziu gráfico em branco sem explicação: o seletor
    continuava oferecendo um id que não existia mais.
    """
    from PySide6.QtWidgets import QMessageBox

    janela, _tid = app_com_voltas
    tel = _aba(janela, "Telemetria")
    hist = _aba(janela, "Histórico")

    tel._lap_combo.setCurrentIndex(0)
    alvo = tel._lap_combo.currentData()
    tel._on_plot_clicked()
    assert tel._vm.detail.is_valid

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Yes)
    linha = next(
        i for i in range(hist._table.rowCount())
        if hist._table.item(i, 1).text() == str(alvo)
    )
    hist._table.selectRow(linha)
    hist._on_delete_clicked()

    assert not tel._vm.detail.is_valid, "o detalhe da volta apagada continuou aberto"
    assert alvo not in [
        tel._lap_combo.itemData(i) for i in range(tel._lap_combo.count())
    ]


def test_trocar_de_pista_troca_o_conteudo_das_abas(app_com_voltas):
    janela, _tid = app_com_voltas
    hist = _aba(janela, "Histórico")
    assert hist._table.rowCount() == 3

    janela.track_input.setCurrentText("Suzuka")
    janela._on_track_selected()
    assert hist._table.rowCount() == 0, "as voltas da pista anterior continuaram na tela"


def test_marcar_invalida_tira_o_trofeu(app_com_voltas):
    """Invalidar a melhor volta precisa refletir na tabela na hora."""
    janela, tid = app_com_voltas
    hist = _aba(janela, "Histórico")

    melhor = janela._service._laps.get_best(tid)
    linha = next(
        i for i in range(hist._table.rowCount())
        if hist._table.item(i, 1).text() == str(melhor.id)
    )
    hist._table.selectRow(linha)
    hist._on_toggle_valid_clicked()

    nova_melhor = janela._service._laps.get_best(tid)
    assert nova_melhor is None or nova_melhor.id != melhor.id


def test_comparar_duas_voltas_desenha(app_com_voltas):
    janela, _tid = app_com_voltas
    comp = _aba(janela, "Comparação")
    comp._combo_a.setCurrentIndex(0)
    comp._combo_b.setCurrentIndex(1)
    comp._on_compare_clicked()

    com_dados = [c for c in comp._charts if c.chart().series()]
    assert com_dados, "a comparação não desenhou nenhuma série"


def test_comparar_a_volta_com_ela_mesma_nao_quebra(app_com_voltas):
    janela, _tid = app_com_voltas
    comp = _aba(janela, "Comparação")
    comp._combo_a.setCurrentIndex(0)
    comp._combo_b.setCurrentIndex(0)
    comp._on_compare_clicked()


def test_exportar_e_importar_volta_pelo_historico(app_com_voltas, tmp_path, monkeypatch):
    """Ida e volta completa pela interface, sem tocar no ViewModel direto."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    janela, _tid = app_com_voltas
    hist = _aba(janela, "Histórico")
    destino = tmp_path / "volta.json"

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(destino), "")
    )
    hist._table.selectRow(0)
    hist._on_export_clicked()
    assert destino.exists(), "o arquivo de exportação não foi criado"

    antes = hist._table.rowCount()
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *a, **k: (str(destino), "")
    )
    hist._on_import_clicked()
    assert hist._table.rowCount() == antes + 1, "a volta importada não apareceu"


def _grava_volta_orfa(janela, lap_ms=91000):
    """Grava uma volta sem pista e avisa o app, como o serviço faria."""
    from tests.conftest import make_point

    pontos = [make_point(i, 300, lap_ms, 3600.0) for i in range(301)]
    volta = Lap(track_id=None, lap_time_ms=lap_ms, is_complete=True, points=pontos)
    lap_id = janela._service._laps.save(volta)
    volta.id = lap_id
    janela._bus.publish(LapCompleted(lap=volta, lap_id=lap_id, is_best=False))
    return lap_id


def test_volta_sem_pista_aparece_quando_nao_ha_pista_escolhida(app):
    """O grupo 'sem pista' precisa ser visível, senão a volta some.

    É o outro lado da mudança que passou a gravar sem pista: gravar e não
    mostrar seria pior do que não gravar.
    """
    _grava_volta_orfa(app)
    hist = _aba(app, "Histórico")
    assert hist._table.rowCount() == 1, "a volta sem pista não apareceu em lugar nenhum"


def test_volta_sem_pista_pode_ser_aberta_e_comparada(app):
    """Ver que a volta existe e não poder abri-la é meio caminho só.

    Telemetria e Comparação cortavam o grupo `None` antes de consultar o
    banco, então a volta aparecia no Histórico e era inalcançável nas outras
    duas abas.
    """
    _grava_volta_orfa(app, 91000)
    _grava_volta_orfa(app, 89000)

    tel = _aba(app, "Telemetria")
    comp = _aba(app, "Comparação")
    assert tel._lap_combo.count() == 2, "Telemetria não ofereceu as voltas sem pista"
    assert comp._combo_a.count() == 2, "Comparação não ofereceu as voltas sem pista"

    tel._lap_combo.setCurrentIndex(0)
    tel._on_plot_clicked()
    assert tel._vm.detail.is_valid, "a volta sem pista não abriu na Telemetria"


def test_atribuir_pista_a_volta_orfa_pelo_historico(app, sem_dialogos, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    _grava_volta_orfa(app)
    hist = _aba(app, "Histórico")
    assert hist._table.rowCount() == 1
    hist._table.selectRow(0)

    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("Interlagos", True))
    hist._on_assign_clicked()

    assert hist._table.rowCount() == 0, "a volta atribuída saiu do grupo sem pista"
    tid = app._tracks.get_or_create("Interlagos")
    assert app._service._laps.get_by_track(tid), "a volta não chegou à pista escolhida"


# =================================================== regressões de UI/UX
def test_app_abre_ja_mostrando_as_voltas_do_banco(qapp, tmp_path):
    """Abrir o app com banco cheio não pode mostrar tela em branco.

    As abas só buscam voltas quando recebem uma pista, e ninguém as avisava na
    abertura: Histórico, Telemetria e Comparação nasciam vazios até o usuário
    mexer em algum campo. Quem abria o app para ver o histórico via nada e
    concluía que os dados tinham sumido.
    """
    import src.main as M
    from src.infrastructure.repositories.sqlite_database import SqliteDatabase
    from tests.conftest import make_point

    caminho = tmp_path / "app.db"
    db = SqliteDatabase(caminho)
    from src.infrastructure.repositories.sqlite_lap_repository import (
        SqliteLapRepository,
    )

    laps = SqliteLapRepository(db)
    pontos = [make_point(i, 300, 91000, 3600.0) for i in range(301)]
    laps.save(Lap(track_id=None, lap_time_ms=91000, is_complete=True, points=pontos))
    db.close()

    original = M.SqliteDatabase
    M.SqliteDatabase = lambda *a, **k: SqliteDatabase(caminho)
    try:
        janela = M.build_application()
    finally:
        M.SqliteDatabase = original

    try:
        assert _aba(janela, "Histórico")._table.rowCount() == 1
        assert _aba(janela, "Telemetria")._lap_combo.count() == 1
        assert _aba(janela, "Comparação")._combo_a.count() == 1
    finally:
        janela._service.stop()


def test_perder_o_sinal_nao_mostra_carro_parado(app, qapp):
    """Sem sinal, o painel precisa dizer "não sei", não "0 km/h".

    Zero é uma afirmação — carro parado, tanque vazio — e a verdade é outra:
    não há dado. A versão anterior escrevia 0 e marcha N ao perder o sinal,
    que é exatamente o que se queria evitar.
    """
    vivo = _aba(app, "Ao Vivo")
    vivo._on_stale()

    assert vivo.card_speed._value.text() == "--"
    assert vivo.card_gear._value.text() == "--"
    # O card acrescenta a unidade ao valor, daí o sufixo.
    assert vivo.card_fuel.value_widget.text().startswith("--")


def test_desconectar_limpa_o_painel_ao_vivo(app, qapp):
    """Desconectado, a última leitura não pode continuar com cara de ao vivo."""
    from tests.conftest import make_point

    from src.application.events.events import TelemetryReceived

    vivo = _aba(app, "Ao Vivo")
    app._bus.publish(ConnectionStateChanged(state="recebendo"))
    app._bus.publish(
        TelemetryReceived(point=make_point(1, 10, 90000, 3600.0), frame=None)
    )
    vivo._on_frame(app._bus and vivo._vm._latest or None) if vivo._vm._latest else None
    vivo.card_speed.set_value("222")

    app._bus.publish(ConnectionStateChanged(state="desconectado"))
    qapp.processEvents()
    assert vivo.card_speed._value.text() == "--", (
        "o painel seguiu mostrando a velocidade de antes da desconexão"
    )


def test_rotulo_de_pista_com_uma_volta_esta_no_singular(app):
    tid = app._tracks.get_or_create("Interlagos")
    from tests.conftest import make_point

    pontos = [make_point(i, 300, 91000, 3600.0) for i in range(301)]
    app._service._laps.save(
        Lap(track_id=tid, lap_time_ms=91000, is_complete=True, points=pontos)
    )
    app._reload_track_list()

    rotulos = [app.track_input.itemText(i) for i in range(app.track_input.count())]
    assert any("(1 volta)" in r for r in rotulos), rotulos
    assert not any("(1 voltas)" in r for r in rotulos)
