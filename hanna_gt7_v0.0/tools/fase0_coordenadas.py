"""
Etapa 0 da assinatura de traçado: as coordenadas do GT7 são estáveis?

Rode isto na sua máquina, com o banco que já tem voltas gravadas:

    python3 tools/fase0_coordenadas.py

A pergunta que ele responde é **uma só**, e ela decide todo o desenho do
reconhecimento automático de pista:

    Duas voltas da mesma pista, gravadas em sessões diferentes, caem nas
    mesmas coordenadas do mundo?

Se **sim**, reconhecer uma pista é comparar caixas delimitadoras — barato e
trivialmente exato. Se **não** (a origem desloca a cada sessão), é preciso uma
assinatura invariante a translação e rotação, que é mais trabalho e tem limiar
para calibrar.

Não quero escolher entre os dois por palpite: errar aqui significa construir a
peça errada e só descobrir contra o console. O script não altera nada — só lê.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BANCO_PADRAO = Path.home() / ".hanna_gt7" / "hanna.db"


def caixa(conn: sqlite3.Connection, lap_id: int) -> tuple[float, float, float, float]:
    """Extremos de posição da volta: (x_min, x_max, z_min, z_max)."""
    linha = conn.execute(
        "SELECT MIN(position_x), MAX(position_x), MIN(position_z), MAX(position_z) "
        "FROM lap_frames WHERE lap_id = ? AND position_x IS NOT NULL",
        (lap_id,),
    ).fetchone()
    return tuple(0.0 if v is None else float(v) for v in linha)  # type: ignore[return-value]


def main() -> int:
    caminho = Path(sys.argv[1]) if len(sys.argv) > 1 else BANCO_PADRAO
    if not caminho.is_file():
        print(f"banco não encontrado: {caminho}")
        print("passe o caminho como argumento, se estiver em outro lugar.")
        return 1

    conn = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)
    print(f"banco: {caminho}\n")

    pistas = conn.execute(
        "SELECT t.id, t.name, COUNT(l.id) FROM tracks t "
        "LEFT JOIN laps l ON l.track_id = t.id "
        "GROUP BY t.id ORDER BY COUNT(l.id) DESC"
    ).fetchall()

    if not pistas:
        print("nenhuma pista gravada ainda — rode uma sessão antes.")
        return 1

    for track_id, nome, total in pistas:
        print(f"── {nome}  ({total} volta(s))")
        if total < 2:
            print("   menos de duas voltas: não dá para comparar\n")
            continue

        voltas = conn.execute(
            "SELECT l.id, l.session_id, l.recorded_at, l.lap_time_ms "
            "FROM laps l WHERE l.track_id = ? ORDER BY l.recorded_at ASC LIMIT 12",
            (track_id,),
        ).fetchall()

        sessoes: dict[object, list[int]] = {}
        for lap_id, session_id, _quando, _tempo in voltas:
            sessoes.setdefault(session_id, []).append(lap_id)

        print(f"   sessões distintas: {len(sessoes)}")
        for lap_id, session_id, _quando, tempo_ms in voltas:
            x0, x1, z0, z1 = caixa(conn, lap_id)
            if x0 == x1 == 0.0:
                print(f"   volta {lap_id}: sem posição gravada")
                continue
            print(
                f"   volta {lap_id:<4} sessão {str(session_id):<5} "
                f"{tempo_ms / 1000:7.3f}s   "
                f"x [{x0:9.1f} .. {x1:9.1f}]   z [{z0:9.1f} .. {z1:9.1f}]"
            )

        # O veredito: comparar a caixa da primeira com a da última.
        primeira, ultima = voltas[0][0], voltas[-1][0]
        a, b = caixa(conn, primeira), caixa(conn, ultima)
        if a[0] == a[1] == 0.0 or b[0] == b[1] == 0.0:
            print("   (sem posição para comparar)\n")
            continue

        desvio = max(abs(x - y) for x, y in zip(a, b, strict=True))
        largura = max(a[1] - a[0], 1.0)
        print(f"\n   maior diferença entre as caixas: {desvio:.1f} m")
        print(f"   como fração da largura da pista : {desvio / largura:.1%}")
        if desvio < largura * 0.02:
            print("   => COORDENADAS ESTÁVEIS: dá para comparar caixa direto.")
        else:
            print("   => COORDENADAS DESLOCAM: precisa de assinatura invariante.")
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
