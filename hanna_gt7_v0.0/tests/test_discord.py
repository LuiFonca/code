"""
Fase 10 — o bot do Discord.

Nenhum teste aqui toca a rede, exige token ou importa `discord.py`. Isso não é
conveniência: é o desenho. O que decide o valor do bot — **o que postar, quando,
e o que suprimir** — é Python puro atrás de um `MessageSink`, e o que sobra do
lado da biblioteca é entregar uma string.

O teste mais importante do arquivo é `test_evento_ao_vivo_nao_vira_mensagem`.
A Fase 9 mede doze eventos numa sessão de duas voltas; no celular isso é spam, e
spam ensina o piloto a silenciar o canal — o que mata junto as mensagens que
importavam. A supressão é uma decisão de produto, e por isso é vigiada.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from gt7core.config.settings import DiscordConfig, SecretStr
from gt7core.domain.models import Car, Lap, Session, Track
from gt7core.events.bus import EventBus
from gt7core.session.manager import (
    LapSaved,
    LapSaveFailed,
    SessionEnded,
    SessionStarted,
)
from gt7discord import (
    Command,
    Context,
    DiscordBot,
    DiscordSink,
    Notifier,
    NotifierPolicy,
    RecordingSink,
    discover,
    formatting,
)


def lap(ms: int, lap_id: int = 1, track_id: int = 1) -> Lap:
    return Lap(
        id=lap_id,
        track_id=track_id,
        lap_time_ms=ms,
        start_time=datetime.now(),
        end_time=datetime.now(),
    )


class TestFormatacao:
    def test_tempo_no_formato_do_painel(self) -> None:
        assert formatting.lap_time(92_345) == "1:32.345"
        assert formatting.lap_time(0) == "—"

    def test_melhor_volta_ganha_estrela(self) -> None:
        texto = formatting.lap_saved(lap(101_500), is_best=True)
        assert texto.startswith("★")
        assert "melhor" in texto

    def test_volta_comum_mostra_a_diferenca(self) -> None:
        texto = formatting.lap_saved(lap(103_000), is_best=False, best_ms=101_500)
        assert "+1.500 s" in texto

    def test_corta_na_quebra_de_linha(self) -> None:
        """Cortar no meio da frase parece defeito; na quebra parece resumo."""
        texto = "\n".join(f"linha {i} com algum conteúdo" for i in range(300))
        cortado = formatting.clamp(texto, limit=200)
        assert len(cortado) <= 210
        assert cortado.endswith("…")
        assert "linha 0" in cortado

    def test_respeita_o_teto_da_plataforma(self) -> None:
        assert formatting.SAFE_LIMIT < formatting.DISCORD_LIMIT

    def test_conselho_traz_a_proveniencia(self) -> None:
        from gt7ai import Action, Advice, AdviceLevel, AdviceSource

        texto = formatting.advice(
            Advice(
                level=AdviceLevel.SESSION,
                headline="O ritmo caiu no fim.",
                detail="As últimas três voltas perderam consistência.",
                actions=[Action(where="Curva 1", instruction="Freie mais tarde.")],
                source=AdviceSource.LOCAL,
            ),
            title="Relatório",
        )
        assert "O ritmo caiu no fim." in texto
        assert "Curva 1" in texto
        assert "análise local" in texto

    def test_conselho_vazio_nao_vira_mensagem(self) -> None:
        assert formatting.advice(object()) == ""


class TestPolitica:
    """O que vira mensagem — e o que não vira."""

    def _notifier(self, **policy: object) -> tuple[Notifier, RecordingSink]:
        sink = RecordingSink()
        return Notifier(sink, policy=NotifierPolicy(**policy)), sink  # type: ignore[arg-type]

    def test_melhor_volta_sempre_notifica(self) -> None:
        notifier, sink = self._notifier()
        notifier.on_lap_saved(LapSaved(lap=lap(101_500), lap_id=1, is_best=True))
        assert len(sink.messages) == 1
        assert "★" in sink.last

    def test_volta_comum_fica_calada_por_padrao(self) -> None:
        """Trinta voltas de treino não são trinta notificações."""
        notifier, sink = self._notifier()
        notifier.on_lap_saved(LapSaved(lap=lap(101_500), lap_id=1, is_best=True))
        for i in range(10):
            notifier.on_lap_saved(
                LapSaved(lap=lap(103_000 + i), lap_id=i + 2, is_best=False)
            )
        assert len(sink.messages) == 1, "voltas comuns viraram spam"
        assert notifier.lap_count == 11

    def test_quem_quiser_o_registro_completo_liga(self) -> None:
        notifier, sink = self._notifier(post_every_lap=True)
        notifier.on_lap_saved(LapSaved(lap=lap(103_000), lap_id=1, is_best=False))
        assert len(sink.messages) == 1

    def test_evento_ao_vivo_nao_vira_mensagem(self) -> None:
        """O rádio é da tela; o Discord é do momento parado.

        Se este teste falhar, alguém assinou `RaceEventDetected` no notificador
        e a sessão passará a render dezenas de mensagens.
        """
        from gt7core.analytics.live import RaceEvent, RaceEventDetected

        bus = EventBus()
        sink = RecordingSink()
        Notifier(sink).register(bus)

        for i in range(12):
            bus.publish(
                RaceEventDetected(
                    event=RaceEvent(
                        kind="travamento", distance_m=900.0, elapsed_ms=i * 1000
                    )
                )
            )
        assert sink.messages == []

    def test_falha_de_gravacao_sempre_avisa(self) -> None:
        """O piloto precisa saber que perdeu a volta."""
        notifier, sink = self._notifier()
        notifier.on_lap_save_failed(
            LapSaveFailed(message="banco cheio", lap_time_ms=101_500)
        )
        assert "banco cheio" in sink.last
        assert "⚠️" in sink.last
        # Saber **qual** volta se perdeu é metade da informação.
        assert "1:41.500" in sink.last

    def test_fim_de_sessao_resume(self) -> None:
        notifier, sink = self._notifier(post_session_report=False)
        notifier.on_session_started(
            SessionStarted(session_id=1, track_name="Suzuka", car_name="GT-R")
        )
        notifier.on_lap_saved(LapSaved(lap=lap(101_500), lap_id=1, is_best=True))
        sink.clear()
        notifier.on_session_ended(SessionEnded(session_id=1, lap_count=1))

        assert "Sessão encerrada" in sink.last
        assert "Suzuka" in sink.last
        assert "1:41.500" in sink.last

    def test_o_relatorio_roda_fora_da_thread_de_captura(self) -> None:
        """Consultar o modelo de dentro do barramento travaria a gravação."""
        adiadas: list[object] = []
        sink = RecordingSink()

        class Engenheiro:
            def session_report(self, profile, **kwargs):  # noqa: ANN001, ANN202
                from gt7ai import Advice, AdviceLevel

                return Advice(level=AdviceLevel.SESSION, headline="Relatório pronto.")

        notifier = Notifier(
            sink, engineer=Engenheiro(), defer=adiadas.append
        )
        notifier.on_session_ended(SessionEnded(session_id=1, lap_count=3))

        assert len(adiadas) == 1, "o relatório foi montado na thread do evento"
        assert "Relatório pronto." not in sink.last

        adiadas[0]()  # type: ignore[operator]
        assert "Relatório pronto." in sink.last

    def test_falha_de_envio_nao_derruba_a_captura(self) -> None:
        """Uma exceção de rede subindo daqui mataria a gravação da sessão."""

        class SinkQuebrado:
            def send(self, text: str) -> None:
                raise RuntimeError("rede caiu")

        notifier = Notifier(SinkQuebrado())
        notifier.on_lap_saved(LapSaved(lap=lap(101_500), lap_id=1, is_best=True))


class TestDescobertaDeComandos:
    """§23: adicionar comando não toca no núcleo."""

    def test_os_comandos_sao_encontrados_por_varredura(self) -> None:
        commands = discover()
        assert {"help", "status", "best", "last", "report"} <= set(commands)

    def test_um_arquivo_novo_aparece_sozinho(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        """A prova do §23: nenhuma lista para atualizar.

        Escreve um módulo novo no pacote, redescobre, e ele está lá — sem ter
        editado registro nenhum.
        """
        import gt7discord.commands as pkg

        novo = tmp_path / "ping.py"
        novo.write_text(
            "from . import Command, Context\n"
            "def run(context, args):\n"
            "    return 'pong'\n"
            "COMMAND = Command(name='ping', help='teste', run=run)\n",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path.parent))
        pkg.__path__.append(str(tmp_path))  # type: ignore[attr-defined]
        try:
            commands = discover()
            assert "ping" in commands
            assert commands["ping"].run(None, []) == "pong"  # type: ignore[arg-type]
        finally:
            pkg.__path__.remove(str(tmp_path))  # type: ignore[attr-defined]

    def test_o_help_sai_da_mesma_descoberta(self) -> None:
        """Sem lista escrita à mão, o help nunca diverge do que funciona."""
        commands = discover()
        texto = commands["help"].run(
            Context(laps=None, tracks=None, session=None), []
        )
        for name in commands:
            assert f"`{name}`" in texto

    def test_modulo_quebrado_nao_impede_os_outros(self, tmp_path) -> None:  # noqa: ANN001
        import gt7discord.commands as pkg

        (tmp_path / "quebrado.py").write_text("raise RuntimeError('boom')\n", "utf-8")
        pkg.__path__.append(str(tmp_path))  # type: ignore[attr-defined]
        try:
            commands = discover()
            assert "help" in commands
            assert "quebrado" not in commands
        finally:
            pkg.__path__.remove(str(tmp_path))  # type: ignore[attr-defined]


class FakeLaps:
    def __init__(self, laps: list[Lap]) -> None:
        self._laps = laps

    def get_all(self, limit: int | None = None) -> list[Lap]:
        return self._laps[:limit] if limit else self._laps

    def get_best(self, track_id: int | None) -> Lap | None:
        matching = [x for x in self._laps if x.track_id == track_id]
        return min(matching, key=lambda x: x.lap_time_ms) if matching else None

    def get_by_track(self, track_id: int, limit: int | None = None) -> list[Lap]:
        return [x for x in self._laps if x.track_id == track_id][:limit]

    def load_points(self, lap_id: int) -> list:
        return []


class FakeTracks:
    def __init__(self, tracks: list[Track]) -> None:
        self._tracks = tracks

    def get_all(self) -> list[Track]:
        return self._tracks


@pytest.fixture
def context() -> Context:
    session = Session(car=Car(id=1, name="GT-R"), track=Track(id=1, name="Suzuka"))
    session.laps = [lap(101_500), lap(103_000, lap_id=2)]
    return Context(
        laps=FakeLaps([lap(101_500), lap(103_000, lap_id=2)]),
        tracks=FakeTracks([Track(id=1, name="Suzuka")]),
        session=session,
    )


class TestComandos:
    def test_status(self, context: Context) -> None:
        texto = discover()["status"].run(context, [])
        assert "Suzuka" in texto
        assert "GT-R" in texto
        assert "1:41.500" in texto

    def test_best(self, context: Context) -> None:
        assert "1:41.500" in discover()["best"].run(context, [])

    def test_last(self, context: Context) -> None:
        assert "1:41.500" in discover()["last"].run(context, [])

    def test_report_sem_engenheiro_e_honesto(self, context: Context) -> None:
        texto = discover()["report"].run(context, [])
        assert "não está instalado" in texto

    def test_report_monta_o_perfil_e_consulta_o_engenheiro(
        self, tmp_path
    ) -> None:  # noqa: ANN001
        """O caminho feliz do comando mais caro do bot.

        Cobria só a recusa por falta de engenheiro — o que deixava sem
        verificação justamente a parte que carrega voltas do banco, monta o
        perfil e chama o modelo.
        """
        from gt7app.application import build_core
        from gt7core.config.settings import (
            AIConfig,
            Settings,
            StorageConfig,
            TelemetryConfig,
        )
        from gt7core.telemetry.sources.mock import synthetic_session

        core = build_core(
            Settings(
                telemetry=TelemetryConfig(source="mock"),
                storage=StorageConfig(
                    database_path=tmp_path / "a.db", telemetry_path=tmp_path / "t"
                ),
                ai=AIConfig(enabled=False),
            )
        )
        try:
            track_id = core.tracks.get_or_create("Suzuka")
            core.session_manager.set_track(Track(id=track_id, name="Suzuka"))
            core.session_manager.start_session()
            for frame in synthetic_session(lap_count=4):
                core.engine.on_frame(frame)
            core.session_manager.end_session()

            pedidos: list[str] = []

            class Engenheiro:
                def session_report(self, profile, **kwargs):  # noqa: ANN001, ANN202
                    from gt7ai import Advice, AdviceLevel

                    pedidos.append(kwargs.get("track", ""))
                    assert profile is not None, "o perfil não foi montado"
                    assert profile.lap_count > 0
                    return Advice(
                        level=AdviceLevel.SESSION, headline="O ritmo melhorou."
                    )

            texto = discover()["report"].run(
                Context(
                    laps=core.laps,
                    tracks=core.tracks,
                    session=core.session_manager.session,
                    engineer=Engenheiro(),
                ),
                [],
            )
            assert pedidos == ["Suzuka"]
            assert "O ritmo melhorou." in texto
            assert "Suzuka" in texto
        finally:
            core.close()

    def test_report_sem_pista_selecionada(self) -> None:
        vazio = Context(
            laps=FakeLaps([]),
            tracks=FakeTracks([]),
            session=Session(),
            engineer=object(),
        )
        assert "Nenhuma pista" in discover()["report"].run(vazio, [])

    def test_report_com_voltas_insuficientes(self) -> None:
        """Volta sem amostras não produz perfil, e o comando diz isso."""
        contexto = Context(
            laps=FakeLaps([lap(101_500)]),
            tracks=FakeTracks([Track(id=1, name="Suzuka")]),
            session=Session(track=Track(id=1, name="Suzuka")),
            engineer=object(),
        )
        assert "insuficientes" in discover()["report"].run(contexto, [])

    def test_sem_voltas_nao_estoura(self) -> None:
        vazio = Context(laps=FakeLaps([]), tracks=FakeTracks([]), session=Session())
        assert "Nenhuma volta" in discover()["last"].run(vazio, [])
        assert "Nenhuma pista" in discover()["best"].run(vazio, [])


class TestDespacho:
    def _bot(self, context: Context) -> DiscordBot:
        return DiscordBot(
            DiscordConfig(token=SecretStr("x"), command_prefix="!engineer"),
            lambda: context,
        )

    def test_ignora_mensagem_sem_prefixo(self, context: Context) -> None:
        assert self._bot(context).handle_message("boa volta!") is None

    def test_despacha_pelo_nome(self, context: Context) -> None:
        resposta = self._bot(context).handle_message("!engineer best")
        assert resposta is not None and "1:41.500" in resposta

    def test_prefixo_sozinho_mostra_a_ajuda(self, context: Context) -> None:
        resposta = self._bot(context).handle_message("!engineer")
        assert resposta is not None and "Comandos disponíveis" in resposta

    def test_comando_desconhecido_lista_os_que_existem(self, context: Context) -> None:
        resposta = self._bot(context).handle_message("!engineer voar")
        assert resposta is not None
        assert "não existe" in resposta
        assert "`best`" in resposta

    def test_comando_quebrado_responde_em_vez_de_derrubar(
        self, context: Context
    ) -> None:
        def explode(_context: Context, _args: list[str]) -> str:
            raise RuntimeError("boom")

        bot = DiscordBot(
            DiscordConfig(token=SecretStr("x"), command_prefix="!"),
            lambda: context,
            commands={"boom": Command("boom", "quebra", explode)},
        )
        resposta = bot.handle_message("!boom")
        assert resposta is not None and "falhou" in resposta

    def test_sem_token_nao_sobe(self, context: Context) -> None:
        from gt7discord import DiscordUnavailable

        bot = DiscordBot(DiscordConfig(), lambda: context)
        with pytest.raises(DiscordUnavailable):
            bot.start()


class TestSinkDoDiscord:
    """A travessia thread → asyncio, sem subir a `discord.py`."""

    def test_guarda_o_que_chega_antes_de_conectar(self) -> None:
        """A sessão pode começar enquanto o bot ainda está conectando."""
        sink = DiscordSink()
        sink.send("sessão iniciada")
        assert not sink.is_connected
        assert sink.pending_count == 1

    def test_a_fila_e_limitada(self) -> None:
        """A fila existe para não perder o começo, não para virar histórico."""
        sink = DiscordSink(limit=3)
        for i in range(10):
            sink.send(f"m{i}")
        assert sink.pending_count == 3

    def test_ao_conectar_esvazia_na_ordem(self) -> None:
        import asyncio

        enviados: list[str] = []

        class Canal:
            async def send(self, text: str) -> None:
                enviados.append(text)

        async def cenario() -> None:
            sink = DiscordSink()
            sink.send("primeira")
            sink.send("segunda")
            sink.attach(asyncio.get_running_loop(), Canal())
            # `run_coroutine_threadsafe` agenda; ceder o controle deixa rodar.
            await asyncio.sleep(0.05)
            assert enviados == ["primeira", "segunda"]

        asyncio.run(cenario())

    def test_desconectar_volta_a_guardar(self) -> None:
        import asyncio

        class Canal:
            async def send(self, text: str) -> None:
                return None

        async def cenario() -> None:
            sink = DiscordSink()
            sink.attach(asyncio.get_running_loop(), Canal())
            assert sink.is_connected
            sink.detach()
            sink.send("depois da queda")
            assert not sink.is_connected
            assert sink.pending_count == 1

        asyncio.run(cenario())


class TestIntegracaoComONucleo:
    def test_o_notificador_reage_ao_barramento_de_verdade(self) -> None:
        bus = EventBus()
        sink = RecordingSink()
        Notifier(sink, policy=NotifierPolicy(post_session_report=False)).register(bus)

        bus.publish(SessionStarted(session_id=1, track_name="Suzuka", car_name="GT-R"))
        bus.publish(LapSaved(lap=lap(101_500), lap_id=1, is_best=True))
        bus.publish(SessionEnded(session_id=1, lap_count=1))

        assert len(sink.messages) == 3
        assert "Sessão iniciada" in sink.messages[0]
        assert "★" in sink.messages[1]
        assert "Sessão encerrada" in sink.messages[2]

    def test_o_carro_identificado_chega_ao_status(self, tmp_path) -> None:  # noqa: ANN001
        """O defeito que o comando `status` revelou.

        Desde que a identificação automática passou a montar o carro a partir
        do catálogo — em memória, sem tocar o banco —, o objeto chega **sem
        id**. `set_car` comparava só por id, então um carro novo (`id=None`)
        parecia idêntico a "nenhum carro" (`None`) e a troca era descartada em
        silêncio: painel e Discord mostravam "Carro: —" a sessão inteira, com a
        telemetria correta o tempo todo.
        """
        from gt7app.application import build_core
        from gt7core.config.settings import AIConfig, Settings, StorageConfig, TelemetryConfig
        from gt7core.telemetry.sources.mock import synthetic_lap

        core = build_core(
            Settings(
                telemetry=TelemetryConfig(source="mock"),
                storage=StorageConfig(
                    database_path=tmp_path / "a.db", telemetry_path=tmp_path / "t"
                ),
                ai=AIConfig(enabled=False),
            )
        )
        try:
            track_id = core.tracks.get_or_create("Suzuka")
            core.session_manager.set_track(Track(id=track_id, name="Suzuka"))
            core.session_manager.start_session()
            for frame in synthetic_lap(lap_time_ms=102_000):
                core.engine.on_frame(frame)

            texto = discover()["status"].run(
                Context(
                    laps=core.laps,
                    tracks=core.tracks,
                    session=core.session_manager.session,
                ),
                [],
            )
            assert "—" not in texto.split("**Carro:**")[1].split("\n")[0]
        finally:
            core.close()

    def test_build_bot_devolve_none_sem_token(self) -> None:
        from gt7discord import build_bot

        class NucleoFalso:
            from gt7core.config.settings import Settings

            settings = Settings()
            bus = EventBus()
            laps = None
            tracks = None

        assert build_bot(NucleoFalso()) is None
