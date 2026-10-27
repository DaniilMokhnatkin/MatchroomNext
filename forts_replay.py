"""Read Forts v1.38.2 / FWR format 5 player metadata. Standard library only.

This parses the player table, not the simulation. See FORMAT.md for the
independently verified Steam ID encoding and the limits of this reader.
"""
from __future__ import annotations

import argparse
import json
import lzma
import re
import struct
import sys
from pathlib import Path

MAX_HEADER = 4 * 1024 * 1024
MAX_REPLAY = 256 * 1024 * 1024
RECORD_SIZE = 101
STEAM_XOR = 0x314E62818B5F1701
TABLE_END = b"\xa1" * 4
HEADER_END = b"\xed\xd1\xed\xd1"
ROLES = {"self": "Вы / автор реплея", "opponent": "Соперник",
         "ally": "Союзник", "observer": "Наблюдатель", "player": "Игрок"}


class ReplayError(ValueError):
    """Unsupported or incomplete player metadata."""


def _last_state_block(data: bytes, absolute_start: int = 0, file_size: int | None = None) -> bytes | None:
    """Decompress the last complete Forts state block found in a file tail."""
    size = file_size if file_size is not None else len(data)
    search_to = len(data)
    while True:
        marker = data.rfind(HEADER_END, 0, search_to)
        if marker < 0:
            return None
        pos = marker + 4
        if pos + 17 <= len(data):
            raw_size, total_size, repeated_size = struct.unpack_from('<III', data, pos)
            absolute_end = absolute_start + pos + 8 + total_size
            if (raw_size == repeated_size and 13 <= total_size <= len(data)
                    and 0 < raw_size <= 64 * 1024 * 1024
                    and pos + 8 + total_size == len(data)
                    and absolute_end == size):
                props = data[pos + 12:pos + 17]
                prop = props[0]
                lc, remainder = prop % 9, prop // 9
                lp, pb = remainder % 5, remainder // 5
                if pb <= 4:
                    filters = [{'id': lzma.FILTER_LZMA1, 'dict_size': struct.unpack_from('<I', props, 1)[0],
                                'lc': lc, 'lp': lp, 'pb': pb}]
                    try:
                        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
                        return decoder.decompress(data[pos + 17:pos + 4 + total_size], max_length=raw_size)
                    except lzma.LZMAError:
                        pass
        search_to = marker


def winner_from_bytes(data: bytes) -> tuple[int | None, str]:
    state = _last_state_block(data)
    if not state or b'gameEnded = true,' not in state:
        return None, 'unknown'
    values = re.findall(rb'gameWinner = (-?[0-9]+),', state)
    if not values:
        return None, 'unknown'
    winner = int(values[-1])
    return (winner, 'winner') if winner in (1, 2) else (None, 'draw' if winner == 0 else 'unknown')


def read_replay_winner(path: Path) -> tuple[int | None, str]:
    size = path.stat().st_size
    tail_size = min(size, 16 * 1024 * 1024)
    with path.open('rb') as stream:
        stream.seek(size - tail_size)
        tail = stream.read(tail_size)
    state = _last_state_block(tail, size - tail_size, size)
    if not state or b'gameEnded = true,' not in state:
        return None, 'unknown'
    values = re.findall(rb'gameWinner = (-?[0-9]+),', state)
    if not values:
        return None, 'unknown'
    winner = int(values[-1])
    return (winner, 'winner') if winner in (1, 2) else (None, 'draw' if winner == 0 else 'unknown')


def header_length(prefix: bytes) -> int:
    if len(prefix) < 17:
        raise ReplayError("Файл слишком короткий: нет заголовка FWR.")
    if prefix[0] != 0x67 or struct.unpack_from("<I", prefix, 1)[0] != 5:
        raise ReplayError("Неизвестный формат. Поддерживается FWR format 5 (проверен на Forts 1.38.2).")
    length = struct.unpack_from("<I", prefix, 13)[0]
    if not 17 <= length <= MAX_HEADER:
        raise ReplayError("Некорректная длина заголовка FWR.")
    return length


def valid_steam_id(value: int) -> bool:
    # Public universe, individual account, desktop instance, nonzero account ID.
    return value >> 32 == 0x01100001 and value & 0xFFFFFFFF != 0


def decode_steam_id(record: bytes) -> tuple[str | None, str]:
    block = record[57:89]
    if block == bytes(32):
        return None, "absent"
    random_low, random_high, encoded = struct.unpack_from("<IIQ", record, 65)
    value = encoded ^ ((random_high << 32) | random_low) ^ STEAM_XOR
    if value == 0:
        return None, "absent"
    if not valid_steam_id(value):
        return None, "invalid"
    # Strings prevent JavaScript / JSON consumers from rounding 64-bit IDs.
    return str(value), "decoded"


def _tables(header: bytes):
    for marker in re.finditer(re.escape(TABLE_END), header):
        end = marker.start()
        for count in range(1, 65):
            start = end - count * RECORD_SIZE
            if start < 12:
                break
            if struct.unpack_from("<I", header, start - 4)[0] != count:
                continue
            records = [header[start + i * RECORD_SIZE:start + (i + 1) * RECORD_SIZE]
                       for i in range(count)]
            keys = [struct.unpack_from("<iii", record) for record in records]
            if any(not 0 <= slot < 64 or not 0 < client < 1_000_000
                   or not (team in (-3, -2, -1, 0) or 0 < team < 10_000)
                   for slot, client, team in keys):
                continue
            if len({k[0] for k in keys}) != count or len({k[1] for k in keys}) != count:
                continue
            local_id, local_team = struct.unpack_from("<ii", header, start - 12)
            yield start, local_id, local_team, records


