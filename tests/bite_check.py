"""
Verificação de mordida: um teste que passa mas não falha quando o código
quebra não protege nada.

Cada entrada abaixo desfaz uma correção no código de produção, roda o teste
que deveria pegar isso, e confirma que ele fica vermelho. O arquivo é sempre
restaurado, mesmo se algo der errado.

    python3 tests/bite_check.py
"""
import pathlib, re, subprocess, sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# (defeito, arquivo, trecho original, mutacao, teste que deve falhar)
MUTACOES = [
    ("01 volta parcial", "src/application/services/telemetry_service.py",
     "is_best = is_complete and (", "is_best = True and (",
     "test_01_volta_parcial_nao_vira_recorde"),

    ("02 indice slip", "src/application/viewmodels/telemetry_viewmodel.py",
     "return min(abs(slip_value) / SLIP_SATURATION, 1.0) * 100.0",
     "return min(abs(slip_value) * 12.0, 12.0)",
     "test_02_indice_deslizamento_sem_unidade_falsa"),

    ("03 delta apos exclusao", "src/application/services/telemetry_service.py",
     "self._bus.subscribe(LapDeleted, self._on_lap_deleted)",
     "pass  # inscricao removida",
     "test_03_excluir_melhor_volta_recarrega_delta"),

    ("04 poda silenciosa", "src/infrastructure/repositories/sqlite_lap_repository.py",
     "self.last_purged_count = self._conn.execute(",
     "self.last_purged_count = 0 and self._conn.execute(",
     "test_04_poda_avisa_quantas_voltas_saiu"),

    ("05 cache de arrays", "src/domain/services/lap_analysis.py",
     "cached = self._array_cache.get(channel)\n        if cached is not None:\n            return cached",
     "cached = None",
     "test_05_comparacao_usa_cache_de_arrays"),

    ("06 gravacao assincrona", "src/application/services/telemetry_service.py",
     "self._writer.submit(lap, context=is_best)",
     "self._on_lap_written(lap, self._laps.save(lap), 0, is_best)",
     "test_06_gravacao_fora_da_thread_da_interface"),

    ("07 teto de forca G", "src/application/services/telemetry_service.py",
     "return max(-MAX_G, min(MAX_G, value))", "return value",
     "test_07_forca_g_saturada"),

    ("08 exibicao ao vivo", "src/application/services/telemetry_service.py",
     "self._bus.publish(TelemetryReceived(point=point, frame=frame))",
     "if not suspend_accumulation:\n            self._bus.publish(TelemetryReceived(point=point, frame=frame))",
     "test_08_fora_da_pista_suspende_gravacao_mas_nao_a_exibicao"),

    ("09 combustivel em %", "src/application/viewmodels/telemetry_viewmodel.py",
     "return used / self._tank_capacity * 100.0", "return used",
     "test_09_combustivel_em_percentual_do_tanque"),

    ("10 desempate recorde", "src/infrastructure/repositories/sqlite_lap_repository.py",
     "ORDER BY lap_time_ms ASC, id ASC LIMIT 1", "ORDER BY lap_time_ms ASC, id DESC LIMIT 1",
     "test_10_empate_de_recorde_desempata_pelo_mais_antigo"),

    ("11 dispose", "src/application/viewmodels/history_viewmodel.py",
     "self._bus.unsubscribe(LapCompleted, self._on_lap_completed)",
     "pass  # dispose desativado",
     "test_11_dispose_libera_inscricoes"),

    ("12 setores por pista", "src/infrastructure/repositories/sqlite_database.py",
     "fractions = sector_fractions_for(conn, track_id)", "fractions = None",
     "test_12_setores_configuraveis_por_pista"),

    # A chave estrangeira e garantida por DOIS caminhos: o _create_schema (banco
    # novo) e a migracao v6, que reconstroi a tabela se nao encontrar a
    # restricao. Quebrar um so deixa o outro reparar, entao a mutacao desativa
    # os dois de uma vez. Descobrir essa redundancia foi merito da verificacao.
    ("13 chave estrangeira", "src/infrastructure/repositories/sqlite_database.py",
     [("""CREATE TABLE IF NOT EXISTS laps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER REFERENCES tracks(id),""",
       """CREATE TABLE IF NOT EXISTS laps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER,"""),
      ("        if has_fk:", "        if True:")],
     "test_13_track_id_tem_chave_estrangeira"),
]


def rodar(teste):
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-k", teste, "-q", "--no-header", "-x"],
        cwd=RAIZ, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "QT_QPA_PLATFORM": "offscreen",
             "HOME": "/root"},
    )
    return r.returncode == 0


def main():
    print(f"VERIFICACAO DE MORDIDA — {len(MUTACOES)} correcoes\n")
    mordem, nao_mordem, invalidas = [], [], []

    for entrada in MUTACOES:
        # Entrada simples tem 5 campos; a composta tem 4, porque a lista de
        # pares ja carrega alvo e substituicao juntos.
        if len(entrada) == 5:
            nome, rel, alvo, mutado, teste = entrada
        else:
            nome, rel, alvo, teste = entrada
            mutado = None
        caminho = RAIZ / rel
        original = caminho.read_text()
        # Uma correcao pode ser garantida por mais de um ponto do codigo; nesses
        # casos a mutacao vem como lista de pares e desativa todos de uma vez.
        edicoes = alvo if isinstance(alvo, list) else [(alvo, mutado)]
        faltando = [a for a, _ in edicoes if a not in original]
        if faltando:
            invalidas.append(nome)
            print(f"  [?] {nome:26} trecho nao encontrado — mutacao desatualizada")
            continue
        try:
            texto = original
            for a, m in edicoes:
                texto = texto.replace(a, m, 1)
            caminho.write_text(texto)
            passou = rodar(teste)
        finally:
            caminho.write_text(original)

        if passou:
            nao_mordem.append(nome)
            print(f"  [!] {nome:26} teste PASSOU com o codigo quebrado — nao protege")
        else:
            mordem.append(nome)
            print(f"  [ok] {nome:26} teste ficou vermelho — protege")

    print(f"\n  mordem: {len(mordem)}/{len(MUTACOES)}")
    if nao_mordem:
        print(f"  NAO MORDEM: {nao_mordem}")
    if invalidas:
        print(f"  mutacoes invalidas: {invalidas}")
    return 0 if not nao_mordem and not invalidas else 1


if __name__ == "__main__":
    sys.exit(main())
