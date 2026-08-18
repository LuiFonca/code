"""
Testes do design system e do registro de comandos.

Ambos são Python puro, e é isso que torna estes testes possíveis: nenhum deles
precisa de `QApplication`, de servidor gráfico ou de PySide6 instalado. Um
design system que só pode ser verificado abrindo a janela e olhando não é um
sistema — é um acidente que aconteceu de dar certo daquela vez.

O teste mais valioso do arquivo é `test_a_folha_de_estilo_so_usa_cores_da_paleta`:
ele é o equivalente visual do teste de arquitetura que impede o núcleo de
importar Qt. Falha se alguém escrever um hexadecimal direto no QSS, que é
exatamente como a aplicação anterior acabou com cinco cinzas quase iguais.
"""

from __future__ import annotations

import re
from dataclasses import fields

import pytest

from gt7app.commands import CommandRegistry
from gt7app.design.theme import build_stylesheet
from gt7app.design.tokens import (
    DARK_THEME,
    DEFAULT_THEME,
    LIGHT_THEME,
    THEMES,
    Palette,
    Radius,
    Space,
    get_theme,
)

# O `\b` no fim não é detalhe: sem ele o padrão casa o começo de seletores por
# nome de objeto cujas letras são dígitos hexadecimais válidos — `#badge` vira
# um falso "#bad". Exigir fronteira de palavra separa cor de seletor.
HEX_PATTERN = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


class TestTokens:
    def test_os_dois_temas_definem_todas_as_cores(self) -> None:
        """Um tema com um campo a menos quebraria só na tela que o usa."""
        names = {f.name for f in fields(Palette)} - {"speed_ramp"}
        for theme in (DARK_THEME, LIGHT_THEME):
            for name in names:
                value = getattr(theme.palette, name)
                assert isinstance(value, str) and value.startswith("#"), (
                    f"{theme.name}.{name} não é uma cor"
                )

    def test_nenhuma_cor_esta_repetida_por_engano_no_tema_escuro(self) -> None:
        """Superfícies e textos precisam ser distinguíveis entre si.

        Não exige que *todas* as cores sejam únicas — canais e rodas
        compartilham o acento de propósito —, só que a estratificação de
        superfície e a hierarquia de texto não colapsem.
        """
        palette = DARK_THEME.palette
        layers = [
            palette.canvas,
            palette.surface,
            palette.surface_raised,
            palette.surface_overlay,
        ]
        assert len(set(layers)) == len(layers), "camadas de superfície colapsadas"

        text = [palette.text_primary, palette.text_secondary, palette.text_muted]
        assert len(set(text)) == len(text), "hierarquia de texto colapsada"

    def test_escala_de_espacamento_e_crescente(self) -> None:
        values = [space.px for space in Space]
        assert values == sorted(values)
        assert values[0] > 0

    def test_raios_expõem_pixels(self) -> None:
        assert Radius.SM.px == 6
        assert Radius.PILL.px > Radius.LG.px

    def test_delta_verde_a_frente_amarelo_atras(self) -> None:
        palette = DARK_THEME.palette
        assert palette.delta(-0.4) == palette.green
        assert palette.delta(0.0) == palette.green
        assert palette.delta(0.4) == palette.yellow

    def test_cor_por_roda_e_por_canal(self) -> None:
        palette = DARK_THEME.palette
        assert palette.wheel("fl") == palette.wheel_fl
        assert palette.channel("speed") == palette.channel_speed
        # Canal desconhecido cai no acento em vez de estourar: um gráfico novo
        # deve aparecer com cor plausível, não derrubar a página.
        assert palette.channel("inexistente") == palette.accent

    def test_tema_desconhecido_cai_no_padrao(self) -> None:
        assert get_theme("não-existe") is THEMES[DEFAULT_THEME]
        assert get_theme(None) is THEMES[DEFAULT_THEME]
        assert get_theme("  DARK  ") is DARK_THEME

    def test_temas_claro_e_escuro_sao_distintos(self) -> None:
        assert DARK_THEME.is_dark
        assert not LIGHT_THEME.is_dark
        assert DARK_THEME.palette.canvas != LIGHT_THEME.palette.canvas