def parse_replay(data: bytes, filename: str = "replay.fwr", my_id: str | None = None) -> dict:
    length = header_length(data)
    if len(data) < length + 4 or data[length:length + 4] != HEADER_END:
        raise ReplayError("Заголовок обрезан или его структура не поддерживается.")
    candidates = list(_tables(data[:length]))
    if len(candidates) != 1:
        raise ReplayError("Не удалось однозначно определить таблицу игроков. Формат мог измениться.")
    start, local_id, local_team, records = candidates[0]
    players, warnings = [], []
    for record in records:
        slot, client_id, team = struct.unpack_from("<iii", record)
        # Fixed 16 UTF-16 code units, not a variable length ASCII string.
        units = record[12:44]
        for offset in range(0, 32, 2):
            if units[offset:offset + 2] == b"\x00\x00":
                units = units[:offset]
                break
        try:
            name = units.decode("utf-16-le")
        except UnicodeDecodeError:
            name = units.decode("utf-16-le", errors="replace")
            warnings.append(f"Имя в слоте {slot} содержит неполную последовательность UTF-16.")
        steam_id, status = decode_steam_id(record)
        if status == "invalid":
            warnings.append(f"Steam ID в слоте {slot} не прошёл проверку; значение не выдано за настоящий ID.")
        players.append({"slot": slot, "client_id": client_id, "name": name,
                        "team": team, "side": team % 100 if team > 0 else None,
                        "steam_id": steam_id, "steam_id_status": status,
                        "profile_url": f"https://steamcommunity.com/profiles/{steam_id}" if steam_id else None,
                        "record_offset": start + len(players) * RECORD_SIZE,
                        "role": "player"})
    if my_id:
        local = next((p for p in players if p["steam_id"] == my_id), None)
        if local is None:
            warnings.append("Указанный Steam ID не найден: нельзя определить ваших соперников.")
    else:
        local = next((p for p in players if p["client_id"] == local_id), None)
    for player in players:
        if player is local:
            player["role"] = "self"
        elif player["team"] == -3:
            player["role"] = "observer"
        elif local and local["side"] and player["side"]:
            player["role"] = "ally" if player["side"] == local["side"] else "opponent"
    if local is None and not my_id:
        warnings.append("Автор реплея не найден в таблице; показан список участников без определения соперников.")
    map_match = re.search(rb"maps/[^\x00\r\n]{1,1024}\.fwe", data[:start])
    map_path = map_match.group().decode("utf-8", errors="replace") if map_match else None
    return {"file": filename, "format_version": 5, "header_bytes": length,
            "map": map_path.rsplit("/", 1)[-1][:-4] if map_path else None,
            "perspective": "specified_steam_id" if my_id else "replay_recorder",
            "recorder_client_id": local_id, "recorder_team": local_team,
            "players": players, "warnings": warnings}


def read_replay(path: Path, my_id: str | None = None) -> dict:
    with path.open("rb") as stream:
        prefix = stream.read(17)
        length = header_length(prefix)
        data = prefix + stream.read(length + 4 - len(prefix))
    return parse_replay(data, path.name, my_id)


def analyze_paths(paths: list[Path], my_id: str | None = None) -> list[dict]:
    results = []
    for path in paths:
        try:
            results.append(read_replay(path, my_id))
        except (OSError, ReplayError) as exc:
            results.append({"file": path.name, "error": str(exc)})
    return results


def print_results(results: list[dict]) -> None:
    for result in results:
        print(f"\n{result['file']}")
        if "error" in result:
            print("  Ошибка:", result["error"])
            continue
        for player in result["players"]:
            print(f"  {ROLES[player['role']]} | {player['name'] or '(без имени)'} | "
                  f"Steam ID: {player['steam_id'] or 'не сохранён / не распознан'} | команда {player['team']}")
            if player["profile_url"]:
                print("    " + player["profile_url"])
        for warning in result["warnings"]:
            print("  Примечание:", warning)


def make_server(*args, **kwargs):
    """Compatibility entry point; the server lives in app_server.py."""
    from app_server import make_server as build
    return build(*args, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Forts: ники и Steam ID из .fwr. Без аргументов открывается окно в браузере.")
    parser.add_argument("files", nargs="*", type=Path, help="Один или несколько .fwr")
    parser.add_argument("--gui", action="store_true", help="Открыть локальное окно с перетаскиванием файлов")
    parser.add_argument("--my-id", help="Ваш Steam ID64; по умолчанию используется автор реплея")
    parser.add_argument("--json", type=Path, metavar="FILE", help="Сохранить результат в JSON")
    args = parser.parse_args(argv)
    if args.my_id and (not re.fullmatch(r"[0-9]{17}", args.my_id) or not valid_steam_id(int(args.my_id))):
        parser.error("--my-id должен содержать корректный Steam ID64")
    if args.json and not args.files:
        parser.error("Для --json укажите хотя бы один файл")
    results = analyze_paths(args.files, args.my_id)
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    if args.gui or not args.files:
        from desktop_app import run
        return run(files=args.files, my_id=args.my_id)
    print_results(results)
    return 1 if any("error" in result for result in results) else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    raise SystemExit(main())
