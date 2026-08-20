"""
Decodificação dos pacotes de telemetria do GT7.

Portado de `src/infrastructure/telemetry/gt7_protocol.py` **sem alteração de
lógica**: o arquivo já era puro (struct + Salsa20, sem rede, sem Qt, sem
estado), que é exatamente por que ele pôde vir para o núcleo intacto.

A auditoria classificou este módulo como "preservar sem reescrita" — os offsets
vêm de engenharia reversa da comunidade e cada byte foi validado contra pacote
real. Mexer aqui sem um pacote gravado para comparar é risco puro.

O `TelemetryFrame` daqui é um **DTO de formato de fio** — espelha o pacote de
296 bytes, incluindo campos que o domínio não usa (pressão de óleo, marcha
sugerida, farol alto). Não confundir com o `TelemetryPoint` do domínio: a
conversão de um para o outro é trabalho da camada de aplicação.
"""

import struct
from dataclasses import dataclass

try:
    from Crypto.Cipher import Salsa20
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "pycryptodome é obrigatório para decodificar a telemetria do GT7 "
        "(os pacotes são cifrados com Salsa20). Instale com: pip install pycryptodome"
    ) from exc

GT7_KEY = b"Simulator Interface Packet GT7 ver 0.0"
MAGIC_NUMBER = 0x47375330  # "G7S0"

# Bitfield de flags no offset 0x8E (uint16).
FLAG_CAR_ON_TRACK = 1 << 0
FLAG_PAUSED = 1 << 1
FLAG_LOADING = 1 << 2
FLAG_IN_GEAR = 1 << 3
FLAG_HAS_TURBO = 1 << 4
FLAG_REV_LIMITER = 1 << 5
FLAG_HANDBRAKE = 1 << 6
FLAG_LIGHTS_ON = 1 << 7
FLAG_HIGH_BEAM = 1 << 8
FLAG_LOW_BEAM = 1 << 9
FLAG_ASM_ACTIVE = 1 << 10
FLAG_TCS_ACTIVE = 1 << 11


def salsa20_decode(data: bytes) -> bytes | None:
    """Decifra um pacote bruto. None se não for um pacote reconhecível.

    O IV sai do próprio pacote (offset 0x40) e é combinado com a constante
    0xDEADBEAF. O magic number confirma que a decifragem deu certo — pacote de
    outra origem ou chave errada produz lixo e é descartado silenciosamente,
    que é o comportamento correto para um socket UDP aberto na rede local.
    """
    key = GT7_KEY[:32]
    oiv = data[0x40:0x44]
    iv1 = int.from_bytes(oiv, byteorder="little")
    iv2 = iv1 ^ 0xDEADBEAF
    iv = iv2.to_bytes(4, byteorder="little") + iv1.to_bytes(4, byteorder="little")

    cipher = Salsa20.new(key=key, nonce=bytes(iv))
    decoded = cipher.decrypt(data)

    magic = struct.unpack("<I", decoded[0:4])[0]
    if magic != MAGIC_NUMBER:
        return None
    return decoded


