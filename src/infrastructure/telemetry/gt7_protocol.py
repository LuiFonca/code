"""
Decodificação dos pacotes de telemetria do GT7.

Lógica pura: sem rede, sem Qt, sem estado. Isso permite testá-la com um pacote
gravado em arquivo e reaproveitá-la fora do listener (replay, análise offline).

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
    current_lap_ms: int
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
        best_lap_ms = struct.unpack("<i", d[0x70:0x74])[0]
        lap_count = struct.unpack("<h", d[0x74:0x76])[0]
        total_laps = struct.unpack("<h", d[0x76:0x78])[0]
        current_lap_ms = struct.unpack("<i", d[0x78:0x7C])[0]
        last_lap_ms = struct.unpack("<i", d[0x7C:0x80])[0]
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

        suspension_fl, suspension_fr, suspension_rl, suspension_rr = struct.unpack(
            "<ffff", d[0x98:0xA8]
        )
        tire_slip_fl, tire_slip_fr, tire_slip_rl, tire_slip_rr = struct.unpack(
            "<ffff", d[0xE4:0xF4]
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
            current_lap_ms=current_lap_ms,
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
