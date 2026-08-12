"""
Decodificação de pacotes de telemetria do GT7.
Lógica pura (sem rede, sem UI) para poder ser testada e reutilizada
tanto no listener quanto em ferramentas futuras (replay, análise offline).
"""

import struct
from dataclasses import dataclass

GT7_KEY = b"Simulator Interface Packet GT7 ver 0.0"
MAGIC_NUMBER = 0x47375330  # "G7S0"


def salsa20_decode(data: bytes):
    """Decodifica um pacote bruto do GT7. Retorna None se o pacote não
    for reconhecido (magic number não bate)."""
    from Crypto.Cipher import Salsa20

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


@dataclass
class TelemetryFrame:
    speed_kmh: float
    rpm: float
    gear: int
    throttle: float  # 0-100%
    brake: float      # 0-100%
    fuel: float
    lap_count: int
    total_laps: int
    position_x: float
    position_y: float
    position_z: float
    best_lap_ms: int
    last_lap_ms: int
    current_lap_ms: int
    tire_temp_fl: float
    tire_temp_fr: float
    tire_temp_rl: float
    tire_temp_rr: float
    # Offset 0x124: car_id documentado pela comunidade (ex: gt7-udp, pdtools).
    # Valor int32 que mapeia para o catálogo de carros em data/cars.csv.
    # Pode ser -1 ou 0 em menus/loading — tratar como "desconhecido".
    car_id: int

    @staticmethod
    def from_bytes(d: bytes) -> "TelemetryFrame":
        position_x, position_y, position_z = struct.unpack("<fff", d[0x04:0x10])
        speed_ms = struct.unpack("<f", d[0x4C:0x50])[0]
        rpm = struct.unpack("<f", d[0x3C:0x40])[0]
        gear_byte = struct.unpack("<B", d[0x90:0x91])[0]
        gear = gear_byte & 0x0F
        throttle = struct.unpack("<B", d[0x91:0x92])[0] / 255 * 100
        brake = struct.unpack("<B", d[0x92:0x93])[0] / 255 * 100
        fuel = struct.unpack("<f", d[0x44:0x48])[0]
        current_lap_ms = struct.unpack("<i", d[0x78:0x7C])[0]
        last_lap_ms = struct.unpack("<i", d[0x7C:0x80])[0]
        best_lap_ms = struct.unpack("<i", d[0x70:0x74])[0]
        lap_count = struct.unpack("<h", d[0x74:0x76])[0]
        total_laps = struct.unpack("<h", d[0x76:0x78])[0]
        tire_temp_fl, tire_temp_fr, tire_temp_rl, tire_temp_rr = struct.unpack("<ffff", d[0x60:0x70])
        car_id = struct.unpack("<i", d[0x124:0x128])[0]

        return TelemetryFrame(
            speed_kmh=speed_ms * 3.6,
            rpm=rpm,
            gear=gear,
            throttle=throttle,
            brake=brake,
            fuel=fuel,
            lap_count=lap_count,
            total_laps=total_laps,
            position_x=position_x,
            position_y=position_y,
            position_z=position_z,
            best_lap_ms=best_lap_ms,
            last_lap_ms=last_lap_ms,
            current_lap_ms=current_lap_ms,
            tire_temp_fl=tire_temp_fl,
            tire_temp_fr=tire_temp_fr,
            tire_temp_rl=tire_temp_rl,
            tire_temp_rr=tire_temp_rr,
            car_id=car_id,
        )
