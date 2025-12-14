DOMAIN = "modbus_mapped_device"

CONF_TRANSPORT = "transport"        # "tcp" | "rtu"
CONF_MAPPING = "mapping_file"       # e.g. "demo_device_a.json"

# TCP
CONF_HOST = "host"
CONF_PORT = "port"

# RTU
CONF_PORT_DEVICE = "serial_port"    # e.g. /dev/ttyUSB0
CONF_BAUDRATE = "baudrate"
CONF_BYTESIZE = "bytesize"
CONF_PARITY = "parity"
CONF_STOPBITS = "stopbits"

# Common
CONF_SLAVE_ID = "slave_id"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_TCP_PORT = 502
DEFAULT_SLAVE_ID = 1
DEFAULT_SCAN_INTERVAL = 5  # minutes

PLATFORMS = ["sensor", "binary_sensor"]
