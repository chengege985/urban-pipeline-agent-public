"""Authoritative channel identities for the registered synthetic GAS-M0 replay."""

K001_PRESSURE_CHANNEL = ("syn-gas-pipe-K-001", "syn-gas-monitor-pressure-k001", "pressure", "kPa")
K003_FLOW_CHANNEL = ("syn-gas-pipe-K-003", "syn-gas-monitor-flow-k003", "flow", "Nm3/h")
GAS_M0_MONITORING_CHANNELS = frozenset((K001_PRESSURE_CHANNEL, K003_FLOW_CHANNEL))
