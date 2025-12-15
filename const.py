DOMAIN = "modbus_mapped_device"

PLATFORMS = [
    "sensor",
    "binary_sensor",
    "number",
    "switch",
    "select",
    "button",
]

CONF_TRANSPORT = "transport"
CONF_MAPPING = "mapping_file"

CONF_HOST = "host"
CONF_PORT = "port"

CONF_PORT_DEVICE = "serial_port"
CONF_BAUDRATE = "baudrate"
CONF_BYTESIZE = "bytesize"
CONF_PARITY = "parity"
CONF_STOPBITS = "stopbits"

CONF_SLAVE_ID = "slave_id"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_TCP_PORT = 502
DEFAULT_SLAVE_ID = 1
DEFAULT_SCAN_INTERVAL = 60