@dataclass(slots=True)
class TelemetryFrame:
    """Um pacote decodificado, campo a campo."""

    speed_kmh: float
    rpm: float
    gear: int
    suggested_gear: int
    throttle: float
    brake: float
    fuel: float
    fuel_capacity: float
    lap_count: int
    total_laps: int
    position_x: float
    position_y: float
    position_z: float
    velocity_x: float
    velocity_y: float
    velocity_z: float
    body_height: float
    best_lap_ms: int
    last_lap_ms: int

    packet_id: int
    """Contador de quadros do jogo, em 0x70. O relógio real da telemetria.

    **O GT7 não transmite o tempo da volta corrente.** Essa ausência custou
    caro: a versão anterior lia 0x78 como `current_lap_ms`, mas 0x78 é o
    *melhor* tempo — um valor que não muda durante a volta. O motor integra
    distância por `Δt`, e `Δt` de um valor constante é zero, então **toda volta
    capturada de um PS5 real era gravada com distância 0,0 m**. Curvas, zonas de
    frenagem e atribuição de perda são todas indexadas por distância; a volta
    era salva e a análise inteira nascia morta.

    O sintoma escondia a causa: o tempo de volta aparecia certo, porque vem de
    `last_lap_ms` (0x7C), que estava no offset correto. E nada disso aparecia
    com a fonte sintética, que constrói quadros direto e nunca passa por estes
    offsets — é o tipo de defeito que só existe contra hardware de verdade.

    Preferido à hora do dia (0x80) para derivar tempo: o tick conta quadros do
    **jogo**, então sobrevive a perda de pacote UDP (o salto no contador revela
    o intervalo real) e não é afetado pelo multiplicador de tempo que o GT7
    aplica em provas de endurance.
    """

    day_progression_ms: int
    """Hora do dia na pista, em 0x80. Decodificada por completude."""
    tire_temp_fl: float
    tire_temp_fr: float
    tire_temp_rl: float
    tire_temp_rr: float
    suspension_fl: float
    suspension_fr: float
    suspension_rl: float
    suspension_rr: float
    tire_slip_fl: float
    tire_slip_fr: float
    tire_slip_rl: float
    tire_slip_rr: float
    turbo_boost: float
    oil_pressure: float
    oil_temp: float
    water_temp: float
    rpm_flashing_min: int
    rpm_flashing_max: int
    max_speed_kmh: int
    flags: int
    car_id: int

    @property
    def is_on_track(self) -> bool:
        return bool(self.flags & FLAG_CAR_ON_TRACK)

    @property
    def is_paused(self) -> bool:
        return bool(self.flags & FLAG_PAUSED)

    @property
    def is_loading(self) -> bool:
        return bool(self.flags & FLAG_LOADING)

    @property
    def has_turbo(self) -> bool:
        return bool(self.flags & FLAG_HAS_TURBO)

    @property
    def rev_limiter_active(self) -> bool:
        return bool(self.flags & FLAG_REV_LIMITER)

    @property
    def tcs_active(self) -> bool:
        return bool(self.flags & FLAG_TCS_ACTIVE)

    @property
    def asm_active(self) -> bool:
        return bool(self.flags & FLAG_ASM_ACTIVE)

    @staticmethod
    def from_bytes(d: bytes) -> "TelemetryFrame":
        """Lê o pacote decifrado nos offsets conhecidos.

        Os offsets vêm de engenharia reversa da comunidade — não há
        especificação oficial. Um pacote curto levanta `struct.error`, que o
        listener trata descartando o pacote.
        """
        position_x, position_y, position_z = struct.unpack("<fff", d[0x04:0x10])
        velocity_x, velocity_y, velocity_z = struct.unpack("<fff", d[0x10:0x1C])
        body_height = struct.unpack("<f", d[0x38:0x3C])[0]
        rpm = struct.unpack("<f", d[0x3C:0x40])[0]
        fuel = struct.unpack("<f", d[0x44:0x48])[0]
        fuel_capacity = struct.unpack("<f", d[0x48:0x4C])[0]
        speed_ms = struct.unpack("<f", d[0x4C:0x50])[0]
        turbo_boost = struct.unpack("<f", d[0x50:0x54])[0]
        oil_pressure = struct.unpack("<f", d[0x54:0x58])[0]
        water_temp = struct.unpack("<f", d[0x58:0x5C])[0]
        oil_temp = struct.unpack("<f", d[0x5C:0x60])[0]
        tire_temp_fl, tire_temp_fr, tire_temp_rl, tire_temp_rr = struct.unpack(
            "<ffff", d[0x60:0x70]
        )
        # 0x70 é o TICK — contador de quadros do jogo —, não o melhor tempo.
        # A versão anterior lia `best_lap` aqui e `current_lap` em 0x78, o que
        # deslocava dois campos e produziu o defeito descrito em `packet_id`.
        packet_id = struct.unpack("<i", d[0x70:0x74])[0]
        lap_count = struct.unpack("<h", d[0x74:0x76])[0]
        total_laps = struct.unpack("<h", d[0x76:0x78])[0]
        best_lap_ms = struct.unpack("<i", d[0x78:0x7C])[0]
        last_lap_ms = struct.unpack("<i", d[0x7C:0x80])[0]
        day_progression_ms = struct.unpack("<i", d[0x80:0x84])[0]
        rpm_flashing_min = struct.unpack("<H", d[0x88:0x8A])[0]
        rpm_flashing_max = struct.unpack("<H", d[0x8A:0x8C])[0]
        max_speed_kmh = struct.unpack("<H", d[0x8C:0x8E])[0]
        flags = struct.unpack("<H", d[0x8E:0x90])[0]

        # Um byte carrega duas marchas: nibble baixo = atual, alto = sugerida.
        gear_byte = struct.unpack("<B", d[0x90:0x91])[0]
        gear = gear_byte & 0x0F
        suggested_gear = (gear_byte >> 4) & 0x0F

        # Pedais chegam como 0-255 e são normalizados para porcentagem.
        throttle = struct.unpack("<B", d[0x91:0x92])[0] / 255 * 100
        brake = struct.unpack("<B", d[0x92:0x93])[0] / 255 * 100

        # Rotação das rodas (rad/s), raio do pneu (m) e altura de suspensão, nesta
        # ordem e coladas. Os offsets antigos — 0x98 para suspensão e 0xE4 para
        # escorregamento — estavam errados, e a prova veio da tela: **as quatro
        # rodas marcavam 0,000 a volta inteira**, contra um PS5 real. Um canal de
        # telemetria não é identicamente zero; 0xE4 cai no bloco não usado do
        # pacote (0xD4–0xF4) e 0x98 dentro do vetor do plano da pista.
        #
        # A cadeia que fixa estes três é verificável de trás para frente a partir
        # de um ponto conhecido-bom: `car_id` em 0x124 funciona (a detecção de
        # carro funciona), e 0x104 + 8 razões de marcha × 4 bytes = 0x124. Antes
        # das razões vêm embreagem e transmissão (0xF4–0x104), antes o bloco não
        # usado (0xD4), e antes dele os três grupos de quatro floats abaixo.
        wheel_rps = struct.unpack("<ffff", d[0xA4:0xB4])
        tire_radius = struct.unpack("<ffff", d[0xB4:0xC4])
        suspension_fl, suspension_fr, suspension_rl, suspension_rr = struct.unpack(
            "<ffff", d[0xC4:0xD4]
        )

        # Velocidade da superfície do pneu, em m/s. **Fisicamente definida**:
        # |ω| × raio. É o que elimina a ambiguidade que o módulo de análise vinha
        # contornando por inferência — não havia campo "escorregamento" no
        # pacote, havia rotação e raio, e o escorregamento sempre foi derivado.
        tire_slip_fl, tire_slip_fr, tire_slip_rl, tire_slip_rr = (
            abs(rps) * radius for rps, radius in zip(wheel_rps, tire_radius, strict=True)
        )
        car_id = struct.unpack("<i", d[0x124:0x128])[0]

        return TelemetryFrame(
            speed_kmh=speed_ms * 3.6,
            rpm=rpm,
            gear=gear,
            suggested_gear=suggested_gear,
            throttle=throttle,
            brake=brake,
            fuel=fuel,
            fuel_capacity=fuel_capacity,
            lap_count=lap_count,
            total_laps=total_laps,
            position_x=position_x,
            position_y=position_y,
            position_z=position_z,
            velocity_x=velocity_x,
            velocity_y=velocity_y,
            velocity_z=velocity_z,
            body_height=body_height,
            best_lap_ms=best_lap_ms,
            last_lap_ms=last_lap_ms,
            packet_id=packet_id,
            day_progression_ms=day_progression_ms,
            tire_temp_fl=tire_temp_fl,
            tire_temp_fr=tire_temp_fr,
            tire_temp_rl=tire_temp_rl,
            tire_temp_rr=tire_temp_rr,
            suspension_fl=suspension_fl,
            suspension_fr=suspension_fr,
            suspension_rl=suspension_rl,
            suspension_rr=suspension_rr,
            tire_slip_fl=tire_slip_fl,
            tire_slip_fr=tire_slip_fr,
            tire_slip_rl=tire_slip_rl,
            tire_slip_rr=tire_slip_rr,
            turbo_boost=turbo_boost,
            oil_pressure=oil_pressure,
            oil_temp=oil_temp,
            water_temp=water_temp,
            rpm_flashing_min=rpm_flashing_min,
            rpm_flashing_max=rpm_flashing_max,
            max_speed_kmh=max_speed_kmh,
            flags=flags,
            car_id=car_id,
        )