class TestStylesheet:
    @pytest.mark.parametrize("theme", [DARK_THEME, LIGHT_THEME], ids=lambda t: t.name)
    def test_a_folha_de_estilo_so_usa_cores_da_paleta(self, theme: object) -> None:
        """Nenhum hexadecimal no QSS que não venha dos tokens.

        É o guarda que mantém o design system sendo um sistema. Sem ele, um
        ajuste apressado escreve `#2a2e3a` direto na folha, ninguém percebe, e
        seis meses depois existem três bordas cinza levemente diferentes.
        """
        from gt7app.design.tokens import Theme

        assert isinstance(theme, Theme)
        allowed = {
            value.lower()
            for f in fields(Palette)
            if isinstance(value := getattr(theme.palette, f.name), str)
        }
        found = {match.lower() for match in HEX_PATTERN.findall(build_stylesheet(theme))}

        unexpected = found - allowed
        assert not unexpected, f"cores fora da paleta na folha de estilo: {unexpected}"

    def test_a_folha_cobre_os_seletores_declarados(self) -> None:
        """Cada `objectName` constante precisa existir na folha.

        Um `setObjectName` com nome que a folha não conhece produz um widget sem
        estilo e nenhum aviso — o pior tipo de falha para depurar.
        """
        from gt7app.design import theme as theme_module

        stylesheet = build_stylesheet(DARK_THEME)
        selectors = [
            value
            for name, value in vars(theme_module).items()
            if name.startswith("OBJ_") and isinstance(value, str)
        ]
        assert selectors, "nenhuma constante de seletor encontrada"
        for selector in selectors:
            assert f"#{selector}" in stylesheet, f"seletor sem estilo: {selector}"

    def test_o_fundo_da_janela_e_sempre_explicito(self) -> None:
        """Herdar do sistema já produziu texto escuro sobre fundo escuro."""
        for theme in (DARK_THEME, LIGHT_THEME):
            stylesheet = build_stylesheet(theme)
            assert f"background-color: {theme.palette.canvas}" in stylesheet
            assert f"color: {theme.palette.text_primary}" in stylesheet


class TestCommandRegistry:
    def _registry(self) -> tuple[CommandRegistry, list[str]]:
        executed: list[str] = []
        registry = CommandRegistry()
        registry.add("go.live", "Ir para Ao vivo", lambda: executed.append("live"))
        registry.add(
            "go.compare",
            "Ir para Comparar",
            lambda: executed.append("compare"),
            keywords=("comparação", "diff"),
        )
        registry.add("capture.start", "Conectar e começar a capturar",
                     lambda: executed.append("start"), keywords=("iniciar",))
        return registry, executed

    def test_busca_vazia_devolve_tudo(self) -> None:
        registry, _ = self._registry()
        assert len(registry.search("")) == 3

    def test_casamento_por_subsequencia(self) -> None:
        """`cmp` encontra "Comparar" — é o que se espera de uma paleta."""
        registry, _ = self._registry()
        results = registry.search("cmp")
        assert results
        assert results[0].id == "go.compare"

    def test_prefixo_ganha_de_subsequencia(self) -> None:
        registry, _ = self._registry()
        results = registry.search("ir para comparar")
        assert results[0].id == "go.compare"

    def test_busca_por_palavra_chave(self) -> None:
        """Sinônimos importam: ninguém digita o título exato."""
        registry, _ = self._registry()
        assert registry.search("iniciar")[0].id == "capture.start"
        assert registry.search("diff")[0].id == "go.compare"

    def test_consulta_sem_casamento_devolve_vazio(self) -> None:
        registry, _ = self._registry()
        assert registry.search("zzzzzzq") == []

    def test_registrar_o_mesmo_id_substitui(self) -> None:
        """Uma página que se recarrega não deve duplicar suas ações."""
        registry, _ = self._registry()
        registry.add("go.live", "Ir para Ao vivo (novo)", lambda: None)
        matches = [c for c in registry.all() if c.id == "go.live"]
        assert len(matches) == 1
        assert matches[0].title == "Ir para Ao vivo (novo)"

    def test_executar_pelo_registro(self) -> None:
        registry, executed = self._registry()
        command = registry.get("capture.start")
        assert command is not None
        command.run()
        assert executed == ["start"]

    def test_id_inexistente_devolve_none(self) -> None:
        registry, _ = self._registry()
        assert registry.get("nao.existe") is None

    def test_limite_de_resultados(self) -> None:
        registry, _ = self._registry()
        assert len(registry.search("", limit=2)) == 2


