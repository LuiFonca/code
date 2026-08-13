"""
Reconhecimento da pista a partir do traçado da volta.

Junta as duas metades: a matemática de comparação está no domínio
(`domain/services/track_fingerprint.py`) e o acesso às pistas conhecidas está
no repositório. Esta classe é a costura, e é onde mora a política — quando
aprender uma assinatura nova e quando confiar num reconhecimento.

A política de aprendizado
--------------------------
Só voltas **completas e escolhidas à mão** viram assinatura de referência. É
deliberado: se uma volta reconhecida automaticamente pudesse virar referência,
um reconhecimento errado se autoconfirmaria, e o erro ficaria gravado como
verdade da pista.
"""

from ...domain.services.track_fingerprint import build_fingerprint, identify_track


class TrackIdentifier:
    """Aprende o desenho das pistas e identifica voltas sem pista definida."""

    def __init__(self, track_repository):
        self._tracks = track_repository

    # ---------- aprendizado ----------

    def learn(self, track_id: int, points) -> bool:
        """Guarda a assinatura desta pista se ela ainda não tiver uma.

        Devolve True quando aprendeu algo. Não sobrescreve: a primeira volta
        completa da pista já descreve o traçado, e reescrever a cada volta faria
        a referência oscilar com a linha de pilotagem do dia.
        """
        if track_id is None:
            return False
        if not hasattr(self._tracks, "set_fingerprint"):
            return False
        if self._tracks.get_fingerprint(track_id):
            return False

        assinatura = build_fingerprint(points)
        if assinatura is None:
            return False
        self._tracks.set_fingerprint(track_id, assinatura)
        return True

    # ---------- reconhecimento ----------

    def identify(self, points) -> tuple[int, str, float] | None:
        """Descobre de que pista é esta volta. `(id, nome, desvio)` ou None.

        None é resposta comum e legítima: primeira volta na pista, volta
        parcial, ou dois traçados parecidos demais para decidir. A volta é
        gravada de qualquer forma — só fica sem pista até alguém dizer qual é.
        """
        if not hasattr(self._tracks, "all_fingerprints"):
            return None

        assinatura = build_fingerprint(points)
        if assinatura is None:
            return None

        candidatas = self._tracks.all_fingerprints()
        resultado = identify_track(assinatura, candidatas)
        if resultado is None:
            return None

        track_id, desvio = resultado
        track = self._tracks.get_by_id(track_id)
        if track is None:
            return None
        return track_id, track.name, desvio
