# Modbus Mapped Device – Mapping File Format (YAML)

Dieses Repository liefert Mapping-Dateien als YAML (`.yaml` / `.yml`) aus.
Jede Mapping-Datei beschreibt:
- ein Gerät (Metadaten)
- eine Liste von Entities
- pro Entity: wie aus Modbus-Registern gelesen wird und optional wie geschrieben wird

## Dateiname
- Muss auf `.yaml` oder `.yml` enden
- Wird im Config-/Option-Flow als Auswahl angezeigt

## Top-Level Struktur

```yaml
device:
  name: <string>
  manufacturer: <string|null>
  model: <string|null>

entities:
  - platform: <sensor|binary_sensor|number|switch|select|button>
    key: <string>
    name: <string>             # optional, default=key
    unit: <string>             # optional (z.B. "V", "W", "°C")
    icon: <string>             # optional (mdi:...)

    read:                      # optional; ohne read ist Entity nur "write-only" (eher selten)
      type: holding            # aktuell implementiert: holding (kann später erweitert werden)
      address: <int>           # 0-basiert (Modbus Address)
      data_type: <uint16|int16|uint32|int32|float32>   # optional, default=uint16
      scale: <float>           # optional, wird beim Lesen multipliziert
      word_order: <AB|BA>      # optional, default=AB (BA = Word Swap bei 32-bit)

    write:                     # optional; wenn gesetzt ist Entity schreibbar
      type: holding            # aktuell implementiert: holding
      address: <int>
      scale: <float>           # optional, UI-Wert wird durch scale geteilt, bevor geschrieben wird
      bit: <int>               # optional; wenn gesetzt -> Holding-Bit-Switch (Read-Modify-Write)