class TestSequentialRamp:
    """A escala de magnitude do mapa de calor.

    Os passos vieram de uma escala documentada e foram validados contra a
    superfície de cada tema (claridade monotônica, separação entre passos,
    contraste da ponta que encosta no fundo). Estes testes fixam as
    propriedades que um ajuste futuro não pode quebrar sem perceber.
    """

    def test_extremos_e_interpolacao(self) -> None:
        ramp = DARK_THEME.palette.speed_ramp
        assert ramp.at(0.0) == ramp.steps[0]
        assert ramp.at(1.0) == ramp.steps[-1]
        # Um valor entre passos produz cor nova, não o passo mais próximo.
        middle = ramp.at(0.125)
        assert middle not in ramp.steps

    def test_valores_fora_da_faixa_saturam(self) -> None:
        """Velocidade fora do intervalo não deve produzir cor inválida."""
        ramp = LIGHT_THEME.palette.speed_ramp
        assert ramp.at(-5.0) == ramp.steps[0]
        assert ramp.at(9.9) == ramp.steps[-1]

    def test_a_escala_e_de_uma_cor_so(self) -> None:
        """Nunca arco-íris: a ordem tem que estar na claridade, não na matiz.

        Num arco-íris o leitor não sabe se verde é mais ou menos que laranja
        sem consultar a legenda.
        """
        import colorsys

        for theme in (DARK_THEME, LIGHT_THEME):
            hues = []
            for step in theme.palette.speed_ramp.steps:
                r, g, b = (int(step[i : i + 2], 16) / 255 for i in (1, 3, 5))
                hues.append(colorsys.rgb_to_hls(r, g, b)[0])
            spread = (max(hues) - min(hues)) * 360
            assert spread < 20, f"{theme.name}: matiz varia {spread:.0f}°"

    def test_claridade_e_monotonica(self) -> None:
        """Do menor ao maior valor, a claridade só anda numa direção."""
        import colorsys

        for theme in (DARK_THEME, LIGHT_THEME):
            lightness = []
            for step in theme.palette.speed_ramp.steps:
                r, g, b = (int(step[i : i + 2], 16) / 255 for i in (1, 3, 5))
                lightness.append(colorsys.rgb_to_hls(r, g, b)[1])
            assert lightness == sorted(lightness) or lightness == sorted(
                lightness, reverse=True
            ), f"{theme.name}: claridade não é monotônica"

    def test_cada_tema_ancora_no_seu_fundo(self) -> None:
        """A escala é escolhida por tema, não invertida automaticamente.

        No escuro a ponta lenta é a escura (encosta no fundo); no claro é a
        clara. Inverter uma na outra produziria a ponta errada sumindo.
        """
        import colorsys

        def lightness(hex_color: str) -> float:
            r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
            return colorsys.rgb_to_hls(r, g, b)[1]

        dark = DARK_THEME.palette.speed_ramp
        light = LIGHT_THEME.palette.speed_ramp
        assert lightness(dark.steps[0]) < lightness(dark.steps[-1])
        assert lightness(light.steps[0]) > lightness(light.steps[-1])
