"""
O catálogo do jogo — o que a telemetria não informa.

Estes testes existem porque o catálogo é a única fonte de duas informações que
o protocolo do GT7 simplesmente não manda: o **nome** do carro (chega só um id
numérico) e **qual pista** é (não chega nada). Perder este módulo significaria
um histórico de "carro 24 em algum lugar".

Os dados vêm da comunidade e envelhecem a cada atualização do jogo, então nada
aqui fixa um total exato de linhas — o que se verifica é a forma: ids conhecidos
resolvem, ids desconhecidos devolvem None sem estourar, e um catálogo ausente
degrada para operação manual.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gt7core.catalog import MIN_DISTANCE_FOR_GUESS_M, GameCatalog


@pytest.fixture(scope="module")
def catalog() -> GameCatalog:
    return GameCatalog()


class TestCarga:
    def test_os_tres_arquivos_carregam(self, catalog: GameCatalog) -> None:
        assert len(catalog.cars) > 400
        assert len(catalog.makers) > 50
        assert len(catalog.tracks) > 80
        assert not catalog.is_empty

    def test_a_carga_e_preguicosa(self) -> None:
        """Nada de disco até alguém perguntar — o núcleo sobe num teste sem I/O."""
        fresh = GameCatalog()
        assert fresh._cars is None  # noqa: SLF001
        _ = fresh.cars
        assert fresh._cars is not None  # noqa: SLF001

    def test_catalogo_ausente_e_modo_de_operacao_e_nao_falha(
        self, tmp_path: Path
    ) -> None:
        """Sem CSV, a aplicação segue com identificação manual.

        É como ela funcionava antes de os dados existirem; transformar isso em
        exceção impediria o programa de subir por causa de um arquivo de apoio.
        """
        empty = GameCatalog(tmp_path)
        assert empty.cars == {}
        assert empty.tracks == {}
        assert empty.is_empty
        assert empty.car_name(24) is None
        assert empty.guess_by_length(5807.0) == []

    def test_linha_malformada_nao_derruba_o_resto(self, tmp_path: Path) -> None:
        (tmp_path / "cars.csv").write_text(
            "ID,ShortName,Maker\n"
            "1,Carro Bom,3\n"
            "xx,Carro Com Id Ruim,3\n"
            "2,Outro Bom,3\n",
            encoding="utf-8",
        )
        cars = GameCatalog(tmp_path).cars
        assert set(cars) == {1, 2}


class TestNomeDoCarro:
    def test_id_conhecido_vira_montadora_mais_modelo(self, catalog: GameCatalog) -> None:
        assert catalog.car_name(24) == "Nissan 180SX Type X '96"

    def test_id_desconhecido_devolve_none(self, catalog: GameCatalog) -> None:
        """Carro novo do jogo aparece sem nome; nada mais quebra."""
        assert catalog.car_name(999_999) is None
        assert catalog.car_maker(999_999) is None

    def test_nao_produz_espaco_duplo(self, catalog: GameCatalog) -> None:
        """Vários `ShortName` do CSV vêm com espaço à esquerda.

        Com `strip()` no lugar de `split()`, o nome sairia "Ford  GT '06".
        """
        for car_id in list(catalog.cars)[:200]:
            name = catalog.car_name(car_id)
            assert name is not None
            assert "  " not in name
            assert name == name.strip()

    def test_o_catalogo_nao_entrega_id_de_banco(self, catalog: GameCatalog) -> None:
        """O erro que este desenho impede.

        A primeira versão expunha `car(id) -> Car` já montado, com o id do
        **jogo** dentro. Esse id foi parar na coluna que é chave estrangeira
        para a tabela local de carros, e toda volta passou a falhar ao gravar
        com `FOREIGN KEY constraint failed` — visível só no log, com a volta
        perdida em silêncio.

        O catálogo sabe nomes; quem tem id é o banco.
        """
        assert not hasattr(catalog, "car")
        assert catalog.car_maker(24) == "Nissan"


class TestIdentificacaoDePista:
    def test_encontra_o_circuito_pela_distancia_medida(
        self, catalog: GameCatalog
    ) -> None:
        """A única forma de saber onde o piloto está: o jogo não informa."""
        candidates = catalog.guess_by_length(5807.0)
        assert candidates
        assert candidates[0].name == "Suzuka Circuit"

    def test_devolve_candidatos_e_nao_um_palpite(self, catalog: GameCatalog) -> None:
        """A propriedade que protege o histórico.

        Circuitos de comprimento parecido são indistinguíveis só pela distância,
        e a medida ainda carrega o erro de integrar a 60 Hz. Escolher sozinho
        acertaria na maioria das vezes e arquivaria a volta na pista errada no
        resto — contaminando a comparação para sempre, em silêncio.
        """
        candidates = catalog.guess_by_length(5807.0)
        assert len(candidates) > 1, "com 5% de tolerância há mais de um circuito"

    def test_ordenados_do_mais_proximo_ao_menos(self, catalog: GameCatalog) -> None:
        candidates = catalog.guess_by_length(5807.0)
        gaps = [abs((t.length_m or 0) - 5807.0) for t in candidates]
        assert gaps == sorted(gaps)

    def test_distancia_curta_demais_nao_produz_palpite(
        self, catalog: GameCatalog
    ) -> None:
        """Saída dos boxes ou volta abortada não é uma volta."""
        assert catalog.guess_by_length(MIN_DISTANCE_FOR_GUESS_M - 1) == []
        assert catalog.guess_by_length(0.0) == []

    def test_tolerancia_mais_apertada_reduz_os_candidatos(
        self, catalog: GameCatalog
    ) -> None:
        largo = catalog.guess_by_length(5807.0, tolerance_pct=5.0, limit=99)
        estreito = catalog.guess_by_length(5807.0, tolerance_pct=0.1, limit=99)
        assert len(estreito) < len(largo)
        assert estreito[0].name == "Suzuka Circuit"

    def test_distancia_sem_nenhum_circuito_proximo(self, catalog: GameCatalog) -> None:
        assert catalog.guess_by_length(999_999.0) == []

    def test_a_ficha_traz_o_que_a_analise_usa(self, catalog: GameCatalog) -> None:
        suzuka = catalog.guess_by_length(5807.0)[0]
        assert suzuka.corners > 0
        assert not suzuka.is_oval
        assert suzuka.as_track().name == suzuka.name
        # `as_track()` não carrega id: quem grava no banco é que atribui o dele.
        assert suzuka.as_track().id is None

    def test_oval_e_invertido_sao_lidos(self, catalog: GameCatalog) -> None:
        assert any(t.is_oval for t in catalog.tracks.values())
        assert any(t.is_reverse for t in catalog.tracks.values())


class TestBusca:
    def test_busca_por_nome_ignora_maiusculas(self, catalog: GameCatalog) -> None:
        assert any("Suzuka" in t.name for t in catalog.find_tracks("suzuka"))

    def test_busca_vazia_nao_devolve_tudo(self, catalog: GameCatalog) -> None:
        assert catalog.find_tracks("   ") == []


class TestFerramentaDeDiagnostico:
    """O que sobrou de `src/tools/diagnose.py` depois de portado."""

    def test_sem_ip_configurado_nao_inventa_um(self, monkeypatch) -> None:  # noqa: ANN001
        """O P3 da auditoria, que sobrevivia esquecido naquele arquivo.

        A versão anterior trazia `192.168.15.156` embutido — a rede doméstica do
        autor, versionada. Agora não existir IP é um erro explicado, e não um
        pacote enviado para a casa de outra pessoa.
        """
        from gt7core.tools.diagnose import main, resolve_target

        monkeypatch.delenv("GT7_PS_IP", raising=False)
        monkeypatch.chdir("/")
        assert resolve_target(None) == ""
        assert main([]) == 2

    def test_o_argumento_ganha_da_configuracao(self, monkeypatch) -> None:  # noqa: ANN001
        from gt7core.tools.diagnose import resolve_target

        monkeypatch.setenv("GT7_PS_IP", "10.0.0.1")
        assert resolve_target("192.168.1.50") == "192.168.1.50"
        assert resolve_target(None) == "10.0.0.1"

    def test_compara_sub_rede_por_tres_octetos(self) -> None:
        from gt7core.tools.diagnose import same_subnet

        assert same_subnet("192.168.1.10", "192.168.1.50")
        assert not same_subnet("192.168.1.10", "192.168.2.50")

    def test_usa_o_decodificador_do_nucleo(self) -> None:
        """Uma cópia só do Salsa20.

        Com duas, uma mudança no protocolo faria a ferramenta relatar "não é
        GT7" com confiança — no exato momento em que alguém depende dela para
        achar o problema.
        """
        from gt7core.tools import diagnose

        # Compara a origem, e não a identidade do objeto: o teste de
        # arquitetura remove e reimporta os módulos do núcleo para provar que
        # ele sobe com Qt bloqueado, e depois disso as duas referências deixam
        # de ser o mesmo objeto. A propriedade que interessa — "veio do
        # protocolo, não é cópia local" — sobrevive à reimportação.
        assert diagnose.salsa20_decode.__module__ == "gt7core.telemetry.protocol"
